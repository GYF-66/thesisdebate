"""Local manuscript checks, source extraction and explicitly limited metadata checks.

Uploaded prose is data. Nothing in a manuscript, comment or material is executed
as an instruction, and none of these helpers changes manuscript content.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
from io import BytesIO
import json
import posixpath
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zipfile import ZipFile

MAX_BYTES = 10 * 1024 * 1024
MAX_TEXT = 300_000


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _heading_name(value: str) -> str:
    value = re.sub(r"^\s*#{1,6}\s*", "", value).strip()
    value = re.sub(r"^(?:第[一二三四五六七八九十百零〇0-9]+[章节篇]|[一二三四五六七八九十]+[、.．]|[0-9]+(?:[.．][0-9]+)*[.．、]?|[（(][一二三四五六七八九十0-9]+[）)])\s*", "", value)
    return re.sub(r"[\s:：*]+", "", value).lower()


def document_headings(content: str) -> list[dict]:
    result = []
    plain = {"摘要", "abstract", "引言", "绪论", "研究背景", "研究方法", "研究内容", "研究设计", "方法", "结论", "总结", "讨论", "结果", "参考文献", "references", "致谢", "进度安排"}
    for match in re.finditer(r"(?m)^.*$", content):
        line = match.group().strip()
        if not line or len(line) > 100:
            continue
        name = _heading_name(line)
        if re.match(r"^#{1,6}\s+|^第[一二三四五六七八九十0-9]+[章节]|^[一二三四五六七八九十]+[、．.]|^\d+(?:\.\d+)*[.、]?\s+", line) or name in plain:
            result.append({"title": name, "quote": match.group(), "start": match.start(), "end": match.end()})
    return result


def references_in(content: str) -> tuple[list[dict], str]:
    heading = next((item for item in document_headings(content) if item["title"] in {"参考文献", "references"}), None)
    if heading is None:
        return [], content
    start = heading["end"]
    tail = content[start:]
    following = next((item for item in document_headings(tail) if item["title"] in {"致谢", "附录", "appendix"} or re.match(r"^#{1,2}\s", item["quote"])), None)
    if following:
        tail = tail[:following["start"]]
    matches = list(re.finditer(r"(?m)^\s*\[(\d+)\]\s*(.+)$", tail))
    entries = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(tail)
        text = tail[match.start():end].strip()
        actual_start = start + match.start() + len(tail[match.start():end]) - len(tail[match.start():end].lstrip())
        entries.append({"number": int(match.group(1)), "text": text, "start": actual_start, "end": actual_start + len(text), "verification": {"format": "not_checked", "existence": "not_verified", "claim_support": "not_checked"}})
    return entries, content[:heading["start"]]


def split_mentor_comments(comments: str | list[str], content: str, source: str = "粘贴意见", source_quote: str = "") -> list[dict]:
    lines = comments if isinstance(comments, list) else re.split(r"[\r\n]+|[；;](?=\s*(?:\d+[.、）)]|[一二三四五六七八九十]+[、.]))", comments)
    result = []
    for value in lines:
        for line in re.split(r"[\r\n]+", value):
            text = re.sub(r"^\s*(?:[-*•]|\d+[.、）)]|[一二三四五六七八九十]+[、.])\s*", "", line).strip()
            if not text:
                continue
            quoted = re.search(r"[“\"]([^”\"]{2,200})[”\"]", text)
            candidate = source_quote or (quoted.group(1) if quoted else "")
            offset = content.find(candidate) if candidate else -1
            located = candidate if offset >= 0 and content.find(candidate, offset + len(candidate)) < 0 else ""
            result.append({"comment": text[:4000], "source": source, "source_quote": candidate, "quote": located, "start": offset if located else None, "end": offset + len(located) if located else None, "location_status": "located" if located else "needs_confirmation", "status": "pending", "reply": "", "version": 1})
            if len(result) >= 100:
                return result
    return result


def analyze_document(content: str, document_kind: str = "thesis", required_sections: list[str] | None = None, mentor_comments: str | list[str] = "") -> dict:
    findings = []
    digest = content_hash(content)
    headings = document_headings(content)

    def add(category, title, detail, text="", start=None, severity="warning", **extra):
        if len(findings) >= 150:
            return
        if start is None and text:
            found = content.find(text)
            start = found if found >= 0 else None
        quote_text = text if start is not None and content[start:start + len(text)] == text else ""
        findings.append({"id": "check-" + content_hash(f"{category}:{title}:{start}:{text}")[:16], "category": category, "title": title, "detail": detail, "quote": quote_text, "start": start if quote_text else None, "end": start + len(quote_text) if quote_text else None, "severity": severity, "requires_confirmation": True, **extra})

    from app.document_profiles import DEFAULT_SECTIONS
    defaults = {**DEFAULT_SECTIONS, "report": [], "other": []}
    required = defaults.get(document_kind, []) if required_sections is None else required_sections
    if not content.strip():
        add("structure", "文档正文为空", "请先导入或输入正文，再进行检查。", severity="error")
    for section in required:
        alternatives = [_heading_name(value) for value in section.split("/") if value.strip()]
        aliases = {"绪论": ["引言"], "研究方法": ["研究设计", "方法"], "结论": ["总结"]}
        alternatives += [alias for value in list(alternatives) for alias in aliases.get(value, [])]
        if alternatives and not any(any(value == heading["title"] or (len(value) >= 3 and value in heading["title"]) for value in alternatives) for heading in headings):
            add("structure", "未识别到章节：" + section, "仅按标题识别；若使用了其他标题请核对后调整必需章节。不会据此断言正文缺少相关内容。")

    counts = defaultdict(list)
    for match in re.finditer(r"(样本量|样本总量|有效问卷(?:数量)?|受试者人数|参与者人数)\s*(?:为|是|共|共有|[:：=])?\s*(\d{1,7})\s*(?:人|份|例|个)?", content):
        counts[match.group(1)].append(match)
    for label, matches in counts.items():
        if len({item.group(2) for item in matches}) > 1:
            values = "、".join(sorted({item.group(2) for item in matches}))
            for match in matches[:5]:
                add("consistency", label + "出现不同数值", f"同名指标出现 {values}。可能属于不同组别或时间点，请对照研究记录确认，系统不会修改数字。", match.group(), match.start())
    definitions = defaultdict(list)
    for match in re.finditer(r"([\u4e00-\u9fff]{2,20})[（(]([A-Z][A-Z0-9-]{1,10})[）)]", content):
        definitions[match.group(2)].append(match)
    for acronym, matches in definitions.items():
        if len({match.group(1) for match in matches}) > 1:
            for match in matches[:4]:
                add("consistency", acronym + "的中文定义可能不一致", "识别到同一缩写对应多个中文表达，请核对是否为同一术语；本检查为候选提醒。", match.group(), match.start())

    entries, body = references_in(content)
    by_number = defaultdict(list)
    by_text = defaultdict(list)
    for entry in entries:
        by_number[entry["number"]].append(entry)
        normalized = re.sub(r"[^\w\u4e00-\u9fff]", "", re.sub(r"^\[\d+\]", "", entry["text"]).lower())
        if normalized:
            by_text[normalized].append(entry)
    for number, duplicates in by_number.items():
        if len(duplicates) > 1:
            add("reference", f"参考文献编号 [{number}] 重复", "多个条目使用同一编号，请确认后重新编号并同步正文引用。", duplicates[1]["text"], duplicates[1]["start"], "error")
    for duplicates in by_text.values():
        if len(duplicates) > 1:
            add("reference", "疑似重复参考文献", "去除编号、空白和标点后条目相同，请核对后合并。", duplicates[1]["text"], duplicates[1]["start"])
    cited = set()
    citation_pattern = r"\[(\d+(?:\s*(?:[,，、–—-]\s*)\d+)*)\](?!\()"
    for match in re.finditer(citation_pattern, body):
        numbers = []
        for token in re.split(r"[,，、]", match.group(1)):
            bounds = re.split(r"[-–—]", token)
            if len(bounds) == 2:
                low, high = map(int, bounds)
                if 0 <= high - low <= 100:
                    numbers.extend(range(low, high + 1))
            else:
                numbers.append(int(token))
        cited.update(numbers)
        missing = sorted(set(numbers) - set(by_number))
        if missing:
            add("reference", "正文引用未找到对应条目", "编号 " + "、".join(map(str, missing)) + " 未出现在已识别的文后列表。当前支持数字编号制；其他引用制需人工核对。", match.group(), match.start(), "error")
    for entry in entries:
        if entry["number"] not in cited:
            add("reference", f"文献 [{entry['number']}] 未识别到正文引用", "可能是漏引、未引用或采用了其他引用制；请人工确认，不会自动删除条目。", entry["text"], entry["start"], "info")
    tasks = split_mentor_comments(mentor_comments, content)
    checklist = [
        {"id": "document", "title": "正文可读取", "status": "pass" if content.strip() else "attention", "detail": f"已读取 {len(content)} 个字符。"},
        {"id": "checks", "title": "检查提示已核对", "status": "attention" if findings else "pass", "detail": f"有 {len(findings)} 条确定性检查提示；未覆盖的问题仍需人工审阅。"},
        {"id": "requirements", "title": "对照提交规范与导出文件", "status": "manual", "detail": "人工确认文件命名、版式、图片、公式、页码和导出结果符合接收方要求。"},
    ]
    if document_kind == "thesis":
        checklist.append({"id": "references", "title": "引用证据已核对", "status": "manual", "detail": "编号匹配、文献元数据存在、原文支持论点属于三个不同核验层次；本次未联网核验真实性。"})
    questions = []
    for heading in headings:
        if re.search(r"方法|设计|实验|结果|结论|局限|总结", heading["title"]):
            questions.append({"question": f"《{heading['title']}》中的关键依据是什么？有哪些适用条件或局限？", "quote": heading["quote"], "reason": "依据当前文档的实际章节生成的准备问题，未推断研究事实。"})
    for finding in findings[:3]:
        questions.append({"question": f"如何解释或处理“{finding['title']}”？", "quote": finding["quote"], "reason": "来自当前尚待人工核对的检查提示。"})
    return {"content_hash": digest, "document_kind": document_kind, "required_sections": required, "findings": findings, "mentor_tasks": tasks, "submission_checklist": checklist, "defense_questions": questions[:8], "references": entries[:200], "method": "deterministic_local_checks", "limitations": ["检查仅提供可核对线索，不保证论文合格、查重率或 AI 检测结果。", "数字引用检查支持 [1]、[1,2] 和 [1-3]；文献存在性与论点支持需要单独核验。"]}


def extract_comments(data: bytes, content: str, filename: str) -> list[dict]:
    from app.document_import import _DocxReader, _local, _attr, _resolve_part
    if len(data) > MAX_BYTES:
        raise ValueError("批注文件不能超过 10 MB。")
    try:
        with ZipFile(BytesIO(data)) as archive:
            reader = _DocxReader(archive)
            reader.check_archive()
            main = reader.main_part()
            document = reader.read_xml(main)
            rel_path = posixpath.join(posixpath.dirname(main), "_rels", posixpath.basename(main) + ".rels")
            parts = [_resolve_part(main, relation.get("Target", "")) for relation in reader.relationships(rel_path) if relation.get("Type", "").rsplit("/", 1)[-1] == "comments" and relation.get("TargetMode", "").lower() != "external"]
            if not parts:
                raise ValueError("DOCX 中没有可导入的 Word 批注，可改用粘贴导师意见。")
            active = set()
            anchors = defaultdict(list)
            for node in document.iter():
                kind = _local(node.tag)
                identity = _attr(node, "id")
                if kind == "commentRangeStart":
                    active.add(identity)
                elif kind == "commentRangeEnd":
                    active.discard(identity)
                elif kind == "t" and node.text:
                    for item in active:
                        anchors[item].append(node.text)
            tasks = []
            for part in parts:
                for comment in reader.read_xml(part):
                    if _local(comment.tag) != "comment":
                        continue
                    text = "\n".join("".join(node.text or "" for node in paragraph.iter() if _local(node.tag) == "t") for paragraph in comment if _local(paragraph.tag) == "p")
                    identity = _attr(comment, "id")
                    quote_text = "".join(anchors.get(identity, []))
                    tasks.extend(split_mentor_comments(text, content, filename + " · 批注 " + identity, quote_text))
                    if len(tasks) >= 100:
                        break
            if not tasks:
                raise ValueError("DOCX 批注中没有可读取的文字。")
            return tasks[:100]
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("无法读取 DOCX 批注，请确认文件未加密且结构完整。") from error


def extract_material(filename: str, data: bytes) -> dict:
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if not name or len(name) > 255 or any(ord(char) < 32 for char in name):
        raise ValueError("资料文件名无效。")
    if not data or len(data) > MAX_BYTES:
        raise ValueError("资料文件不能为空且不能超过 10 MB。")
    suffix = posixpath.splitext(name)[1].lower()
    segments, warnings = [], []
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as error:
            raise ValueError("当前服务未安装 PDF 读取组件 pypdf，请安装依赖后重试。") from error
        try:
            reader = PdfReader(BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise ValueError("不支持加密 PDF，请先提供未加密副本。")
            if len(reader.pages) > 150:
                raise ValueError("PDF 不能超过 150 页，请拆分资料。")
            total = 0
            for page_number, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                total += len(text)
                if total > MAX_TEXT:
                    raise ValueError("资料提取文字超过 300000 字符，请拆分后上传。")
                for paragraph, part in enumerate(re.split(r"\n\s*\n", text), 1):
                    if part.strip():
                        segments.append({"text": part.strip(), "page": page_number, "paragraph": paragraph})
            warnings.append("页码为所上传 PDF 的物理页序；提取顺序可能与版面不同，引用前请核对原页。")
        except ValueError:
            raise
        except Exception as error:
            raise ValueError("PDF 无法读取或结构损坏；扫描件暂不支持 OCR。") from error
    elif suffix in {".txt", ".md", ".markdown", ".docx"}:
        from app.document_import import parse_document
        parsed = parse_document(name, data)
        warnings.extend(parsed["warnings"])
        segments = [{"text": value.strip(), "page": None, "paragraph": index} for index, value in enumerate(re.split(r"\n\s*\n|\r?\n", parsed["content"]), 1) if value.strip()]
        if suffix == ".docx":
            warnings.append("DOCX 段号为提取文本段序，不代表 Word 页码。")
    else:
        raise ValueError("资料支持 UTF-8 TXT、Markdown、PDF 和 DOCX。")
    if not segments:
        raise ValueError("资料没有可提取文字；扫描件暂不支持 OCR。")
    return {"name": name, "kind": suffix.lstrip("."), "file_hash": hashlib.sha256(data).hexdigest(), "characters": sum(len(part["text"]) for part in segments), "segments": segments, "warnings": warnings}


def retrieve_evidence(question: str, materials: list[dict]) -> dict:
    question = question.strip()
    tokens = set(re.findall(r"[a-z0-9]{2,}", question.lower()))
    for phrase in re.findall(r"[\u4e00-\u9fff]+", question):
        tokens.update(phrase[index:index + 2] for index in range(len(phrase) - 1))
    stop = {"什么", "如何", "哪些", "是否", "资料", "论文", "请问", "中的", "一下", "关于"}
    tokens -= stop
    scored = []
    for material in materials:
        for segment in material.get("segments", []):
            lowered = segment["text"].lower()
            hits = [token for token in tokens if token in lowered]
            if not hits:
                continue
            score = len(hits) / max(1, len(tokens))
            first = min(lowered.find(token) for token in hits)
            start = max(0, first - 100)
            excerpt = segment["text"][start:start + 600]
            scored.append((score, {"material_id": material["id"], "source_name": material["name"], "page": segment.get("page"), "paragraph": segment["paragraph"], "quote": excerpt, "matched_terms": sorted(hits)}))
    scored.sort(key=lambda item: (-item[0], item[1]["source_name"], item[1]["paragraph"]))
    citations = [item[1] for item in scored[:5]]
    return {"mode": "deterministic_retrieval", "question": question, "answer": "已按关键词检索到以下原文摘录，请打开对应资料核对；摘录不等于对论点的自动证明。" if citations else "已上传资料中未检索到匹配摘录，这不代表资料中一定不存在答案。请换用更具体的术语或上传相关材料。", "citations": citations, "model_used": False}


def _fetch_json(url: str) -> dict:
    request = Request(url, headers={"User-Agent": "ThesisDebate/0.2 metadata-check", "Accept": "application/json"})
    with urlopen(request, timeout=5) as response:
        data = response.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValueError("元数据响应过大")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("元数据响应无效")
    return value


def _normalized_title(title: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", title.lower())


def verify_reference(reference: str) -> dict:
    """User-triggered metadata lookup; existence never implies claim support."""
    doi_match = re.search(r"10\.\d{4,9}/[^\s<>\"]+", reference, re.I)
    doi = doi_match.group().rstrip(".,;，。；]") if doi_match else None
    if doi:
        for opening, closing in (("(", ")"), ("（", "）")):
            while doi.endswith(closing) and doi.count(closing) > doi.count(opening):
                doi = doi[:-1]
    title = re.sub(r"^\s*\[\d+\]\s*", "", reference).strip()
    base = {"reference": reference, "doi": doi, "checked_at": now_iso(), "format_status": "not_checked", "existence_status": "not_verified", "claim_support_status": "not_checked", "status": "unverified", "candidates": [], "sources": []}
    errors = []
    try:
        url = "https://api.crossref.org/works/" + quote(doi, safe="") if doi else "https://api.crossref.org/works?" + urlencode({"query.bibliographic": title, "rows": 3})
        data = _fetch_json(url).get("message", {})
        if not isinstance(data, dict):
            raise ValueError("元数据消息格式无效")
        items = [data] if doi else data.get("items", [])
        for item in items[:3]:
            if not isinstance(item, dict):
                continue
            names = item.get("title", [])
            candidate_title = names[0] if isinstance(names, list) and names else (names if isinstance(names, str) else "")
            if not isinstance(candidate_title, str):
                continue
            item_doi = str(item.get("DOI", ""))
            normalized = _normalized_title(candidate_title)
            query_title = _normalized_title(title)
            matched = bool(doi and item_doi.casefold() == doi.casefold()) or (not doi and len(normalized) >= 8 and (normalized in query_title or SequenceMatcher(None, query_title, normalized).ratio() >= .85))
            base["candidates"].append({"title": candidate_title, "doi": item_doi, "source": "Crossref", "url": "https://doi.org/" + item_doi if item_doi else None, "matched": matched})
        base["sources"].append("Crossref")
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, TypeError, KeyError) as error:
        errors.append("Crossref 查询未完成（" + type(error).__name__ + "）。")
    if doi and not any(item["matched"] for item in base["candidates"]):
        try:
            data = _fetch_json("https://api.openalex.org/works/https://doi.org/" + quote(doi, safe=""))
            item_doi = str(data.get("doi") or "").removeprefix("https://doi.org/")
            base["candidates"].append({"title": str(data.get("display_name") or ""), "doi": item_doi, "source": "OpenAlex", "url": "https://doi.org/" + item_doi if item_doi else None, "matched": item_doi.casefold() == doi.casefold()})
            base["sources"].append("OpenAlex")
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, TypeError, KeyError) as error:
            errors.append("OpenAlex 查询未完成（" + type(error).__name__ + "）。")
    if any(item["matched"] for item in base["candidates"]):
        base.update(status="metadata_matched", existence_status="metadata_found", message="已找到匹配的文献元数据；尚未核验作者、年份等全部字段，也未读取原文判断是否支持论点。")
    elif base["candidates"]:
        base.update(status="candidates_only", message="仅检索到候选文献，未确认与所选条目匹配。未匹配不等于文献虚构。")
    else:
        base["message"] = "本次未能核验文献元数据。未查到或网络失败不代表文献虚构。"
    base["warnings"] = errors
    return base
