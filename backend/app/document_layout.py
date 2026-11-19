"""Validated document appearance, stored independently from content revisions."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.document_fonts import validate_font_name


FontFamily = str


class AppearanceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    @field_validator("font_family", check_fields=False)
    @classmethod
    def valid_font_family(cls, value: str) -> str:
        return validate_font_name(value)


class HeadingAppearance(AppearanceModel):
    font_family: FontFamily = "黑体"
    font_size_pt: float = Field(default=18, ge=8, le=48)
    line_spacing: float = Field(default=1.5, ge=1, le=3)
    space_before_pt: float = Field(default=18, ge=0, le=72)
    space_after_pt: float = Field(default=12, ge=0, le=72)


def default_headings() -> list[HeadingAppearance]:
    return [HeadingAppearance(font_size_pt=size, space_before_pt=before, space_after_pt=after)
            for size, before, after in zip((18, 16, 14, 12, 12, 12), (18, 12, 12, 6, 6, 6), (12, 6, 6, 6, 6, 6))]


class DocumentStyle(AppearanceModel):
    font_family: FontFamily = "宋体"
    font_size_pt: float = Field(default=12, ge=8, le=36)
    line_spacing: float = Field(default=1.5, ge=1, le=3)
    paragraph_before_pt: float = Field(default=0, ge=0, le=72)
    paragraph_after_pt: float = Field(default=6, ge=0, le=72)
    first_line_indent_pt: float = Field(default=24, ge=0, le=72)
    margin_top_mm: float = Field(default=25.4, ge=10, le=60)
    margin_bottom_mm: float = Field(default=25.4, ge=10, le=60)
    margin_left_mm: float = Field(default=25.4, ge=10, le=60)
    margin_right_mm: float = Field(default=25.4, ge=10, le=60)
    headings: list[HeadingAppearance] = Field(default_factory=default_headings, min_length=6, max_length=6)


class LayoutUpdate(AppearanceModel):
    expected_revision: int = Field(ge=1)
    style: DocumentStyle

    @model_validator(mode="before")
    @classmethod
    def complete_style(cls, value):
        if isinstance(value, dict) and isinstance(value.get("style"), dict):
            style = value["style"]
            missing = set(DocumentStyle.model_fields) - set(style)
            if missing:
                raise ValueError("排版设置必须完整提交，缺少：" + ", ".join(sorted(missing)))
            for heading in style.get("headings", []):
                if isinstance(heading, dict) and set(HeadingAppearance.model_fields) - set(heading):
                    raise ValueError("每一级标题必须完整提交所有排版字段。")
        return value


class DocumentLayout(AppearanceModel):
    document_id: str
    revision: int
    style: DocumentStyle


def _existing_document(connection, document_id: str) -> None:
    if connection.execute("SELECT id FROM workspace_documents WHERE id=?", (document_id,)).fetchone() is None:
        raise HTTPException(404, "文档不存在。")


def _stored_layout(connection, document_id: str) -> DocumentLayout:
    row = connection.execute("SELECT payload FROM records WHERE namespace='document_layouts' AND record_id=?", (document_id,)).fetchone()
    if row is None:
        return DocumentLayout(document_id=document_id, revision=1, style=DocumentStyle())
    return DocumentLayout.model_validate_json(row["payload"])


def get_document_layout(store, document_id: str) -> DocumentLayout:
    with store._lock:
        _existing_document(store._connection, document_id)
        return _stored_layout(store._connection, document_id)


def save_document_layout(store, document_id: str, expected_revision: int, style: DocumentStyle) -> DocumentLayout:
    with store._transaction() as connection:
        _existing_document(connection, document_id)
        previous = _stored_layout(connection, document_id)
        if previous.revision != expected_revision:
            raise HTTPException(409, "排版设置已更新，请刷新后重试。")
        saved = DocumentLayout(document_id=document_id, revision=previous.revision + 1, style=style)
        connection.execute("INSERT INTO records(namespace,record_id,payload,updated_at) VALUES ('document_layouts',?,?,?) "
                           "ON CONFLICT(namespace,record_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                           (document_id, saved.model_dump_json(), datetime.now(timezone.utc).isoformat()))
        return saved


def extract_docx_document_style(reader, document_root) -> tuple[DocumentStyle | None, list[str]]:
    """Infer explicit OOXML appearance; every defaulted field remains disclosed."""
    from app.document_import import _attr, _child, _local, _resolve_part
    warnings: list[str] = []
    base = DocumentStyle().model_dump()
    recognized: set[str] = set()
    style_nodes = {}
    defaults = {}
    main = reader.main_part()
    path = PurePosixPath(main)
    rels = str(path.parent / "_rels" / (path.name + ".rels"))
    styles_name = next((_resolve_part(main, rel.get("Target", "")) for rel in reader.relationships(rels)
                        if rel.get("Type", "").endswith("/styles") and rel.get("TargetMode", "").lower() != "external"), None)

    def number(value, divisor=1):
        try:
            return float(value) / divisor
        except (TypeError, ValueError):
            return None

    def props(node):
        result = {}
        run = _child(node, "rPr") if node is not None else None
        paragraph = _child(node, "pPr") if node is not None else None
        fonts = _child(run, "rFonts") if run is not None else None
        if fonts is not None:
            raw = _attr(fonts, "eastAsia") or _attr(fonts, "ascii") or _attr(fonts, "hAnsi")
            if raw:
                try:
                    result["font_family"] = validate_font_name(raw)
                except ValueError:
                    warnings.append(f"字体名称 {raw[:80]} 无效，未作为排版规则使用，请确认。")
        size = _child(run, "sz") if run is not None else None
        if size is not None:
            value = number(_attr(size, "val"), 2)
            if value is not None:
                result["font_size_pt"] = value
        spacing = _child(paragraph, "spacing") if paragraph is not None else None
        if spacing is not None:
            for attr, key in (("before", "space_before_pt"), ("after", "space_after_pt")):
                raw = _attr(spacing, attr)
                if raw:
                    value = number(raw, 20)
                    if value is not None:
                        result[key] = value
            raw_line = _attr(spacing, "line")
            if raw_line and _attr(spacing, "lineRule") in {"", "auto"}:
                value = number(raw_line, 240)
                if value is not None:
                    result["line_spacing"] = value
            elif raw_line:
                warnings.append("发现固定值或最小值行距，当前倍数行距不能直接表达，暂用默认值，请确认。")
        indent = _child(paragraph, "ind") if paragraph is not None else None
        if indent is not None and _attr(indent, "firstLine"):
            result["first_line_indent_pt"] = number(_attr(indent, "firstLine"), 20)
        return result

    if styles_name:
        styles_root = reader.read_xml(styles_name)
        doc_defaults = _child(styles_root, "docDefaults")
        if doc_defaults is not None:
            for child in doc_defaults:
                defaults.update(props(child))
        for node in styles_root:
            if _local(node.tag) == "style" and _attr(node, "type") == "paragraph":
                style_nodes[_attr(node, "styleId")] = node

    def effective(identity, seen=None):
        seen = set() if seen is None else seen
        if identity in seen or len(seen) > 50 or identity not in style_nodes:
            return dict(defaults)
        seen.add(identity)
        node = style_nodes[identity]
        based = _child(node, "basedOn")
        value = effective(_attr(based, "val"), seen) if based is not None else dict(defaults)
        value.update(props(node))
        return value

    normal_id = next((identity for identity, node in style_nodes.items() if _attr(node, "default") == "1"), "Normal")
    body = effective(normal_id)
    # A template may express body appearance directly on paragraphs. Only agree-once
    # values are usable: mixed paragraph formatting must not masquerade as a rule.
    direct_values: dict[str, set] = {}
    for paragraph in document_root.iter():
        if _local(paragraph.tag) != "p":
            continue
        ppr = _child(paragraph, "pPr")
        pstyle = _child(ppr, "pStyle") if ppr is not None else None
        identity = _attr(pstyle, "val") if pstyle is not None else normal_id
        if identity in reader.heading_styles:
            continue
        direct = props(paragraph)
        for run in paragraph:
            if _local(run.tag) == "r":
                for key, value in props(run).items():
                    direct_values.setdefault(key, set()).add(value)
        for key, value in direct.items():
            direct_values.setdefault(key, set()).add(value)
    for key, choices in direct_values.items():
        if len(choices) == 1:
            body[key] = next(iter(choices))
        elif len(choices) > 1:
            warnings.append(f"正文 {key} 有多种取值，未使用混合值覆盖样式规则。")

    def apply(values, target, prefix="", heading=False):
        fields = HeadingAppearance.model_fields if heading else DocumentStyle.model_fields
        for key, value in values.items():
            actual = key if heading else {"space_before_pt": "paragraph_before_pt", "space_after_pt": "paragraph_after_pt"}.get(key, key)
            if actual not in fields or value is None:
                continue
            trial = {**target, actual: value}
            try:
                (HeadingAppearance if heading else DocumentStyle).model_validate(trial)
            except ValueError:
                warnings.append(f"识别到 {prefix + actual}={value} 超出当前可调范围，暂用默认值，请确认。")
                continue
            target[actual] = value
            recognized.add(prefix + actual)

    apply(body, base)
    for level in range(1, 7):
        candidates = [identity for identity, found_level in reader.heading_styles.items() if found_level == level]
        if candidates:
            values = [effective(identity) for identity in candidates]
            agreed = {key: value for key, value in values[0].items() if all(item.get(key) == value for item in values[1:])}
            apply(agreed, base["headings"][level - 1], f"headings[{level - 1}].", heading=True)
    sections = [node for node in document_root.iter() if _local(node.tag) == "sectPr"]
    margins = []
    for section in sections:
        node = _child(section, "pgMar")
        if node is not None:
            margins.append({f"margin_{edge}_mm": number(_attr(node, edge), 1440 / 25.4) for edge in ("top", "bottom", "left", "right") if _attr(node, edge)})
    if margins:
        apply({key: value for key, value in margins[0].items() if all(item.get(key) == value for item in margins[1:])}, base)
        if any(item != margins[0] for item in margins[1:]):
            warnings.append("各节页边距不同，仅使用一致的项目；分节分页不能自动还原，请确认。")
    if not recognized:
        return None, list(dict.fromkeys(warnings + ["未识别可用的字体、段落或页边距规则；未生成学校排版配置，请手动设置。 "]))[:20]
    missing = [key for key in DocumentStyle.model_fields if key != "headings" and key not in recognized]
    if missing:
        warnings.append("正文/页面未识别字段暂用默认值，待确认：" + ", ".join(missing))
    for index in range(6):
        missing_heading = [key for key in HeadingAppearance.model_fields if f"headings[{index}].{key}" not in recognized]
        if missing_heading:
            warnings.append(f"第{index + 1}级标题未识别字段暂用默认值，待确认：" + ", ".join(missing_heading))
    warnings.insert(0, f"已识别{len(recognized)}个排版字段；未识别项保留默认值供确认，不能视为学校规定。")
    return DocumentStyle.model_validate(base), list(dict.fromkeys(warnings))[:20]


router = APIRouter()


@router.get("/workspace/documents/{document_id}/layout", response_model=DocumentLayout)
def read_layout(document_id: str) -> DocumentLayout:
    from app.agent_api import get_store
    return get_document_layout(get_store(), document_id)


@router.put("/workspace/documents/{document_id}/layout", response_model=DocumentLayout)
def update_layout(document_id: str, payload: LayoutUpdate) -> DocumentLayout:
    from app.agent_api import get_store
    return save_document_layout(get_store(), document_id, payload.expected_revision, payload.style)
