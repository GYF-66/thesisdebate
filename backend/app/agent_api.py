"""Durable, snapshot-bound agent review API. Models never mutate documents."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.storage import ReviewStore

from app.agent_settings import router as settings_router

router = APIRouter()
router.include_router(settings_router)
STORE: ReviewStore | None = None
TASKS: dict[str, asyncio.Task] = {}
CANCELLATIONS: dict[str, asyncio.Event] = {}
PROVIDER_OVERRIDE = None  # Dependency injection in tests only; never set by HTTP.
TERMINAL = {"completed", "partial", "failed", "cancelled"}


def main():
    from app import main as module
    return module


def get_store() -> ReviewStore:
    global STORE
    if STORE is None:
        default = Path(__file__).resolve().parents[1] / "data" / "reviews.sqlite3"
        STORE = ReviewStore(os.environ.get("THESISDEBATE_DB_PATH", str(default)))
        STORE.recover_interrupted()
    return STORE


def persist_format_state():
    module = main()
    get_store().save_format_state(
        {key: value.model_dump(mode="json") for key, value in module.RUNS.items()},
        {key: value.model_dump(mode="json") for key, value in module.CHANGES.items()},
        dict(module.SOURCES),
    )


def restore_format_state():
    module = main()
    saved = get_store().load_format_state()
    module.RUNS.update({key: module.FormatRun.model_validate(value) for key, value in saved["runs"].items()})
    module.CHANGES.update({key: module.FormatChange.model_validate(value) for key, value in saved["changes"].items()})
    module.SOURCES.update(saved["sources"])


def assert_current_run(run):
    if not run.document_id:
        return
    head = get_store().get_head(run.document_id)
    if head and head["id"] != run.current_version_id:
        raise HTTPException(409, "当前文档已有更新版本，请加载当前版本后重新扫描。")


def commit_format_mutation(run, changes: dict, previous: str, origin: str):
    module = main()
    source = module.SOURCES[run.id]
    run_changes = [item for item in changes.values() if item.run_id == run.id]
    preview, _ = module.build_preview(source, run_changes, run.document_version_id)
    runs = {**module.RUNS, run.id: run}
    try:
        committed = get_store().commit_format_state(
            {key: item.model_dump(mode="json") for key, item in runs.items()},
            {key: item.model_dump(mode="json") for key, item in changes.items()},
            dict(module.SOURCES), run.id, run.document_id, preview, origin,
            expected_head_hash=hashlib.sha256(previous.encode("utf-8")).hexdigest(),
            create_version=preview != previous,
        )
    except ValueError as error:
        raise HTTPException(409, "版本已变化，请刷新后重试。") from error
    module.RUNS[run.id] = module.FormatRun.model_validate(committed["run"])
    module.CHANGES.clear()
    module.CHANGES.update(changes)


class MaterialInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=300_000)


class ReviewCreate(BaseModel):
    format_run_id: str
    expected_source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    materials: list[MaterialInput] = Field(default_factory=list, max_length=10)


class SnapshotGuard(BaseModel):
    expected_preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_source_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class FindingDecision(SnapshotGuard):
    decision: Literal["accept", "reject"]


@router.get("/agent-config")
def agent_config():
    from app.agent_engine import public_config
    return public_config()


def review_or_404(review_id: str):
    review = get_store().get_review(review_id)
    if review is None:
        raise HTTPException(404, "agent review not found")
    return review


def public_review(review: dict):
    # Inputs can be large; clients obtain evidence excerpts from findings, not all uploaded materials.
    return {key: value for key, value in review.items() if key not in {"snapshot", "role_results"}}


def snapshot_for(run_id: str, source_hash: str | None, preview_hash: str, require_preflight: bool = True):
    module = main()
    run = module.RUNS.get(run_id)
    if run is None:
        raise HTTPException(404, "format run not found")
    assert_current_run(run)
    report = module.get_revision_review(run_id)
    if require_preflight and report.status == "blocked":
        raise HTTPException(409, "原文或修订完整性未通过，请先解决规则核验的阻断项。")
    if report.preview_hash != preview_hash or (source_hash and report.source_hash != source_hash):
        raise HTTPException(409, "正文快照已变化，请刷新或重新扫描。")
    preview = module.get_preview(run_id)
    return run, preview, report


async def drive(review_id: str, resume_results=None):
    from app.agent_engine import execute_review
    store = get_store()
    review = review_or_404(review_id)
    cancel = CANCELLATIONS[review_id]
    review["status"] = "running"
    store.put_review(review)
    store.add_event(review_id, "review_started", {"status": "running"})

    async def emit(event_type, payload):
        current = review_or_404(review_id)
        agent_id = payload.get("agent_id") or payload.get("role_id")
        if agent_id:
            for agent in current["agents"]:
                if agent["id"] == agent_id:
                    for key in ("status", "summary", "error", "started_at", "completed_at"):
                        if key in payload:
                            agent[key] = payload[key]
                    if "status" not in payload and event_type.endswith("started"):
                        agent["status"] = "running"
                    elif "status" not in payload and event_type.endswith("completed"):
                        agent["status"] = "completed"
                    elif "status" not in payload and event_type.endswith("failed"):
                        agent["status"] = "failed"
        # Persist completed chunks/roles so a restart does not fabricate completion.
        if "role_results" in payload:
            current["role_results"] = payload["role_results"]
        if agent_id and "role_result" in payload:
            current.setdefault("role_results", {})[agent_id] = payload["role_result"]
        store.put_review(current)
        store.add_event(review_id, event_type, payload)

    try:
        result = await execute_review(review["snapshot"], emit, cancel,
                                      provider=PROVIDER_OVERRIDE, resume_results=resume_results)
        current = review_or_404(review_id)
        current.update(result)
        if cancel.is_set():
            current["status"] = "cancelled"
        current["completed_at"] = main().now_iso()
        store.put_review(current)
        store.add_event(review_id, "review_completed", {"status": current["status"]})
    except asyncio.CancelledError:
        current = review_or_404(review_id)
        current.update(status="cancelled", completed_at=main().now_iso())
        store.put_review(current)
        store.add_event(review_id, "review_cancelled", {"status": "cancelled"})
    except Exception:
        current = review_or_404(review_id)
        current.update(status="failed", error="审查执行失败；请检查兼容服务配置与本地事件记录。", completed_at=main().now_iso())
        store.put_review(current)
        store.add_event(review_id, "review_failed", {"status": "failed", "error": current["error"]})
    finally:
        TASKS.pop(review_id, None)
        CANCELLATIONS.pop(review_id, None)


def schedule(review_id: str, resume_results=None):
    CANCELLATIONS[review_id] = asyncio.Event()
    TASKS[review_id] = asyncio.create_task(drive(review_id, resume_results))


def new_review(run, preview, report, materials: list, review_id: str | None = None, comparison_base: str | None = None):
    from app.agent_engine import AGENT_ROLES
    review_id = review_id or "review-" + uuid4().hex
    original = preview.source if comparison_base is None else comparison_base
    snapshot = {"id": review_id, "source": original, "preview": preview.preview,
                "source_hash": hashlib.sha256(original.encode("utf-8")).hexdigest(),
                "preview_hash": report.preview_hash, "materials": materials}
    review = {"id": review_id, "format_run_id": run.id, "document_id": run.document_id,
              "source_hash": report.source_hash, "preview_hash": report.preview_hash,
              "comparison_base_hash": snapshot["source_hash"],
              "status": "queued", "agents": [{**role, "status": "queued", "summary": "", "error": None} for role in AGENT_ROLES],
              "findings": [], "summary": "", "coverage": {},
              "model": agent_config().get("model"), "created_at": main().now_iso(),
              "snapshot": snapshot, "role_results": {}}
    get_store().put_review(review)
    get_store().add_event(review_id, "review_queued", {"status": "queued"})
    schedule(review_id)
    return review


@router.post("/agent-reviews", status_code=202)
async def create_review(payload: ReviewCreate):
    if PROVIDER_OVERRIDE is None and not agent_config()["configured"]:
        raise HTTPException(503, agent_config().get("reason") or "模型服务尚未配置。")
    if sum(len(item.content) for item in payload.materials) > 300_000:
        raise HTTPException(422, "上传资料总长度不能超过 300000 字符。")
    module = main()
    with module.REVISION_LOCK:
        run, preview, report = snapshot_for(payload.format_run_id, payload.expected_source_hash, payload.expected_preview_hash)
        if any(item["status"] in {"queued", "running"} for item in get_store().list_reviews(run.id)):
            raise HTTPException(409, "该版本已有正在执行的审查。")
        materials = [{"id": uuid4().hex, "name": item.name, "content": item.content,
                      "hash": hashlib.sha256(item.content.encode("utf-8")).hexdigest()} for item in payload.materials]
        review = new_review(run, preview, report, materials)
    return public_review(review)


@router.get("/agent-reviews")
def list_reviews(format_run_id: str | None = None):
    return [public_review(item) for item in get_store().list_reviews(format_run_id)]


@router.get("/agent-reviews/workspace-status")
def workspace_review_status(document_ids: str = Query(..., min_length=1, max_length=12_000)):
    """Return compact review status for a bounded list of document IDs.

    Running/queued work takes precedence over prior results, so returning to a
    document makes its current task discoverable. Terminal histories otherwise
    resolve to the most recently updated review. Manuscript and prompt payloads
    never cross this endpoint boundary.
    """
    supplied = document_ids.split(",")
    ids = list(dict.fromkeys(item.strip() for item in supplied))
    if any(not item or len(item) > 100 for item in ids):
        raise HTTPException(422, "document_ids must contain non-empty IDs up to 100 characters")
    if len(ids) > 100:
        raise HTTPException(422, "at most 100 document IDs may be requested")

    requested = set(ids)
    latest: dict[str, dict] = {}
    active: dict[str, dict] = {}
    for review in get_store().list_reviews_for_documents(ids):
        document_id = review.get("document_id")
        if document_id not in requested:
            continue
        if document_id not in latest:
            latest[document_id] = review
        if review.get("status") in {"queued", "running"} and document_id not in active:
            active[document_id] = review

    tasks = []
    for document_id in ids:
        review = active.get(document_id) or latest.get(document_id)
        if review is None:
            continue
        findings = review.get("findings")
        findings = findings if isinstance(findings, list) else []
        tasks.append({
            "document_id": document_id,
            "review_id": review.get("id"),
            "format_run_id": review.get("format_run_id"),
            "status": review.get("status"),
            "pending_count": sum(1 for item in findings
                                 if isinstance(item, dict) and item.get("decision") == "pending"),
            "finding_count": len(findings),
            "updated_at": review.get("updated_at") or review.get("created_at"),
        })
    return {"tasks": tasks}


@router.get("/agent-reviews/{review_id}")
def get_review(review_id: str):
    return public_review(review_or_404(review_id))


@router.get("/agent-reviews/{review_id}/events")
async def review_events(review_id: str, request: Request, after: int = 0):
    review_or_404(review_id)
    try:
        cursor = max(after, int(request.headers.get("last-event-id", "0")))
    except ValueError:
        raise HTTPException(422, "invalid event cursor")

    async def stream():
        nonlocal cursor
        while not await request.is_disconnected():
            events = get_store().events(review_id, cursor)
            for event in events:
                cursor = event["id"]
                yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if review_or_404(review_id)["status"] in TERMINAL:
                return
            if not events:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.5)
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/agent-reviews/{review_id}/cancel")
async def cancel_review(review_id: str):
    review = review_or_404(review_id)
    if review["status"] in TERMINAL:
        return public_review(review)
    if review_id in CANCELLATIONS:
        CANCELLATIONS[review_id].set()
    task = TASKS.get(review_id)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    review = review_or_404(review_id)
    if review["status"] not in TERMINAL:
        review["status"] = "cancelled"
        get_store().put_review(review)
        get_store().add_event(review_id, "review_cancelled", {"status": "cancelled"})
    return public_review(review)


@router.post("/agent-reviews/{review_id}/retry", status_code=202)
async def retry_review(review_id: str, payload: SnapshotGuard):
    review = review_or_404(review_id)
    if review["status"] not in {"failed", "partial", "cancelled"}:
        raise HTTPException(409, "只有失败、部分完成或已取消的审查可以重试。")
    if PROVIDER_OVERRIDE is None and not agent_config()["configured"]:
        raise HTTPException(503, agent_config().get("reason"))
    with main().REVISION_LOCK:
        snapshot_for(review["format_run_id"], payload.expected_source_hash, payload.expected_preview_hash)
        if payload.expected_preview_hash != review["preview_hash"]:
            raise HTTPException(409, "旧审查不能重试到新的正文版本。")
        review.update(status="queued", error=None, completed_at=None)
        get_store().put_review(review)
        get_store().add_event(review_id, "review_retry", {"status": "queued"})
    schedule(review_id, review.get("role_results"))
    return public_review(review)


def format_run_from_version(old_run, version, run_id: str | None = None):
    module = main()
    run_id = run_id or "run-" + version["id"]
    if run_id in module.RUNS:
        return module.RUNS[run_id]
    run = old_run.model_copy(update={"id": run_id, "document_version_id": version["id"],
        "base_version_id": version["id"], "current_version_id": version["id"],
        "source_hash": version["hash"], "changes_count": 0, "accepted_count": 0, "rejected_count": 0,
        "created_at": module.now_iso(), "completed_at": module.now_iso()})
    changes = module.make_content_changes(run_id, version["id"], version["content"], old_run.requested_rules or None)
    run = run.model_copy(update={"changes_count": len(changes)})
    proposed_runs = {**module.RUNS, run_id: run}
    proposed_changes = {**module.CHANGES, **{item.id: item for item in changes}}
    proposed_sources = {**module.SOURCES, run_id: version["content"]}
    get_store().save_format_state(
        {key: item.model_dump(mode="json") for key, item in proposed_runs.items()},
        {key: item.model_dump(mode="json") for key, item in proposed_changes.items()}, proposed_sources,
    )
    module.RUNS[run_id] = run
    module.CHANGES.update({item.id: item for item in changes})
    module.SOURCES[run_id] = version["content"]
    return run


def followup_review(run, baseline: str, materials: list, review_id: str):
    existing = get_store().get_review(review_id)
    if existing is not None:
        return existing
    module = main()
    return new_review(run, module.get_preview(run.id), module.get_revision_review(run.id),
                      materials, review_id=review_id, comparison_base=baseline)


@router.post("/agent-reviews/{review_id}/findings/{finding_id}/decision")
async def decide_finding(review_id: str, finding_id: str, payload: FindingDecision):
    module = main()
    with module.REVISION_LOCK:
        review = review_or_404(review_id)
        if review["status"] not in {"completed", "partial"}:
            raise HTTPException(409, "请等待审查结束后处理意见。")
        finding = next((item for item in review["findings"] if item["id"] == finding_id), None)
        if finding is None:
            raise HTTPException(404, "finding not found")
        existing = get_store().get_decision(review_id, finding_id)
        if existing:
            if existing["decision"] != payload.decision:
                raise HTTPException(409, "该意见已经处理；撤销请使用版本历史。")
            result = existing["result"]
            version = result.get("version")
            target_run = module.RUNS.get(result.get("format_run_id"))
            if version and target_run is None:
                target_run = format_run_from_version(module.RUNS[review["format_run_id"]], version, result.get("format_run_id"))
            finding["decision"] = "accepted" if payload.decision == "accept" else "rejected"
            get_store().put_review(review)
            followup = followup_review(target_run, review["snapshot"]["preview"], review["snapshot"]["materials"],
                                       result.get("followup_review_id", "review-followup-" + version["id"])) if version else None
            return {"review": public_review(review), "format_run_id": target_run.id if target_run else None, "version": version,
                    "followup_review": public_review(followup) if followup else None}
        run, preview, report = snapshot_for(review["format_run_id"], payload.expected_source_hash, payload.expected_preview_hash)
        if report.preview_hash != review["preview_hash"]:
            raise HTTPException(409, "审查版本已经过期，请重新审查。")
        version = None
        new_run = None
        followup = None
        if payload.decision == "accept":
            if not finding.get("applicable") or finding.get("verdict") != "supported":
                raise HTTPException(409, "该意见缺少可验证依据或唯一锚点，只能人工复核。")
            start, end = finding.get("start"), finding.get("end")
            mapping = module.utf16_index_map(preview.preview)
            a, b = mapping.get(start), mapping.get(end)
            if a is None or b is None or preview.preview[a:b] != finding["quote"] or finding.get("replacement") is None:
                raise HTTPException(409, "意见锚点已经失效。")
            content = preview.preview[:a] + finding["replacement"] + preview.preview[b:]
            if len(content) > 300_000:
                raise HTTPException(422, "接受后的正文不能超过 300000 字符。")
            new_run_id = "run-" + uuid4().hex[:10]
            followup_id = "review-" + uuid4().hex
            try:
                decision = get_store().commit_version_decision(
                    review_id, finding_id, payload.decision, run.document_id, content,
                    expected_head_hash=payload.expected_preview_hash, origin="agent_accept",
                    result={"format_run_id": new_run_id, "followup_review_id": followup_id},
                )
            except ValueError as error:
                raise HTTPException(409, "文档版本或决定已变化，请刷新。") from error
            version = decision["result"]["version"]
            new_run = format_run_from_version(run, version, new_run_id)
            followup = followup_review(new_run, preview.preview, review["snapshot"]["materials"], followup_id)
        else:
            get_store().save_decision(review_id, finding_id, payload.decision, {})
        finding["decision"] = "accepted" if payload.decision == "accept" else "rejected"
        finding["decided_at"] = module.now_iso()
        get_store().put_review(review)
        get_store().add_event(review_id, "finding_decided", {"finding_id": finding_id, "decision": finding["decision"],
                                                         "version_id": version["id"] if version else None})
        return {"review": public_review(review), "format_run_id": new_run.id if new_run else None, "version": version,
                "followup_review": public_review(followup) if followup else None}


@router.get("/format-runs/{run_id}/versions")
def version_history(run_id: str):
    run = main().RUNS.get(run_id)
    if run is None:
        raise HTTPException(404, "format run not found")
    return get_store().list_versions(run.document_id)


@router.post("/format-runs/{run_id}/versions/{version_id}/restore")
async def restore_version(run_id: str, version_id: str, payload: SnapshotGuard):
    with main().REVISION_LOCK:
        # Recovery must also work from an intentionally empty current version.
        # The atomic commit still validates the durable current-head hash.
        module = main()
        run, preview, _ = snapshot_for(run_id, payload.expected_source_hash, payload.expected_preview_hash, require_preflight=False)
        version = get_store().get_version(version_id)
        if version is None or version["document_id"] != run.document_id:
            raise HTTPException(404, "document version not found")
        new_run = module._create_format_run(module.FormatRunCreate(
            profile_id=run.profile_id, document_version_id="restore-" + uuid4().hex,
            document_name=run.document_name, document_type=run.document_type,
            document_id=run.document_id, expected_head_hash=payload.expected_preview_hash,
            content=version["content"], requested_rules=run.requested_rules or None,
        ), origin="history_restore")
        restored = get_store().get_version(new_run.current_version_id)
        previous = get_store().list_reviews(run_id)
        materials = previous[0]["snapshot"]["materials"] if previous else []
        followup = followup_review(new_run, preview.preview, materials, "review-followup-" + restored["id"])
        return {"format_run_id": new_run.id, "version": restored, "followup_review": public_review(followup)}
