"""Persistent editable drafts, separate from immutable review/version history."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.agent_api import get_store, main

router = APIRouter(prefix="/workspace", tags=["workspace"])
MAX_IMPORT_BYTES = 10 * 1024 * 1024


class DraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    content: str = Field(max_length=300_000)
    editor_json: dict[str, Any] | None = None
    profile_id: str = Field(default="generic-thesis", min_length=1, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("editor_json")
    @classmethod
    def bounded_editor_state(cls, value):
        if value is None:
            return None
        try:
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError, RecursionError) as error:
            raise ValueError("编辑器内容必须是有限的 JSON 对象。") from error
        if len(encoded.encode("utf-8")) > 2_000_000:
            raise ValueError("编辑器内容不能超过 2 MB。")
        return value


class DraftSave(DraftCreate):
    expected_revision: int = Field(ge=1, strict=True)
    last_run_id: str | None = Field(default=None, min_length=1, max_length=100)


class ActiveDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str | None = Field(max_length=100)


@router.get("")
def get_workspace():
    return get_store().workspace()


@router.post("/documents", status_code=201)
def create_document(payload: DraftCreate):
    if main().resolve_format_profile(payload.profile_id) is None:
        raise HTTPException(422, "学校模板不存在，请重新选择。")
    return get_store().create_workspace_document(payload.name, payload.content, payload.editor_json, payload.profile_id)


@router.post("/import", status_code=201)
async def import_document(request: Request, filename: str = Query(min_length=1, max_length=255),
                          profile_id: str = Query(default="generic-thesis", min_length=1, max_length=100)):
    if main().resolve_format_profile(profile_id) is None:
        raise HTTPException(422, "学校模板不存在，请重新选择。")
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            length = int(declared)
            if length < 0:
                raise ValueError()
        except ValueError as error:
            raise HTTPException(400, "文件长度无效。") from error
        if length > MAX_IMPORT_BYTES:
            raise HTTPException(413, "导入文件不能超过 10 MB。")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_IMPORT_BYTES:
            raise HTTPException(413, "导入文件不能超过 10 MB。")
        data.extend(chunk)
    from app.document_import import parse_document
    try:
        parsed = await run_in_threadpool(parse_document, filename, bytes(data))
        payload = DraftCreate(name=parsed["name"], content=parsed["content"], profile_id=profile_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    document = await run_in_threadpool(get_store().create_workspace_document,
                                       payload.name, payload.content, payload.editor_json, payload.profile_id)
    return {**document, "source_type": parsed["source_type"], "warnings": parsed["warnings"]}


@router.get("/documents/{document_id}")
def get_document(document_id: str):
    document = get_store().get_workspace_document(document_id)
    if document is None:
        raise HTTPException(404, "文档不存在。")
    return document


@router.put("/documents/{document_id}")
def save_document(document_id: str, payload: DraftSave):
    module = main()
    with module.REVISION_LOCK:
        if "profile_id" in payload.model_fields_set and module.resolve_format_profile(payload.profile_id) is None:
            raise HTTPException(422, "学校模板不存在，请重新选择。")
        if payload.last_run_id is not None:
            run = module.RUNS.get(payload.last_run_id)
            if run is None or run.document_id != document_id:
                raise HTTPException(409, "扫描记录不属于当前文档。")
        try:
            return get_store().save_workspace_document(
                document_id, payload.name, payload.content, payload.editor_json, payload.expected_revision,
                payload.last_run_id, update_run="last_run_id" in payload.model_fields_set,
                profile_id=payload.profile_id if "profile_id" in payload.model_fields_set else None,
            )
        except KeyError as error:
            raise HTTPException(404, "文档不存在。") from error
        except ValueError as error:
            raise HTTPException(409, "草稿已被更新或扫描记录不匹配，请重新打开文档后再保存。") from error


@router.post("/active")
def set_active(payload: ActiveDocument):
    try:
        return get_store().set_workspace_active(payload.document_id)
    except KeyError as error:
        raise HTTPException(404, "文档不存在。") from error
