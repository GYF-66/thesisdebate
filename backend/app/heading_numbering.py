"""Deterministic heading prefixes shared by formatting and revision checks."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal, Sequence


HeadingStyle = Literal["preserve", "decimal", "chinese", "chinese-paren", "arabic-paren", "chapter", "none"]
DEFAULT_HEADING_STYLES: list[HeadingStyle] = ["preserve"] * 6
CHINESE = "零〇一二三四五六七八九十百千万两"
UNNUMBERED_TITLES = {
    "摘要", "中文摘要", "英文摘要", "abstract", "关键词", "keywords", "目录", "contents",
    "参考文献", "references", "参考资料", "致谢", "acknowledgments", "acknowledgements", "附录",
}


@dataclass(frozen=True)
class HeadingPrefix:
    style: str
    values: tuple[int, ...]
    prefix: str
    body: str


def chinese_number(value: int) -> str:
    if value <= 0:
        return "零"
    if value >= 10_000:
        high, low = divmod(value, 10_000)
        return chinese_number(high) + "万" + (("零" if low < 1000 else "") + chinese_number(low) if low else "")
    digits = "零一二三四五六七八九"
    pieces = []
    pending_zero = False
    for divisor, unit in ((1000, "千"), (100, "百"), (10, "十"), (1, "")):
        digit, value = divmod(value, divisor)
        if digit:
            if pending_zero:
                pieces.append("零")
            if not (divisor == 10 and digit == 1 and not pieces):
                pieces.append(digits[digit])
            pieces.append(unit)
            pending_zero = False
        elif pieces and value:
            pending_zero = True
    return "".join(pieces)


def _chinese_value(text: str) -> int:
    digits = {char: index for index, char in enumerate("零一二三四五六七八九")}
    digits.update({"〇": 0, "两": 2})
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    total = group = digit = 0
    for char in text:
        if char in digits:
            digit = digits[char]
        elif char == "万":
            total += (group + digit) * 10000
            group = digit = 0
        else:
            group += (digit or 1) * units[char]
            digit = 0
    return total + group + digit


def unwrap_heading(title: str) -> tuple[str, str]:
    """Keep an existing whole-heading bold wrapper when replacing its prefix."""
    title = title.strip()
    for marker in ("**", "__"):
        if title.startswith(marker) and title.endswith(marker) and len(title) > len(marker) * 2:
            # A prefix-only wrapper such as **二、** 标题 is handled by parse_prefix.
            inner = title[len(marker):-len(marker)]
            if marker not in inner:
                return inner.strip(), marker
    return title, ""


def parse_prefix(title: str) -> HeadingPrefix | None:
    """Recognize explicit separators, never number-like words such as 2026年/3D."""
    original = title
    marker = ""
    for candidate in ("**", "__"):
        if title.startswith(candidate):
            marker = candidate
            title = title[len(candidate):]
            break
    patterns = (
        ("chapter", rf"第([{CHINESE}\d]+)[章节篇部]\s*"),
        ("appendix", rf"附录(?:[{CHINESE}A-Za-z\d]+)(?:\s+|(?=[\u4e00-\u9fff])|$)"),
        ("chinese-paren", rf"[（(]([{CHINESE}]+)[）)]\s*"),
        ("arabic-paren", r"[（(](\d+)[）)]\s*"),
        ("chinese", rf"([{CHINESE}]+)[、．.]\s*"),
        ("decimal", r"(\d+(?:[.．]\d+)+)(?:[、．.]?(?:\s+|$)|(?=\*\*|__))"),
        ("decimal", r"(\d+)[、．.](?!\d)\s*"),
        ("decimal", r"(\d+)(?:\s+|(?=\*\*|__)|$)"),
    )
    for style, expression in patterns:
        match = re.match(expression, title)
        if not match:
            continue
        body = title[match.end():]
        if marker:
            if not body.startswith(marker):
                continue
            body = body[len(marker):]
        if not body.strip():
            continue
        if style == "appendix":
            values = ()
        else:
            number = match.group(1)
            values = tuple(int(part) for part in re.split(r"[.．]", number)) if number[0].isdigit() else (_chinese_value(number),)
        consumed = len(original) - len(body)
        return HeadingPrefix(style, values, original[:consumed], body.lstrip())
    return None


def clean_duplicate_prefix(title: str) -> str:
    """Undo the old engine's Arabic prefix in '2 二、标题', without deleting body numbers."""
    core, wrapper = unwrap_heading(title)
    for _ in range(4):
        first = parse_prefix(core)
        second_core, _ = unwrap_heading(first.body) if first else ("", "")
        second = parse_prefix(second_core) if first else None
        if not first or not second or first.style != "decimal":
            break
        # Two bare integers may be actual title content. A different explicit
        # numbering form, or a hierarchical inner number, is an unambiguous duplicate.
        if second.style == "decimal" and len(second.values) == 1:
            break
        core = first.body
    return wrapper + core + wrapper


def heading_body(title: str) -> str:
    core, _ = unwrap_heading(clean_duplicate_prefix(title))
    parsed = parse_prefix(core)
    return parsed.body if parsed else core


def is_unnumbered_heading(title: str) -> bool:
    core, _ = unwrap_heading(clean_duplicate_prefix(title))
    parsed = parse_prefix(core)
    if parsed and parsed.style == "appendix":
        return True
    body = parsed.body if parsed else core
    return re.sub(r"\s+", "", body).casefold() in UNNUMBERED_TITLES


def format_heading(title: str, counts: Sequence[int], level: int, style: HeadingStyle = "preserve") -> str:
    cleaned = clean_duplicate_prefix(title)
    core, wrapper = unwrap_heading(cleaned)
    parsed = parse_prefix(core)
    if is_unnumbered_heading(cleaned):
        return cleaned
    if style == "preserve" and parsed:
        return cleaned
    body = parsed.body if parsed else core
    local = counts[level - 1]
    prefixes = {
        "decimal": ".".join(str(value) for value in counts[:level]),
        "chinese": chinese_number(local) + "、",
        "chinese-paren": "（" + chinese_number(local) + "）",
        "arabic-paren": "（" + str(local) + "）",
        "chapter": "第" + chinese_number(local) + "章",
        "none": "",
    }
    prefix = prefixes["decimal" if style == "preserve" else style]
    return wrapper + ((prefix + " ") if prefix else "") + body + wrapper


def strip_heading_prefixes(text: str) -> str:
    """Remove structural prefixes only from Markdown heading lines for token checks."""
    return re.sub(r"(?m)^#{1,6}\s+([^\r\n]+)", lambda match: heading_body(match.group(1)), text)
