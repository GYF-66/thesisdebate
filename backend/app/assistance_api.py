"""Document-scoped, content-hash-guarded assistance records and real exports."""
from __future__ import annotations

import csv
from io import StringIO
import json
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.agent_api import get_store
from app.document_assistance import (MAX_BYTES, analyze_document, content_hash,
                                     extract_comments, extract_material, now_iso,
                                     retrieve_evidence, split_mentor_comments, verify_reference)

router = APIRouter(prefix="/document-assistance", tags=["document-assistance"])
NAMESPACE = "document_assistance"
HASH = r"^[0-9a-f]{64}$"


class AnalyzeInput(BaseModel):
    content: str = Field(max_length=300_000)
    document_kind: Literal["thesis", "course_report", "lab_report", "internship_report", "proposal", "generic", "report", "other"] = "thesis"
    required_sections: list[str] | None = Field(default=None, max_length=30)
    mentor_comments: str | list[str] = ""
    profile_id: str | None = Field(default=None, max_length=100)
    document_id: str | None = Field(default=None, max_length=100)
    expected_content_hash: str | None = Field(default=None, pattern=HASH)


class GuardedInput(BaseModel):
    document_id: str = Field(min_length=1, max_length=100)
    content: str = Field(max_length=300_000)
    expected_content_hash: str = Field(pattern=HASH)


class MentorInput(GuardedInput):
    comments: str = Field(min_length=1, max_length=30_000)


class TaskUpdate(BaseModel):
    content: str = Field(max_length=300_000)
    expected_content_hash: str = Field(pattern=HASH)
    expected_task_version: int = Field(ge=1, strict=True)
    status: Literal["pending", "in_progress", "done", "skipped"] | None = None
    reply: str | None = Field(default=None, max_length=4000)
    quote: str | None = Field(default=None, max_length=2000)
    rebase: bool = False


class QuestionInput(GuardedInput):
    question: str = Field(min_length=2, max_length=2000)
    material_ids: list[str] | None = Field(default=None, max_length=20)


class ReferenceInput(GuardedInput):
    reference: str = Field(min_length=3, max_length=4000)


def _guard(connection, document_id, expected_hash=None, content=None):
    row = connection.execute("SELECT id,name,content,profile_id FROM workspace_documents WHERE id=?", (document_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "当前工作区文档不存在。")
    document = dict(row)
    document["content_hash"] = content_hash(document["content"])
    if expected_hash is not None and document["content_hash"] != expected_hash:
        raise HTTPException(409, "正文已变化或尚未保存，请等待自动保存完成后重新操作。")
    if content is not None and content_hash(content) != expected_hash:
        raise HTTPException(409, "提交的正文与内容哈希不一致，请重新检查。")
    return document


def _state(connection, document_id):
    row = connection.execute("SELECT payload FROM records WHERE namespace=? AND record_id=?", (NAMESPACE, document_id)).fetchone()
    if row is None:
        return {"document_id": document_id, "revision": 0, "mentor_tasks": [], "materials": [], "reference_checks": [], "document_kind": "generic", "required_sections": None}
    return json.loads(row["payload"])


def _save(connection, document_id, state):
    state["revision"] += 1
    state["updated_at"] = now_iso()
    connection.execute("""INSERT INTO records(namespace,record_id,payload,updated_at) VALUES (?,?,?,?)
                          ON CONFLICT(namespace,record_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at""",
                       (NAMESPACE, document_id, json.dumps(state, ensure_ascii=False, allow_nan=False), state["updated_at"]))


def _public(state, document):
    value = json.loads(json.dumps(state, ensure_ascii=False))
    value["content_hash"] = document["content_hash"]
    for task in value["mentor_tasks"]:
        task["stale"] = task.get("source_content_hash") != document["content_hash"]
    value["materials"] = [{key: item for key, item in material.items() if key != "segments"} for material in value["materials"]]
    return value


def _append_tasks(connection, document, tasks):
    state = _state(connection, document["id"])
    if len(state["mentor_tasks"]) + len(tasks) > 200:
        raise HTTPException(422, "每个文档最多保存 200 条导师任务。")
    for task in tasks:
        task.update(id="mentor-" + uuid4().hex, source_content_hash=document["content_hash"], created_at=now_iso(), updated_at=now_iso())
    state["mentor_tasks"].extend(tasks)
    _save(connection, document["id"], state)
    return {"added": len(tasks), **_public(state, document)}


def _add_task_context(result, state, digest):
    pending = [task for task in state["mentor_tasks"] if task["status"] not in {"done", "skipped"} or task["source_content_hash"] != digest]
    result["submission_checklist"].append({"id": "mentor_tasks", "title": "导师意见闭环", "status": "attention" if pending else "manual" if not state["mentor_tasks"] else "pass", "detail": f"{len(pending)} 条任务尚未处理或需重新核对正文。" if state["mentor_tasks"] else "尚未录入导师意见，请确认是否有待处理反馈。"})
    for task in pending[:3]:
        result["defense_questions"].append({"question": "如何回应导师意见：“" + task["comment"][:300] + "”？", "quote": task["quote"] if task["source_content_hash"] == digest else "", "reason": "来自当前文档尚未闭环的导师任务。"})
    result["defense_questions"] = result["defense_questions"][:11]


async def _upload(request: Request):
    length = request.headers.get("content-length")
    if length is not None:
        try:
            declared = int(length)
        except ValueError as error:
            raise HTTPException(400, "文件长度无效。") from error
        if declared < 0:
            raise HTTPException(400, "文件长度无效。")
        if declared > MAX_BYTES:
            raise HTTPException(413, "文件不能超过 10 MB。")
    data = bytearray()
    async for part in request.stream():
        if len(data) + len(part) > MAX_BYTES:
            raise HTTPException(413, "文件不能超过 10 MB。")
        data.extend(part)
    if not data:
        raise HTTPException(422, "文件为空。")
    return bytes(data)


@router.post("/analyze")
def analyze(payload: AnalyzeInput):
    if payload.profile_id:
        from app.agent_api import main
        profile = main().resolve_format_profile(payload.profile_id)
        if profile is None:
            raise HTTPException(422, "文档模板不存在，请重新选择。")
        if "document_kind" not in payload.model_fields_set:
            payload.document_kind = profile.document_kind
        if payload.required_sections is None and ("document_kind" not in payload.model_fields_set or payload.document_kind == profile.document_kind):
            payload.required_sections = list(profile.required_sections)
    if payload.required_sections is not None and any(not item.strip() or len(item) > 100 for item in payload.required_sections):
        raise HTTPException(422, "必需章节不能为空，每个章节名最多 100 字。")
    if isinstance(payload.mentor_comments, str) and len(payload.mentor_comments) > 30_000 or isinstance(payload.mentor_comments, list) and (len(payload.mentor_comments) > 100 or sum(map(len, payload.mentor_comments)) > 30_000):
        raise HTTPException(422, "导师意见过长，请分批处理。")
    result = analyze_document(payload.content, payload.document_kind, payload.required_sections, payload.mentor_comments)
    result["document_id"] = payload.document_id
    result["profile_id"] = payload.profile_id
    if payload.document_id:
        if not payload.expected_content_hash:
            raise HTTPException(422, "关联已保存文档时必须提交 expected_content_hash。")
        with get_store()._transaction() as connection:
            document = _guard(connection, payload.document_id, payload.expected_content_hash, payload.content)
            state = _state(connection, payload.document_id)
            state.update(document_kind=payload.document_kind, required_sections=payload.required_sections, profile_id=payload.profile_id, last_analysis_hash=document["content_hash"])
            _save(connection, payload.document_id, state)
            result["saved_mentor_tasks"] = _public(state, document)["mentor_tasks"]
            _add_task_context(result, state, document["content_hash"])
    if payload.profile_id:
        for index, item in enumerate(profile.submission_checklist):
            result["submission_checklist"].append({"id": f"template-{index}", "title": item, "status": "manual", "detail": "来自当前选用模板，请人工确认交付材料。"})
    return result


@router.get("/documents/{document_id}/state")
def get_state(document_id: str):
    with get_store()._transaction() as connection:
        document = _guard(connection, document_id)
        return _public(_state(connection, document_id), document)


@router.post("/mentor-comments", status_code=201)
def add_comments(payload: MentorInput):
    tasks = split_mentor_comments(payload.comments, payload.content)
    if not tasks:
        raise HTTPException(422, "没有可保存的导师意见。")
    with get_store()._transaction() as connection:
        document = _guard(connection, payload.document_id, payload.expected_content_hash, payload.content)
        return _append_tasks(connection, document, tasks)


@router.post("/mentor-comments/import", status_code=201)
async def import_comments(request: Request, document_id: str = Query(min_length=1, max_length=100),
                          filename: str = Query(min_length=1, max_length=255),
                          expected_content_hash: str = Query(pattern=HASH)):
    if not filename.lower().endswith(".docx"):
        raise HTTPException(422, "批注导入仅支持 DOCX。")
    with get_store()._transaction() as connection:
        document = _guard(connection, document_id, expected_content_hash)
    data = await _upload(request)
    try:
        tasks = await run_in_threadpool(extract_comments, data, document["content"], filename)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    with get_store()._transaction() as connection:
        document = _guard(connection, document_id, expected_content_hash)
        return _append_tasks(connection, document, tasks)


@router.patch("/documents/{document_id}/mentor-tasks/{task_id}")
def update_task(document_id: str, task_id: str, payload: TaskUpdate):
    with get_store()._transaction() as connection:
        document = _guard(connection, document_id, payload.expected_content_hash, payload.content)
        state = _state(connection, document_id)
        task = next((item for item in state["mentor_tasks"] if item["id"] == task_id), None)
        if task is None:
            raise HTTPException(404, "当前文档中没有该导师任务。")
        if task["version"] != payload.expected_task_version:
            raise HTTPException(409, "该任务已被更新，请刷新任务后重试。")
        if task["source_content_hash"] != document["content_hash"] and not payload.rebase:
            raise HTTPException(409, "任务基于旧正文，请先核对并重新关联当前版本。")
        if payload.quote is not None or payload.rebase:
            text = payload.quote if payload.quote is not None else task.get("source_quote", "")
            offset = document["content"].find(text) if text else -1
            if text and (offset < 0 or document["content"].find(text, offset + len(text)) >= 0):
                raise HTTPException(422, "关联原文必须在当前正文中唯一出现；可清空后人工确认。")
            task.update(quote=text, source_quote=text, start=offset if text else None, end=offset + len(text) if text else None, location_status="located" if text else "needs_confirmation")
        if payload.reply is not None:
            task["reply"] = payload.reply.strip()
        if payload.status is not None:
            if payload.status in {"done", "skipped"} and not task["reply"]:
                raise HTTPException(422, "完成或暂不处理任务前，请填写处理说明。")
            task["status"] = payload.status
        task.update(source_content_hash=document["content_hash"], version=task["version"] + 1, updated_at=now_iso())
        _save(connection, document_id, state)
        return _public(state, document)


@router.post("/materials", status_code=201)
async def upload_material(request: Request, document_id: str = Query(min_length=1, max_length=100),
                          filename: str = Query(min_length=1, max_length=255),
                          expected_content_hash: str = Query(pattern=HASH)):
    with get_store()._transaction() as connection:
        _guard(connection, document_id, expected_content_hash)
    data = await _upload(request)
    try:
        material = await run_in_threadpool(extract_material, filename, data)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    with get_store()._transaction() as connection:
        document = _guard(connection, document_id, expected_content_hash)
        state = _state(connection, document_id)
        if len(state["materials"]) >= 20 or sum(item["characters"] for item in state["materials"]) + material["characters"] > 2_000_000:
            raise HTTPException(422, "每个文档最多保存 20 份资料、合计 200 万字符。")
        if any(item["file_hash"] == material["file_hash"] for item in state["materials"]):
            raise HTTPException(409, "当前文档已经上传过相同资料。")
        material.update(id="material-" + uuid4().hex, created_at=now_iso(), source_content_hash=expected_content_hash)
        state["materials"].append(material)
        _save(connection, document_id, state)
        return _public(state, document)


@router.delete("/documents/{document_id}/materials/{material_id}")
def delete_material(document_id: str, material_id: str, expected_content_hash: str = Query(pattern=HASH)):
    with get_store()._transaction() as connection:
        document = _guard(connection, document_id, expected_content_hash)
        state = _state(connection, document_id)
        existing = [item for item in state["materials"] if item["id"] == material_id]
        if not existing:
            raise HTTPException(404, "当前文档中没有该资料。")
        state["materials"] = [item for item in state["materials"] if item["id"] != material_id]
        _save(connection, document_id, state)
        return _public(state, document)


@router.post("/documents/{document_id}/questions")
def ask_materials(document_id: str, payload: QuestionInput):
    if not payload.question.strip():
        raise HTTPException(422, "请输入具体的检索问题。")
    if document_id != payload.document_id:
        raise HTTPException(409, "问题与当前文档不匹配。")
    with get_store()._transaction() as connection:
        _guard(connection, document_id, payload.expected_content_hash, payload.content)
        state = _state(connection, document_id)
        materials = state["materials"]
        if payload.material_ids is not None:
            selected = set(payload.material_ids)
            if selected - {item["id"] for item in materials}:
                raise HTTPException(404, "部分资料不属于当前文档。")
            materials = [item for item in materials if item["id"] in selected]
        if not materials:
            raise HTTPException(422, "请先上传当前文档的相关资料。")
        return {"content_hash": payload.expected_content_hash, **retrieve_evidence(payload.question, materials)}


@router.post("/references/verify")
async def check_reference(payload: ReferenceInput):
    with get_store()._transaction() as connection:
        document = _guard(connection, payload.document_id, payload.expected_content_hash, payload.content)
        if payload.reference not in document["content"]:
            raise HTTPException(422, "请选择当前正文中的文献条目；不会核验脱离文档的隐藏内容。")
    result = await run_in_threadpool(verify_reference, payload.reference)
    result.update(id="reference-" + uuid4().hex, source_content_hash=payload.expected_content_hash)
    with get_store()._transaction() as connection:
        _guard(connection, payload.document_id, payload.expected_content_hash, payload.content)
        state = _state(connection, payload.document_id)
        state["reference_checks"] = [item for item in state["reference_checks"] if item["reference"] != payload.reference][-99:] + [result]
        _save(connection, payload.document_id, state)
    return result


def _csv_cell(value):
    text = str(value if value is not None else "")
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


@router.get("/documents/{document_id}/mentor-replies.csv")
def export_replies(document_id: str, expected_content_hash: str | None = Query(default=None, pattern=HASH)):
    with get_store()._transaction() as connection:
        document = _guard(connection, document_id, expected_content_hash)
        state = _state(connection, document_id)
    buffer = StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["任务编号", "导师意见", "关联原文", "状态", "处理说明", "来源", "正文时效", "任务版本"])
    labels = {"pending": "待处理", "in_progress": "处理中", "done": "已处理", "skipped": "暂不处理"}
    for task in state["mentor_tasks"]:
        writer.writerow([_csv_cell(value) for value in [task["id"], task["comment"], task["quote"], labels[task["status"]], task["reply"], task["source"], "当前版本" if task["source_content_hash"] == document["content_hash"] else "旧正文，需重新核对", task["version"]]])
    return Response("\ufeff" + buffer.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="mentor-replies.csv"'})


@router.get("/documents/{document_id}/report.md")
def export_report(document_id: str, expected_content_hash: str | None = Query(default=None, pattern=HASH)):
    with get_store()._transaction() as connection:
        document = _guard(connection, document_id, expected_content_hash)
        state = _state(connection, document_id)
    kind, required = state.get("document_kind", "generic"), state.get("required_sections")
    if not state.get("last_analysis_hash") or state.get("profile_id") and state["profile_id"] != document["profile_id"]:
        from app.agent_api import main
        selected = main().resolve_format_profile(document["profile_id"])
        if selected:
            kind, required = selected.document_kind, list(selected.required_sections)
    analysis = analyze_document(document["content"], kind, required)
    _add_task_context(analysis, state, document["content_hash"])
    lines = ["# 文档检查与交付报告", "", "文档：" + document["name"], "生成时间：" + now_iso(), "正文 SHA-256：" + document["content_hash"], "", "## 检查提示", ""]
    for item in analysis["findings"]:
        lines.extend(["### " + item["title"], item["detail"], ("> " + item["quote"].replace("\n", "\n> ")) if item["quote"] else "无唯一正文锚点，请按章节核对。", ""])
    if not analysis["findings"]:
        lines.append("当前已覆盖的规则未发现提示，不代表所有要求均已满足。")
    lines.extend(["", "## 导师意见"])
    for task in state["mentor_tasks"]:
        freshness = "当前正文" if task["source_content_hash"] == document["content_hash"] else "旧正文，需重新核对"
        lines.extend([f"- [{task['status']}] {task['comment']}（{freshness}）", "  处理说明：" + (task["reply"] or "尚未填写")])
    if not state["mentor_tasks"]:
        lines.append("尚未录入导师意见。")
    lines.extend(["", "## 提交清单"])
    for item in analysis["submission_checklist"]:
        lines.append(f"- {item['title']} [{item['status']}]：{item['detail']}")
    lines.extend(["", "## 答辩准备问题" if kind == "thesis" else "## 汇报准备问题"])
    lines.extend("- " + item["question"] + " " + item["reason"] for item in analysis["defense_questions"])
    lines.extend(["", "## 参考文献核验记录"])
    for item in state["reference_checks"]:
        lines.extend(["- " + item["reference"], "  " + item["message"], "  来源：" + ("、".join(item["sources"]) or "本次查询未完成") + "；原文支持论点：未核验。"])
    if not state["reference_checks"]:
        lines.append("尚未执行联网元数据核验。")
    lines.extend(["", "## 能力边界", *["- " + item for item in analysis["limitations"]]])
    return Response("\n".join(lines) + "\n", media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="document-report.md"'})
