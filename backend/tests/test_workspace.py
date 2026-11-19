"""Real SQLite drafts survive restart without changing immutable review heads."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from zipfile import ZipFile

from app import agent_api, main
from app.storage import ReviewStore
from tests.test_agent_api import ASGIClient, ASGIResponse


class WorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "workspace.sqlite3"
        self.store = ReviewStore(self.path)
        self.saved = agent_api.STORE, main.RUNS, main.CHANGES, main.SOURCES
        agent_api.STORE = self.store
        main.RUNS, main.CHANGES, main.SOURCES = {}, {}, {}
        self.client = ASGIClient(main.app)

    async def asyncTearDown(self):
        self.store.close()
        agent_api.STORE, main.RUNS, main.CHANGES, main.SOURCES = self.saved
        self.temp.cleanup()

    async def create(self, name="真实论文.md", content="", editor_json=None):
        response = await self.client.post("/workspace/documents", json={
            "name": name, "content": content, "editor_json": editor_json})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def save(self, document, **values):
        return await self.client.request("PUT", "/workspace/documents/" + document["id"], json_body={
            "name": document["name"], "content": document["content"],
            "editor_json": document["editor_json"], "expected_revision": document["revision"], **values})

    async def scan(self, document, **values):
        return await self.client.post("/format-runs", json={
            "document_id": document["id"], "document_name": document["name"],
            "document_type": "markdown", "content": document["content"], **values})

    async def binary_import(self, filename, data, declared=None, chunk_size=None):
        query = urlencode({"filename": filename}).encode()
        headers = [(b"content-type", b"application/octet-stream")]
        if declared is not None:
            headers.append((b"content-length", str(declared).encode()))
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "http_version": "1.1", "scheme": "http", "method": "POST",
                 "path": "/workspace/import", "raw_path": b"/workspace/import", "query_string": query,
                 "root_path": "", "headers": headers, "client": ("127.0.0.1", 12345),
                 "server": ("testserver", 80)}
        messages = []
        position = 0

        async def receive():
            nonlocal position
            size = chunk_size or max(1, len(data))
            chunk = data[position:position + size]
            position += len(chunk)
            return {"type": "http.request", "body": chunk, "more_body": position < len(data)}

        async def send(message):
            messages.append(message)

        await main.app(scope, receive, send)
        start = next(message for message in messages if message["type"] == "http.response.start")
        content = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        return ASGIResponse(start["status"], content, start["headers"])

    async def test_school_profile_is_per_document_durable_and_old_clients_preserve_it(self):
        created_profile = await self.client.post("/format-profiles", json={
            "name": "学校标题规则", "institution": "测试学院",
            "heading_numbering": ["chinese", "chinese-paren", "arabic-paren", "decimal", "decimal", "decimal"],
        })
        self.assertEqual(created_profile.status_code, 201, created_profile.text)
        profile_id = created_profile.json()["id"]
        response = await self.client.post("/workspace/documents", json={
            "name": "学院论文.md", "content": "# 绪论", "profile_id": profile_id,
        })
        self.assertEqual(response.status_code, 201, response.text)
        document = response.json()
        self.assertEqual(document["profile_id"], profile_id)
        second = await self.create()
        self.assertEqual(second["profile_id"], "generic-thesis")
        saved = await self.save(document, content="# 绪论\n\n正文")
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["profile_id"], profile_id)
        self.store.close()
        self.store = ReviewStore(self.path)
        agent_api.STORE = self.store
        reopened = (await self.client.get("/workspace/documents/" + document["id"])).json()
        self.assertEqual(reopened["profile_id"], profile_id)
        summary = next(item for item in (await self.client.get("/workspace")).json()["documents"] if item["id"] == document["id"])
        self.assertEqual(summary["profile_id"], profile_id)
        changed = await self.save(reopened, profile_id="generic-thesis", last_run_id=None)
        self.assertEqual(changed.status_code, 200, changed.text)
        stale = await self.save(reopened, profile_id=profile_id)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.store.get_workspace_document(document["id"])["profile_id"], "generic-thesis")

    async def test_unknown_school_profile_is_rejected_without_overwriting_draft(self):
        response = await self.client.post("/workspace/documents", json={"name": "论文", "content": "", "profile_id": "missing-profile"})
        self.assertEqual(response.status_code, 422)
        document = await self.create(content="原文保持")
        response = await self.save(document, profile_id="missing-profile", content="不得覆盖")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.store.get_workspace_document(document["id"]), document)

    async def test_existing_workspace_database_migrates_without_changing_document(self):
        path = Path(self.temp.name) / "legacy.sqlite3"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE workspace_documents (id TEXT PRIMARY KEY,name TEXT NOT NULL,content TEXT NOT NULL,editor_json TEXT,document_type TEXT NOT NULL,revision INTEGER NOT NULL,updated_at TEXT NOT NULL,last_run_id TEXT)")
        connection.execute("INSERT INTO workspace_documents VALUES ('legacy','旧论文.md','二、核心接口清单',NULL,'markdown',3,'2026-11-19',NULL)")
        connection.commit(); connection.close()
        migrated = ReviewStore(path)
        try:
            document = migrated.get_workspace_document("legacy")
            self.assertEqual(document["profile_id"], "generic-thesis")
            self.assertEqual(document["content"], "二、核心接口清单")
            self.assertEqual(document["revision"], 3)
        finally:
            migrated.close()

    async def test_fresh_workspace_is_empty_and_does_not_make_demo_or_request_model(self):
        with patch.object(agent_api, "new_review", side_effect=AssertionError("must not call model")):
            response = await self.client.get("/workspace")
            self.assertEqual(response.json(), {"documents": [], "active_document_id": None})
            document = await self.create(name="  新论文.md  " )
            self.assertEqual(document["name"], "新论文.md")
            self.assertEqual(document["content"], "")
            self.assertIsNone(document["editor_json"])
            self.assertEqual(document["document_type"], "markdown")
            self.assertEqual(document["revision"], 1)
            self.assertIsNone(document["last_run_id"])
            self.assertIsNone(document["head_hash"])
            self.assertEqual(main.RUNS, {})
            self.assertEqual(self.store.list_versions(document["id"]), [])
            workspace = (await self.client.get("/workspace")).json()
            self.assertEqual(workspace["active_document_id"], document["id"])
            self.assertNotIn("content", workspace["documents"][0])

    async def test_empty_draft_and_editor_state_survive_reopen(self):
        editor = {"type": "doc", "content": [{"type": "paragraph"}]}
        document = await self.create(content="", editor_json=editor)
        updated = await self.save(document, name="修改后的名称.md")
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["revision"], 2)
        self.store.close()
        self.store = ReviewStore(self.path)
        agent_api.STORE = self.store
        actual = (await self.client.get("/workspace/documents/" + document["id"])).json()
        self.assertEqual(actual, updated.json())
        self.assertEqual(actual["editor_json"], editor)
        self.assertEqual((await self.client.get("/workspace")).json()["active_document_id"], document["id"])

    async def test_cas_rejects_stale_revision_without_overwriting_new_content(self):
        document = await self.create(content="最初草稿")
        saved = await self.save(document, content="新的草稿")
        self.assertEqual(saved.status_code, 200)
        stale = await self.save(document, content="过期请求覆盖")
        self.assertEqual(stale.status_code, 409)
        actual = (await self.client.get("/workspace/documents/" + document["id"])).json()
        self.assertEqual(actual, saved.json())

    async def test_document_isolation_active_selection_and_missing_id(self):
        first, second = await self.create(content="第一篇"), await self.create(content="第二篇")
        self.assertEqual((await self.save(first, content="第一篇变更")).status_code, 200)
        self.assertEqual((await self.client.get("/workspace/documents/" + second["id"])).json(), second)
        response = await self.client.post("/workspace/active", json={"document_id": first["id"]})
        self.assertEqual(response.json(), {"active_document_id": first["id"]})
        self.assertEqual((await self.client.post("/workspace/active", json={"document_id": "missing"})).status_code, 404)
        self.assertEqual((await self.client.get("/workspace")).json()["active_document_id"], first["id"])
        self.assertEqual((await self.client.post("/workspace/active", json={"document_id": None})).json(), {"active_document_id": None})
        self.assertEqual((await self.client.get("/workspace/documents/missing")).status_code, 404)

    async def test_first_scan_uses_workspace_id_then_head_compare_and_swap_is_required(self):
        document = await self.create(content="参考文献\n- Entry. 2024.  \n")
        scanned = await self.scan(document)
        self.assertEqual(scanned.status_code, 200, scanned.text)
        self.assertEqual(scanned.json()["document_id"], document["id"])
        metadata = (await self.client.get("/workspace/documents/" + document["id"])).json()
        self.assertIsNotNone(metadata["head_hash"])
        self.assertEqual(metadata["content"], document["content"])
        self.assertEqual((await self.scan(document)).status_code, 409)
        self.assertEqual((await self.scan(document, expected_head_hash="0" * 64)).status_code, 409)
        self.assertEqual((await self.scan(document, expected_head_hash=metadata["head_hash"])).status_code, 200)
        unknown = {**document, "id": "unknown-workspace-id"}
        self.assertEqual((await self.scan(unknown)).status_code, 409)

    async def test_draft_does_not_change_version_head_and_last_run_must_match_document(self):
        document = await self.create(content="参考文献\n- Entry. 2024.  \n")
        run = (await self.scan(document)).json()
        head = self.store.get_head(document["id"])
        saved = await self.save(document, content="尚未扫描的全新草稿", last_run_id=run["id"])
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["last_run_id"], run["id"])
        self.assertEqual(self.store.get_head(document["id"]), head)
        other = await self.create(content="另一篇")
        self.assertEqual((await self.save(other, last_run_id=run["id"])).status_code, 409)
        self.assertEqual((await self.save(other, last_run_id="run-missing")).status_code, 409)
        kept = (await self.save(saved.json(), content="继续编辑")).json()
        self.assertEqual(kept["last_run_id"], run["id"])
        cleared = (await self.save(kept, last_run_id=None)).json()
        self.assertIsNone(cleared["last_run_id"])
        self.assertEqual(self.store.get_head(document["id"]), head)

    async def test_live_head_hash_updates_after_real_accept_without_overwriting_draft(self):
        document = await self.create(content="参考文献\n- Entry. 2024.  \n")
        run = (await self.scan(document)).json()
        old_hash = self.store.get_head(document["id"])["hash"]
        changes = (await self.client.get(f"/format-runs/{run['id']}/changes")).json()
        reference = next(item for item in changes if item["rule_id"] == "reference.gb7714")
        response = await self.client.post(f"/format-changes/{reference['id']}/decision", json={"decision": "accept"})
        self.assertEqual(response.status_code, 200)
        draft = (await self.client.get("/workspace/documents/" + document["id"])).json()
        self.assertEqual(draft["content"], document["content"])
        self.assertNotEqual(draft["head_hash"], old_hash)
        self.assertEqual(draft["head_hash"], self.store.get_head(document["id"])["hash"])
        self.assertEqual(draft["revision"], 1)

    async def test_name_content_editor_and_revision_limits_are_enforced(self):
        for body in [{"name": "  ", "content": ""}, {"name": "x" * 256, "content": ""},
                     {"name": "论文", "content": "x" * 300_001},
                     {"name": "论文", "content": "", "editor_json": {"value": "x" * 2_000_001}}]:
            self.assertEqual((await self.client.post("/workspace/documents", json=body)).status_code, 422)
        document = await self.create()
        self.assertEqual((await self.save(document, expected_revision=True)).status_code, 422)
        self.assertEqual((await self.save(document, expected_revision=0)).status_code, 422)

    async def test_two_sqlite_connections_allow_only_one_matching_revision_write(self):
        document = await self.create(content="first")
        second = ReviewStore(self.path)
        try:
            def save(store, value):
                try:
                    return store.save_workspace_document(document["id"], "论文", value, None, 1)
                except ValueError:
                    return "conflict"
            with ThreadPoolExecutor(max_workers=2) as pool:
                tasks = [pool.submit(save, self.store, "A"), pool.submit(save, second, "B")]
                results = [task.result() for task in tasks]
            self.assertEqual(sum(result == "conflict" for result in results), 1)
            self.assertEqual(self.store.get_workspace_document(document["id"])["revision"], 2)
        finally:
            second.close()

    async def test_first_head_creation_conflict_does_not_mutate_draft_or_format_state(self):
        document = await self.create(content="草稿")
        self.store.create_version(document["id"], "已存在的版本")
        before = self.store.load_format_state()
        with self.assertRaises(ValueError):
            self.store.commit_format_state({"r": {"id": "r"}}, {}, {"r": "草稿"}, "r",
                document["id"], "草稿", "scan", expected_head_absent=True)
        self.assertEqual(self.store.load_format_state(), before)
        self.assertEqual(self.store.get_head(document["id"])["content"], "已存在的版本")

    async def test_real_markdown_text_and_docx_imports_create_active_durable_documents(self):
        archive = BytesIO()
        with ZipFile(archive, "w") as zipped:
            zipped.writestr("[Content_Types].xml",
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                '</Types>')
            zipped.writestr("_rels/.rels",
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                '</Relationships>')
            zipped.writestr("word/document.xml",
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body><w:p><w:r><w:t>真实 DOCX 正文</w:t></w:r></w:p></w:body></w:document>')
        for filename, data, source_type, expected in [
            ("资料.md", "# 真正的标题\n正文".encode(), "markdown", "真正的标题"),
            ("资料.txt", "真实文本正文".encode(), "text", "真实文本正文"),
            ("资料.docx", archive.getvalue(), "docx", "真实 DOCX 正文"),
        ]:
            response = await self.binary_import(filename, data, declared=len(data), chunk_size=113)
            self.assertEqual(response.status_code, 201, response.text)
            document = response.json()
            self.assertEqual(document["source_type"], source_type)
            self.assertEqual(document["document_type"], "markdown")
            self.assertIn(expected, document["content"])
            self.assertIsInstance(document["warnings"], list)
            self.assertEqual((await self.client.get("/workspace")).json()["active_document_id"], document["id"])
            self.assertEqual((await self.client.get("/workspace/documents/" + document["id"])).json()["content"], document["content"])
            self.assertIsNone(self.store.get_head(document["id"]))
        self.assertEqual(main.RUNS, {})

    async def test_import_size_header_stream_and_unsupported_pdf_never_create_documents(self):
        from app.workspace import MAX_IMPORT_BYTES
        self.assertEqual((await self.binary_import("large.md", b"tiny", declared=MAX_IMPORT_BYTES + 1)).status_code, 413)
        self.assertEqual((await self.binary_import("large.md", b"x" * (MAX_IMPORT_BYTES + 1), declared=1, chunk_size=262_144)).status_code, 413)
        self.assertEqual((await self.binary_import("invalid.pdf", b"%PDF-fixture")).status_code, 422)
        self.assertEqual((await self.binary_import("bad.docx", b"not a ZIP file")).status_code, 422)
        self.assertEqual((await self.client.get("/workspace")).json(), {"documents": [], "active_document_id": None})


if __name__ == "__main__":
    unittest.main()
