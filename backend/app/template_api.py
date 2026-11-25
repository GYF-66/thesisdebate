"""Bounded template tools. All endpoints return drafts, never edit a document."""
from __future__ import annotations

from io import BytesIO
from pathlib import PurePosixPath

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.school_templates import SchoolTemplateDraft
from app.template_rules import TemplateAudit, audit_template, extract_rules, merge_template_rules, optimize_template, resolve_rules

router = APIRouter(prefix="/template-tools", tags=["文档模板"])


class SpecificationExtract(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)
    source_name: str = Field(default="粘贴的规范", min_length=1, max_length=240)
    document_kind: str = "thesis"
    base_template: SchoolTemplateDraft | None = None


class TemplateInput(BaseModel):
    draft: SchoolTemplateDraft


class ResolveInput(TemplateInput):
    confirm_ids: list[str] = Field(default_factory=list, max_length=200)
    reject_ids: list[str] = Field(default_factory=list, max_length=200)


class OptimizeInput(TemplateInput):
    selected_issue_ids: list[str] = Field(min_length=1, max_length=30)
    parent_template_id: str | None = Field(default=None, max_length=100)


class MergeInput(TemplateInput):
    incoming: SchoolTemplateDraft


def parse_specification(filename: str, data: bytes, document_kind: str = "thesis",
                        base_template: SchoolTemplateDraft | None = None) -> SchoolTemplateDraft:
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if not name or len(name) > 255 or any(ord(char) < 32 for char in name):
        raise ValueError("规范文件名无效。")
    if not data or len(data) > 10 * 1024 * 1024:
        raise ValueError("规范文件应为 1 字节至 10 MB。")
    suffix = PurePosixPath(name).suffix.lower()
    sample_sources = []
    if suffix in {".txt", ".md", ".markdown"}:
        try:
            text = data.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("规范文字文件必须采用 UTF-8 编码。") from error
        if any(ord(char) < 32 and char not in "\r\n\t" for char in text):
            raise ValueError("规范文件含不支持的控制字符。")
    elif suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError("该文件不是有效 PDF。")
        try:
            from pypdf import PdfReader
        except ImportError as error:
            raise ValueError("PDF 规范解析依赖尚未安装，请安装 pypdf 或粘贴规范文字。") from error
        try:
            reader = PdfReader(BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise ValueError("请提供未加密的 PDF 规范。")
            if len(reader.pages) > 100:
                raise ValueError("PDF 规范不能超过 100 页，请提取相关页后上传。")
            parts, count, runs = [], 0, []
            for index, page in enumerate(reader.pages, 1):
                def visit(content, cm, tm, font, size):
                    # Visitor matrices may include scaling; use the text y-axis scale.
                    scale = ((cm[2] * tm[3] + cm[0] * tm[2]) ** 2 + (cm[3] * tm[3] + cm[1] * tm[2]) ** 2) ** .5
                    if font and content.strip() and len(runs) < 20000:
                        runs.append((content[:3000], str(font.get('/BaseFont', '')), float(size) * scale, index))
                extracted = page.extract_text(visitor_text=visit) or ""
                count += len(extracted)
                if count > 100_000:
                    raise ValueError("规范文字不能超过 100000 字。")
                parts.extend(f"[第{index}页] {line}" for line in extracted.splitlines())
            text = "\n".join(parts)
            if not text.strip():
                raise ValueError("PDF 没有可读取的文字层，当前不进行 OCR；请上传文字版或粘贴规范。")
            from app.template_analysis import pdf_sample_sources
            sample_sources = pdf_sample_sources(runs, name)
        except ValueError:
            raise
        except Exception as error:
            raise ValueError("PDF 损坏或结构不受支持，请另存为文字版 PDF。") from error
    else:
        raise ValueError("规范材料支持 TXT、Markdown 或带文字层的 PDF。")
    from app.template_analysis import fill_recognized
    draft = extract_rules(text, name, document_kind, base_template)
    if suffix == '.pdf':
        draft.warnings.append('PDF 的学校要求优先采用明文；重复正文可识别实际字体和字号。未识别的行距、页边距与标题层级请核对。扫描图片需要先转为带文字层的 PDF。')
    return draft if base_template else fill_recognized(draft, sample_sources)


@router.post("/extract", response_model=SchoolTemplateDraft)
def extract_specification(payload: SpecificationExtract):
    try:
        return extract_rules(payload.text, payload.source_name, payload.document_kind, payload.base_template)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.post("/import", response_model=SchoolTemplateDraft)
async def import_specification(request: Request, filename: str = Query(min_length=1, max_length=255),
                               document_kind: str = "thesis", base_profile_id: str | None = None):
    maximum = 10 * 1024 * 1024
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > maximum:
            raise HTTPException(413, "规范文件不能超过 10 MB。")
        data.extend(chunk)
    base = None
    if base_profile_id:
        from app.main import resolve_format_profile
        profile = resolve_format_profile(base_profile_id)
        if profile is None:
            raise HTTPException(404, "原模板不存在。")
        base = SchoolTemplateDraft.model_validate(profile.model_dump(mode="json"))
    try:
        return await run_in_threadpool(parse_specification, filename, bytes(data), document_kind, base)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.post("/audit", response_model=TemplateAudit)
def inspect_template(payload: TemplateInput):
    return audit_template(payload.draft)


@router.post("/merge", response_model=SchoolTemplateDraft)
def merge_specification_rules(payload: MergeInput):
    try:
        return merge_template_rules(payload.draft, payload.incoming)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.post("/resolve", response_model=SchoolTemplateDraft)
def confirm_template_rules(payload: ResolveInput):
    try:
        return resolve_rules(payload.draft, payload.confirm_ids, payload.reject_ids)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.post("/optimize", response_model=SchoolTemplateDraft)
def improve_template(payload: OptimizeInput):
    try:
        return optimize_template(payload.draft, payload.selected_issue_ids, payload.parent_template_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
