"""Persist search provenance; add only server-observed results to a document."""
from __future__ import annotations

import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.agent_api import get_store
from app.assistance_api import _guard, _state, _save
from app.literature import LiteratureQuery, digest, now, search_literature

router = APIRouter(prefix="/literature", tags=["literature"])


def read_record(connection, namespace, key, default=None):
    row = connection.execute("SELECT payload FROM records WHERE namespace=? AND record_id=?", (namespace, key)).fetchone()
    return json.loads(row["payload"]) if row else default


def write_record(connection, namespace, key, value):
    connection.execute("INSERT INTO records(namespace,record_id,payload,updated_at) VALUES(?,?,?,?) "
                       "ON CONFLICT(namespace,record_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                       (namespace, key, json.dumps(value, ensure_ascii=False, allow_nan=False), now()))


@router.get("/documents/{document_id}")
def document_literature(document_id: str):
    with get_store()._transaction() as connection:
        _guard(connection, document_id)
        state = read_record(connection, "literature", document_id, {"searches": [], "sources": []})
        retained = {m["id"] for m in _state(connection, document_id)["materials"]}
        state["sources"] = [source for source in state["sources"] if source.get("material_id") in retained]
        return {"document_id": document_id, **state}


@router.post("/documents/{document_id}/search")
async def search(document_id: str, payload: LiteratureQuery):
    with get_store()._transaction() as connection:
        _guard(connection, document_id)
    result = await run_in_threadpool(search_literature, payload)
    result["id"] = "search-" + uuid4().hex
    with get_store()._transaction() as connection:
        _guard(connection, document_id)
        state = read_record(connection, "literature", document_id, {"searches": [], "sources": []})
        state["searches"] = (state["searches"] + [result])[-12:]
        write_record(connection, "literature", document_id, state)
    return result


class SelectSource(BaseModel):
    search_id: str = Field(max_length=100)
    source_id: str = Field(max_length=100)
    expected_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.post("/documents/{document_id}/sources")
def select_source(document_id: str, payload: SelectSource):
    with get_store()._transaction() as connection:
        document = _guard(connection, document_id, payload.expected_content_hash)
        state = read_record(connection, "literature", document_id, {"searches": [], "sources": []})
        source = next((source for search in state["searches"] if search["id"] == payload.search_id
                       for source in search["results"] if source["id"] == payload.source_id), None)
        if source is None:
            raise HTTPException(404, "这条结果已不在当前文档的检索记录中，请重新检索。")
        materials = _state(connection, document_id)
        existing = next((item for item in materials["materials"] if item.get("literature_id") == source["id"]), None)
        if existing:
            return {"source": source, "material_id": existing["id"], "already_added": True}
        if len(materials["materials"]) >= 20 or sum(m["characters"] for m in materials["materials"]) + len(source["text"]) > 2_000_000:
            raise HTTPException(422, "当前文档的资料已达上限，请移除暂时不用的资料。")
        material_id = "material-" + uuid4().hex
        material = {"id": material_id, "name": source["title"][:255], "kind": "literature", "source_type": "literature",
                    "literature_id": source["id"], "created_at": now(), "source_content_hash": document["content_hash"],
                    "file_hash": digest(source["id"] + source["text_hash"]), "characters": len(source["text"]),
                    "text_level": source["text_level"], "provenance": source,
                    "segments": [{"text": source["text"], "paragraph": 1, "page": None}] if source["text"] else [],
                    "warnings": ["仅保存题录；尚未取得可作为论据的摘要或全文。"] if not source["text"] else ["已取得摘要，尚未取得全文。"]}
        materials["materials"].append(material)
        _save(connection, document_id, materials)
        state["sources"] = [s for s in state["sources"] if s["id"] != source["id"]] + [{**source, "material_id": material_id, "selected_at": now()}]
        write_record(connection, "literature", document_id, state)
        return {"source": source, "material_id": material_id, "already_added": False}
