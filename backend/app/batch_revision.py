"""Atomic, snapshot-guarded low-risk formatting batches and exact batch undo."""
import hashlib
import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

router = APIRouter(tags=["batch-revision"])
SAFE_RULES = {"paragraph.spacing", "heading.spacing", "heading.numbering"}


class BatchRequest(BaseModel):
    decision: Literal["accept", "reject", "undo"] = "accept"
    change_ids: list[str] = Field(default_factory=list, max_length=500)
    batch_id: str | None = Field(default=None, max_length=100)
    expected_source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.get("/format-runs/{run_id}/batches")
def list_batches(run_id: str):
    from app import main
    from app.agent_api import get_store
    if run_id not in main.RUNS:
        raise HTTPException(404, "扫描记录不存在。")
    store = get_store()
    with main.REVISION_LOCK, store._lock:
        current = main._get_preview(run_id).preview
        digest = hashlib.sha256(current.encode("utf-8")).hexdigest()
        rows = store._connection.execute("SELECT payload FROM records WHERE namespace='format_batches' ORDER BY updated_at DESC,rowid DESC").fetchall()
        records = [json.loads(row["payload"]) for row in rows]
        return [{"id": record["id"], "count": len(record["before"]), "undone": record["undone"],
                 "can_undo": not record["undone"] and record["after_hash"] == digest}
                for record in records if record["run_id"] == run_id]


@router.post("/format-runs/{run_id}/batch-decisions")
def decide_batch(run_id: str, payload: BatchRequest):
    from app import main
    from app.agent_api import get_store, assert_current_run
    from app.revision_review import checked_edits
    with main.REVISION_LOCK:
        run = main.RUNS.get(run_id)
        if not run:
            raise HTTPException(404, "扫描记录不存在。")
        assert_current_run(run)
        source = main.SOURCES[run_id]
        previous = main._get_preview(run_id).preview
        digest = lambda text: hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest(source) != payload.expected_source_hash or run.source_hash != payload.expected_source_hash or digest(previous) != payload.expected_preview_hash:
            raise HTTPException(409, "正文或建议快照已变化，请刷新后预览该批次。")
        store = get_store()
        proposed = dict(main.CHANGES)
        if payload.decision in {"accept", "reject"}:
            ids = list(dict.fromkeys(payload.change_ids))
            if not ids:
                raise HTTPException(422, "请选择已预览的低风险格式修改。")
            selected = [proposed.get(key) for key in ids]
            if any(change is None or change.run_id != run_id or change.status != "pending" or (payload.decision == "accept" and change.rule_id not in SAFE_RULES) for change in selected):
                raise HTTPException(409, "整批确认仅支持当前文档待处理的低风险格式项；内容与引用修改请逐项审阅。")
            batch_id = "batch-" + uuid4().hex
            record = {"id": batch_id, "run_id": run_id, "before": {change.id: change.model_dump(mode="json") for change in selected}, "undone": False}
            for change in selected:
                proposed[change.id] = change.model_copy(update={"status": main.ChangeStatus.accepted if payload.decision == "accept" else main.ChangeStatus.rejected, "decided_at": main.now_iso()})
        else:
            with store._lock:
                row = store._connection.execute("SELECT payload FROM records WHERE namespace='format_batches' AND record_id=?", (payload.batch_id,)).fetchone()
            record = json.loads(row["payload"]) if row else None
            if not record or record["run_id"] != run_id or record["undone"] or record["after_hash"] != digest(previous):
                raise HTTPException(409, "该批次已撤销或后续内容已变化，请使用版本恢复。")
            batch_id = record["id"]
            for key, value in record["before"].items():
                current = proposed.get(key)
                if current is None or current.model_dump(mode="json") != record["after"][key]:
                    raise HTTPException(409, "批次内的建议已被单独处理，不能覆盖新决定。")
                proposed[key] = main.FormatChange.model_validate(value)
            record["undone"] = True
        changes = [item for item in proposed.values() if item.run_id == run_id]
        _, blocked = checked_edits(source, changes, run.document_version_id)
        if blocked:
            raise HTTPException(409, "批次中存在范围冲突，整批未应用：" + "; ".join(blocked.values()))
        preview, _ = main.build_preview(source, changes, run.document_version_id)
        if payload.decision in {"accept", "reject"}:
            record["after_hash"] = digest(preview)
            record["after"] = {key: proposed[key].model_dump(mode="json") for key in record["before"]}
        updated = run.model_copy(update={"accepted_count": sum(item.status == "accepted" for item in changes), "rejected_count": sum(item.status == "rejected" for item in changes)})
        try:
            result = store.commit_format_state(
                {key: item.model_dump(mode="json") for key, item in {**main.RUNS, run_id: updated}.items()},
                {key: item.model_dump(mode="json") for key, item in proposed.items()}, dict(main.SOURCES),
                run_id, run.document_id, preview, "format_batch_" + payload.decision,
                expected_head_hash=digest(previous), create_version=preview != previous,
                history_record=("format_batches", batch_id, record),
            )
        except ValueError as error:
            raise HTTPException(409, "版本已变化，批次未应用。") from error
        main.RUNS[run_id] = main.FormatRun.model_validate(result["run"])
        main.CHANGES.clear()
        main.CHANGES.update(proposed)
        return {"batch_id": batch_id, "decision": payload.decision, "count": len(record["before"]),
                "recheck": main.get_revision_review(run_id).model_dump(mode="json")}
