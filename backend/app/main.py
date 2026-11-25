from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
import re
from threading import RLock
from typing import Dict, List, Literal, Optional, Sequence, Tuple
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.revision_review import (
    DiffRow, RevisionReview, aligned_diff, checked_edits, edits_conflict, review_revision,
    source_digest, validate_change,
)
from app.heading_numbering import (
    DEFAULT_HEADING_STYLES, HeadingStyle, clean_duplicate_prefix, format_heading,
    heading_body, is_unnumbered_heading, parse_prefix, unwrap_heading,
)
from app.school_templates import SchoolTemplateDraft, TemplateRuleSource
from app.document_profiles import DOCUMENT_KINDS, DEFAULT_SECTIONS
from app.document_layout import DocumentStyle


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChangeStatus(str, Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"


class ChangeDecision(str, Enum):
    accept = "accept"
    reject = "reject"
    undo = "undo"


class FormatRule(BaseModel):
    id: str
    category: Literal["format", "structure", "reference"]
    property: str
    description: str
    enabled_by_default: bool = True


class FormatProfile(BaseModel):
    id: str
    name: str
    institution: str
    version: str
    description: str
    rules_count: int
    rules: List[FormatRule] = Field(default_factory=list)
    updated_at: str
    heading_numbering: List[HeadingStyle] = Field(default_factory=lambda: list(DEFAULT_HEADING_STYLES), min_length=6, max_length=6)
    custom: bool = False
    source_name: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    document_style: DocumentStyle | None = None
    document_kind: str = "thesis"
    review_profile_id: str = "academic"
    required_sections: List[str] = Field(default_factory=list)
    cover_fields: List[str] = Field(default_factory=list)
    submission_checklist: List[str] = Field(default_factory=list)
    rule_sources: List[TemplateRuleSource] = Field(default_factory=list)
    parent_template_id: str | None = None


class FormatRunCreate(BaseModel):
    profile_id: str = "nju-2026"
    document_version_id: str = "demo-v3"
    document_name: str = "毕业论文初稿.docx"
    document_type: Literal["docx", "pdf", "markdown"] = "docx"
    requested_rules: Optional[List[str]] = None
    content: Optional[str] = Field(default=None, max_length=300_000)
    document_id: Optional[str] = None
    expected_head_hash: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class FormatRun(BaseModel):
    id: str
    profile_id: str
    profile_version: str
    document_version_id: str
    document_name: str
    document_type: str
    status: Literal["queued", "running", "completed", "failed"]
    changes_count: int
    accepted_count: int
    rejected_count: int
    requested_rules: List[str] = Field(default_factory=list)
    created_at: str
    completed_at: Optional[str] = None
    source_hash: Optional[str] = None
    document_id: str = ""
    base_version_id: Optional[str] = None
    current_version_id: Optional[str] = None
    document_kind: str = "thesis"
    review_profile_id: str = "academic"
    template_snapshot: dict = Field(default_factory=dict)


class Anchor(BaseModel):
    node_id: str
    start: int
    end: int
    quote: str
    context_hash: str


class FormatChange(BaseModel):
    id: str
    run_id: str
    document_version_id: str
    anchor: Anchor
    property: str
    before: str
    after: str
    rule_id: str
    explanation: str
    kind: Literal["format", "structure", "reference"]
    replacement: Optional[str] = None
    status: ChangeStatus = ChangeStatus.pending
    created_at: str
    decided_at: Optional[str] = None


class DecisionRequest(BaseModel):
    decision: ChangeDecision
    expected_source_hash: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class LatexCompileRequest(BaseModel):
    filename: str = "main.tex"
    source: str = Field(default="\\section{Introduction}\nE = mc^2")
    engine: Literal["xelatex", "pdflatex", "lualatex"] = "xelatex"


class LatexCompileResponse(BaseModel):
    job_id: str
    filename: str
    status: Literal["preview_validated", "preview_rejected"]
    engine: str
    pdf_url: Optional[str] = None
    validation_only: Literal[True] = True
    log: List[str]
    warnings: List[str]
    errors: List[str]
    source_map: Dict[str, int]


class PreviewResponse(BaseModel):
    run_id: str
    document_version_id: str
    source: str
    preview: str
    accepted_change_ids: List[str]
    applied_change_ids: List[str]
    pending_change_ids: List[str]
    rejected_change_ids: List[str]
    applied_count: int
    notes: List[str] = Field(default_factory=list)


class SuggestedPreviewResponse(BaseModel):
    source: str
    preview: str
    diff_rows: List[DiffRow]
    applied_count: int
    proposed_count: int
    blocked_change_ids: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


app = FastAPI(
    title="研辩台格式修订 API",
    version="0.1.0",
    description="Format review, color diff decisions, and LaTeX preview prototype.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


PROFILES: Dict[str, FormatProfile] = {
    "nju-2026": FormatProfile(
        id="nju-2026",
        name="本科毕业论文格式规范",
        institution="示例大学",
        version="2026.1",
        description="适用于本科毕业论文、开题报告和大创申报书的排版与引用规则。",
        rules_count=5,
        rules=[
            FormatRule(id="heading.numbering", category="structure", property="标题编号", description="按标题层级补齐 Markdown 章节编号。"),
            FormatRule(id="heading.spacing", category="format", property="标题间距", description="统一标题前后的段落间隔。"),
            FormatRule(id="reference.gb7714", category="reference", property="参考文献列表", description="规范参考文献条目编号与空白。"),
            FormatRule(id="paragraph.spacing", category="format", property="段落尾部空格", description="清理段落末尾的无效空格。"),
            FormatRule(id="document.pdf.editable-copy", category="structure", property="PDF 编辑副本", description="保留原 PDF，并提示导出可编辑副本。", enabled_by_default=False),
        ],
        updated_at="2026-09-01",
    ),
    "generic-thesis": FormatProfile(
        id="generic-thesis",
        name="通用学术论文规范",
        institution="研辩台默认模板",
        version="1.0",
        description="用于没有学校模板时的通用论文格式检查。",
        rules_count=4,
        rules=[
            FormatRule(id="heading.numbering", category="structure", property="标题编号", description="按标题层级补齐 Markdown 章节编号。"),
            FormatRule(id="heading.spacing", category="format", property="标题间距", description="统一标题前后的段落间隔。"),
            FormatRule(id="reference.gb7714", category="reference", property="参考文献列表", description="规范参考文献条目编号与空白。"),
            FormatRule(id="paragraph.spacing", category="format", property="段落尾部空格", description="清理段落末尾的无效空格。"),
        ],
        updated_at="2026-01-10",
    ),
}
for _kind, _label in DOCUMENT_KINDS.items():
    if _kind == "thesis":
        continue
    PROFILES["preset-" + _kind] = PROFILES["generic-thesis"].model_copy(update={
        "id": "preset-" + _kind, "name": _label + "模板", "institution": "研辩台默认模板",
        "description": "可按正式要求补充的" + _label + "预设；章节清单供确认，不代表学校要求。",
        "document_kind": _kind, "review_profile_id": "general", "required_sections": DEFAULT_SECTIONS[_kind],
        "heading_numbering": ["preserve"] * 6,
    })
RUNS: Dict[str, FormatRun] = {}
CHANGES: Dict[str, FormatChange] = {}
SOURCES: Dict[str, str] = {}
REVISION_LOCK = RLock()

RULE_ALIASES = {
    "heading.numbering": {"heading.numbering", "heading", "all"},
    "heading.spacing": {"heading.spacing", "heading", "all"},
    "reference.gb7714": {"reference.gb7714", "references", "all"},
    "paragraph.spacing": {"paragraph.spacing", "paragraph", "all"},
}

def utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def utf16_index_map(value: str) -> Dict[int, int]:
    offsets = {0: 0}
    units = 0
    for index, char in enumerate(value, 1):
        units += 2 if ord(char) > 0xFFFF else 1
        offsets[units] = index
    return offsets


def rule_enabled(rule_id: str, requested_rules: Optional[Sequence[str]]) -> bool:
    if requested_rules is None:
        return True
    requested = set(requested_rules)
    return bool(requested.intersection(RULE_ALIASES.get(rule_id, {rule_id})))


def content_change(
    run_id: str,
    document_version_id: str,
    *,
    start: int,
    quote: str,
    property: str,
    before: str,
    after: str,
    rule_id: str,
    explanation: str,
    kind: Literal["format", "structure", "reference"],
    replacement: Optional[str],
    index: int,
    context: str = "",
) -> FormatChange:
    change = FormatChange(
        id=f"chg-{uuid4().hex[:10]}",
        run_id=run_id,
        document_version_id=document_version_id,
        anchor=Anchor(
            node_id=f"content-node-{index + 1:03d}",
            start=start,
            end=start + utf16_length(quote),
            quote=quote,
            context_hash=hashlib.sha256((context or quote).encode("utf-8")).hexdigest()[:16],
        ),
        property=property,
        before=before,
        after=after,
        rule_id=rule_id,
        explanation=explanation,
        kind=kind,
        replacement=replacement,
        created_at=now_iso(),
    )
    return change


def make_content_changes(
    run_id: str,
    document_version_id: str,
    content: str,
    requested_rules: Optional[Sequence[str]],
    heading_numbering: Optional[Sequence[HeadingStyle]] = None,
) -> List[FormatChange]:
    changes: List[FormatChange] = []
    lines = content.splitlines(keepends=True)
    offsets: List[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += utf16_length(line)

    heading_counts = [0] * 6
    in_references = False
    reference_index = 0
    change_index = 0
    for line_index, line in enumerate(lines):
        raw = line.rstrip("\r\n")
        if not raw.strip():
            continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", raw)
        plain_reference_heading = re.match(r"^(?:#{1,6}\s*)?(?:参考文献|References|参考资料)$", raw, re.IGNORECASE)
        if plain_reference_heading and not heading:
            in_references = True
            continue
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).strip()
            in_references = heading_body(title).casefold() in {"参考文献", "references", "参考资料"}
            style = heading_numbering[level - 1] if heading_numbering else "preserve"
            if not is_unnumbered_heading(title):
                for position in range(level, 6):
                    heading_counts[position] = 0
                heading_counts[level - 1] += 1
                for position in range(level - 1):
                    if heading_counts[position] == 0:
                        heading_counts[position] = 1
                clean_core, _ = unwrap_heading(clean_duplicate_prefix(title))
                parsed = parse_prefix(clean_core)
                if style == "preserve" and parsed and parsed.values:
                    if len(parsed.values) == level:
                        heading_counts[:level] = parsed.values
                    elif len(parsed.values) == 1:
                        heading_counts[level - 1] = parsed.values[0]
            adjusted_title = format_heading(title, heading_counts, level, style)
            if rule_enabled("heading.numbering", requested_rules) and adjusted_title != title:
                replacement_line = f"{heading.group(1)} {adjusted_title}"
                changes.append(
                    content_change(
                        run_id, document_version_id, start=offsets[line_index], quote=raw,
                        property="标题编号", before=raw, after=replacement_line,
                        rule_id="heading.numbering", kind="structure", replacement=replacement_line,
                        explanation=("移除旧格式引擎叠加的重复编号，保留标题本身的编号与文字。"
                                     if clean_duplicate_prefix(title) != title and style == "preserve" else
                                     "按当前模板的标题层级生成唯一编号，替换已有前缀而不重复追加。"), index=change_index,
                        context=raw,
                    )
                )
                change_index += 1

            previous_blank = line_index == 0 or not lines[line_index - 1].strip()
            next_blank = line_index == len(lines) - 1 or not lines[line_index + 1].strip()
            if rule_enabled("heading.spacing", requested_rules):
                if not previous_blank:
                    changes.append(
                        content_change(
                            run_id, document_version_id, start=offsets[line_index], quote="",
                            property="标题前空行", before="", after="新增空行",
                            rule_id="heading.spacing", kind="format", replacement="\n",
                            explanation="标题前缺少空行，添加一个空行以统一章节间距。", index=change_index,
                            context=raw,
                        )
                    )
                    change_index += 1
                if not next_blank:
                    changes.append(
                        content_change(
                            run_id, document_version_id, start=offsets[line_index] + utf16_length(raw), quote="",
                            property="标题后空行", before="", after="新增空行",
                            rule_id="heading.spacing", kind="format", replacement="\n",
                            explanation="标题后缺少空行，添加一个空行以统一章节间距。", index=change_index,
                            context=raw,
                        )
                    )
                    change_index += 1
            continue

        reference_replaced = False
        if in_references and rule_enabled("reference.gb7714", requested_rules):
            reference_index += 1
            if re.match(r"^[-*+]\s+", raw):
                body = re.sub(r"^[-*+]\s+", "", raw).strip()
                replacement = f"[{reference_index}] {body}"
                changes.append(
                    content_change(
                        run_id, document_version_id, start=offsets[line_index], quote=raw,
                        property="参考文献编号", before=raw, after=replacement,
                        rule_id="reference.gb7714", kind="reference", replacement=replacement,
                        explanation="参考文献使用无序列表，统一转换为带编号的引用条目。", index=change_index,
                        context=raw,
                    )
                )
                change_index += 1
                reference_replaced = True
            elif re.match(r"^\[\d+\]\s+", raw) and not re.search(r"\[[A-Z]\]\.", raw):
                normalized = re.sub(r"\s+$", "", raw)
                if normalized != raw:
                    changes.append(
                        content_change(
                            run_id, document_version_id, start=offsets[line_index], quote=raw,
                            property="参考文献尾部空格", before=raw, after=normalized,
                            rule_id="reference.gb7714", kind="reference", replacement=normalized,
                            explanation="参考文献条目存在尾部空格，统一清理以便后续导出。", index=change_index,
                            context=raw,
                        )
                    )
                    change_index += 1
                    reference_replaced = True

        if not reference_replaced and rule_enabled("paragraph.spacing", requested_rules):
            normalized = re.sub(r"[ \t]+$", "", raw)
            if normalized != raw:
                changes.append(
                    content_change(
                        run_id, document_version_id, start=offsets[line_index], quote=raw,
                        property="段落尾部空格", before=raw, after=normalized,
                        rule_id="paragraph.spacing", kind="format", replacement=normalized,
                        explanation="清理段落尾部空格，避免导出时出现不可见排版差异。", index=change_index,
                        context=raw,
                    )
                )
                change_index += 1
    return changes


def build_preview(source: str, changes: Sequence[FormatChange], document_version_id: Optional[str] = None) -> Tuple[str, List[str]]:
    edits, _ = checked_edits(source, changes, document_version_id)
    preview = source
    applied: List[str] = []
    written = set()
    for edit in sorted(edits, key=lambda item: (item.start, item.end - item.start), reverse=True):
        identity = (edit.start, edit.end, edit.replacement)
        if identity not in written:
            preview = preview[:edit.start] + edit.replacement + preview[edit.end:]
            written.add(identity)
        applied.append(edit.change_id)
    return preview, list(reversed(applied))


def make_demo_changes(run_id: str, document_version_id: str, document_type: str) -> List[FormatChange]:
    # Stable demo findings keep the competition walkthrough deterministic.
    items = [
        (
            "正文-字体",
            "小四 宋体",
            "小四 宋体（已规范）",
            "format.font.body",
            "format",
            "正文段落存在字体混用，建议统一为模板规定的中文正文样式。",
            "研究背景与意义",
        ),
        (
            "一级标题-段前间距",
            "段前 6 磅",
            "段前 12 磅",
            "format.heading.spacing",
            "format",
            "一级标题的段前间距不足，统一后可改善章节层级辨识度。",
            "1 绪论",
        ),
        (
            "参考文献-编号样式",
            "[1] Author. Title. 2024.",
            "[1] 作者. 题名[J]. 期刊, 2024, 12(3): 1-8.",
            "reference.gb7714",
            "reference",
            "参考文献缺少文献类型、卷期和页码字段。",
            "参考文献",
        ),
        (
            "图题位置",
            "图题位于图片上方",
            "图题移至图片下方并居中",
            "figure.caption.position",
            "structure",
            "模板要求图题置于图下方，系统建议仅调整结构，不修改图中内容。",
            "图 2-1 系统架构",
        ),
    ]
    if document_type == "pdf":
        items.append(
            (
                "PDF 可编辑性",
                "固定版式 PDF",
                "生成可编辑 DOCX 副本",
                "document.pdf.editable-copy",
                "structure",
                "PDF 原件保留为对照，格式修复将生成可编辑副本。",
                "文档概览",
            )
        )

    changes: List[FormatChange] = []
    for index, (prop, before, after, rule_id, kind, explanation, quote) in enumerate(items):
        change = FormatChange(
            id=f"chg-{uuid4().hex[:10]}",
            run_id=run_id,
            document_version_id=document_version_id,
            anchor=Anchor(
                node_id=f"node-{index + 1:03d}",
                start=20 + index * 48,
                end=20 + index * 48 + len(quote),
                quote=quote,
                context_hash=f"demo-context-{index + 1:02d}",
            ),
            property=prop,
            before=before,
            after=after,
            rule_id=rule_id,
            explanation=explanation,
            kind=kind,
            created_at=now_iso(),
        )
        changes.append(change)
    return changes


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": "yanbian-format-api"}


@app.get("/format-profiles", response_model=List[FormatProfile])
def list_profiles() -> List[FormatProfile]:
    from app.agent_api import get_store
    from app.school_templates import load_profiles
    profiles = dict(PROFILES)
    for data in load_profiles(get_store()):
        profile = FormatProfile.model_validate(data)
        if profile.custom and profile.id not in PROFILES:
            profiles[profile.id] = profile
    return list(profiles.values())


def resolve_format_profile(profile_id: str) -> Optional[FormatProfile]:
    return next((profile for profile in list_profiles() if profile.id == profile_id), None)


@app.post("/format-profiles/import", response_model=SchoolTemplateDraft)
async def import_school_template(request: Request, filename: str = Query(min_length=1, max_length=255)) -> SchoolTemplateDraft:
    from app.school_templates import import_template
    from starlette.concurrency import run_in_threadpool
    maximum = 10 * 1024 * 1024
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            length = int(declared)
            if length < 0:
                raise ValueError()
        except ValueError as error:
            raise HTTPException(400, "模板文件长度无效。") from error
        if length > maximum:
            raise HTTPException(413, "学校模板不能超过 10 MB。")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > maximum:
            raise HTTPException(413, "学校模板不能超过 10 MB。")
        data.extend(chunk)
    try:
        return await run_in_threadpool(import_template, filename, bytes(data))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.post("/format-profiles", response_model=FormatProfile, status_code=201)
def create_school_template(payload: SchoolTemplateDraft) -> FormatProfile:
    from app.agent_api import get_store
    from app.school_templates import save_profile
    defaults = PROFILES["generic-thesis"]
    if any(item.status == "conflict" and not item.note.startswith("未采用") for item in payload.rule_sources):
        raise HTTPException(409, "请先处理模板规则冲突。")
    parent = resolve_format_profile(payload.parent_template_id) if payload.parent_template_id else None
    if payload.parent_template_id and parent is None:
        raise HTTPException(422, "派生模板的原版本不存在。")
    profile = FormatProfile(
        id="school-" + uuid4().hex[:12], name=payload.name, institution=payload.institution,
        version=now_iso() if parent else "1.0", description="文档自定义标题、章节与排版规则；每次审查绑定此模板版本。",
        rules_count=defaults.rules_count, rules=defaults.rules, updated_at=now_iso(),
        heading_numbering=payload.heading_numbering, custom=True,
        source_name=payload.source_name, warnings=payload.warnings, document_style=payload.document_style,
        document_kind=payload.document_kind, review_profile_id=payload.review_profile_id,
        required_sections=payload.required_sections, cover_fields=payload.cover_fields,
        submission_checklist=payload.submission_checklist, rule_sources=payload.rule_sources,
        parent_template_id=payload.parent_template_id,
    )
    save_profile(get_store(), profile.model_dump(mode="json"))
    return profile


@app.get("/demo")
def demo() -> Dict[str, object]:
    return {
        "document": {
            "id": "demo-doc",
            "name": "数字化学习平台研究与设计.docx",
            "version_id": "demo-v3",
            "type": "docx",
            "pages": 42,
            "sections": 7,
        },
        "profile": PROFILES["nju-2026"].model_dump(),
        "review": {
            "score": 82,
            "delta": 9,
            "open_findings": 4,
            "debate_round": 2,
        },
        "agents": [
            {"id": "method", "name": "方法论审查官", "status": "completed", "score": 78},
            {"id": "innovation", "name": "创新点挑刺官", "status": "completed", "score": 84},
            {"id": "norms", "name": "学术规范检查官", "status": "completed", "score": 88},
            {"id": "judge", "name": "证据裁判官", "status": "completed", "score": 82},
        ],
    }


@app.post("/format-runs", response_model=FormatRun)
def create_format_run(payload: FormatRunCreate) -> FormatRun:
    with REVISION_LOCK:
        return _create_format_run(payload)


def _create_format_run(payload: FormatRunCreate, origin: str = "scan") -> FormatRun:
    profile = resolve_format_profile(payload.profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="format profile not found")
    run_id = f"run-{uuid4().hex[:10]}"
    run = FormatRun(
        id=run_id,
        profile_id=payload.profile_id,
        profile_version=profile.version,
        document_version_id=payload.document_version_id,
        document_name=payload.document_name,
        document_type=payload.document_type,
        status="running",
        changes_count=0,
        accepted_count=0,
        rejected_count=0,
        requested_rules=list(payload.requested_rules or []),
        created_at=now_iso(),
        source_hash=source_digest(payload.content or ""),
        document_kind=profile.document_kind, review_profile_id=profile.review_profile_id,
        template_snapshot=profile.model_dump(mode="json"),
    )
    if payload.content is None:
        source = ""
        changes = make_demo_changes(run_id, payload.document_version_id, payload.document_type)
    else:
        source = payload.content
        changes = make_content_changes(
            run_id, payload.document_version_id, payload.content, payload.requested_rules,
            profile.heading_numbering,
        )
    run = run.model_copy(
        update={
            "status": "completed",
            "changes_count": len(changes),
            "completed_at": now_iso(),
        }
    )
    from app.agent_api import get_store
    document_id = payload.document_id or f"doc-{uuid4().hex}"
    first_workspace_scan = False
    if payload.document_id:
        current_head = get_store().get_head(document_id)
        first_workspace_scan = current_head is None and get_store().get_workspace_document(document_id) is not None
        if not first_workspace_scan and (current_head is None or payload.expected_head_hash is None):
            raise HTTPException(409, "重新扫描已有文档必须提供当前版本哈希。")
    run = run.model_copy(update={"document_id": document_id})
    proposed_runs = {**RUNS, run_id: run}
    proposed_changes = {**CHANGES, **{change.id: change for change in changes}}
    proposed_sources = {**SOURCES, run_id: source}
    try:
        committed = get_store().commit_format_state(
            {key: item.model_dump(mode="json") for key, item in proposed_runs.items()},
            {key: item.model_dump(mode="json") for key, item in proposed_changes.items()},
            proposed_sources, run_id, document_id, source, origin,
            expected_head_hash=payload.expected_head_hash,
            expected_head_absent=first_workspace_scan,
        )
    except ValueError as error:
        raise HTTPException(409, "文档已有更新版本，请刷新后重新扫描。") from error
    RUNS[run_id] = FormatRun.model_validate(committed["run"])
    CHANGES.update({change.id: change for change in changes})
    SOURCES[run_id] = source
    return RUNS[run_id]


@app.get("/format-runs/{run_id}", response_model=FormatRun)
def get_format_run(run_id: str) -> FormatRun:
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(404, "format run not found")
    return run


@app.get("/format-runs/{run_id}/changes", response_model=List[FormatChange])
def list_changes(run_id: str) -> List[FormatChange]:
    if run_id not in RUNS:
        raise HTTPException(status_code=404, detail="format run not found")
    return [change for change in CHANGES.values() if change.run_id == run_id]


@app.get("/format-runs/{run_id}/preview", response_model=PreviewResponse)
def get_preview(run_id: str) -> PreviewResponse:
    with REVISION_LOCK:
        return _get_preview(run_id)


def _get_preview(run_id: str) -> PreviewResponse:
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="format run not found")
    changes = [change for change in CHANGES.values() if change.run_id == run_id]
    source = SOURCES.get(run_id, "")
    accepted = [change for change in changes if change.status == ChangeStatus.accepted]
    pending = [change for change in changes if change.status == ChangeStatus.pending]
    rejected = [change for change in changes if change.status == ChangeStatus.rejected]
    notes: List[str] = []
    if not source:
        notes.append("演示模式没有原始正文；提交 content 后才会生成可应用的正文预览。")
    if run.document_type == "pdf":
        notes.append("PDF 预览基于提取文本，不代表版面重排；格式修订应输出新的可编辑副本。")
    _, blocked = checked_edits(source, changes, run.document_version_id)
    if run.source_hash is not None and source_digest(source) != run.source_hash:
        preview, applied_ids = source, []
        notes.append("原文版本校验失败，已停止应用旧建议，请重新扫描。")
    else:
        preview, applied_ids = build_preview(source, changes, run.document_version_id)
    notes.extend(f"未应用 {change_id}: {reason}" for change_id, reason in blocked.items())
    return PreviewResponse(
        run_id=run_id,
        document_version_id=run.document_version_id,
        source=source,
        preview=preview,
        accepted_change_ids=[change.id for change in accepted],
        applied_change_ids=applied_ids,
        pending_change_ids=[change.id for change in pending],
        rejected_change_ids=[change.id for change in rejected],
        applied_count=len(applied_ids),
        notes=notes,
    )


@app.get("/format-runs/{run_id}/review", response_model=RevisionReview)
def get_revision_review(run_id: str) -> RevisionReview:
    with REVISION_LOCK:
        return _get_revision_review(run_id)


@app.get("/format-runs/{run_id}/suggested-preview", response_model=SuggestedPreviewResponse)
def get_suggested_preview(run_id: str) -> SuggestedPreviewResponse:
    """Read-only proposal; pending decisions never become accepted in storage."""
    with REVISION_LOCK:
        run = RUNS.get(run_id)
        if run is None:
            raise HTTPException(404, "format run not found")
        source = SOURCES.get(run_id, "")
        actual = _get_preview(run_id)
        changes = list_changes(run_id)
        accepted = [change for change in changes if change.status == ChangeStatus.accepted]
        pending = [change for change in changes if change.status == ChangeStatus.pending]
        candidates = accepted + [change.model_copy(update={"status": ChangeStatus.accepted}) for change in pending]
        if run.source_hash is None or source_digest(source) != run.source_hash:
            return SuggestedPreviewResponse(
                source=source, preview=source, diff_rows=aligned_diff(source, source), applied_count=0,
                proposed_count=0, blocked_change_ids=[change.id for change in candidates],
                notes=["扫描原文校验失败，建议预览已停止，请重新扫描。"],
            )
        edits, blocked = checked_edits(source, candidates, run.document_version_id)
        preview, _ = build_preview(source, candidates, run.document_version_id)
        pending_ids = {change.id for change in pending}
        return SuggestedPreviewResponse(
            source=source, preview=preview, diff_rows=aligned_diff(source, preview),
            applied_count=actual.applied_count,
            proposed_count=sum(edit.change_id in pending_ids for edit in edits),
            blocked_change_ids=list(blocked),
            notes=["建议稿包含尚未接受的修改；正文与版本记录尚未改变。"] +
                  [f"未预览 {change_id}: {reason}" for change_id, reason in blocked.items()],
        )


def _get_revision_review(run_id: str) -> RevisionReview:
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="format run not found")
    changes = list_changes(run_id)
    source = SOURCES.get(run_id, "")
    _, blocked = checked_edits(source, changes, run.document_version_id)
    source_integrity = run.source_hash is not None and source_digest(source) == run.source_hash
    preview = get_preview(run_id)
    if not source_integrity:
        blocked.update({change.id: "原文版本与扫描版本不一致。"
                        for change in changes if change.status == ChangeStatus.accepted})
    return review_revision(
        run_id=run_id, source=source, preview=preview.preview, changes=changes,
        applied_ids=preview.applied_change_ids, blocked=blocked, source_integrity=source_integrity,
    )


@app.post("/format-changes/{change_id}/decision", response_model=FormatChange)
def decide_change(change_id: str, payload: DecisionRequest) -> FormatChange:
    with REVISION_LOCK:
        return _decide_change(change_id, payload)


def _decide_change(change_id: str, payload: DecisionRequest) -> FormatChange:
    change = CHANGES.get(change_id)
    if not change:
        raise HTTPException(status_code=404, detail="format change not found")
    run = RUNS[change.run_id]
    from app.agent_api import assert_current_run, commit_format_mutation
    assert_current_run(run)
    previous_preview = _get_preview(run.id).preview
    source = SOURCES.get(change.run_id, "")
    digest = source_digest(source)
    if payload.expected_source_hash is not None and payload.expected_source_hash != digest:
        raise HTTPException(status_code=409, detail="原文哈希已变化，请重新扫描后处理建议。")
    if payload.decision == ChangeDecision.accept:
        if run.source_hash is None or digest != run.source_hash:
            raise HTTPException(status_code=409, detail="扫描时原文已变化，不能接受旧建议。")
        if change.document_version_id != run.document_version_id:
            raise HTTPException(status_code=409, detail="建议的文档版本与扫描版本不一致。")
        edit, error = validate_change(source, change)
        if error or edit is None:
            raise HTTPException(status_code=409, detail=error or "无效修改。")
        existing, _ = checked_edits(source, [item for item in CHANGES.values()
                                            if item.run_id == change.run_id and item.id != change_id],
                                     run.document_version_id)
        conflict = next((item for item in existing if edits_conflict(item, edit)), None)
        if conflict is not None:
            raise HTTPException(status_code=409, detail=f"与已接受修改 {conflict.change_id} 冲突，请撤销后重试。")
    status_by_decision = {
        ChangeDecision.accept: ChangeStatus.accepted,
        ChangeDecision.reject: ChangeStatus.rejected,
        ChangeDecision.undo: ChangeStatus.pending,
    }
    updated = change.model_copy(
        update={
            "status": status_by_decision[payload.decision],
            "decided_at": None if payload.decision == ChangeDecision.undo else now_iso(),
        }
    )
    proposed_changes = {**CHANGES, change_id: updated}
    run = RUNS[change.run_id]
    changes = [item for item in proposed_changes.values() if item.run_id == change.run_id]
    proposed_run = run.model_copy(
        update={
            "accepted_count": sum(item.status == ChangeStatus.accepted for item in changes),
            "rejected_count": sum(item.status == ChangeStatus.rejected for item in changes),
        }
    )
    commit_format_mutation(proposed_run, proposed_changes, previous_preview, "format_" + payload.decision.value)
    return updated


@app.post("/format-runs/{run_id}/restore", response_model=PreviewResponse)
def restore_run(run_id: str) -> PreviewResponse:
    with REVISION_LOCK:
        return _restore_run(run_id)


def _restore_run(run_id: str) -> PreviewResponse:
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="format run not found")
    from app.agent_api import assert_current_run, commit_format_mutation
    assert_current_run(run)
    previous_preview = _get_preview(run_id).preview
    proposed_changes = dict(CHANGES)
    for change in list(proposed_changes.values()):
        if change.run_id == run_id:
            proposed_changes[change.id] = change.model_copy(
                update={"status": ChangeStatus.pending, "decided_at": None}
            )
    proposed_run = run.model_copy(update={"accepted_count": 0, "rejected_count": 0})
    commit_format_mutation(proposed_run, proposed_changes, previous_preview, "format_restore")
    return get_preview(run_id)


@app.post("/latex/compile", response_model=LatexCompileResponse)
def compile_latex(payload: LatexCompileRequest) -> LatexCompileResponse:
    job_id = f"latex-{uuid4().hex[:10]}"
    warnings: List[str] = []
    errors: List[str] = []
    if payload.source.count("{") != payload.source.count("}"):
        errors.append("brace mismatch: source has unbalanced braces")
    if "\\cite{" in payload.source and "references.bib" not in payload.source:
        warnings.append("citation commands detected; connect a .bib file for bibliography validation")
    validated = not errors
    return LatexCompileResponse(
        job_id=job_id,
        filename=payload.filename,
        status="preview_validated" if validated else "preview_rejected",
        engine="not_run",
        pdf_url=None,
        log=[
            "[研辩台] lightweight source preview validation only",
            f"requested_engine={payload.engine} (not invoked)",
            "[研辩台] no PDF was produced",
        ],
        warnings=warnings,
        errors=errors,
        source_map={"document": 1, "body": max(1, payload.source.count("\n") + 1)},
    )


from app.agent_api import router as agent_router, restore_format_state
app.include_router(agent_router)
restore_format_state()
from app.workspace import router as workspace_router
app.include_router(workspace_router)
from app.document_layout import router as layout_router
from app.document_export import router as export_router
from app.agent_chat import router as chat_router
app.include_router(layout_router)
app.include_router(export_router)
app.include_router(chat_router)
from app.document_fonts import router as fonts_router
app.include_router(fonts_router)
from app.template_api import router as template_tools_router
from app.assistance_api import router as assistance_router
app.include_router(template_tools_router)
app.include_router(assistance_router)
from app.docx_preservation import router as source_protection_router
app.include_router(source_protection_router)
from app.batch_revision import router as batch_revision_router
app.include_router(batch_revision_router)
from app.literature_api import router as literature_router
from app.workspace_assistant import router as workspace_assistant_router
app.include_router(literature_router)
app.include_router(workspace_assistant_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)

