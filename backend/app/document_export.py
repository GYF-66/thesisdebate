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


class DocumentExportRequest(AppearanceModel):
    name: str = Field(default="论文", min_length=1, max_length=255)
    content: str = Field(max_length=300_000)
    style: DocumentStyle
    format: Literal["pdf", "md", "docx"]
    track_history: bool = False
    document_id: str | None = Field(default=None, max_length=100)
    export_scope: Literal['original', 'expected', 'current'] = 'current'

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
    normal.paragraph_format.line_spacing = style.line_spacing
    normal.paragraph_format.space_before = Pt(style.paragraph_before_pt)
    normal.paragraph_format.space_after = Pt(style.paragraph_after_pt)
    normal.paragraph_format.first_line_indent = Pt(style.first_line_indent_pt)
    for level, appearance in enumerate(style.headings, 1):
        heading = document.styles[f"Heading {level}"]
        _docx_font(heading.font, appearance.font_family, appearance.font_size_pt)
        heading.paragraph_format.line_spacing = appearance.line_spacing
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


FONT_FILES = {"宋体": "simsun.ttc", "黑体": "simhei.ttf", "仿宋": "simfang.ttf", "楷体": "simkai.ttf",
              "微软雅黑": "msyh.ttc", "Times New Roman": "times.ttf", "Arial": "arial.ttf"}
FONT_LOCK = RLock()


def _pdf_font(family: str, warnings: list[str]) -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont
    alias = "DocumentFont" + str(list(FONT_FILES).index(family))
    with FONT_LOCK:
        if alias in pdfmetrics.getRegisteredFontNames():
            return alias
        file = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / FONT_FILES[family]
        if file.is_file():
            try:
                pdfmetrics.registerFont(TTFont(alias, str(file), subfontIndex=0))
                pdfmetrics.registerFontFamily(alias, normal=alias, bold=alias, italic=alias, boldItalic=alias)
                return alias
            except Exception:
                pass
        fallback = "STSong-Light" if family not in {"Times New Roman", "Arial"} else "Times-Roman" if family == "Times New Roman" else "Helvetica"
        if fallback == "STSong-Light" and fallback not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont(fallback))
        warnings.append(f"系统未提供可嵌入的{family}字体，PDF使用{fallback}替代；字体外观可能不同。")
        return fallback


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


def export_pdf(content: str, style: DocumentStyle, warnings: list[str] | None = None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, TableStyle
    warnings = warnings if warnings is not None else []
    body_font = _pdf_font(style.font_family, warnings)
    cjk_font = body_font if style.font_family not in {"Arial", "Times New Roman"} else _pdf_font("宋体", warnings)
    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4,
                                 topMargin=style.margin_top_mm * mm, bottomMargin=style.margin_bottom_mm * mm,
                                 leftMargin=style.margin_left_mm * mm, rightMargin=style.margin_right_mm * mm,
                                 title="论文导出", author="研辩台")
    body = ParagraphStyle("DocumentBody", fontName=body_font, fontSize=style.font_size_pt,
                          leading=style.font_size_pt * style.line_spacing, firstLineIndent=style.first_line_indent_pt,
                          spaceBefore=style.paragraph_before_pt, spaceAfter=style.paragraph_after_pt,
                          wordWrap="CJK", splitLongWords=True, alignment=TA_LEFT, allowWidows=0, allowOrphans=0)
    heading_styles = []
    for index, heading in enumerate(style.headings):
        heading_styles.append(ParagraphStyle(f"DocumentHeading{index + 1}", parent=body,
            fontName=_pdf_font(heading.font_family, warnings), fontSize=heading.font_size_pt,
            leading=heading.font_size_pt * heading.line_spacing, firstLineIndent=0,
            spaceBefore=heading.space_before_pt, spaceAfter=heading.space_after_pt, keepWithNext=True))
    flat = ParagraphStyle("DocumentFlat", parent=body, firstLineIndent=0)
    story, counter = [], 0
    blocks = markdown_blocks(content)
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
        actual_cjk = font if block.kind == "heading" and style.headings[block.level - 1].font_family not in {"Arial", "Times New Roman"} else cjk_font
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
        data, media = export_docx(payload.content, payload.style), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        warnings.append("DOCX保留标题、表格、列表和基础行内样式；图片仅保留源地址，复杂嵌套及原Word分页不作保真还原。")
    else:
        data, media = export_pdf(payload.content, payload.style, warnings), "application/pdf"
        warnings.append("PDF按A4分页；图片仅保留源地址，复杂嵌套样式不作保真还原，字体粗斜体取决于可用字形。")
    if payload.format != "md" and re.search(r"\$|\\\(|\\\[", payload.content):
        warnings.append("公式以原始LaTeX源码保留；本次导出未将公式排版为数学字形。")
    if payload.format != "md" and any(block.kind == "table-fallback" for block in markdown_blocks(payload.content)):
        warnings.append("超过8列的宽表格以逐行文本保留，避免在页面中截断；请按学校要求调整表格。")
    name = payload.name.replace("\\", "/").rsplit("/", 1)[-1].strip() or "document"
    filename = re.sub(r"\.(?:md|markdown|docx|pdf)$", "", name, flags=re.I) + "." + payload.format
    return data, media, filename, list(dict.fromkeys(warnings))


router = APIRouter()


@router.post("/document-export")
def download_document(payload: DocumentExportRequest) -> Response:
    if payload.track_history and payload.document_id:
        from app.agent_api import get_store
        if get_store().get_workspace_document(payload.document_id) is None:
            raise HTTPException(404, '文档不存在，请重新选择。')
    data, media, filename, warnings = export_bytes(payload)
    if payload.track_history:
        from app.agent_api import get_store
        get_store().save_export(payload.document_id, filename, payload.format, payload.export_scope, payload.content, media, data)
    return Response(data, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="document.{payload.format}"; filename*=UTF-8\'\'{quote(filename, safe="")}',
        "X-Export-Limitations": quote(json.dumps(warnings, ensure_ascii=False), safe=""),
        "X-Export-Limitations-Count": str(len(warnings)),
        "Access-Control-Expose-Headers": "Content-Disposition, X-Export-Limitations, X-Export-Limitations-Count",
        "Cache-Control": "no-store",
    })
