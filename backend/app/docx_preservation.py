"""Retain the original OPC package and fail closed on unsafe paragraph edits.

This is deliberately a bounded writer, not a Markdown-to-Word round trip. Parts
other than the main document are copied byte for byte. Unsupported changes must
be explicitly exported as an editable copy, never silently flattened.
"""
from __future__ import annotations

import base64
from bisect import bisect_right
import hashlib
from io import BytesIO
import json
import re
from zipfile import ZipFile

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from lxml import etree

from app.document_import import _DocxReader, _local, _namespace, WORD_NAMESPACES, parse_document

NAMESPACE = "original_docx"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(store, document_id: str) -> dict | None:
    with store._lock:
        row = store._connection.execute(
            "SELECT payload FROM records WHERE namespace=? AND record_id=?", (NAMESPACE, document_id),
        ).fetchone()
    return json.loads(row["payload"]) if row else None


def _mapping(data: bytes) -> tuple[str, list[dict], dict]:
    captures, roots = [], {}
    with ZipFile(BytesIO(data)) as archive:
        reader = _DocxReader(archive)
        original_read, original_paragraph = reader.read_xml, reader.paragraph

        def read(name):
            node = original_read(name)
            roots[name] = node
            return node

        def paragraph(node):
            result = original_paragraph(node)
            captures.append((node, result))
            return result

        reader.read_xml, reader.paragraph = read, paragraph
        content, _ = reader.extract()
        line_positions, cursor = {}, 0
        for line in content.splitlines(keepends=True):
            line_positions.setdefault(line.rstrip("\r\n"), []).append(cursor)
            cursor += len(line)
        main_part = reader.main_part()
        document = roots[main_part]
        paragraphs = [node for node in document.iter() if _local(node.tag) == "p" and _namespace(node.tag) in WORD_NAMESPACES]
        indices = {id(node): index for index, node in enumerate(paragraphs)}
        body = next(node for node in document if _local(node.tag) == "body")
        eligible, field_depth, comment_depth = set(), 0, 0
        for node in body:
            protected = field_depth > 0 or comment_depth > 0
            for item in node.iter():
                kind = _local(item.tag)
                if kind in {"fldChar", "fldSimple", "commentRangeStart", "commentRangeEnd"}:
                    protected = True
                if kind == "fldChar":
                    marker = item.get("{" + W + "}fldCharType")
                    field_depth = field_depth + 1 if marker == "begin" else max(0, field_depth - 1) if marker == "end" else field_depth
                if kind == "commentRangeStart":
                    comment_depth += 1
                if kind == "commentRangeEnd":
                    comment_depth = max(0, comment_depth - 1)
            if _local(node.tag) == "p" and not protected:
                eligible.add(id(node))
        spans = []
        for node, rendered in captures:
            ordinal = indices.get(id(node))
            positions = line_positions.get(rendered, [])
            if ordinal is None or id(node) not in eligible or not rendered or len(positions) != 1:
                continue
            # Protected descendants/anchors and non-text runs are never rewritten.
            safe = all(_local(child.tag) in {"pPr", "r"} for child in node)
            safe = safe and all(all(_local(child.tag) in {"rPr", "t"} for child in run)
                                for run in node if _local(run.tag) == "r")
            raw = "".join(item.text or "" for item in node.iter() if item.tag == "{" + W + "}t")
            prefix = re.match(r"^#{1,6} ", rendered)
            offset = len(prefix.group()) if prefix else 0
            if not safe or rendered[offset:] != raw or not raw:
                continue
            start = positions[0] + offset
            spans.append({"paragraph": ordinal, "start": start, "end": start + len(raw), "text": raw})
        counts = {}
        for key, tags in {
            "images": {"drawing", "pict"}, "formulas": {"oMath"}, "fields": {"fldSimple", "fldChar"},
            "comments": {"commentRangeStart"}, "revisions": {"ins", "del", "moveFrom", "moveTo"},
            "sections": {"sectPr"}, "tables": {"tbl"}, "footnotes": {"footnoteReference", "endnoteReference"},
        }.items():
            counts[key] = sum(_local(node.tag) in tags for node in document.iter())
        counts["parts"] = len(archive.infolist())
    return main_part, spans, counts


def prepare_original(filename: str, data: bytes, content: str) -> dict:
    main_part, spans, objects = _mapping(data)
    value = {"filename": filename.replace("\\", "/").rsplit("/", 1)[-1], "sha256": _hash(data),
             "data": base64.b64encode(data).decode("ascii"), "content": content,
             "main_part": main_part, "paragraphs": spans, "objects": objects,
             "mode": "protected_paragraph_backwrite"}
    return value


def retain_original(store, document_id: str, filename: str, data: bytes, content: str) -> dict:
    value = prepare_original(filename, data, content)
    with store._transaction() as connection:
        connection.execute(
            "INSERT INTO records(namespace,record_id,payload,updated_at) VALUES (?,?,?,datetime('now')) "
            "ON CONFLICT(namespace,record_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
            (NAMESPACE, document_id, json.dumps(value, ensure_ascii=False)),
        )
    return compatibility(store, document_id)


def compatibility(store, document_id: str) -> dict:
    value = _read(store, document_id)
    if not value:
        return {"retained": False, "mode": "editable_copy", "warnings": ["当前文档没有留存原始 DOCX 包；DOCX 导出为可编辑副本。"]}
    return {"retained": True, "filename": value["filename"], "sha256": value["sha256"],
            "objects": value["objects"], "mapped_paragraphs": len(value["paragraphs"]), "mode": value["mode"],
            "warnings": ["结构保护导出只回写可唯一映射的普通文字段落，保留原 Word 版式与其他部件。",
                         "含公式、批注、修订、域、图片或复杂样式的段落，以及增删段落/表格等结构修改会被阻止。",
                         "结构保护模式不应用右侧预览样式；最终分页、目录域和打印效果仍需在 Word 中检查。"]}


def protected_export(store, document_id: str, content: str) -> tuple[bytes, list[str]] | None:
    value = _read(store, document_id)
    if not value:
        return None
    original = base64.b64decode(value["data"], validate=True)
    if _hash(original) != value["sha256"]:
        raise ValueError("原 DOCX 校验失败，已停止回写；请重新导入原文件。")
    if content == value["content"]:
        return original, ["正文未变化，已逐字节返回留存原 DOCX；未应用预览样式。"]
    before_lines, after_lines = value["content"].splitlines(keepends=True), content.splitlines(keepends=True)
    if len(before_lines) != len(after_lines):
        raise ValueError("修改增删了正文行或段落，不能安全回写原 DOCX；请使用可编辑副本并检查对象兼容性。")
    edits, cursor = {}, 0
    spans = sorted(value["paragraphs"], key=lambda span: span["start"])
    starts = [span["start"] for span in spans]
    for before, after in zip(before_lines, after_lines):
        offset = cursor
        cursor += len(before)
        if before == after:
            continue
        # Linear bounded diff for one original line; no quadratic fulltext diff.
        left = 0
        while left < min(len(before), len(after)) and before[left] == after[left]:
            left += 1
        right = 0
        while right < min(len(before), len(after)) - left and before[-right - 1] == after[-right - 1]:
            right += 1
        a0, a1 = offset + left, offset + len(before) - right
        index = bisect_right(starts, a0) - 1
        matches = [spans[index]] if index >= 0 and a1 <= spans[index]["end"] else []
        replacement = after[left:len(after) - right if right else len(after)]
        if len(matches) != 1 or any(char in replacement for char in "\r\n*_[]" + chr(96)):
            raise ValueError("修改涉及受保护对象、重复定位或文档结构，不能安全回写原 DOCX。原文件已保留；请局部调整，或明确选择“可编辑副本”导出。")
        span = matches[0]
        edits.setdefault(span["paragraph"], []).append((a0 - span["start"], a1 - span["start"], replacement))
    with ZipFile(BytesIO(original)) as archive:
        xml = archive.read(value["main_part"])
        root = etree.fromstring(xml, etree.XMLParser(resolve_entities=False, no_network=True))
        paragraphs = [node for node in root.iter() if _local(node.tag) == "p" and _namespace(node.tag) in WORD_NAMESPACES]
        for ordinal, changes in edits.items():
            paragraph = paragraphs[ordinal]
            texts = list(paragraph.iter("{" + W + "}t"))
            # Apply each edit to its original runs, preserving unchanged run formatting.
            for start, end, replacement in sorted(changes, reverse=True):
                cursor, touched = 0, []
                for node in texts:
                    length = len(node.text or "")
                    if cursor <= start <= cursor + length or max(start, cursor) < min(end, cursor + length):
                        touched.append((node, cursor, length))
                    cursor += length
                if not touched:
                    raise ValueError("段落映射已失效，未生成 DOCX。")
                inserted = False
                for node, offset, length in touched:
                    old = node.text or ""
                    left = max(0, min(length, start - offset))
                    right = max(left, min(length, end - offset))
                    node.text = old[:left] + (replacement if not inserted else "") + old[right:]
                    inserted = True
                    node.set(XML_SPACE, "preserve")
        updated_xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
        output = BytesIO()
        with ZipFile(output, "w") as target:
            for info in archive.infolist():
                target.writestr(info, updated_xml if info.filename == value["main_part"] else archive.read(info.filename))
    data = output.getvalue()
    if parse_document(value["filename"], data)["content"] != content:
        raise ValueError("回写复检发现正文与修改提案不一致，已阻止导出；原文件保持完整。")
    with ZipFile(BytesIO(original)) as before, ZipFile(BytesIO(data)) as after:
        if before.namelist() != after.namelist() or any(before.read(name) != after.read(name) for name in before.namelist() if name != value["main_part"]):
            raise ValueError("DOCX 部件保留校验失败，已阻止导出。")
    return data, [f"已安全回写 {len(edits)} 个文字段落并复检正文；其他 DOCX 部件逐字节保留。",
                  "已保留原版式、图片、公式、域、批注和既有修订；未应用预览样式。请在 Word 中复核分页及更新目录域。"]


router = APIRouter(prefix="/workspace/documents", tags=["source-protection"])


@router.get("/{document_id}/source-compatibility")
def source_compatibility(document_id: str):
    from app.agent_api import get_store
    store = get_store()
    if not store.get_workspace_document(document_id):
        raise HTTPException(404, "文档不存在。")
    return compatibility(store, document_id)


@router.get("/{document_id}/original.docx")
def download_original(document_id: str):
    from app.agent_api import get_store
    value = _read(get_store(), document_id)
    if not value:
        raise HTTPException(404, "此文档没有留存原 DOCX。")
    return Response(base64.b64decode(value["data"]), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": 'attachment; filename="original.docx"', "Cache-Control": "no-store"})
