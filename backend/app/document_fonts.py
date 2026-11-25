"""One read-only font catalogue for import, natural language, preview and PDF.

Requested names are never rewritten through Windows FontSubstitutes. A missing
school font remains a valid requirement, and substitution needs an export choice.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from hashlib import sha256
import os
from pathlib import Path
import re
import struct
from threading import RLock
import unicodedata

from fastapi import APIRouter, Query

COMMON_FONTS = ("宋体", "黑体", "仿宋", "楷体", "微软雅黑", "Times New Roman", "Arial", "仿宋_GB2312", "楷体_GB2312")
COMMON_ALIASES = {
    "宋体": ("SimSun",), "黑体": ("SimHei",), "仿宋": ("FangSong",),
    "楷体": ("KaiTi",), "微软雅黑": ("Microsoft YaHei",),
    "Times New Roman": (), "Arial": (),
    "仿宋_GB2312": ("FangSong_GB2312", "仿宋 GB2312", "FangSong GB2312"),
    "楷体_GB2312": ("KaiTi_GB2312", "楷体 GB2312", "KaiTi GB2312"),
}
KNOWN_NAMES = {
    "华文仿宋": ("STFangsong",), "华文楷体": ("STKaiti",),
    "华文宋体": ("STSong",), "新宋体": ("NSimSun",),
    "等线": ("DengXian",), "思源黑体": ("Source Han Sans SC",),
    "思源宋体": ("Source Han Serif SC",),
}
ALL_ALIASES = {**COMMON_ALIASES, **KNOWN_NAMES}


def font_key(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).strip().split()).casefold()


def validate_font_name(name: str) -> str:
    if not isinstance(name, str) or not name.strip() or len(name) > 128:
        raise ValueError("字体名称必须为1–128个字符。")
    if name != name.strip() or any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF or ord(char) in {0xFFFE, 0xFFFF} or char in ';{}<>\\/\"' for char in name):
        raise ValueError("字体名称包含不支持的字符。")
    return name


@dataclass(frozen=True)
class FontFace:
    family: str
    aliases: tuple[str, ...]
    path: str
    index: int
    style: str
    coverage: frozenset[int] = field(repr=False)
    pdf_reason: str | None = None
    regular: bool = True

    @property
    def identity(self):
        return sha256((self.path + "#" + str(self.index)).encode()).hexdigest()[:24]


def _font_sources() -> tuple[list[Path], dict[str, str]]:
    files: set[Path] = set()
    substitutes = {"FangSong_GB2312": "FangSong", "KaiTi_GB2312": "KaiTi"}
    roots = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
    if os.environ.get("LOCALAPPDATA"):
        roots.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft/Windows/Fonts")
    if os.name == "nt":
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts") as key:
                    for index in range(winreg.QueryInfoKey(key)[1]):
                        _, value, _ = winreg.EnumValue(key, index)
                        if not isinstance(value, str):
                            continue
                        path = Path(os.path.expandvars(value))
                        if path.is_absolute():
                            files.add(path)
                        else:
                            for root in roots:
                                if (root / path).is_file():
                                    files.add(root / path)
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\FontSubstitutes") as key:
                    for index in range(winreg.QueryInfoKey(key)[1]):
                        name, value, _ = winreg.EnumValue(key, index)
                        if isinstance(value, str):
                            substitutes[name.split(",")[0]] = value.split(",")[0]
            except OSError:
                pass
    else:
        roots.extend([Path("/usr/share/fonts"), Path.home() / ".local/share/fonts", Path.home() / ".fonts"])
    for root in roots:
        if root.is_dir():
            for path in root.rglob("*"):
                if path.suffix.lower() in {".ttf", ".ttc", ".otf", ".otc"} and path.is_file():
                    files.add(path)
    return sorted(files, key=lambda path: str(path).casefold()), substitutes


def _names(font, identities) -> tuple[str, ...]:
    values = []
    for record in font["name"].names:
        if record.nameID in identities:
            try:
                value = record.toUnicode().strip()
                validate_font_name(value)
                if value not in values:
                    values.append(value)
            except (UnicodeError, ValueError):
                pass
    return tuple(values)


def _read_faces(path: Path) -> list[FontFace]:
    from fontTools.ttLib import TTFont
    with path.open("rb") as handle:
        header = handle.read(12)
    if header[:4] not in {b'\x00\x01\x00\x00', b'OTTO', b'ttcf', b'true'}:
        return []
    count = struct.unpack(">I", header[8:12])[0] if header[:4] == b"ttcf" and len(header) == 12 else 1
    if not 1 <= count <= 128:
        return []
    result = []
    for index in range(count):
        try:
            with path.open('rb') as handle, TTFont(handle, fontNumber=index, lazy=True) as font:
                names = _names(font, {1, 16})
                if not names:
                    continue
                aliases = tuple(dict.fromkeys(names + _names(font, {4, 6})))
                family = next((common for common, spellings in ALL_ALIASES.items() if any(font_key(name) in {font_key(common), *(font_key(x) for x in spellings)} for name in names)), None)
                family = family or next((name for name in names if any('\u3400' <= c <= '\u9fff' for c in name)), names[0])
                styles = _names(font, {2, 17})
                style = next(iter(styles), "Regular")
                coverage = frozenset(code for code, glyph in (font.getBestCmap() or {}).items() if glyph != '.notdef')
                rights = int(getattr(font.get("OS/2"), "fsType", 0))
                reason = ("字体许可位不允许当前PDF嵌入/子集方式" if rights & 0x0302 else
                          "可变字体暂不支持当前PDF嵌入方式" if "fvar" in font else
                          "当前PDF引擎不支持此字体轮廓格式" if "glyf" not in font else None)
                regular = not any(word in style.casefold() for word in ("bold", "italic", "oblique", "粗", "斜"))
                result.append(FontFace(family, aliases, str(path), index, style, coverage, reason, regular))
        except Exception:
            # One unsupported face must not hide the rest of the installed library.
            continue
    return result


class FontCatalogue:
    def __init__(self, faces: list[FontFace], substitutes: dict[str, str] | None = None, warnings: list[str] | None = None):
        self.faces = faces
        self.substitutes = {font_key(key): value for key, value in (substitutes or {}).items()}
        self.warnings = warnings or []
        self.groups: dict[str, list[FontFace]] = {}
        self.aliases: dict[str, str] = {}
        for face in faces:
            self.groups.setdefault(face.family, []).append(face)
            for name in (face.family, *face.aliases):
                self.aliases[font_key(name)] = face.family
        for family, names in ALL_ALIASES.items():
            # Declared aliases are identities, never substitutes.
            for name in (family, *names):
                self.aliases.setdefault(font_key(name), family)

    def canonical(self, name: str) -> str:
        return self.aliases.get(font_key(name), name)

    def face(self, name: str, text: str = "") -> FontFace | None:
        choices = self.groups.get(self.canonical(name), [])
        required = {ord(char) for char in text if not char.isspace() and ord(char) >= 32}
        return next(iter(sorted(choices, key=lambda face: (bool(required - face.coverage), bool(face.pdf_reason), not face.regular, face.index))), None)

    def substitute(self, name: str) -> str | None:
        for alias in (name, *ALL_ALIASES.get(self.canonical(name), ())):
            substitute = self.substitutes.get(font_key(alias))
            if substitute and self.face(substitute):
                return self.canonical(substitute)
        return None

    def item(self, name: str) -> dict:
        canonical = self.canonical(name)
        face = self.face(canonical)
        substitute = None if face else self.substitute(canonical)
        aliases = tuple(dict.fromkeys((*ALL_ALIASES.get(canonical, ()), *(alias for candidate in self.groups.get(canonical, []) for alias in candidate.aliases))))
        return {
            "id": face.identity if face else "missing-" + sha256(font_key(canonical).encode()).hexdigest()[:20],
            "name": canonical, "aliases": list(aliases), "common": canonical in COMMON_FONTS,
            "installed": face is not None, "status": "installed" if face else "substituted" if substitute else "missing",
            "substitute": substitute, "preview_available": face is not None,
            "pdf_available": bool(face and not face.pdf_reason),
            "pdf_reason": face.pdf_reason if face else "缺少精确字体文件；系统替代不等于已安装" if substitute else "本机未安装该字体",
            "has_cjk": bool(face and {ord(char) for char in "中文论文"} <= face.coverage),
            "styles": list(dict.fromkeys(candidate.style for candidate in self.groups.get(canonical, []))),
        }

    def entries(self) -> list[dict]:
        names = list(dict.fromkeys((*COMMON_FONTS, *sorted(self.groups, key=str.casefold))))
        entries: dict[str, dict] = {}
        for name in names:
            item = self.item(name)
            # Different source families may resolve through metadata aliases to
            # the same display family. Deduplicate the public identity last.
            entries.setdefault(font_key(item['name']), item)
        return list(entries.values())


_CATALOGUE_LOCK = RLock()


@lru_cache(maxsize=1)
def get_font_catalogue() -> FontCatalogue:
    with _CATALOGUE_LOCK:
        sources, substitutes = _font_sources()
        try:
            import fontTools.ttLib  # noqa: F401
        except ImportError:
            return FontCatalogue([], substitutes, ["字体目录依赖fontTools不可用；字体要求仍会保留，PDF导出将阻止不确定替代。"])
        faces = [face for path in sources for face in _read_faces(path)]
        return FontCatalogue(faces, substitutes)


@lru_cache(maxsize=4)
def _name_pattern(catalogue: FontCatalogue):
    spellings = {name: family for family, aliases in ALL_ALIASES.items() for name in (family, *aliases)}
    spellings.update({alias: family for family, faces in catalogue.groups.items() for face in faces for alias in (family, *face.aliases)})
    pattern = re.compile('|'.join(re.escape(name) for name in sorted(spellings, key=len, reverse=True)), re.I)
    return pattern, {font_key(name): family for name, family in spellings.items()}


_KNOWN_CATALOGUE = FontCatalogue([])


def match_font_name(text: str, *, installed: bool = True) -> str | None:
    """Match complete longest names; never take 仿宋 out of 华文仿宋/仿宋_GB2312."""
    pattern, spellings = _name_pattern(get_font_catalogue() if installed else _KNOWN_CATALOGUE)
    for match in pattern.finditer(text):
        before = text[match.start() - 1] if match.start() else ""
        after = text[match.end():match.end() + 1]
        if before and before.isascii() and (before.isalnum() or before in "_-"):
            continue
        if before and '\u3400' <= before <= '\u9fff' and not re.search(r'(?:正文|全文|全篇|标题|字体|[为用成])$', text[:match.start()]):
            continue
        if after and (after.isascii() and (after.isalnum() or after in "_-")):
            continue
        if after and '\u3400' <= after <= '\u9fff' and not re.match(r'(?:小[初一二三四五]号?|[初一二三四五六]号|字体|加粗|斜体|字号|正文|标题|行距|段前|段后)', text[match.end():]):
            continue
        return spellings[font_key(match[0])]
    # Preserve a named unknown requirement instead of silently ignoring it.
    named = re.search(r'(?:字体|font(?:\s+family)?)\s*(?:改为|设为|调整为|为|用|[:：=])\s*[“\']?([^，,；;。\n”\']{1,128})', text, re.I)
    if named:
        candidate = re.split(r'\s*(?:小[初一二三四五]号?|[初一二三四五六]号|字号|[0-9.]+\s*(?:pt|磅))', named[1], maxsplit=1)[0].strip()
        try:
            return validate_font_name(candidate)
        except ValueError:
            pass
    return None


router = APIRouter(tags=["document fonts"])


@router.get("/document-fonts")
def document_fonts(q: str = Query(default="", max_length=128), limit: int = Query(default=500, ge=1, le=1000)):
    catalogue = get_font_catalogue()
    entries = catalogue.entries()
    query = font_key(q)
    if query:
        entries = [item for item in entries if any(query in font_key(name) for name in (item["name"], *item["aliases"]))]
        if not entries:
            try:
                entries = [catalogue.item(validate_font_name(q))]
            except ValueError:
                entries = []
    return {"fonts": entries[:limit], "total": len(entries), "warnings": catalogue.warnings, "common_names": list(COMMON_FONTS)}
