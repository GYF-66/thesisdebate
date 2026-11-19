"""Deterministic template proposals with evidence and explicit human decisions.

Uploaded prose is parsed as bounded data. It never becomes an instruction or
executes code, fetches URLs, changes documents, or overrides a formal rule.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
import re

from pydantic import BaseModel, Field

from app.document_layout import DocumentStyle
from app.document_fonts import match_font_name
from app.document_profiles import DEFAULT_SECTIONS, DOCUMENT_KINDS
from app.heading_numbering import DEFAULT_HEADING_STYLES
from app.school_templates import SchoolTemplateDraft, TemplateRuleSource

SIZES = {"小初": 36, "小一": 24, "小二": 18, "小三": 15, "小四": 12, "小五": 9, "初": 42, "一": 26, "二": 22, "三": 16, "四": 14, "五": 10.5, "六": 7.5}
STYLE_FIELDS = {key for key in DocumentStyle.model_fields if key != "headings"}
LIST_FIELDS = {"required_sections", "cover_fields", "submission_checklist"}
LEVELS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, **{str(i): i - 1 for i in range(1, 7)}}


class TemplateIssue(BaseModel):
    id: str
    severity: str
    title: str
    explanation: str
    field: str
    proposed_value: object | None = None
    can_apply: bool = False


class TemplateAudit(BaseModel):
    issues: list[TemplateIssue] = Field(default_factory=list)
    unresolved_conflicts: int = 0
    pending_sources: int = 0
    summary: str


def _identity(source: str, quote: str, field: str, value) -> str:
    packed = json.dumps([source, quote, field, value], ensure_ascii=False, sort_keys=True)
    return "rule-" + sha256(packed.encode()).hexdigest()[:18]


def rule(source: str, quote: str, field: str, value, status: str = "explicit", note: str = "") -> TemplateRuleSource:
    return TemplateRuleSource(id=_identity(source, quote, field, value), source=source[:300], quote=quote[:1000],
                              field=field, value=value, status=status, note=note[:500])


def sample_rule_sources(name: str, candidates: list[set[str]], style: DocumentStyle | None) -> list[TemplateRuleSource]:
    result = []
    for index, choices in enumerate(candidates):
        for value in sorted(choices):
            result.append(rule(name, f"样例中第 {index + 1} 级标题的编号形式", f"heading_numbering.{index}", value,
                               "conflict" if len(choices) > 1 else "inferred", "来自样例推断，不等于规范明文；保存前请确认。"))
    if style is not None:
        values = style.model_dump(mode="json")
        for field in sorted(STYLE_FIELDS):
            result.append(rule(name, "DOCX 样例的正文与页面样式", "document_style." + field, values[field],
                               "inferred", "样例推断，未识别项目可能为默认值；请结合识别警告核对。"))
        for index, heading in enumerate(style.headings):
            for field, value in heading.model_dump(mode="json").items():
                result.append(rule(name, f"DOCX 样例的第 {index + 1} 级标题样式", f"document_style.headings.{index}.{field}", value,
                                   "inferred", "样例推断，未识别项目可能为默认值；请结合识别警告核对。"))
    return result


def _unused(item: TemplateRuleSource) -> bool:
    return item.note.startswith("未采用")


def _get(draft: SchoolTemplateDraft, field: str):
    if field in LIST_FIELDS:
        return list(getattr(draft, field))
    if field == "document_style":
        return draft.document_style.model_dump(mode="json") if draft.document_style else None
    match = re.fullmatch(r"heading_numbering\.([0-5])", field)
    if match:
        return draft.heading_numbering[int(match[1])]
    if field.startswith("document_style.") and draft.document_style:
        parts = field.split(".")[1:]
        value = draft.document_style.model_dump(mode="json")
        try:
            for part in parts:
                value = value[int(part)] if isinstance(value, list) else value[part]
            return value
        except (KeyError, IndexError, ValueError, TypeError):
            return None
    return None


def _set(draft: SchoolTemplateDraft, field: str, value) -> SchoolTemplateDraft:
    payload = draft.model_dump(mode="json")
    if field in LIST_FIELDS:
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError("章节、封面和提交要求必须是文字列表。")
        payload[field] = value
    elif field == "document_style":
        payload[field] = DocumentStyle.model_validate(value).model_dump(mode="json")
    elif re.fullmatch(r"heading_numbering\.([0-5])", field):
        payload["heading_numbering"][int(field.rsplit(".", 1)[1])] = value
    elif field.startswith("document_style."):
        subfield = field.removeprefix("document_style.")
        style = draft.document_style.model_dump(mode="json") if draft.document_style else DocumentStyle().model_dump(mode="json")
        heading = re.fullmatch(r"headings\.([0-5])\.(font_family|font_size_pt|line_spacing|space_before_pt|space_after_pt)", subfield)
        if subfield in STYLE_FIELDS:
            style[subfield] = value
        elif heading:
            style["headings"][int(heading[1])][heading[2]] = value
        else:
            raise ValueError("规则字段不属于可应用的模板参数。")
        payload["document_style"] = DocumentStyle.model_validate(style).model_dump(mode="json")
    else:
        raise ValueError("规则字段不属于可应用的模板参数。")
    return SchoolTemplateDraft.model_validate(payload)


def _items(value: str) -> list[str]:
    return list(dict.fromkeys(item.strip(" 	。.;；：:、-") for item in re.split(r"[、,，;；]|\n", value)
                             if item.strip(" 	。.;；：:、-")))[:30]


def extract_rules(text: str, source_name: str = "粘贴的规范", document_kind: str = "thesis",
                  base_template: SchoolTemplateDraft | None = None) -> SchoolTemplateDraft:
    if document_kind not in DOCUMENT_KINDS:
        raise ValueError("不支持的文档类型。")
    if not text.strip() or len(text) > 100_000:
        raise ValueError("规范文字应为 1 至 100000 字。")
    source_name = source_name.strip()[:240] or "粘贴的规范"
    draft = base_template.model_copy(deep=True) if base_template else SchoolTemplateDraft(
        name=source_name.rsplit(".", 1)[0][:100] + "规则草案", heading_numbering=list(DEFAULT_HEADING_STYLES),
        document_kind=document_kind, review_profile_id="academic" if document_kind == "thesis" else "general")
    draft.source_name = source_name
    found: list[TemplateRuleSource] = []
    warnings = list(draft.warnings)
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if len(line) > 3000:
            warnings.append(f"第 {number} 行过长，仅检查前 3000 字。")
            line = line[:3000]
        source = source_name + f" · 第 {number} 行"
        if line.startswith("[第") and "页]" in line:
            page, line = line.split("页]", 1)
            source = source_name + " · " + page.lstrip("[") + "页"
            line = line.strip()
        level = re.search(r"([一二三四五六1-6])级标题", line)
        prefix = "document_style.headings." + str(LEVELS[level[1]]) + "." if level else "document_style."
        applicable = bool(level or re.search(r"正文|全文|全篇|字体|字号|行距|首行|页边距|段前|段后|font|line spacing", line, re.I))

        def add(field, value):
            if len(found) >= 180:
                return
            # Validate every proposed value without applying it to the returned draft.
            try:
                _set(draft, field, value)
            except ValueError:
                warnings.append("无法表达或超出支持范围，请人工核对：" + line[:120])
                return
            found.append(rule(source, line, field, value))

        if applicable:
            font = match_font_name(line)
            if font:
                add(prefix + "font_family", font)
            size = re.search(r"(小[初一二三四五]|[初一二三四五六])号", line)
            if not size:
                size = re.search(r"(?:字体|字号|正文)\s*[:：为用采用]*\s*(小[初一二三四五])", line)
            if size:
                add(prefix + "font_size_pt", SIZES[size[1]])
            else:
                points = re.search(r"(?:字号|字体大小|font size)\s*[:：为用采用]*\s*(\d+(?:\.\d+)?)\s*(?:磅|pt)?", line, re.I)
                if points:
                    add(prefix + "font_size_pt", float(points[1]))
            spacing = re.search(r"(\d+(?:\.\d+)?)\s*倍(?:行距)?|行距\s*[:：为]*\s*(\d+(?:\.\d+)?)\s*倍", line)
            if spacing:
                add(prefix + "line_spacing", float(spacing[1] or spacing[2]))
            elif re.search(r"单倍行距|双倍行距|两倍行距", line):
                add(prefix + "line_spacing", 1.0 if "单倍" in line else 2.0)
            elif re.search(r"(?:固定|最小)值?\s*(?:行距)?\s*\d+|行距.{0,8}\d+(?:\.\d+)?\s*(?:磅|pt|厘米|毫米)", line):
                warnings.append("固定值/最小值行距暂不能表达，未强制转换：" + line[:120])
            for label, field in (("段前", "space_before_pt" if level else "paragraph_before_pt"),
                                 ("段后", "space_after_pt" if level else "paragraph_after_pt")):
                match = re.search(label + r"\s*[:：为]*\s*(\d+(?:\.\d+)?)\s*(?:磅|pt)", line, re.I)
                if match:
                    add(prefix + field, float(match[1]))
            indent = re.search(r"首行(?:缩进)?\s*[:：为]*\s*(\d+(?:\.\d+)?)\s*(?:磅|pt)", line, re.I)
            if indent and not level:
                add("document_style.first_line_indent_pt", float(indent[1]))
            elif re.search(r"首行(?:缩进)?\s*[二两2]\s*(?:个)?[字字符]", line):
                warnings.append("首行按字符缩进需结合已确认字号换算；未将“两字”擅自固定为磅值。")
        for label, edge in (("上", "top"), ("下", "bottom"), ("左", "left"), ("右", "right")):
            match = re.search(label + r"(?:页)?边距\s*[:：为]*\s*(\d+(?:\.\d+)?)\s*(厘米|毫米|cm|mm)", line, re.I)
            if match:
                add("document_style.margin_" + edge + "_mm", float(match[1]) * (10 if match[2].lower() in {"厘米", "cm"} else 1))
        for pattern, field in ((r"(?:必须|应|需)?(?:包含|包括|设置)(?:以下)?(?:必要|必需)?章节\s*[:：]\s*(.+)", "required_sections"),
                               (r"(?:必要|必需|必须|应有)章节\s*[:：]\s*(.+)", "required_sections"),
                               (r"封面(?:须|应|需|必须)?(?:包含|包括|字段)?\s*[:：]\s*(.+)", "cover_fields"),
                               (r"提交(?:要求|清单|材料)\s*[:：]\s*(.+)", "submission_checklist")):
            match = re.search(pattern, line)
            if match:
                add(field, _items(match[1]))
        if level and re.search(r"编号|序号|采用|使用", line):
            numbering = None
            if re.search(r"第一章|第[一1]章", line):
                numbering = "chapter"
            elif re.search(r"[（(]一[）)]", line):
                numbering = "chinese-paren"
            elif re.search(r"[（(]1[）)]", line):
                numbering = "arabic-paren"
            elif re.search(r"一、", line):
                numbering = "chinese"
            elif re.search(r"1\.1|层级数字|阿拉伯数字", line):
                numbering = "decimal"
            elif re.search(r"不(?:要|加|设)?编号|无编号", line):
                numbering = "none"
            if numbering:
                add("heading_numbering." + str(LEVELS[level[1]]), numbering)
    sources = list(draft.rule_sources)
    known = {item.id for item in sources}
    for item in found:
        if item.id not in known:
            sources.append(item)
            known.add(item.id)
    # Compare against existing selected settings without overwriting either value.
    if base_template:
        for item in found:
            previous = _get(base_template, item.field)
            if previous is not None and previous != item.value and not any(
                old.field == item.field and old.value == previous and not _unused(old) for old in sources
            ):
                sources.append(rule(base_template.source_name or "当前模板", "当前已选模板的设置", item.field, previous, "inferred",
                                    "保留当前值作为候选，需与新增规范并列确认。"))
    grouped = defaultdict(list)
    for item in sources:
        if not _unused(item):
            grouped[item.field].append(item)
    for items in grouped.values():
        if len({json.dumps(item.value, ensure_ascii=False, sort_keys=True) for item in items}) > 1:
            for item in items:
                item.status = "conflict"
    draft.rule_sources = sources[:200]
    if not found:
        warnings.append("未识别到可直接表达的规则。原文未作为指令执行，请手动填写或补充明确规范。")
    warnings.append("识别结果是待确认草案；只有选择并确认的规则才写入模板。未知项不会自动成为强制要求。")
    draft.warnings = list(dict.fromkeys(warnings))[:30]
    return draft


def resolve_rules(draft: SchoolTemplateDraft, confirm_ids: list[str], reject_ids: list[str]) -> SchoolTemplateDraft:
    if set(confirm_ids) & set(reject_ids):
        raise ValueError("同一规则不能同时确认和排除。")
    known = {item.id: item for item in draft.rule_sources}
    if (set(confirm_ids) | set(reject_ids)) - set(known):
        raise ValueError("规则已变化，请重新检查草案。")
    chosen = {}
    for identity in confirm_ids:
        item = known[identity]
        if item.field in chosen and chosen[item.field] != item.value:
            raise ValueError("同一字段只能确认一个取值。")
        chosen[item.field] = item.value
    result = draft.model_copy(deep=True)
    for field, value in chosen.items():
        result = _set(result, field, value)
    for item in result.rule_sources:
        if item.id in confirm_ids:
            item.status = "confirmed"
            item.note = "用户已确认；作为当前模板规则。"
        elif item.id in reject_ids or item.field in chosen:
            item.status = "inferred"
            item.note = "未采用：用户已排除或选择同字段的其他取值。"
    return result


def merge_template_rules(base: SchoolTemplateDraft, incoming: SchoolTemplateDraft) -> SchoolTemplateDraft:
    """Merge provenance into an unsaved draft without dropping local form edits."""
    result = base.model_copy(deep=True)
    known = {item.id for item in result.rule_sources}
    for item in incoming.rule_sources:
        if item.id not in known:
            result.rule_sources.append(item.model_copy(deep=True))
            known.add(item.id)
        previous = _get(base, item.field)
        if previous is not None and previous != item.value and not any(
            old.field == item.field and old.value == previous and not _unused(old) for old in result.rule_sources
        ):
            result.rule_sources.append(rule(base.source_name or "当前草案", "当前草案保留的设置", item.field, previous, "inferred"))
    groups = defaultdict(list)
    for item in result.rule_sources:
        if not _unused(item):
            groups[item.field].append(item)
    for items in groups.values():
        if len({json.dumps(item.value, ensure_ascii=False, sort_keys=True) for item in items}) > 1:
            for item in items:
                item.status = "conflict"
    result.warnings = list(dict.fromkeys([*result.warnings, *incoming.warnings]))[:30]
    result.source_name = incoming.source_name
    return SchoolTemplateDraft.model_validate(result.model_dump(mode="json"))


def audit_template(draft: SchoolTemplateDraft) -> TemplateAudit:
    issues = []
    active = [item for item in draft.rule_sources if not _unused(item)]
    conflicts = len({item.field for item in active if item.status == "conflict"})
    if conflicts:
        issues.append(TemplateIssue(id="source-conflicts", severity="high", title="规范与样例存在冲突",
            explanation="同一字段出现不同取值。请在来源规则中选择一个并确认，不能由优化器替用户决定。", field="rule_sources"))
    pending = sum(item.status in {"explicit", "inferred"} for item in active)
    if pending:
        issues.append(TemplateIssue(id="pending-sources", severity="medium", title="仍有来源规则待确认",
            explanation="明文识别和样例推断均不等于已经确认；请逐项核对原文。", field="rule_sources"))
    if not draft.required_sections and DEFAULT_SECTIONS[draft.document_kind]:
        issues.append(TemplateIssue(id="missing-sections", severity="medium", title="未设置文档章节清单",
            explanation="可选用该文档类型的常见章节作为起点；这是产品建议，不是学校硬性规范。",
            field="required_sections", proposed_value=DEFAULT_SECTIONS[draft.document_kind], can_apply=True))
    if not draft.submission_checklist:
        issues.append(TemplateIssue(id="missing-delivery", severity="low", title="尚无提交前清单",
            explanation="可添加通用核对事项，再根据课程或学校要求调整。", field="submission_checklist",
            proposed_value=["核对文件命名和提交格式", "核对模板要求及必要章节", "人工检查导出文件的分页、图表和公式"], can_apply=True))
    if not draft.rule_sources:
        issues.append(TemplateIssue(id="missing-provenance", severity="medium", title="规则缺少出处",
            explanation="上传规范文字或 PDF，保留原文位置；当前设置不能据此宣称通过学校规范。", field="rule_sources"))
    style = draft.document_style
    if style and style.font_size_pt < 10:
        field = "document_style.font_size_pt"
        protected = any((item.field == field or item.field == "document_style") and item.status in {"explicit", "confirmed"} for item in active)
        issues.append(TemplateIssue(id="small-body-font", severity="medium", title="正文较小，请核对可读性",
            explanation="该值有正式来源，优化不会改动；如确需调整，请建立派生规则并人工确认。" if protected else "可选择改为 12 磅；只有明确勾选后才改变派生模板。",
            field=field, proposed_value=None if protected else 12, can_apply=not protected))
    if draft.document_kind != "thesis" and draft.review_profile_id == "academic":
        issues.append(TemplateIssue(id="review-profile", severity="medium", title="非论文文档仍使用论文审查",
            explanation="建议在文档类型设置中选用通用文档审查，避免强加创新点或研究方法要求。", field="review_profile_id"))
    return TemplateAudit(issues=issues, unresolved_conflicts=conflicts, pending_sources=pending,
        summary=f"发现 {len(issues)} 项待核对事项；{conflicts} 个字段冲突。" if issues else "未发现当前检查项中的问题；不代表规范完整或内容正确。")


def optimize_template(draft: SchoolTemplateDraft, selected_issue_ids: list[str], parent_template_id: str | None = None) -> SchoolTemplateDraft:
    report = audit_template(draft)
    issues = {item.id: item for item in report.issues}
    if set(selected_issue_ids) - set(issues):
        raise ValueError("体检结果已变化，请重新检查模板。")
    if not selected_issue_ids:
        raise ValueError("请先勾选要采用的优化建议。")
    result = draft.model_copy(deep=True)
    for identity in dict.fromkeys(selected_issue_ids):
        item = issues[identity]
        if not item.can_apply:
            raise ValueError("包含必须人工处理的事项，不能自动应用。")
        result = _set(result, item.field, item.proposed_value)
        result.rule_sources.append(rule("用户选择的模板优化", item.explanation, item.field, item.proposed_value, "confirmed",
                                        "这是用户选用的产品建议，不是对学校正式规则的修改。"))
    if not result.name.endswith("·优化副本"):
        result.name = result.name[:114] + "·优化副本"
    result.parent_template_id = parent_template_id or draft.parent_template_id
    result.warnings = list(dict.fromkeys([*result.warnings, "已生成独立优化草案，保存时创建新模板；原模板不变。"]))[:30]
    return SchoolTemplateDraft.model_validate(result.model_dump(mode="json"))
