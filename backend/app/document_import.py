"""Bounded, local-only text extraction into an editable manuscript copy."""
from __future__ import annotations

from io import BytesIO
import posixpath
import re
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree as ET
import zipfile


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_EXPANDED_BYTES = 20 * 1024 * 1024
MAX_CONTENT_CHARACTERS = 300_000
MAX_ZIP_ENTRIES = 2000
MAX_COMPRESSION_RATIO = 200
MIN_RATIO_CHECK_BYTES = 1024 * 1024
MAX_XML_ELEMENTS = 200_000
MAX_XML_DEPTH = 128
WORD_NAMESPACES = {
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "http://purl.oclc.org/ooxml/wordprocessingml/main",
}
MATH_NAMESPACES = {
    "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "http://purl.oclc.org/ooxml/officeDocument/math",
}
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOCUMENT_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
COPY_WARNING = "已创建可修订的文本副本，原文件未修改。"


class DocumentImportError(ValueError):
    """An unsupported or invalid upload; its message can be shown to the user."""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def _attr(node, name: str, default: str = "") -> str:
    for key, value in node.attrib.items():
        if _local(key) == name:
            return value
    return default


def _child(node, name: str):
    return next((item for item in node if _local(item.tag) == name), None)


class _SafeBuilder(ET.TreeBuilder):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.count = 0

    def start(self, tag, attrs):
        self.depth += 1
        self.count += 1
        if self.depth > MAX_XML_DEPTH or self.count > MAX_XML_ELEMENTS:
            raise DocumentImportError("DOCX 的 XML 结构过大或嵌套过深，无法安全导入。")
        return super().start(tag, attrs)

    def end(self, tag):
        result = super().end(tag)
        self.depth -= 1
        return result

    def doctype(self, name, pubid, system):
        raise DocumentImportError("DOCX 含不允许的 DTD 或实体声明。")


def _xml(data: bytes):
    # The zero-byte removal also detects declarations in UTF-16/UTF-32 XML.
    declarations = data.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
        raise DocumentImportError("DOCX 含不允许的 DTD 或实体声明。")
    try:
        return ET.fromstring(data, parser=ET.XMLParser(target=_SafeBuilder()))
    except DocumentImportError:
        raise
    except (ET.ParseError, ValueError, LookupError, UnicodeError) as error:
        raise DocumentImportError("DOCX 的 XML 内容损坏或编码不受支持。") from error


def _safe_archive_name(name: str) -> bool:
    return bool(name and len(name) <= 512 and not name.startswith("/")
                and not any(char in name for char in "\\\x00:")
                and all(part not in {"", ".", ".."} for part in name.rstrip("/").split("/")))


def _resolve_part(parent: str, target: str) -> str:
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise DocumentImportError("DOCX 的内部部件关系无效。")
    decoded = unquote(parsed.path)
    if "\\" in decoded or "\x00" in decoded:
        raise DocumentImportError("DOCX 的内部部件路径无效。")
    name = posixpath.normpath(decoded.lstrip("/") if decoded.startswith("/")
                                 else posixpath.join(posixpath.dirname(parent), decoded))
    if not _safe_archive_name(name):
        raise DocumentImportError("DOCX 的内部部件路径无效。")
    return name


def _heading_number(value: str) -> int | None:
    match = re.fullmatch(r"(?:heading|标题)\s*([1-6])", value.strip(), flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _enabled(node) -> bool:
    return _attr(node, "val", "true").lower() not in {"0", "false", "off", "no"}


def _number(value: str, maximum: int = 999_999_999) -> int | None:
    if re.fullmatch(r"[0-9]{1,10}", value):
        integer = int(value)
        if integer <= maximum:
            return integer
    return None


def _escape_text(text: str) -> str:
    # DOCX text is literal; actual asterisks and backticks remain visible text.
    return re.sub(r"([\\\x60*_\[\]<>~])", r"\\\1", text.replace("&", "&amp;"))


def _run_properties(props) -> dict[str, bool]:
    result = {}
    if props is not None:
        for name, kind in [("b", "bold"), ("i", "italic"), ("strike", "strike"), ("dstrike", "strike")]:
            item = _child(props, name)
            if item is not None:
                result[kind] = _enabled(item)
    return result


def _number_properties(props) -> dict[str, str]:
    numbering = _child(props, "numPr") if props is not None else None
    return {name: _attr(item, "val") for name in ["numId", "ilvl"]
            if numbering is not None and (item := _child(numbering, name)) is not None}


def _marked_text(text: str, marks: dict[str, bool]) -> str:
    if not text:
        return text
    if marks.get("code"):
        core = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        longest = max((len(item) for item in re.findall(r"\x60+", core)), default=0)
        fence = chr(96) * (longest + 1)
        padding = " " if (core.startswith(chr(96)) or core.endswith(chr(96))
                           or (core.startswith(" ") and core.endswith(" ") and core.strip())) else ""
        core = fence + padding + core + padding + fence
        leading = trailing = ""
    else:
        # Emphasis delimiters cannot enclose edge whitespace.
        match = re.fullmatch(r"(\s*)(.*?)(\s*)", text, flags=re.DOTALL)
        leading, core, trailing = match.groups()
        if not core:
            return text
    if marks.get("bold") and marks.get("italic"):
        core = "***" + core + "***"
    elif marks.get("bold"):
        core = "**" + core + "**"
    elif marks.get("italic"):
        core = "*" + core + "*"
    if marks.get("strike"):
        core = "~~" + core + "~~"
    return leading + core + trailing


def _safe_hyperlink(target: str) -> str | None:
    if (not target or len(target) > 8192 or "\\" in target
            or any(ord(char) < 32 or ord(char) == 127 for char in unquote(target))):
        return None
    try:
        parsed = urlsplit(target)
        if parsed.scheme.lower() in {"http", "https"}:
            if not parsed.hostname or parsed.username is not None or parsed.password is not None:
                return None
            parsed.port  # Reject invalid ports without any I/O.
        elif parsed.scheme.lower() == "mailto":
            if not parsed.path or parsed.netloc:
                return None
        else:
            return None
    except ValueError:
        return None
    return quote(target, safe="/:?#[]@!$&'()*+,;=%")


class _DocxReader:
    def __init__(self, archive: zipfile.ZipFile):
        self.archive = archive
        self.names: set[str] = set()
        self.warnings = [COPY_WARNING, "DOCX 已转换为 Markdown 文本；原版式、分页、图片及不支持的样式不作保真还原。"]
        self.heading_styles: dict[str, int] = {}
        self.styles: dict[str, dict] = {}
        self.default_run: dict[str, bool] = {}
        self.paragraph_run: dict[str, bool] = {}
        self.hyperlinks: dict[str, str] = {}
        self.numberings: dict[str, dict[int, dict]] = {}
        self.numbering_styles: dict[str, tuple[str, int]] = {}
        self.counters: dict[tuple[str, int], int] = {}
        self.list_layout: dict[str, dict[int, tuple[int, int]]] = {}
        self.note_ids: dict[str, list[str]] = {"footnote": [], "endnote": []}

    def warn(self, text: str):
        if text not in self.warnings:
            self.warnings.append(text)

    def check_archive(self):
        infos = self.archive.infolist()
        if not infos or len(infos) > MAX_ZIP_ENTRIES:
            raise DocumentImportError("DOCX 内部文件数量异常。")
        expanded = 0
        folded = set()
        for info in infos:
            name = info.filename
            if not _safe_archive_name(name) or not _safe_archive_name(info.orig_filename):
                raise DocumentImportError("DOCX 含不安全的内部文件路径。")
            if name.casefold() in folded:
                raise DocumentImportError("DOCX 含重复或歧义的内部文件。")
            folded.add(name.casefold())
            if info.flag_bits & 1:
                raise DocumentImportError("不支持加密的 DOCX，请先在本地另存为未加密文件。")
            if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise DocumentImportError("DOCX 使用了不受支持的压缩方式。")
            expanded += info.file_size
            if info.file_size > MAX_EXPANDED_BYTES or expanded > MAX_EXPANDED_BYTES:
                raise DocumentImportError("DOCX 展开后的总大小不能超过 20 MiB。")
            if (info.file_size >= MIN_RATIO_CHECK_BYTES
                    and info.file_size / max(info.compress_size, 1) > MAX_COMPRESSION_RATIO):
                raise DocumentImportError("DOCX 内部压缩比异常，无法安全导入。")
            lower = name.casefold()
            if "vbaproject" in lower or "vbadata" in lower or "/activex/" in "/" + lower:
                raise DocumentImportError("DOCX 含宏或活动控件，不支持伪装为 DOCX 的宏文件。")
            if not info.is_dir():
                self.names.add(name)
        if self.archive.testzip() is not None:
            raise DocumentImportError("DOCX 压缩内容已损坏。")

    def read_xml(self, name: str):
        if name not in self.names:
            raise DocumentImportError("DOCX 缺少必需的内部部件。")
        return _xml(self.archive.read(name))

    def relationships(self, name: str) -> list:
        if name not in self.names:
            return []
        root = self.read_xml(name)
        if root.tag != "{" + RELATIONSHIPS_NS + "}Relationships":
            raise DocumentImportError("DOCX 的部件关系结构无效。")
        result = []
        seen = set()
        for item in root:
            if _local(item.tag) != "Relationship":
                continue
            identity = item.get("Id", "")
            if not identity or identity in seen:
                raise DocumentImportError("DOCX 含重复或无效的部件关系。")
            seen.add(identity)
            if item.get("TargetMode", "").lower() == "external":
                self.warn("外部链接仅保留安全的 HTTP、HTTPS 或邮件地址；未访问链接或加载外部资源。")
            result.append(item)
        return result

    def main_part(self) -> str:
        types = self.read_xml("[Content_Types].xml")
        if types.tag != "{" + CONTENT_TYPES_NS + "}Types":
            raise DocumentImportError("该文件不是有效的标准 DOCX 文档。")
        overrides = {}
        for item in types:
            content_type = item.get("ContentType", "")
            if "macroenabled" in content_type.lower() or "vba" in content_type.lower():
                raise DocumentImportError("检测到宏文档，不能作为 DOCX 导入。")
            if _local(item.tag) == "Override":
                name = _resolve_part("", item.get("PartName", ""))
                if name in overrides:
                    raise DocumentImportError("DOCX 的内容类型定义重复。")
                overrides[name] = content_type
        documents = [item for item in self.relationships("_rels/.rels")
                     if item.get("Type", "").endswith("/officeDocument")]
        if len(documents) != 1 or documents[0].get("TargetMode", "").lower() == "external":
            raise DocumentImportError("DOCX 缺少有效的本地主文档关系。")
        name = _resolve_part("", documents[0].get("Target", ""))
        if overrides.get(name) != DOCUMENT_CONTENT_TYPE:
            raise DocumentImportError("该文件不是标准 DOCX 正文，可能是模板或改名的宏文档。")
        return name

    def load_styles(self, name: str | None):
        if not name:
            return
        root = self.read_xml(name)
        if _local(root.tag) != "styles" or _namespace(root.tag) not in WORD_NAMESPACES:
            raise DocumentImportError("DOCX 的段落样式结构无效。")
        defaults = _child(root, "docDefaults")
        run_defaults = _child(defaults, "rPrDefault") if defaults is not None else None
        self.default_run = _run_properties(_child(run_defaults, "rPr") if run_defaults is not None else None)
        raw = {}
        for node in root:
            if _local(node.tag) != "style" or _attr(node, "type") not in {"paragraph", "character", "numbering"}:
                continue
            identity = _attr(node, "styleId")
            if not identity or identity in raw:
                raise DocumentImportError("DOCX 含重复或无效的样式定义。")
            display = _child(node, "name")
            inherited = _child(node, "basedOn")
            props = _child(node, "pPr")
            outline = _child(props, "outlineLvl") if props is not None else None
            level = _heading_number(identity) or (_heading_number(_attr(display, "val")) if display is not None else None)
            if outline is not None and _attr(outline, "val") in {str(i) for i in range(6)}:
                level = int(_attr(outline, "val")) + 1
            marks = _run_properties(_child(node, "rPr"))
            code_name = (identity + " " + (_attr(display, "val") if display is not None else "")).casefold()
            if re.search(r"(?:\bcode\b|codechar|sourcecode|htmlcode|verbatim|代码|源码)", code_name):
                marks["code"] = True
            raw[identity] = {"level": level, "parent": _attr(inherited, "val") if inherited is not None else "",
                             "marks": marks, "number": _number_properties(props)}
        for identity in raw:
            chain, seen, current = [], set(), identity
            while current and current not in self.styles and current in raw:
                if current in seen or len(chain) >= MAX_XML_DEPTH:
                    raise DocumentImportError("DOCX 的样式继承存在循环或层级过深。")
                seen.add(current); chain.append(current); current = raw[current]["parent"]
            parent = self.styles.get(current, {"level": _heading_number(current), "marks": {}, "number": {}})
            for key in reversed(chain):
                item = raw[key]
                parent = {"level": item["level"] or parent["level"],
                          "marks": {**parent["marks"], **item["marks"]},
                          "number": {**parent["number"], **item["number"]}}
                self.styles[key] = parent
                if parent["level"]:
                    self.heading_styles[key] = parent["level"]

    def load_numbering(self, name: str | None):
        if not name:
            return
        root = self.read_xml(name)
        if _local(root.tag) != "numbering" or _namespace(root.tag) not in WORD_NAMESPACES:
            raise DocumentImportError("DOCX 的列表编号结构无效。")
        abstracts = {}
        def level_value(node, previous=None):
            value = dict(previous or {"format": "decimal", "start": 1, "restart": None, "text": "", "style": ""})
            for child_name, key in [("numFmt", "format"), ("lvlText", "text"), ("pStyle", "style")]:
                child = _child(node, child_name)
                if child is not None:
                    value[key] = _attr(child, "val")
            for child_name, key in [("start", "start"), ("lvlRestart", "restart")]:
                child = _child(node, child_name)
                if child is not None:
                    value[key] = _number(_attr(child, "val"))
            return value
        for abstract in root:
            if _local(abstract.tag) != "abstractNum":
                continue
            identity = _attr(abstract, "abstractNumId")
            if _number(identity) is None or identity in abstracts:
                raise DocumentImportError("DOCX 含重复或无效的列表编号定义。")
            levels = {}
            for node in abstract:
                if _local(node.tag) != "lvl":
                    continue
                level = _number(_attr(node, "ilvl"), 8)
                if level is None or level in levels:
                    raise DocumentImportError("DOCX 的列表层级无效。")
                levels[level] = level_value(node)
            abstracts[identity] = levels
        for number in root:
            if _local(number.tag) != "num":
                continue
            identity = _attr(number, "numId")
            if _number(identity) is None or identity in self.numberings:
                raise DocumentImportError("DOCX 含重复或无效的列表实例。")
            abstract = _child(number, "abstractNumId")
            base = abstracts.get(_attr(abstract, "val") if abstract is not None else "")
            if base is None:
                self.warn("部分列表缺少编号定义，只保留其正文文字。")
                continue
            levels = {key: dict(value) for key, value in base.items()}
            seen = set()
            for override in number:
                if _local(override.tag) != "lvlOverride":
                    continue
                level = _number(_attr(override, "ilvl"), 8)
                if level is None or level in seen:
                    raise DocumentImportError("DOCX 的列表覆盖层级无效。")
                seen.add(level)
                replacement = _child(override, "lvl")
                value = level_value(replacement, levels.get(level)) if replacement is not None else dict(levels.get(level, {}))
                start = _child(override, "startOverride")
                if start is not None:
                    value["start"] = _number(_attr(start, "val"))
                levels[level] = value
            self.numberings[identity] = levels
            for level, value in levels.items():
                if value.get("style"):
                    self.numbering_styles.setdefault(value["style"], (identity, level))

    def list_marker(self, numbering: dict[str, str]) -> tuple[str, int] | None:
        identity = numbering.get("numId", "")
        if identity == "0":
            return None
        level = _number(numbering.get("ilvl", "0"), 8)
        levels = self.numberings.get(identity, {})
        definition = levels.get(level)
        if level is None or not definition or definition.get("start") is None:
            self.warn("部分列表缺少有效的编号或层级定义，只保留其正文文字。")
            return None
        kind = definition.get("format")
        if kind == "none":
            return None
        if kind != "bullet" and kind not in {"decimal", "decimalZero", "upperLetter", "lowerLetter", "upperRoman", "lowerRoman"}:
            self.warn("部分特殊列表编号无法转换为 Markdown，只保留其正文文字。")
            return None
        for other, info in levels.items():
            restart = info.get("restart")
            if restart is None or restart > other:
                restart = other
            if other > level and restart and level <= restart - 1:
                self.counters.pop((identity, other), None)
        key = (identity, level)
        counter = self.counters.get(key, definition["start"] - 1) + 1
        self.counters[key] = counter
        if counter > 999_999_999:
            self.warn("部分列表编号超出 Markdown 支持范围，只保留其正文文字。")
            return None
        if kind not in {"bullet", "decimal"} or re.search(r"%[1-9].*%[1-9]", definition.get("text", "")):
            self.warn("字母、罗马数字或复合列表标签已规范为 Markdown 十进制编号；层级与起始值保留。")
        marker = "-" if kind == "bullet" else str(counter) + "."
        layout = self.list_layout.setdefault(identity, {})
        for other in list(layout):
            if other >= level:
                del layout[other]
        ancestor = layout[max(layout)] if layout else None
        indent = ancestor[0] + max(4, ancestor[1] + 1) if ancestor else 0
        layout[level] = (indent, len(marker))
        return marker, indent

    def math_text(self, node) -> str:
        kind = _local(node.tag)
        if kind == "t":
            return node.text or ""
        if kind.endswith("Pr"):
            return ""
        def part(name):
            child = _child(node, name)
            return self.math_text(child) if child is not None else ""
        if kind == "f":
            return "(" + part("num") + ")/(" + part("den") + ")"
        if kind in {"sSup", "sSub", "sSubSup"}:
            return (part("e") + ("_(" + part("sub") + ")" if kind != "sSup" else "")
                    + ("^(" + part("sup") + ")" if kind != "sSub" else ""))
        if kind == "rad":
            degree = part("deg")
            return ("root(" + degree + ", " if degree else "sqrt(") + part("e") + ")"
        if kind == "nary":
            props = _child(node, "naryPr")
            character = _child(props, "chr") if props is not None else None
            operator = _attr(character, "val", "∫") if character is not None else "∫"
            return operator + ("_(" + part("sub") + ")" if part("sub") else "") + ("^(" + part("sup") + ")" if part("sup") else "") + part("e")
        if kind == "func":
            return part("fName") + "(" + part("e") + ")"
        if kind == "m":
            return "[" + "; ".join(", ".join(self.math_text(cell) for cell in row if _local(cell.tag) == "e")
                                      for row in node if _local(row.tag) == "mr") + "]"
        if kind == "d":
            props = _child(node, "dPr")
            def mark(name, default):
                value = _child(props, name) if props is not None else None
                return _attr(value, "val", default) if value is not None else default
            return mark("begChr", "(") + mark("sepChr", "|").join(self.math_text(item) for item in node if _local(item.tag) == "e") + mark("endChr", ")")
        if kind in {"acc", "bar", "groupChr", "limLow", "limUpp"}:
            self.warn("部分 Word 公式结构只能提取符号片段，请对照原文件逐项核对。")
            return "{" + " ".join(filter(None, (self.math_text(item) for item in node))) + "}"
        return "".join(self.math_text(item) for item in node)

    def run_marks(self, node) -> dict[str, bool]:
        props = _child(node, "rPr")
        style = _child(props, "rStyle") if props is not None else None
        inherited = self.styles.get(_attr(style, "val"), {}) if style is not None else {}
        return {**self.default_run, **self.paragraph_run, **inherited.get("marks", {}), **_run_properties(props)}

    def run_fragment(self, node) -> tuple[str, dict[str, bool]]:
        props = _child(node, "rPr")
        marks = self.run_marks(node)
        text = self.inline_children(node, escape=not marks.get("code", False))
        vertical = _child(props, "vertAlign") if props is not None else None
        alignment = _attr(vertical, "val") if vertical is not None else ""
        if alignment in {"superscript", "subscript"} and text:
            self.warn("正文上下标已转换为 ^(...) 或 _(...) 线性标记，请核对原文件。")
            text = ("^" if alignment == "superscript" else "_") + "(" + text + ")"
        return text, marks

    def standalone_emphasis(self, node) -> str | None:
        # A narrow compatibility rule for DOCX exported from Markdown: an
        # otherwise plain paragraph whose entire visible content is one bold
        # wrapper. Mixed prose, styled runs and code keep their literal symbols.
        raw = []
        for child in node:
            if _local(child.tag) == "pPr":
                continue
            if _namespace(child.tag) not in WORD_NAMESPACES or _local(child.tag) != "r" or any(self.run_marks(child).values()):
                return None
            for item in child:
                if _local(item.tag) == "rPr":
                    continue
                if _namespace(item.tag) not in WORD_NAMESPACES or _local(item.tag) != "t":
                    return None
                raw.append(item.text or "")
        match = re.fullmatch(r"\s*(\*\*|__)(.+)\1\s*", "".join(raw))
        if match is None:
            return None
        wrapper, text = match.group(1), match.group(2).strip()
        if (not text or wrapper in text or chr(96) in text
                or text.startswith(wrapper[0]) or text.endswith(wrapper[0])):
            return None
        self.warn("已将独立纯文本段落中的成对粗体标记规范为 Markdown 粗体；一般正文中的符号仍按原文保留。")
        return "**" + _escape_text(text) + "**"

    def inline_children(self, node, escape: bool = True) -> str:
        output, pending, pending_marks = [], [], None
        def flush():
            if pending:
                output.append(_marked_text("".join(pending), pending_marks or {}))
                pending.clear()
        for item in node:
            if _namespace(item.tag) in WORD_NAMESPACES and _local(item.tag) == "r":
                text, marks = self.run_fragment(item)
                if marks != pending_marks:
                    flush(); pending_marks = marks
                pending.append(text)
            else:
                flush(); pending_marks = None
                output.append(self.inline(item, escape=escape))
        flush()
        return "".join(output)

    def inline(self, node, escape: bool = True) -> str:
        kind, namespace = _local(node.tag), _namespace(node.tag)
        if kind == "AlternateContent":
            selected = _child(node, "Choice")
            if selected is None:
                selected = _child(node, "Fallback")
            return self.inline(selected, escape=escape) if selected is not None else ""
        if namespace in MATH_NAMESPACES and kind in {"oMath", "oMathPara"}:
            self.warn("Word 公式已转为带“公式”标记的线性文本，未转换为可编辑公式或保真 LaTeX；请对照原文件核对。")
            return "[公式：" + self.math_text(node) + "]"
        if namespace not in WORD_NAMESPACES:
            return self.inline_children(node, escape=escape)
        if kind in {"del", "moveFrom", "delText"}:
            self.warn("已按修订后的当前视图导入；删除内容与修订记录仍保留在原文件中。")
            return ""
        if kind in {"ins", "moveTo"}:
            self.warn("已按修订后的当前视图导入；删除内容与修订记录仍保留在原文件中。")
        if kind in {"pPr", "rPr", "sectPr"}:
            return ""
        if kind == "t":
            return _escape_text(node.text or "") if escape else node.text or ""
        if kind == "hyperlink":
            text = self.inline_children(node, escape=escape)
            target = self.hyperlinks.get(_attr(node, "id"))
            if target and text.strip():
                return "[" + text + "](<" + target + ">)"
            if _attr(node, "id") or _attr(node, "anchor"):
                self.warn("部分内部或不安全的超链接仅保留显示文字。")
            return text
        if kind == "tab":
            return "\t"
        if kind in {"br", "cr"}:
            return "\n\n" if _attr(node, "type") == "page" else "\n"
        if kind == "lastRenderedPageBreak":
            return ""
        if kind in {"instrText", "fldChar", "fldSimple"}:
            self.warn("域只导入已有显示文本，未更新域或执行字段指令。")
            if kind != "fldSimple":
                return ""
        if kind in {"footnoteReference", "endnoteReference"}:
            note = "footnote" if kind == "footnoteReference" else "endnote"
            identity = _attr(node, "id")
            if not re.fullmatch(r"[0-9]{1,10}", identity):
                self.warn("部分脚注或尾注引用无效，未能还原其内容。")
                return "[注释引用缺失]"
            if identity not in self.note_ids[note]:
                self.note_ids[note].append(identity)
            return "[^" + ("fn" if note == "footnote" else "en") + identity + "]"
        if kind in {"drawing", "pict", "object"}:
            self.warn("图片与嵌入对象未执行、未做 OCR；仅提取其中已有的正文文字。")
        if kind in {"altChunk", "subDoc", "control"}:
            raise DocumentImportError("DOCX 含外部正文、子文档或活动控件，请先另存为普通 DOCX。")
        if kind == "sym":
            self.warn("特殊字体符号已标记，需对照原文件核对。")
            return "[字体符号：" + _attr(node, "char") + "]"
        if kind == "noBreakHyphen":
            return "‑"
        if kind == "softHyphen":
            return "\u00ad"
        if kind == "r":
            text, marks = self.run_fragment(node)
            return _marked_text(text, marks)
        return self.inline_children(node, escape=escape)

    def paragraph(self, node) -> str:
        props = _child(node, "pPr")
        level, identity, numbering = None, "", {}
        if props is not None:
            style = _child(props, "pStyle")
            if style is not None:
                identity = _attr(style, "val")
                level = self.heading_styles.get(identity) or _heading_number(identity)
            outline = _child(props, "outlineLvl")
            if outline is not None:
                level = int(_attr(outline, "val")) + 1 if _attr(outline, "val") in {str(i) for i in range(6)} else None
        inherited = self.styles.get(identity, {})
        numbering.update(inherited.get("number", {}))
        if not numbering and identity in self.numbering_styles:
            num_id, list_level = self.numbering_styles[identity]
            numbering.update({"numId": num_id, "ilvl": str(list_level)})
        numbering.update(_number_properties(props))
        previous = self.paragraph_run
        self.paragraph_run = inherited.get("marks", {})
        try:
            compatible = self.standalone_emphasis(node)
            text = compatible if compatible is not None else self.inline(node)
        finally:
            self.paragraph_run = previous
        if level and text.strip():
            return "#" * level + " " + text
        marker = self.list_marker(numbering) if numbering and text.strip() else None
        if marker:
            bullet, indent = marker
            continuation = " " * (indent + len(bullet) + 1)
            return " " * indent + bullet + " " + text.replace("\n", "\n" + continuation)
        return text

    def table(self, node) -> list[str]:
        rows, merged, headings = [], False, []
        for row in node:
            if _local(row.tag) != "tr":
                continue
            props = _child(row, "trPr")
            heading = _child(props, "tblHeader") if props is not None else None
            headings.append(heading is not None and _enabled(heading))
            if props is not None and any(_child(props, name) is not None and _attr(_child(props, name), "val", "0") != "0"
                                         for name in ["gridBefore", "gridAfter"]):
                merged = True
            cells = []
            for cell in row:
                if _local(cell.tag) != "tc":
                    continue
                cell_props = _child(cell, "tcPr")
                if cell_props is not None:
                    span = _child(cell_props, "gridSpan")
                    if ((span is not None and _attr(span, "val") != "1")
                            or _child(cell_props, "vMerge") is not None or _child(cell_props, "hMerge") is not None):
                        merged = True
                if any(_local(item.tag) == "tbl" for item in cell.iter()):
                    merged = True
                cells.append("\n\n".join(self.blocks(cell)).strip())
            rows.append(cells)
        if not rows or not any(cell.strip() for row in rows for cell in row):
            return []
        if merged or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
            self.warn("合并、嵌套或不规则表格已按行转为制表符分隔文本；原单元格结构与布局不保留。")
            return ["\t".join(cell.replace("\n", " ") for cell in row) for row in rows]
        self.warn("表格已转换为 Markdown 表格；列宽、边框和原单元格版式不保留。")
        if not headings[0]:
            self.warn("Markdown 表格使用原表首行作为表头，请核对其语义。")
        if any(headings[1:]):
            self.warn("Markdown 表格仅支持一行表头，其余原表头行保留为普通数据行。")
        def line(row):
            return "| " + " | ".join(cell.replace("|", "\\|").replace("\r\n", "\n").replace("\n", "<br>") for cell in row) + " |"
        return ["\n".join([line(rows[0]), "| " + " | ".join("---" for _ in rows[0]) + " |", *[line(row) for row in rows[1:]]])]

    def blocks(self, node) -> list[str]:
        result = []
        for item in node:
            kind = _local(item.tag)
            if kind == "p" and _namespace(item.tag) in WORD_NAMESPACES:
                result.append(self.paragraph(item))
            elif kind == "tbl":
                result.extend(self.table(item))
            elif kind in {"altChunk", "subDoc", "control"}:
                raise DocumentImportError("DOCX 含外部正文、子文档或活动控件，请先另存为普通 DOCX。")
            elif kind in {"del", "moveFrom"}:
                self.warn("已按修订后的当前视图导入；删除内容与修订记录仍保留在原文件中。")
            elif kind == "AlternateContent":
                selected = _child(item, "Choice")
                if selected is None:
                    selected = _child(item, "Fallback")
                if selected is not None:
                    result.extend(self.blocks(selected))
            elif kind not in {"sectPr", "tcPr", "trPr", "tblPr", "tblGrid"}:
                result.extend(self.blocks(item))
        return result

    def notes(self, kind: str, part: str | None) -> list[str]:
        identities = list(self.note_ids[kind])
        if not identities:
            return []
        self.warn("脚注与尾注已按引用标记追加到文本末尾；原注释版式不保留。")
        nodes = {}
        if part:
            root = self.read_xml(part)
            if _local(root.tag) != kind + "s" or _namespace(root.tag) not in WORD_NAMESPACES:
                raise DocumentImportError("DOCX 的脚注或尾注结构无效。")
            for item in root:
                if _local(item.tag) == kind:
                    nodes[_attr(item, "id")] = item
        result = []
        for identity in identities:
            node = nodes.get(identity)
            if node is None:
                self.warn("部分脚注或尾注引用找不到对应内容，请对照原文件。")
                text = "[注释内容缺失]"
            else:
                text = "\n\n".join(self.blocks(node)).strip() or "[空注释]"
            result.append("[^" + ("fn" if kind == "footnote" else "en") + identity + "]: "
                          + text.replace("\n", "\n    "))
        return result

    def extract(self):
        self.check_archive()
        main = self.main_part()
        document = self.read_xml(main)
        if _namespace(document.tag) not in WORD_NAMESPACES or _local(document.tag) != "document":
            raise DocumentImportError("DOCX 缺少有效的 Word 正文结构。")
        bodies = [item for item in document if _local(item.tag) == "body"]
        body = bodies[0] if len(bodies) == 1 else None
        if body is None or _namespace(body.tag) not in WORD_NAMESPACES:
            raise DocumentImportError("DOCX 缺少正文。")
        rel_path = posixpath.join(posixpath.dirname(main), "_rels", posixpath.basename(main) + ".rels")
        parts = {}
        for relation in self.relationships(rel_path):
            kind = relation.get("Type", "").rsplit("/", 1)[-1]
            if relation.get("TargetMode", "").lower() == "external":
                if kind == "hyperlink":
                    target = _safe_hyperlink(relation.get("Target", ""))
                    if target:
                        self.hyperlinks[relation.get("Id")] = target
                    else:
                        self.warn("部分内部或不安全的超链接仅保留显示文字。")
                continue
            if kind in {"styles", "numbering", "footnotes", "endnotes"}:
                if kind in parts:
                    raise DocumentImportError("DOCX 存在重复的正文部件关系。")
                parts[kind] = _resolve_part(main, relation.get("Target", ""))
            if kind in {"header", "footer"}:
                self.warn("页眉与页脚未并入正文文本，请对照原文件。")
            if kind == "comments":
                self.warn("Word 批注未并入正文，仍保留在原文件中。")
        self.load_styles(parts.get("styles"))
        self.load_numbering(parts.get("numbering"))
        content = "\n\n".join(self.blocks(body)).strip()
        if not content:
            raise DocumentImportError("DOCX 没有可提取的正文文字；仅含图片的文档暂不支持 OCR。")
        annotations = self.notes("footnote", parts.get("footnotes")) + self.notes("endnote", parts.get("endnotes"))
        if annotations:
            content += "\n\n" + "\n\n".join(annotations)
        return content, self.warnings


def parse_document(filename: str, data: bytes) -> dict:
    """Extract DOCX/TXT/Markdown without reading paths or following relationships."""
    if not isinstance(filename, str) or not filename.strip():
        raise DocumentImportError("请选择有文件名的 DOCX、TXT 或 Markdown 文件。")
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if (not name.strip() or len(name) > 255
            or any(ord(char) < 32 for char in name)):
        raise DocumentImportError("文件名无效或过长。")
    suffix = posixpath.splitext(name)[1].lower()
    if suffix not in {".docx", ".txt", ".md", ".markdown"}:
        raise DocumentImportError("只支持 DOCX、UTF-8 TXT 和 Markdown；当前不支持 PDF。")
    if not isinstance(data, bytes) or not data:
        raise DocumentImportError("文件为空，无法导入。")
    if len(data) > MAX_UPLOAD_BYTES:
        raise DocumentImportError("上传文件不能超过 10 MiB。")
    if suffix == ".docx":
        if not data.startswith(b"PK\x03\x04"):
            raise DocumentImportError("该文件不是标准 DOCX，或已加密；请另存为未加密的 DOCX。")
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                content, warnings = _DocxReader(archive).extract()
        except DocumentImportError:
            raise
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, RuntimeError,
                EOFError, NotImplementedError, OverflowError, RecursionError, ValueError) as error:
            raise DocumentImportError("DOCX 文件损坏、结构异常或使用了不支持的加密。") from error
        source_type = "docx"
    else:
        try:
            content = data.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as error:
            raise DocumentImportError("TXT 和 Markdown 必须使用 UTF-8 编码，请转为 UTF-8 后导入。") from error
        if any(ord(char) < 32 and char not in "\t\r\n" for char in content):
            raise DocumentImportError("文件含二进制或不支持的控制字符，不是有效的 UTF-8 文本。")
        warnings = [COPY_WARNING]
        source_type = "text" if suffix == ".txt" else "markdown"
    if not content.strip():
        raise DocumentImportError("文件没有可导入的正文文字。")
    if len(content) > MAX_CONTENT_CHARACTERS:
        raise DocumentImportError("导入正文不能超过 300000 个字符，请拆分文档后重试。")
    return {"name": name, "content": content, "document_type": "markdown",
            "source_type": source_type, "warnings": warnings}
