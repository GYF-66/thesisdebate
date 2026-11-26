"""Extract local PDF text and inspect template typography without executing content."""
from __future__ import annotations

from collections import Counter
from io import BytesIO
import math
import re
import unicodedata

from pypdf import PdfReader

MAX_PAGES = 500
MAX_CHARACTERS = 300_000
SIZE_NAMES = {"初号": 42, "小初": 36, "一号": 26, "小一": 24, "二号": 22,
              "小二": 18, "三号": 16, "小三": 15, "四号": 14, "小四": 12,
              "五号": 10.5, "小五": 9, "六号": 7.5}


def font_family(value: str) -> str | None:
    value = value.casefold().replace(" ", "")
    for family, aliases in (
        ("仿宋", ("fangsong", "仿宋", "stfangsong")),
        ("宋体", ("simsun", "stsong", "songti", "宋体")),
        ("楷体", ("simkai", "kaiti", "楷体")),
        ("黑体", ("simhei", "heiti", "黑体")),
        ("微软雅黑", ("microsoftyahei", "微软雅黑")),
        ("Times New Roman", ("timesnewroman", "times-roman", "timesnew")),
        ("Arial", ("arial", "helvetica")),
    ):
        if any(alias in value for alias in aliases):
            return family
    return None


def heading_level(text: str) -> int | None:
    text = unicodedata.normalize('NFKC', text).strip()
    if not _heading_candidate(text, 90):
        return None
    chapter = re.match(r"^第[一二三四五六七八九十百千万两零〇\d]+([章节篇部])\s*\S", text)
    if chapter:
        return 2 if chapter[1] == '节' else 1
    if re.match(r"^[一二三四五六七八九十百]+[、．.]\s*\S", text):
        return 1
    if re.match(r"^[（(][一二三四五六七八九十]+[）)]\s*\S", text):
        return 2
    english = re.match(r"^(chapter|section)\s+([\dIVXLCDM]+(?:\.\d+)*)[. :、-]*\s+\S", text, re.I)
    if english:
        return 1 if english[1].lower() == 'chapter' else min(6, max(2, english[2].count('.') + 1))
    match = re.match(r"^(\d+(?:\.\d+){1,5})(?:[.、]?\s+|(?=[^\d.\s]))\S", text)
    if not match:
        match = re.match(r"^(\d{1,2})[.、]?\s+\S", text)
    if match:
        return min(6, match[1].count(".") + 1)
    if re.fullmatch(r"摘\s*要|参考文献|致\s*谢|引言|绪论|结论|结语|附录(?:\s*[A-Z一二三四五六七八九十\d]+)?|abstract|references|acknowledgements?", text, re.I):
        return 1
    return None


def _heading_candidate(text: str, limit: int = 60) -> bool:
    text = unicodedata.normalize('NFKC', text).strip()
    return bool(text and len(text) <= limit
                and not re.search(r'[。；;！？!?]', text)
                and not re.search(r'(?:\.{2,}|…+|·{2,})\s*\d+\s*$', text)
                and not re.match(r'^\d+(?:\.\d+)?\s*(?:年|月|日|份|个|名|次|项|人|页|%|％)', text)
                and not re.fullmatch(r'[\d\W_]+', text))


def _title_key(text: str) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text)).casefold()


def _pdf_bookmarks(reader: PdfReader) -> dict:
    """Use only bounded, local outline destinations that match real page text."""
    result = {}
    def walk(entries, level=1):
        if level > 6:
            return
        for entry in entries:
            if len(result) >= 2000:
                return
            if isinstance(entry, list):
                walk(entry, level + 1)
            else:
                try:
                    title = str(entry.title).strip(); page = reader.get_destination_page_number(entry)
                    if page is not None and page >= 0 and _heading_candidate(title, 90):
                        result.setdefault((page + 1, _title_key(title)), level)
                except (AttributeError, KeyError, TypeError, ValueError):
                    continue
    try:
        walk(reader.outline)
    except Exception:
        # Broken optional bookmarks must not prevent ordinary text extraction.
        return {}
    return result


def extract_pdf(data: bytes) -> dict:
    if not data.lstrip().startswith(b"%PDF-"):
        raise ValueError("该文件不是有效 PDF，请检查文件内容。")
    try:
        reader = PdfReader(BytesIO(data), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF 已加密，请先用密码打开并另存为未加密副本。")
        if not 0 < len(reader.pages) <= MAX_PAGES:
            raise ValueError("PDF 页数须为 1–500 页，请拆分后导入。")
        pages, spans, empty_pages = [], [], []
        total = 0
        for page_index, page in enumerate(reader.pages, 1):
            def visit(text, cm, tm, font, size):
                clean = text.strip()
                if not clean or len(spans) >= 100_000:
                    return
                actual_size = abs(float(size)) * math.hypot(float(cm[0]), float(cm[1]))
                spans.append({"text": clean, "size": round(actual_size, 2),
                              "font": str((font or {}).get("/BaseFont", "")), "page": page_index})
            text = (page.extract_text(visitor_text=visit) or "").replace("\x00", "")
            total += len(text)
            if total > MAX_CHARACTERS:
                raise ValueError("PDF 正文超过 300000 字符，请拆分后导入。")
            if not text.strip():
                empty_pages.append(page_index)
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if lines:
                pages.append((page_index, lines))
        if not pages:
            raise ValueError("PDF 没有可提取的文字层；请先进行 OCR 文字识别，再导入可搜索 PDF。")
        warnings = ["已提取 PDF 文字并创建可编辑副本，原文件保留。",
                    "PDF 为固定版式：分栏、表格、图片和公式请对照原稿核验。"]
        bookmarks = _pdf_bookmarks(reader)
        body_sizes = Counter()
        sizes = {}
        for span in spans:
            for line in span['text'].splitlines():
                key = (span['page'], _title_key(line))
                sizes[key] = max(sizes.get(key, 0), span['size'])
                if not heading_level(line) and 8 <= span['size'] <= 36:
                    body_sizes[span['size']] += len(line)
        body_size = body_sizes.most_common(1)[0][0] if body_sizes else 12
        title_sizes = sorted({size for (page, key), size in sizes.items()
                              if size >= body_size * 1.18 and 8 <= size <= 48}, reverse=True)
        size_levels = {size: min(6, index + 1) for index, size in enumerate(title_sizes)}
        seen, headings, rendered = {}, [], []
        for page_index, lines in pages:
            output = []
            for line_index, line in enumerate(lines):
                key = _title_key(line)
                size = sizes.get((page_index, key), body_size)
                level = bookmarks.get((page_index, key)) or heading_level(line)
                if not level and _heading_candidate(line) and not re.search(r'[,，:：|]', line):
                    level = size_levels.get(size)
                # A title repeated on later pages is usually a running header.
                earlier = seen.get(key)
                if earlier and earlier[0] != page_index and (size < earlier[1] or (size <= body_size and (line_index < 2 or line_index >= len(lines) - 2))):
                    level = None
                if level:
                    seen.setdefault(key, (page_index, size)); headings.append({'title': line, 'level': level, 'page': page_index})
                output.append('#' * level + ' ' + line if level else line)
            rendered.append('\n\n'.join(output))
        if headings:
            warnings.append(f'已自动识别 {len(headings)} 个章节标题并加入左侧文档目录；可点击目录定位，标题级别可在编辑器中调整。')
        else:
            warnings.append('未识别到可靠的章节标题；可选中章节名称，在工具栏设置为标题后加入文档目录。')
        if empty_pages:
            warnings.append("第 " + "、".join(map(str, empty_pages[:20])) + " 页未检测到文字层，请核对是否需要 OCR。")
        return {"content": "\n\n".join(rendered), "spans": spans, 'headings': headings,
                "page_count": len(reader.pages), "warnings": warnings}
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("PDF 文件损坏或结构异常，请重新导出后导入。") from error


def inspect_pdf_style(extracted: dict):
    from app.document_layout import DocumentStyle
    style = DocumentStyle()
    warnings = []
    body = [span for span in extracted["spans"] if not heading_level(span["text"]) and 8 <= span["size"] <= 36]
    counts = Counter()
    fonts = Counter()
    for span in body:
        counts[span["size"]] += len(span["text"])
        family = font_family(span["font"])
        if family:
            fonts[family] += len(span["text"])
    if counts:
        style.font_size_pt = float(counts.most_common(1)[0][0])
    if fonts:
        style.font_family = fonts.most_common(1)[0][0]
    else:
        warnings.append("PDF 正文字体名称未匹配，暂用宋体，请核对。")
    for span in extracted["spans"]:
        level = heading_level(span["text"])
        if level and 8 <= span["size"] <= 48:
            heading = style.headings[level - 1]
            heading.font_size_pt = float(span["size"])
            family = font_family(span["font"])
            if family:
                heading.font_family = family

    # Explicit school instructions override the typography of the instruction sheet itself.
    text = extracted["content"].replace("#", "")
    size_pattern = "|".join(sorted(SIZE_NAMES, key=len, reverse=True))
    declared = set()
    sections = [("正文", style)] + [(f"{label}级标题", style.headings[i]) for i, label in enumerate("一二三四五六")]
    for label, target in sections:
        label_pattern = label if label == "正文" else "(?:" + label[0] + "|" + str("一二三四五六".index(label[0]) + 1) + ")级标题"
        match = re.search(label_pattern + r"[^\n。；]{0,100}", text)
        if not match:
            continue
        segment = match[0]
        family = font_family(segment)
        if family:
            target.font_family = family
            declared.add(label + "字体")
        number = re.search(r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:磅|pt|points?)", segment, re.I)
        named = re.search(size_pattern, segment)
        size = float(number[1]) if number else SIZE_NAMES[named[0]] if named else None
        if size and 8 <= size <= (36 if label == "正文" else 48):
            target.font_size_pt = float(size)
            declared.add(label + "字号")
        spacing = re.search(r"(\d+(?:\.\d+)?)\s*倍(?:行距)?", segment)
        if spacing and 1 <= float(spacing[1]) <= 3:
            target.line_spacing = float(spacing[1])
            declared.add(label + "行距")
    for chinese, field in (("上", "margin_top_mm"), ("下", "margin_bottom_mm"), ("左", "margin_left_mm"), ("右", "margin_right_mm")):
        match = re.search(chinese + r"(?:页)?边距\s*[:：为]?\s*(\d+(?:\.\d+)?)\s*(厘米|cm|毫米|mm)", text, re.I)
        if match:
            value = float(match[1]) * (10 if match[2].lower() in {"厘米", "cm"} else 1)
            if 10 <= value <= 60:
                setattr(style, field, value)
                declared.add(chinese + "页边距")
    indent = re.search(r"首行(?:缩进)?\s*[:：为]?\s*(\d+(?:\.\d+)?|两|二)\s*(?:个)?字符", text)
    if indent:
        value = (2 if indent[1] in {"两", "二"} else float(indent[1])) * style.font_size_pt
        if 0 <= value <= 72:
            style.first_line_indent_pt = value
            declared.add("首行缩进")
    if declared:
        warnings.append("已读取规范文字中的明确要求：" + "、".join(sorted(declared)) + "。")
    warnings.append("PDF 无 Word 样式定义：未明确写出的行距、页边距、段距及未出现的标题级别采用默认值，请在下方核对后保存。")
    return DocumentStyle.model_validate(style.model_dump()), warnings
