"""Document-bound orchestration of existing tools, with durable resumable steps."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app import agent_api
from app.assistance_api import _guard, _state
from app.document_assistance import analyze_document, retrieve_evidence
from app.literature import LiteratureQuery, digest, now
from app.literature_api import read_record, write_record

router = APIRouter(prefix="/workspace-assistant", tags=["workspace-assistant"])
RUNNING: dict[str, asyncio.Task] = {}
Kind = Literal["check", "literature", "review", "mentor", "layout", "export", "question", "help"]
LABELS = {"check": "检查格式与完整性", "literature": "查找相关文献", "review": "检查论证与依据",
          "mentor": "整理导师意见", "layout": "生成排版提案", "export": "准备导出", "question": "查阅所选资料", "help": "使用说明"}


class TaskStep(BaseModel):
    id: str
    kind: Kind
    label: str
    input: str = ""
    status: str = "pending"
    result: dict = Field(default_factory=dict)
    error: str = ""


class AssistantTask(BaseModel):
    id: str
    document_id: str
    source_hash: str
    profile_id: str = "generic-thesis"
    message: str
    status: str = "queued"
    steps: list[TaskStep]
    created_at: str
    updated_at: str
    planner: str = "local"


class AssistantSession(BaseModel):
    document_id: str
    task_ids: list[str] = Field(default_factory=list)


class TaskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    expected_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_id: str = Field(min_length=8, max_length=100)


class PlanAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Kind
    input: str = Field(max_length=2000)


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actions: list[PlanAction] = Field(max_length=5)


def local_actions(message: str) -> list[PlanAction]:
    if re.search(r"不要|暂不|先别|别执行", message):
        return [PlanAction(kind="help", input=message)]
    patterns = [("literature", r"查找文献|搜索文献|检索文献|搜文献|找.{0,20}文献|DOI:|10\.\d{4,9}/"),
                          ("mentor", r"导师意见|导师说|导师反馈"),
                          ("question", r"根据资料|资料中|资料里"),
                          ("layout", r"排版|行距|页边距|字号|宋体|黑体"),
                          ("review", r"深度检查|论证|五角色|审查|创新点|方法论"),
                          ("check", r"检查|扫描|格式|提交前"),
                          ("export", r"导出|下载")]
    actions = []
    clauses = re.split(r"[；;\n]|然后(?:再)?|接着|再帮我|[，,](?=\s*(?:再|并)?(?:检查|导出|查找文献|搜索文献))", message)
    for clause in clauses:
        value = clause.strip(" ，,。")
        if not value:
            continue
        for kind, pattern in patterns:
            if not re.search(pattern, value, re.I):
                continue
            # Search titles and mentor comments are data for one operation, not
            # extra instructions to schedule a review or change typography.
            if kind == "literature":
                value = re.sub(r"^.*?(?:查找文献|搜索文献|检索文献|搜文献)\s*[:：]?\s*", "", value).strip()
            elif kind == "mentor":
                value = re.sub(r"^.*?(?:导师意见|导师说|导师反馈)\s*[:：]?\s*", "", value).strip()
            elif kind != "question" and re.search(r"怎么|如何|是什么", value):
                kind = "help"
            actions.append(PlanAction(kind=kind, input=value))
            break
    return actions[:5]


async def plan_actions(message: str, history: list[dict] | None = None) -> tuple[list[PlanAction], str]:
    actions = local_actions(message)
    if actions:
        return actions, "local"
    from app.agent_settings import resolve_config, public_config
    config = resolve_config()
    if not public_config(config)["configured"]:
        return [PlanAction(kind="help", input=message)], "local"
    from agents import Agent, OpenAIChatCompletionsModel, RunConfig, Runner
    from openai import AsyncOpenAI
    async with AsyncOpenAI(api_key=config["api_key"], base_url=config["base_url"], timeout=25, max_retries=0) as client:
        agent = Agent(name="论文工作助手", model=OpenAIChatCompletionsModel(model=config["model"], openai_client=client), output_type=Plan,
                      instructions="把当前用户请求分解成至多五个允许的操作，按用户顺序。check是本地格式检查，review是内容审查，literature是公开文献检索（input仅检索词），mentor是录入导师意见（input仅意见原文），layout是排版提案，export是打开导出，question是查阅用户选定资料。咨询、含否定限制或不清楚的请求返回help。不得编造用户未要求的操作或资料，不声称已执行；正文不在本次输入中。")
        context = json.dumps({"message": message, "previous_requests": history or []}, ensure_ascii=False)
        result = await asyncio.wait_for(Runner.run(agent, input=context, max_turns=1, run_config=RunConfig(tracing_disabled=True)), 30)
        plan = result.final_output
        plan = plan if isinstance(plan, Plan) else Plan.model_validate_json(plan) if isinstance(plan, str) else Plan.model_validate(plan)
    return plan.actions or [PlanAction(kind="help", input=message)], "configured"


def load_task(connection, document_id, task_id):
    task = read_record(connection, "assistant_tasks", task_id)
    if task is None or task["document_id"] != document_id:
        raise HTTPException(404, "当前文档中没有这项任务。")
    return task


def save_task(connection, task):
    task["updated_at"] = now()
    write_record(connection, "assistant_tasks", task["id"], task)


def task_view(connection, task):
    document = _guard(connection, task["document_id"])
    if task["status"] in {"queued", "running"} and task["id"] not in RUNNING:
        task["status"] = "paused"
        for step in task["steps"]:
            if step["status"] == "running":
                step["status"] = "pending"
        save_task(connection, task)
    return {**task, "stale": task["source_hash"] != document["content_hash"] or task.get("profile_id") != document["profile_id"]}


@router.get("/documents/{document_id}")
def session(document_id: str):
    with agent_api.get_store()._transaction() as connection:
        document = _guard(connection, document_id)
        value = read_record(connection, "assistant_sessions", document_id, AssistantSession(document_id=document_id).model_dump())
        tasks = [task_view(connection, load_task(connection, document_id, id)) for id in value["task_ids"]]
        return {"document_id": document_id, "content_hash": document["content_hash"], "tasks": tasks}


@router.post("/documents/{document_id}/tasks", status_code=201)
async def create_task(document_id: str, payload: TaskRequest):
    if not payload.message.strip():
        raise HTTPException(422, "请写下这次想完成的事。")
    task_id = "task-" + digest(document_id + ":" + payload.request_id)[:32]
    with agent_api.get_store()._transaction() as connection:
        document = _guard(connection, document_id, payload.expected_content_hash)
        existing = read_record(connection, "assistant_tasks", task_id)
        if existing:
            if existing["message"] != payload.message or existing["source_hash"] != payload.expected_content_hash:
                raise HTTPException(409, "这次请求已绑定另一项任务，请重新提交。")
            return task_view(connection, existing)
        state = read_record(connection, "assistant_sessions", document_id, {"task_ids": []})
        history = [{"message": item["message"], "status": item["status"], "steps": [{"kind": s["kind"], "status": s["status"]} for s in item["steps"]]}
                   for item in [load_task(connection, document_id, id) for id in state["task_ids"][-5:]]]
    try:
        actions, planner = await plan_actions(payload.message, history)
    except Exception:
        actions, planner = [PlanAction(kind="help", input="请求理解服务暂未响应，请使用下面的快捷操作或重试。")], "local_fallback"
    steps = []
    for action in actions:
        if action.kind == "review" and not any(s.kind == "check" for s in steps):
            steps.append(TaskStep(id="step-" + uuid4().hex[:12], kind="check", label=LABELS["check"]))
        steps.append(TaskStep(id="step-" + uuid4().hex[:12], kind=action.kind, label=LABELS[action.kind], input=action.input))
    value = AssistantTask(id=task_id, document_id=document_id, source_hash=payload.expected_content_hash,
                          message=payload.message, steps=steps, created_at=now(), updated_at=now(), planner=planner, profile_id=document["profile_id"]).model_dump()
    with agent_api.get_store()._transaction() as connection:
        _guard(connection, document_id, payload.expected_content_hash)
        existing = read_record(connection, "assistant_tasks", task_id)
        if existing:
            return task_view(connection, existing)
        state = read_record(connection, "assistant_sessions", document_id, AssistantSession(document_id=document_id).model_dump())
        state["task_ids"] = (state["task_ids"] + [task_id])[-40:]
        write_record(connection, "assistant_sessions", document_id, state)
        save_task(connection, value)
    schedule(document_id, task_id)
    return value


def schedule(document_id, task_id):
    if task_id not in RUNNING:
        running = asyncio.create_task(drive(document_id, task_id))
        RUNNING[task_id] = running
        running.add_done_callback(lambda finished: RUNNING.pop(task_id, None))


def current_document(task):
    with agent_api.get_store()._transaction() as connection:
        document = _guard(connection, task["document_id"], task["source_hash"])
        if document["profile_id"] != task.get("profile_id"):
            raise HTTPException(409, "文档规范已变化，请按当前规范重新运行任务。")
        return document


async def execute_step(task, step):
    document = current_document(task)
    kind, value = step["kind"], step["input"]
    document_id = document["id"]
    if kind == "check":
        module = agent_api.main()
        with module.REVISION_LOCK:
            current_document(task)
            saved = agent_api.get_store().get_workspace_document(document_id)
            # Resume reuses a completed run tied to exactly this saved draft.
            run = module.RUNS.get(saved.get("last_run_id"))
            if run is None or module.SOURCES.get(run.id) != document["content"] or run.profile_id != document["profile_id"] or (agent_api.get_store().get_head(document_id) or {}).get("id") != run.current_version_id:
                run = module.create_format_run(module.FormatRunCreate(document_id=document_id, content=document["content"],
                    document_name=document["name"], document_type="markdown", profile_id=document["profile_id"], expected_head_hash=saved["head_hash"]))
                # Scan metadata does not change the editable draft or its revision.
                with agent_api.get_store()._transaction() as connection:
                    _guard(connection, document_id, task["source_hash"])
                    connection.execute("UPDATE workspace_documents SET last_run_id=? WHERE id=?", (run.id, document_id))
        profile = module.resolve_format_profile(document["profile_id"])
        analysis = analyze_document(document["content"], profile.document_kind, list(profile.required_sections))
        return {"run_id": run.id, "changes_count": run.changes_count, "analysis": analysis,
                "summary": f"格式检查发现 {run.changes_count} 项建议；已检查章节与引用关系，请逐项核对。", "action": "changes"}
    if kind == "literature":
        from app.literature_api import search
        if value in {"查找文献", "搜索文献", "检索文献"} or len(value) < 2:
            return {"summary": "请输入研究主题、论文题名或 DOI。只发送你填写的检索词。", "action": "literature"}
        result = await search(document_id, LiteratureQuery(query=value[:500]))
        if result["status"] == "failed":
            raise HTTPException(502, "两处文献服务暂未返回结果，请稍后重试；已完成的其他步骤会保留。")
        return {"search": result, "summary": f"找到 {len(result['results'])} 条结果。选择需要的文献后加入资料。", "action": "literature"}
    if kind == "review":
        from app.agent_settings import public_config
        if not public_config()["configured"] and agent_api.PROVIDER_OVERRIDE is None:
            raise HTTPException(409, "内容审查需要连接模型。格式检查已保留，设置模型后可续跑。")
        check = next(s["result"] for s in task["steps"] if s["kind"] == "check" and s["status"] == "completed")
        with agent_api.get_store()._transaction() as connection:
            state = _state(connection, document_id)
        materials = []
        remaining = 300_000
        for material in state["materials"]:
            text = "\n\n".join(s["text"] for s in material.get("segments", []))
            if text and remaining and len(materials) < 10:
                text = text[:remaining]
                remaining -= len(text)
                materials.append(agent_api.MaterialInput(name=material["name"][:255], content=text))
        report = agent_api.main().get_revision_review(check["run_id"])
        review = await agent_api.create_review(agent_api.ReviewCreate(format_run_id=check["run_id"],
            expected_source_hash=report.source_hash, expected_preview_hash=report.preview_hash, materials=materials,
            user_goal=(value + "\n比较范围仅限用户选定且已取得文本的资料；摘要不代表全文，题录不作为论点证据。")[:2000]))
        # Persist the spawned identity before waiting, so restart/resume never creates another review.
        with agent_api.get_store()._transaction() as connection:
            stored = load_task(connection, document_id, task["id"])
            target = next(s for s in stored["steps"] if s["id"] == step["id"])
            target["result"] = {"review_id": review["id"], "run_id": check["run_id"]}
            save_task(connection, stored)
        return await wait_review(review["id"], check["run_id"])
    if kind == "mentor":
        from app.assistance_api import _append_tasks
        from app.document_assistance import split_mentor_comments
        if value in {"处理导师意见", "导师意见", "整理导师意见"}:
            return {"summary": "打开导师意见，粘贴意见或导入带批注的文档；每条意见可以关联原文并填写答复。", "action": "assistance"}
        with agent_api.get_store()._transaction() as connection:
            document = _guard(connection, document_id, task["source_hash"])
            stored = load_task(connection, document_id, task["id"])
            target = next(s for s in stored["steps"] if s["id"] == step["id"])
            if target["result"].get("mentor_saved"):
                return target["result"]
            result = _append_tasks(connection, document, split_mentor_comments(value, document["content"]))
            target["result"] = {"mentor_saved": True, "summary": f"已整理 {result['added']} 条导师意见，等待关联原文与填写答复。", "action": "assistance"}
            save_task(connection, stored)
            return target["result"]
    if kind == "question":
        with agent_api.get_store()._transaction() as connection:
            materials = _state(connection, document_id)["materials"]
        result = retrieve_evidence(value, [m for m in materials if m.get("segments")])
        return {"summary": "以下是已选资料中的相关摘录，请结合上下文核对。", "evidence": result, "action": "assistance"}
    if kind == "layout":
        from app.agent_chat import ChatRequest, chat
        from app.document_layout import read_layout
        layout = read_layout(document_id)
        profile = agent_api.main().resolve_format_profile(document["profile_id"])
        result = await chat(ChatRequest(document_id=document_id, message=value, style=layout.style,
            profile_style=getattr(profile, "document_style", None)))
        result = json.loads(json.dumps(result, default=lambda v: v.model_dump(mode="json")))
        return {**result, "summary": result["reply"], "expected_revision": layout.revision, "action": "layout"}
    if kind == "export":
        return {"summary": "导出面板已准备好，默认导出当前确认稿。选择文件格式后下载。", "action": "export"}
    return {"summary": "可以直接告诉我：检查论文、查找文献：研究主题、导师意见：具体内容、正文改为宋体小四，或导出。检查会产生待确认的建议；排版会先给出具体变化。", "action": "help"}


async def wait_review(review_id, run_id):
    while True:
        review = agent_api.get_review(review_id)
        if review["status"] in agent_api.TERMINAL:
            if review["status"] in {"failed", "cancelled"}:
                raise HTTPException(502, "内容审查尚未完成，可重试并复用已有进度。")
            return {"review_id": review_id, "run_id": run_id, "summary": "内容审查" + ("部分完成，请在检查详情核对未完成角色。" if review["status"] == "partial" else "已完成，打开建议核对依据。"), "action": "agents"}
        await asyncio.sleep(0.5)


async def drive(document_id, task_id):
    try:
        while True:
            with agent_api.get_store()._transaction() as connection:
                task = load_task(connection, document_id, task_id)
                if task["status"] == "cancelled":
                    return
                step = next((s for s in task["steps"] if s["status"] in {"pending", "running", "failed"}), None)
                if step is None:
                    task["status"] = "needs_confirmation" if any(s["status"] == "needs_confirmation" for s in task["steps"]) else "completed"
                    save_task(connection, task)
                    return
                task["status"], step["status"], step["error"] = "running", "running", ""
                save_task(connection, task)
            current_document(task)
            if step["kind"] == "review" and step["result"].get("review_id"):
                existing = agent_api.get_review(step["result"]["review_id"])
                if existing["status"] in {"failed", "cancelled", "partial"}:
                    await agent_api.retry_review(existing["id"], agent_api.SnapshotGuard(expected_preview_hash=existing["preview_hash"]))
                result = await wait_review(existing["id"], step["result"]["run_id"])
            else:
                result = await execute_step(task, step)
            with agent_api.get_store()._transaction() as connection:
                stored = load_task(connection, document_id, task_id)
                if stored["status"] == "cancelled":
                    return
                target = next(s for s in stored["steps"] if s["id"] == step["id"])
                target["result"] = result
                target["status"] = "needs_confirmation" if result.get("proposed_style") else "completed"
                save_task(connection, stored)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        with agent_api.get_store()._transaction() as connection:
            task = load_task(connection, document_id, task_id)
            if task["status"] == "cancelled":
                return
            task["status"] = "failed"
            for step in task["steps"]:
                if step["status"] == "running":
                    step["status"] = "failed"
                    step["error"] = str(error.detail) if isinstance(error, HTTPException) else "这一步尚未完成，请重试。已完成步骤已保存。"
            save_task(connection, task)


@router.post("/documents/{document_id}/tasks/{task_id}/cancel")
async def cancel(document_id: str, task_id: str):
    with agent_api.get_store()._transaction() as connection:
        task = load_task(connection, document_id, task_id)
        if task["status"] == "completed":
            return task
        task["status"] = "cancelled"
        for step in task["steps"]:
            if step["status"] == "running":
                step["status"] = "pending"
        save_task(connection, task)
    process = RUNNING.get(task_id)
    if process:
        process.cancel()
        await asyncio.gather(process, return_exceptions=True)
    for step in task["steps"]:
        if step["kind"] == "review" and step["result"].get("review_id"):
            await agent_api.cancel_review(step["result"]["review_id"])
    return task


@router.post("/documents/{document_id}/tasks/{task_id}/resume")
async def resume(document_id: str, task_id: str):
    with agent_api.get_store()._transaction() as connection:
        task = load_task(connection, document_id, task_id)
        document = _guard(connection, document_id, task["source_hash"])
        if document["profile_id"] != task.get("profile_id"):
            raise HTTPException(409, "文档规范已变化，请重新运行任务。")
        if task_id in RUNNING or task["status"] in {"completed", "needs_confirmation"}:
            return task
        for step in task["steps"]:
            if step["status"] in {"running", "failed"}:
                step["status"] = "pending"
        task["status"] = "queued"
        save_task(connection, task)
    schedule(document_id, task_id)
    return task


@router.post("/documents/{document_id}/tasks/{task_id}/steps/{step_id}/apply")
def apply_layout(document_id: str, task_id: str, step_id: str):
    from app.document_layout import DocumentLayout, DocumentStyle, _stored_layout
    with agent_api.get_store()._transaction() as connection:
        task = load_task(connection, document_id, task_id)
        document = _guard(connection, document_id, task["source_hash"])
        if document["profile_id"] != task.get("profile_id"):
            raise HTTPException(409, "文档规范已变化，请重新生成排版提案。")
        step = next((s for s in task["steps"] if s["id"] == step_id and s["kind"] == "layout"), None)
        if step is None or task["status"] == "cancelled":
            raise HTTPException(409, "这项排版提案已失效，请重新生成。")
        if step["result"].get("applied"):
            return step["result"]["applied"]
        if step["status"] != "needs_confirmation":
            raise HTTPException(409, "目前没有待确认的排版提案。")
        previous = _stored_layout(connection, document_id)
        if previous.revision != step["result"]["expected_revision"]:
            raise HTTPException(409, "排版已变化，请按当前排版重新生成提案。")
        result = DocumentLayout(document_id=document_id, revision=previous.revision + 1,
                                style=DocumentStyle.model_validate(step["result"]["proposed_style"])).model_dump(mode="json")
        write_record(connection, "document_layouts", document_id, result)
        step["result"]["applied"], step["status"] = result, "completed"
        if all(s["status"] == "completed" for s in task["steps"]):
            task["status"] = "completed"
        save_task(connection, task)
        return result
