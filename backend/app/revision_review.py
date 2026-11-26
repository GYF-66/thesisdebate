"""Independent deterministic revision checks; no language-model inference."""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher, unified_diff
import hashlib
import re
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel
from app.heading_numbering import strip_heading_prefixes


def source_digest(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Edit:
    start: int
    end: int
    replacement: str
    change_id: str


def _utf16_map(source: str) -> Dict[int, int]:
    result = {0: 0}
    units = 0
    for index, char in enumerate(source, 1):
        units += 2 if ord(char) > 0xFFFF else 1
        result[units] = index
    return result


def validate_change(source: str, change: Any, offsets: Optional[Dict[int, int]] = None) -> Tuple[Optional[Edit], Optional[str]]:
    offsets = offsets if offsets is not None else _utf16_map(source)
    start, end = offsets.get(change.anchor.start), offsets.get(change.anchor.end)
    if start is None or end is None or end < start:
        return None, "锚点越界、顺序错误或落在 UTF-16 代理对内部。"
    if change.replacement is None:
        return None, "该建议没有可应用的正文替换内容。"
    if source[start:end] != change.anchor.quote:
        return None, "锚点引用与原文不一致。"
    context = change.anchor.quote
    if not context:
        line_start = source.rfind("\n", 0, start) + 1
        line_end = source.find("\n", start)
        context = source[line_start:line_end if line_end >= 0 else len(source)].rstrip("\r")
    if source_digest(context)[:16] != change.anchor.context_hash:
        return None, "锚点上下文校验失败，请重新扫描当前正文。"
    return Edit(start, end, change.replacement, change.id), None


def edits_conflict(left: Edit, right: Edit) -> bool:
    if left.start == left.end and right.start == right.end:
        return left.start == right.start
    if left.start == left.end:
        return right.start < left.start < right.end
    if right.start == right.end:
        return left.start < right.start < left.end
    return max(left.start, right.start) < min(left.end, right.end)


def checked_edits(source: str, changes: Sequence[Any], expected_version: Optional[str] = None) -> Tuple[List[Edit], Dict[str, str]]:
    offsets = _utf16_map(source)
    edits: List[Edit] = []
    blocked: Dict[str, str] = {}
    for change in changes:
        if change.status != "accepted":
            continue
        if expected_version is not None and change.document_version_id != expected_version:
            blocked[change.id] = "建议的文档版本与扫描版本不一致。"
            continue
        edit, error = validate_change(source, change, offsets)
        if error or edit is None:
            blocked[change.id] = error or "无效修改。"
            continue
        other = next((item for item in edits if edits_conflict(item, edit)), None)
        if other:
            blocked[change.id] = f"与已接受修改 {other.change_id} 的原文范围冲突。"
            continue
        edits.append(edit)
    return edits, blocked


class ReviewCheck(BaseModel):
    id: str
    title: str
    status: Literal["passed", "warning", "failed"]
    detail: str


class DiffRow(BaseModel):
    kind: Literal["equal", "insert", "delete", "replace"]
    source_line_no: Optional[int]
    preview_line_no: Optional[int]
    before: Optional[str]
    after: Optional[str]


class RevisionReview(BaseModel):
    run_id: str
    engine: Literal["deterministic"] = "deterministic"
    llm_enabled: Literal[False] = False
    source_hash: str
    preview_hash: str
    status: Literal["passed", "needs_review", "blocked"]
    summary: str
    applied_count: int
    accepted_count: int
    pending_count: int
    rejected_count: int
    blocked_change_ids: List[str]
    checks: List[ReviewCheck]
    diff_rows: List[DiffRow]
    unified_diff: str


def aligned_diff(source: str, preview: str) -> List[DiffRow]:
    # Keep line endings during matching: newline-only edits remain visible.
    before, after = source.splitlines(keepends=True), preview.splitlines(keepends=True)
    rows: List[DiffRow] = []
    for tag, a1, a2, b1, b2 in SequenceMatcher(None, before, after).get_opcodes():
        for offset in range(max(a2 - a1, b2 - b1)):
            a = a1 + offset if a1 + offset < a2 else None
            b = b1 + offset if b1 + offset < b2 else None
            kind = tag if a is not None and b is not None else ("delete" if a is not None else "insert")
            rows.append(DiffRow(
                kind=kind, source_line_no=a + 1 if a is not None else None,
                preview_line_no=b + 1 if b is not None else None,
                before=before[a].rstrip("\r\n") if a is not None else None,
                after=after[b].rstrip("\r\n") if b is not None else None,
            ))
    return rows


def _body_tokens(text: str) -> str:
    # Ignore only supported numbering edits, not numbers inside prose/titles.
    text = strip_heading_prefixes(text)
    return re.sub(r"(?m)^\[\d+\]\s+", "", text)


def _protected_tokens(text: str) -> Dict[str, List[str]]:
    body = _body_tokens(text)
    return {
        "numbers": re.findall(r"\d+(?:\.\d+)?(?:%|％)?", body),
        "formulas": re.findall(r"\$\$[\s\S]*?\$\$|(?<!\\)\$(?:\\.|[^$\n])+\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]", text),
        "citations": re.findall(r"\[\d+(?:\s*[-,，]\s*\d+)*\]", body),
    }


def review_revision(*, run_id: str, source: str, preview: str, changes: Sequence[Any],
                    applied_ids: Sequence[str], blocked: Dict[str, str], source_integrity: bool) -> RevisionReview:
    accepted = [item for item in changes if item.status == "accepted"]
    pending = sum(item.status == "pending" for item in changes)
    rejected = sum(item.status == "rejected" for item in changes)
    checks = [
        ReviewCheck(id="source.available", title="原文可用性", status="passed" if source else "failed",
                    detail="已使用本次扫描的真实正文。" if source else "没有可核验的原始正文；空文档或演示建议不能验证。"),
        ReviewCheck(id="source.integrity", title="原文版本一致性", status="passed" if source_integrity else "failed",
                    detail="SHA-256 与扫描时原文一致。" if source_integrity else "原文已变化，旧建议不再适用，请重新扫描。"),
        ReviewCheck(id="patch.integrity", title="修改应用完整性",
                    status="failed" if blocked or len(applied_ids) != len(accepted) else "passed",
                    detail=("；".join(f"{key}: {value}" for key, value in blocked.items()) if blocked
                            else f"已接受 {len(accepted)} 项，实际应用 {len(applied_ids)} 项；无静默覆盖。")),
        ReviewCheck(id="decisions.complete", title="修订处理进度", status="warning" if pending else "passed",
                    detail=f"仍有 {pending} 项待处理。" if pending else "当前建议均已处理。"),
    ]
    before_tokens, after_tokens = _protected_tokens(source), _protected_tokens(preview)
    for key, title in [("numbers", "数字保护"), ("formulas", "公式保护"), ("citations", "引用标记保护")]:
        unchanged = before_tokens[key] == after_tokens[key]
        checks.append(ReviewCheck(
            id=f"tokens.{key}", title=title, status="passed" if unchanged else "warning",
            detail=("启发式符号对照未发现变化（忽略支持的章节/文献编号）；不代表语义或学术正确性验证。"
                    if unchanged else "启发式符号对照发现变化，请逐项人工核对；不自动判断对错。"),
        ))
    status = ("blocked" if any(item.status == "failed" for item in checks) else
              "needs_review" if any(item.status == "warning" for item in checks) else "passed")
    summaries = {"blocked": "复核阻断：正文或修改完整性未通过，请查看失败项。",
                 "needs_review": "规则复核完成，仍有待处理建议或需人工核对的变化。",
                 "passed": "规则复核通过；仅确认修订完整性与启发式符号一致性，未进行语义推理。"}
    patch_parts: List[str] = []
    for line in unified_diff(source.splitlines(keepends=True), preview.splitlines(keepends=True),
                             fromfile="original.md", tofile="preview.md"):
        patch_parts.append(line if line.endswith("\n") else line + "\n" + chr(92) + " No newline at end of file\n")
    return RevisionReview(
        run_id=run_id, source_hash=source_digest(source), preview_hash=source_digest(preview),
        status=status, summary=summaries[status], applied_count=len(applied_ids),
        accepted_count=len(accepted), pending_count=pending, rejected_count=rejected,
        blocked_change_ids=list(blocked), checks=checks, diff_rows=aligned_diff(source, preview),
        unified_diff="".join(patch_parts),
    )
