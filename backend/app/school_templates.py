"""School heading templates: validated local data, never executable instructions."""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import PurePosixPath
import re
import zipfile

from typing import Literal

from pydantic import BaseModel, Field, JsonValue, field_validator, model_validator

from app.heading_numbering import DEFAULT_HEADING_STYLES, HeadingStyle, parse_prefix, unwrap_heading
from app.document_layout import DocumentStyle, extract_docx_document_style
from app.document_profiles import DOCUMENT_KINDS, REVIEW_PROFILES


class TemplateRuleSource(BaseModel):
    """A bounded data record. Source text is never executable or a model instruction."""
    id: str = Field(min_length=1, max_length=100)
    source: str = Field(min_length=1, max_length=300)
    quote: str = Field(default="", max_length=1000)
    field: str = Field(min_length=1, max_length=100)
    value: JsonValue
    status: Literal["explicit", "inferred", "confirmed", "conflict"] = "explicit"
    note: str = Field(default="", max_length=500)


class SchoolTemplateDraft(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    institution: str = Field(default="自定义学校模板", min_length=1, max_length=120)
    heading_numbering: list[HeadingStyle] = Field(min_length=6, max_length=6)
    document_style: DocumentStyle | None = None
    source_name: str | None = Field(default=None, max_length=255)
    warnings: list[str] = Field(default_factory=list, max_length=30)
    document_kind: str = "thesis"
    review_profile_id: str = "academic"
    required_sections: list[str] = Field(default_factory=list, max_length=30)
    cover_fields: list[str] = Field(default_factory=list, max_length=30)
    submission_checklist: list[str] = Field(default_factory=list, max_length=40)
    rule_sources: list[TemplateRuleSource] = Field(default_factory=list, max_length=200)
    parent_template_id: str | None = Field(default=None, max_length=100)

    @model_validator(mode="before")
    @classmethod
    def compatible_profile(cls, value):
        if isinstance(value, dict) and "review_profile_id" not in value:
            value = {**value, "review_profile_id": "academic" if value.get("document_kind", "thesis") == "thesis" else "general"}
        return value

    @field_validator("document_kind")
    @classmethod
    def known_kind(cls, value: str) -> str:
        if value not in DOCUMENT_KINDS:
            raise ValueError("不支持的文档类型。")
        return value

    @field_validator("review_profile_id")
    @classmethod
    def known_review_profile(cls, value: str) -> str:
        if value not in REVIEW_PROFILES:
            raise ValueError("不支持的审查模式。")
        return value

    @field_validator("required_sections", "cover_fields", "submission_checklist")
    @classmethod
    def clean_items(cls, value: list[str]) -> list[str]:
        result = []
        for item in value:
            item = item.strip()
            if not item or len(item) > 200 or any(ord(char) < 32 for char in item):
                raise ValueError("模板条目应为 1 至 200 字的单行文字。")
            if item not in result:
                result.append(item)
        return result

    @field_validator("name", "institution")
    @classmethod
    def clean_label(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("模板名称和学校名称不能为空或含控制字符。")
        return value

    @field_validator("warnings")
    @classmethod
    def bounded_warnings(cls, value: list[str]) -> list[str]:
        if any(len(item) > 500 for item in value):
            raise ValueError("模板提示过长。")
        return value


def load_profiles(store) -> list[dict]:
    with store._lock:
        rows = store._connection.execute("SELECT payload FROM records WHERE namespace='format_profiles' ORDER BY updated_at DESC,record_id").fetchall()
    return [json.loads(row["payload"]) for row in rows]


def save_profile(store, profile: dict) -> None:
    encoded = json.dumps(profile, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    with store._transaction() as connection:
        connection.execute("INSERT INTO records(namespace,record_id,payload,updated_at) VALUES ('format_profiles',?,?,?) ON CONFLICT(namespace,record_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at", (profile["id"], encoded, profile["updated_at"]))


def _word_number_style(definition: dict) -> HeadingStyle | None:
    value, pattern = definition.get("format", ""), definition.get("text", "")
    chinese = value.startswith("chinese") or value.startswith("ideograph")
    if re.search(r"第%\d[章节篇部]", pattern):
        return "chapter"
    if re.search(r"[（(]%\d[）)]", pattern):
        return "chinese-paren" if chinese else "arabic-paren" if value == "decimal" else None
    if chinese and re.search(r"%\d[、．.]", pattern):
        return "chinese"
    if value == "decimal" and re.fullmatch(r"%\d(?:[.．]%\d)*[.．、]?\s*", pattern):
        return "decimal"
    if value == "none":
        return "none"
    return None


def import_template(filename: str, data: bytes) -> SchoolTemplateDraft:
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if not name or len(name) > 255 or any(ord(char) < 32 for char in name):
        raise ValueError("模板文件名无效。")
    suffix = PurePosixPath(name).suffix.casefold()
    if not data:
        raise ValueError("模板文件为空。")
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("模板不能超过 10 MB。")
    if suffix == ".json":
        try:
            payload = json.loads(data.decode("utf-8-sig"))
            if not isinstance(payload, dict):
                raise ValueError("模板 JSON 必须是对象。")
            payload = {**payload, "source_name": name}
            return SchoolTemplateDraft.model_validate(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("模板 JSON 必须为有效 UTF-8 JSON。") from error
    if suffix != ".docx":
        raise ValueError("学校模板支持 DOCX 样例或 JSON 编号规则。")
    from app.document_import import _DocxReader, _attr, _child, _heading_number, _local, _number_properties
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            reader = _DocxReader(archive)
            content, _ = reader.extract()
            candidates: list[set[str]] = [set() for _ in range(6)]
            for match in re.finditer(r"(?m)^(#{1,6})\s+(.+)$", content):
                core, _ = unwrap_heading(match.group(2))
                parsed = parse_prefix(core)
                if parsed and parsed.style != "appendix":
                    candidates[len(match.group(1)) - 1].add(parsed.style)
            # Word automatic numbering lives in styles/numbering.xml rather than text.
            for identity, level in reader.heading_styles.items():
                numbering = dict(reader.styles.get(identity, {}).get("number", {}))
                if identity in reader.numbering_styles and not numbering:
                    num_id, list_level = reader.numbering_styles[identity]
                    numbering = {"numId": num_id, "ilvl": str(list_level)}
                if numbering.get("numId"):
                    definition = reader.numberings.get(numbering["numId"], {}).get(int(numbering.get("ilvl", "0")), {})
                    inferred = _word_number_style(definition)
                    if inferred:
                        candidates[level - 1].add(inferred)
            root = reader.read_xml(reader.main_part())
            for paragraph in root.iter():
                if _local(paragraph.tag) != "p":
                    continue
                props = _child(paragraph, "pPr")
                style_node = _child(props, "pStyle") if props is not None else None
                identity = _attr(style_node, "val") if style_node is not None else ""
                outline = _child(props, "outlineLvl") if props is not None else None
                level = reader.heading_styles.get(identity) or _heading_number(identity)
                if outline is not None and _attr(outline, "val") in {str(index) for index in range(6)}:
                    level = int(_attr(outline, "val")) + 1
                numbering = {**reader.styles.get(identity, {}).get("number", {}), **_number_properties(props)}
                if not level or not numbering.get("numId"):
                    continue
                definition = reader.numberings.get(numbering["numId"], {}).get(int(numbering.get("ilvl", "0")), {})
                inferred = _word_number_style(definition)
                if inferred:
                    candidates[level - 1].add(inferred)
            document_style, layout_warnings = extract_docx_document_style(reader, root)
            warnings = ["提取标题编号与可识别排版规则；分节分页、引用版式与未识别字段请确认后保存。"]
            result = list(DEFAULT_HEADING_STYLES)
            for index, choices in enumerate(candidates):
                if len(choices) == 1:
                    result[index] = next(iter(choices))
                elif len(choices) > 1:
                    warnings.append(f"第 {index + 1} 级出现多种编号，暂保留原编号，请手动选择学校规则。")
                else:
                    warnings.append(f"未明确识别第 {index + 1} 级编号，暂保留原编号，可手动调整。")
            from app.template_rules import sample_rule_sources
            return SchoolTemplateDraft(name=PurePosixPath(name).stem[:120], heading_numbering=result,
                                       document_style=document_style, source_name=name,
                                       rule_sources=sample_rule_sources(name, candidates, document_style),
                                       warnings=(warnings + layout_warnings)[:30])
    except (zipfile.BadZipFile, OSError, RuntimeError, EOFError, OverflowError) as error:
        raise ValueError("DOCX 模板损坏或结构异常，请另存为有效 DOCX。") from error
