"""Generate real Markdown, Word and paginated PDF downloads from the editor."""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from io import BytesIO
import json
import os
from pathlib import Path
import re
from threading import RLock
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import Field, field_validator

from app.document_layout import AppearanceModel, DocumentStyle
from app.document_fonts import COMMON_FONTS, get_font_catalogue, validate_font_name


class DocumentExportRequest(AppearanceModel):
    name: str = Field(default="论文", min_length=1, max_length=255)
    content: str = Field(max_length=300_000)
    style: DocumentStyle
    format: Literal["pdf", "md", "docx"]
    document_id: str | None = Field(default=None, max_length=100)
    docx_mode: Literal["preserve", "editable_copy"] = "preserve"
    font_substitutions: dict[str, str] = Field(default_factory=dict, max_length=32)

    @field_validator("font_substitutions")
    @classmethod
    def valid_substitutions(cls, value):
        for requested, replacement in value.items():
            validate_font_name(requested)
            validate_font_name(replacement)
        return value

    @field_validator("name", "content")
    @classmethod
    def valid_xml_characters(cls, value: str) -> str:
        if any((ord(char) < 32 and char not in "\n\r\t") or 0xD800 <= ord(char) <= 0xDFFF or ord(char) in {0xFFFE, 0xFFFF} for char in value):
            raise ValueError("名称和正文不能包含无效控制字符。")
        return value


@dataclass
class Block:
    kind: str
    text: str = ""
    level: int = 0
    rows: list[list[str]] | None = None


def _cells(line: str) -> list[str]:
    return [cell.strip().replace(r"\|", "|") for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def markdown_blocks(content: str) -> list[Block]:
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks, index = [], 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        fence = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        if fence:
            marker, pieces = fence.group(1), []
            index += 1
            while index < len(lines) and not re.match(r"^\s*" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}\s*$", lines[index]):
                pieces.append(lines[index]); index += 1
            if index < len(lines):
                index += 1
            blocks.append(Block("code", "\n".join(pieces)))
            continue
        if line.strip().startswith("$$"):
            pieces = [line]
            index += 1
            if line.strip() == "$$" or not line.rstrip().endswith("$$"):
                while index < len(lines):
                    pieces.append(lines[index]); index += 1
                    if pieces[-1].rstrip().endswith("$$"):
                        break
            blocks.append(Block("formula", "\n".join(pieces)))
            continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            blocks.append(Block("heading", heading.group(2), len(heading.group(1)))); index += 1
            continue
        if index + 1 < len(lines) and "|" in line:
            separator = _cells(lines[index + 1])
            if separator and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in separator):
                rows = [_cells(line)]
                index += 2
                while index < len(lines) and "|" in lines[index] and lines[index].strip():
                    rows.append(_cells(lines[index])); index += 1
                width = max(len(row) for row in rows)
                padded = [row + [""] * (width - len(row)) for row in rows]
                blocks.append(Block("table", rows=padded) if width <= 8 else
                              Block("table-fallback", "\n".join(" | ".join(row) for row in padded)))
                continue
        bullet = re.match(r"^\s*([-+*]|\d+[.)、])\s+(.+)$", line)
        if bullet:
            blocks.append(Block("bullet" if bullet.group(1) in "-+*" else "ordered", bullet.group(2))); index += 1
            continue
        quote_line = re.match(r"^\s*>\s?(.*)$", line)
        if quote_line:
            blocks.append(Block("quote", quote_line.group(1))); index += 1
            continue
        pieces = [line]
        index += 1
        while index < len(lines) and lines[index].strip():
            next_line = lines[index]
            if re.match(r"^(?:#{1,6}\s|\s*(?:[-+*]|\d+[.)、])\s|\s*>|\s*`{3}|\s*~{3}|\s*\$\$)", next_line):
                break
            if index + 1 < len(lines) and "|" in next_line and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in _cells(lines[index + 1])):
                break
            pieces.append(next_line); index += 1
        blocks.append(Block("paragraph", "\n".join(pieces)))
    return blocks


INLINE = re.compile(r"(\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]|\*\*[^*\n]+\*\*|__[^_\n]+__|`[^`\n]+`|\$[^$\n]+\$|\[[^\]\n]+\]\([^\)\n]+\)|\*[^*\n]+\*|_[^_\n]+_)")


def inline_fragments(text: str):
    for piece in INLINE.split(text):
        if not piece:
            continue
        if piece.startswith((r"\(", r"\[")):
            yield piece, "formula"
        elif (piece.startswith("**") and piece.endswith("**")) or (piece.startswith("__") and piece.endswith("__")):
            yield piece[2:-2], "bold"
        elif piece.startswith("`") and piece.endswith("`"):
            yield piece[1:-1], "code"
        elif piece.startswith("$") and piece.endswith("$"):
            yield piece, "formula"
        elif piece.startswith("["):
            link = re.fullmatch(r"\[([^\]]+)\]\(([^)]+)\)", piece)
            yield (link.group(1) + " (" + link.group(2) + ")") if link else piece, "text"
        elif piece.startswith(("*", "_")) and piece.endswith(piece[0]):
            yield piece[1:-1], "italic"
        else:
            yield piece, "text"


def _docx_font(font, family: str, size: float | None = None):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt
    font.name = family
    if size is not None:
        font.size = Pt(size)
    properties = font._element.get_or_add_rPr()
    fonts = properties.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts"); properties.insert(0, fonts)
    for key in ("eastAsia", "ascii", "hAnsi", "cs"):
        fonts.set(qn("w:" + key), family)


def _docx_inline(paragraph, text: str, *, literal: bool = False):
    for content, mark in ([(text, "code")] if literal else inline_fragments(text)):
        run = paragraph.add_run(content)
        run.bold = mark == "bold" or None
        run.italic = mark == "italic" or None


def export_docx(content: str, style: DocumentStyle) -> bytes:
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.shared import Mm, Pt
    document = Document()
    for section in document.sections:
        for edge in ("top", "bottom", "left", "right"):
            setattr(section, edge + "_margin", Mm(getattr(style, "margin_" + edge + "_mm")))
    normal = document.styles["Normal"]
    _docx_font(normal.font, style.font_family, style.font_size_pt)
    normal.paragraph_format.line_spacing = Pt(style.line_spacing_pt) if style.line_spacing_pt else style.line_spacing
    normal.paragraph_format.space_before = Pt(style.paragraph_before_pt)
    normal.paragraph_format.space_after = Pt(style.paragraph_after_pt)
    normal.paragraph_format.first_line_indent = Pt(style.first_line_indent_pt)
    for level, appearance in enumerate(style.headings, 1):
        heading = document.styles[f"Heading {level}"]
        _docx_font(heading.font, appearance.font_family, appearance.font_size_pt)
        heading.paragraph_format.line_spacing = Pt(appearance.line_spacing_pt) if appearance.line_spacing_pt else appearance.line_spacing
        heading.paragraph_format.space_before = Pt(appearance.space_before_pt)
        heading.paragraph_format.space_after = Pt(appearance.space_after_pt)
        heading.paragraph_format.first_line_indent = Pt(0)
        heading.paragraph_format.keep_with_next = True
    for name in ("List Bullet", "List Number"):
        _docx_font(document.styles[name].font, style.font_family, style.font_size_pt)
        document.styles[name].paragraph_format.first_line_indent = Pt(0)
    for block in markdown_blocks(content):
        if block.kind == "table":
            table = document.add_table(rows=len(block.rows), cols=len(block.rows[0]))
            table.style = "Table Grid"
            for row_index, row in enumerate(block.rows):
                for column, cell in enumerate(row):
                    paragraph = table.cell(row_index, column).paragraphs[0]
                    paragraph.paragraph_format.first_line_indent = Pt(0)
                    _docx_inline(paragraph, cell)
                    if row_index == 0:
                        for run in paragraph.runs:
                            run.bold = True
            table.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
        else:
            paragraph = document.add_paragraph(style=(f"Heading {block.level}" if block.kind == "heading" else
                                                     "List Bullet" if block.kind == "bullet" else
                                                     "List Number" if block.kind == "ordered" else "Normal"))
            if block.kind in {"code", "formula", "quote"}:
                paragraph.paragraph_format.first_line_indent = Pt(0)
            _docx_inline(paragraph, block.text, literal=block.kind in {"code", "formula"})
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


FONT_LOCK = RLock()


def _font_problem(family: str, text: str) -> tuple[str | None, str | None]:
    catalogue = get_font_catalogue()
    face = catalogue.face(family, text)
    required = {ord(char) for char in text if not char.isspace() and ord(char) >= 32}
    reason = ("本机未安装精确字体" if face is None else face.pdf_reason or
              ("缺少字符：" + "".join(chr(code) for code in sorted(required - face.coverage)[:12]) if required - face.coverage else None))
    suggested = None
    if reason:
        names = [catalogue.substitute(family), *COMMON_FONTS, *catalogue.groups]
        for name in names:
            candidate = catalogue.face(name, text) if name else None
            if candidate and not candidate.pdf_reason and required <= candidate.coverage:
                suggested = candidate.family
                break
    return reason, suggested


def _font_confirmation(issues: list[dict]):
    return HTTPException(409, {"code": "font_confirmation_required", "message": "字体要求无法精确导出PDF。请检查缺失/不支持的字体，并逐项确认替代；正文和模板要求尚未更改。", "issues": issues})


def _pdf_font(family: str, warnings: list[str], text: str = "") -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    catalogue = get_font_catalogue()
    reason, suggested = _font_problem(family, text)
    if reason:
        raise _font_confirmation([{"requested_font": family, "reason": reason, "suggested_font": suggested}])
    face = catalogue.face(family, text)
    alias = "DocumentFont_" + face.identity
    with FONT_LOCK:
        if alias in pdfmetrics.getRegisteredFontNames():
            return alias
        try:
            pdfmetrics.registerFont(TTFont(alias, face.path, subfontIndex=face.index))
            variants = {"normal": alias, "bold": alias, "italic": alias, "boldItalic": alias}
            required = {ord(char) for char in text if not char.isspace() and ord(char) >= 32}
            for candidate in catalogue.groups.get(face.family, []):
                if candidate.pdf_reason or not required <= candidate.coverage:
                    continue
                lower = candidate.style.casefold()
                bold = "bold" in lower or "粗" in lower
                italic = any(word in lower for word in ("italic", "oblique", "斜"))
                kind = "boldItalic" if bold and italic else "bold" if bold else "italic" if italic else None
                if kind:
                    variant = "DocumentFont_" + candidate.identity
                    try:
                        if variant not in pdfmetrics.getRegisteredFontNames():
                            pdfmetrics.registerFont(TTFont(variant, candidate.path, subfontIndex=candidate.index))
                        variants[kind] = variant
                    except Exception:
                        pass
            pdfmetrics.registerFontFamily(alias, **variants)
            return alias
        except Exception as error:
            raise _font_confirmation([{"requested_font": family, "reason": "当前PDF引擎无法嵌入此字体，请选择其他字体", "suggested_font": suggested}]) from error


def _pdf_inline(text: str, font: str, cjk_font: str, *, literal: bool = False) -> str:
    def plain(value):
        def escaped(piece):
            result = escape(piece)
            return result.replace(" ", "&#160;").replace("\t", "&#160;" * 4) if literal else result
        return "".join((f'<font name="{cjk_font}">{escaped(piece)}</font>' if any(ord(char) > 0x24F for char in piece) else escaped(piece))
                       for piece in re.split(r"([^\u0000-\u024f]+)", value)).replace("\n", "<br/>")
    output = []
    for value, mark in ([(text, "text")] if literal else inline_fragments(text)):
        escaped = plain(value)
        output.append(("<b>" + escaped + "</b>") if mark == "bold" else
                      ("<i>" + escaped + "</i>") if mark == "italic" else escaped)
    return "".join(output) or "&#160;"


def export_pdf(content: str, style: DocumentStyle, warnings: list[str] | None = None, font_substitutions: dict[str, str] | None = None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, TableStyle
    warnings = warnings if warnings is not None else []
    substitutions = font_substitutions or {}
    blocks = markdown_blocks(content)
    family_text: dict[str, list[str]] = {style.font_family: []}
    for block in blocks:
        family = style.headings[block.level - 1].font_family if block.kind == "heading" else style.font_family
        texts = [cell for row in block.rows for cell in row] if block.kind == "table" else [block.text]
        rendered = " ".join(text if block.kind in {"code", "formula"} else "".join(value for value, _ in inline_fragments(text)) for text in texts)
        if block.kind == 'bullet':
            rendered = '• ' + rendered
        elif block.kind == 'ordered':
            rendered = '0123456789. ' + rendered
        family_text.setdefault(family, []).append(rendered)
    resolved, issues = {}, []
    for requested, texts in family_text.items():
        actual = substitutions.get(requested, requested)
        validate_font_name(actual)
        text = " ".join(texts)
        reason, suggested = _font_problem(actual, text)
        if reason:
            issues.append({"requested_font": requested, "reason": ("所选替代字体“" + actual + "”仍不可用：" if actual != requested else "") + reason, "suggested_font": suggested})
        else:
            resolved[requested] = _pdf_font(actual, warnings, text)
            if actual != requested:
                warnings.append(f"已按本次明确确认，将PDF中的“{requested}”替换为“{actual}”；模板和原文档字体要求未修改。")
    if issues:
        raise _font_confirmation(issues)
    body_font = resolved[style.font_family]
    cjk_font = body_font
    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4,
                                 topMargin=style.margin_top_mm * mm, bottomMargin=style.margin_bottom_mm * mm,
                                 leftMargin=style.margin_left_mm * mm, rightMargin=style.margin_right_mm * mm,
                                 title="论文导出", author="研辩台")
    body = ParagraphStyle("DocumentBody", fontName=body_font, fontSize=style.font_size_pt,
                          leading=style.line_spacing_pt or style.font_size_pt * style.line_spacing, firstLineIndent=style.first_line_indent_pt,
                          spaceBefore=style.paragraph_before_pt, spaceAfter=style.paragraph_after_pt,
                          wordWrap="CJK", splitLongWords=True, alignment=TA_LEFT, allowWidows=0, allowOrphans=0)
    heading_styles = []
    for index, heading in enumerate(style.headings):
        heading_styles.append(ParagraphStyle(f"DocumentHeading{index + 1}", parent=body,
            fontName=resolved.get(heading.font_family, body_font), fontSize=heading.font_size_pt,
            leading=heading.line_spacing_pt or heading.font_size_pt * heading.line_spacing, firstLineIndent=0,
            spaceBefore=heading.space_before_pt, spaceAfter=heading.space_after_pt, keepWithNext=True))
    flat = ParagraphStyle("DocumentFlat", parent=body, firstLineIndent=0)
    story, counter = [], 0
    for block_index, block in enumerate(blocks):
        if block.kind == "table":
            table_style = ParagraphStyle("DocumentCell", parent=flat, fontSize=min(style.font_size_pt, 12),
                                          leading=min(style.font_size_pt, 12) * style.line_spacing, spaceBefore=0, spaceAfter=0)
            cells = [[Paragraph(_pdf_inline(cell, body_font, cjk_font), table_style) for cell in row] for row in block.rows]
            table = LongTable(cells, colWidths=[document.width / len(block.rows[0])] * len(block.rows[0]),
                              repeatRows=1, hAlign="LEFT", splitByRow=1, splitInRow=1)
            table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), .4, colors.grey),
                                       ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                                       ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                       ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]))
            story.append(table); counter = 0
            continue
        appearance = heading_styles[block.level - 1] if block.kind == "heading" else body if block.kind == "paragraph" else flat
        if block.kind == "heading" and (block_index + 1 == len(blocks) or blocks[block_index + 1].kind == "heading"):
            appearance = ParagraphStyle(f"UnchainedHeading{block_index}", parent=appearance, keepWithNext=False)
        text = block.text
        if block.kind == "ordered":
            counter += 1; text = f"{counter}. " + text
        else:
            counter = 0
            if block.kind == "bullet":
                text = "• " + text
        font = appearance.fontName
        actual_cjk = font
        story.append(Paragraph(_pdf_inline(text, font, actual_cjk, literal=block.kind in {"code", "formula"}), appearance))
    if not story:
        story.append(Paragraph("&#160;", body))
    document.build(story)
    return output.getvalue()


def export_bytes(payload: DocumentExportRequest) -> tuple[bytes, str, str, list[str]]:
    warnings = []
    if payload.format == "md":
        data, media = payload.content.encode("utf-8"), "text/markdown; charset=utf-8"
        warnings.append("Markdown保留原始源码；字体、字号、行距与页边距不属于Markdown格式，未写入文件。")
    elif payload.format == "docx":
        protected = None
        if payload.document_id and payload.docx_mode == "preserve":
            from app.agent_api import get_store
            from app.docx_preservation import protected_export
            try:
                protected = protected_export(get_store(), payload.document_id, payload.content)
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
        if protected:
            data, notes = protected
            warnings.extend(notes)
        else:
            data = export_docx(payload.content, payload.style)
            warnings.append("此文件是重新生成的可编辑副本，未保留原 Word 图片、公式结构、分节、域、批注及修订。原 DOCX 已单独留存（若曾导入）。")
            catalogue = get_font_catalogue()
            for name in dict.fromkeys([payload.style.font_family, *(heading.font_family for heading in payload.style.headings)]):
                if catalogue.face(name) is None:
                    warnings.append(f"Word已保留请求字体名称“{name}”，但本机缺少精确字体文件，打开时可能由系统替代；请在接收端核对所需字体。")
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        data, media = export_pdf(payload.content, payload.style, warnings, payload.font_substitutions), "application/pdf"
        warnings.append("PDF按A4分页；图片仅保留源地址，复杂嵌套样式不作保真还原，字体粗斜体取决于可用字形。")
    if payload.format != "md" and not (payload.format == "docx" and protected) and re.search(r"\$|\\\(|\\\[", payload.content):
        warnings.append("公式以原始LaTeX源码保留；本次导出未将公式排版为数学字形。")
    if payload.format != "md" and any(block.kind == "table-fallback" for block in markdown_blocks(payload.content)):
        warnings.append("超过8列的宽表格以逐行文本保留，避免在页面中截断；请按学校要求调整表格。")
    name = payload.name.replace("\\", "/").rsplit("/", 1)[-1].strip() or "document"
    filename = re.sub(r"\.(?:md|markdown|docx|pdf)$", "", name, flags=re.I) + "." + payload.format
    return data, media, filename, list(dict.fromkeys(warnings))


router = APIRouter()


@router.post("/document-export")
def download_document(payload: DocumentExportRequest) -> Response:
    data, media, filename, warnings = export_bytes(payload)
    return Response(data, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="document.{payload.format}"; filename*=UTF-8\'\'{quote(filename, safe="")}',
        "X-Export-Limitations": quote(json.dumps(warnings, ensure_ascii=False), safe=""),
        "X-Export-Limitations-Count": str(len(warnings)),
        "Access-Control-Expose-Headers": "Content-Disposition, X-Export-Limitations, X-Export-Limitations-Count",
        "Cache-Control": "no-store",
    })
