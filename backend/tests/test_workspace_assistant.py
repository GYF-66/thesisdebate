"""Real API/storage orchestration; only network and model boundaries are injected."""
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from app import agent_api, main, workspace_assistant as assistant
from app.agent_engine import align_paragraphs, consolidate_findings
from app.literature import LiteratureQuery, _source, digest, search_literature
from app.storage import ReviewStore
from tests.test_agent_api import ASGIClient


class LiteratureTests(unittest.TestCase):
    def test_merge_doi_keeps_observed_abstract_and_providers(self):
        crossref = _source({"title": ["Learning analytics"], "DOI": "10.1234/ABC", "published": {"date-parts": [[2024]]}}, "Crossref", "analytics")
        openalex = _source({"title": "Learning analytics", "doi": "https://doi.org/10.1234/abc", "publication_year": 2024,
                           "abstract_inverted_index": {"Observed": [0], "abstract": [1]}}, "OpenAlex", "analytics")
        def provider(name, query):
            return {"results": [crossref if name == "Crossref" else openalex], "has_more": True}
        with patch("app.literature._provider_search", side_effect=provider):
            result = search_literature(LiteratureQuery(query="analytics"))
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["providers"], ["Crossref", "OpenAlex"])
        self.assertEqual(result["results"][0]["text"], "Observed abstract")
        self.assertEqual(result["results"][0]["text_hash"], digest("Observed abstract"))
        self.assertTrue(result["has_more"])

    def test_partial_429_keeps_other_provider_and_hides_error_payload(self):
        def provider(name, query):
            if name == "OpenAlex":
                raise HTTPError("https://provider/?secret=not-public", 429, "private", {}, None)
            return {"results": [_source({"title": ["A source"]}, "Crossref", query.query)], "has_more": False}
        with patch("app.literature._provider_search", side_effect=provider):
            result = search_literature(LiteratureQuery(query="analytics"))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["results"]), 1)
        self.assertIn("请求频繁", result["warnings"][0])
        self.assertNotIn("not-public", json.dumps(result))

    def test_timeout_and_empty_are_distinct(self):
        with patch("app.literature._provider_search", side_effect=TimeoutError):
            self.assertEqual(search_literature(LiteratureQuery(query="topic"))["status"], "failed")
        with patch("app.literature._provider_search", return_value={"results": [], "has_more": False}):
            self.assertEqual(search_literature(LiteratureQuery(query="topic"))["status"], "completed")

    def test_invalid_year_and_unsafe_url(self):
        with self.assertRaises(ValueError):
            LiteratureQuery(query="topic", year_from=2025, year_to=2020)
        source = _source({"title": ["<b>A title</b>"], "URL": "javascript:alert(1)"}, "Crossref", "topic")
        self.assertEqual(source["url"], "")
        self.assertEqual(source["text_level"], "metadata")


class AlignmentTests(unittest.TestCase):
    def test_planner_keeps_search_terms_and_mentor_feedback_as_data(self):
        self.assertEqual([a.kind for a in assistant.local_actions("查找文献：方法论与创新点")], ["literature"])
        self.assertEqual([a.kind for a in assistant.local_actions("导师意见：检查方法论并调整字号")], ["mentor"])
        self.assertEqual([a.kind for a in assistant.local_actions("检查论文，然后导出")], ["check", "export"])
        self.assertEqual([a.kind for a in assistant.local_actions("根据资料回答研究方法是什么")], ["question"])

    def test_moved_paragraph_is_exact_not_missing(self):
        result = align_paragraphs("第一段内容。\n\n第二段包含数字 120。", "第二段包含数字 120。\n\n第一段内容。")
        self.assertEqual([p["status"] for p in result], ["exact_moved", "exact_moved"])
        self.assertEqual(result[0]["original_quote"], "第二段包含数字 120。")

    def test_repeated_paragraph_is_ambiguous(self):
        self.assertEqual(align_paragraphs("重复。\n\n重复。", "重复。")[0]["status"], "ambiguous")

    def test_dedup_retains_role_evidence_and_blocks_conflicting_edits(self):
        item = {"id": "a", "agent_id": "methods", "title": "表达", "rationale": "依据一", "evidence": [],
                "quote": "原文", "replacement": "修改一", "severity": "medium", "verdict": "supported", "applicable": True}
        merged = consolidate_findings([item, {**item, "id": "b", "agent_id": "norms", "rationale": "依据二"}])
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]["contributions"]), 2)
        conflicts = consolidate_findings([item, {**item, "id": "c", "replacement": "修改二"}])
        self.assertTrue(all(not f["applicable"] and f["verdict"] == "conflict" for f in conflicts))


class AssistantAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.store = ReviewStore(Path(self.temp.name) / "test.db")
        self.saved = (agent_api.STORE, agent_api.TASKS, agent_api.CANCELLATIONS, agent_api.PROVIDER_OVERRIDE, main.RUNS, main.CHANGES, main.SOURCES, assistant.RUNNING)
        agent_api.STORE, agent_api.TASKS, agent_api.CANCELLATIONS, agent_api.PROVIDER_OVERRIDE = self.store, {}, {}, None
        main.RUNS, main.CHANGES, main.SOURCES, assistant.RUNNING = {}, {}, {}, {}
        self.configuration = patch("app.agent_settings.public_config", return_value={"configured": False, "reason": "测试未配置模型"})
        self.configuration.start()
        self.client = ASGIClient(main.app)
        self.content = "# 初稿\n\n## 1.方法\n\n这里介绍课程研究。   \n\n## 2.总结\n\n后续需要验证。\n"
        self.doc = self.store.create_workspace_document("练习.md", self.content)
        self.root = "/workspace-assistant/documents/" + self.doc["id"]

    async def asyncTearDown(self):
        for event in agent_api.CANCELLATIONS.values():
            event.set()
        processes = [*assistant.RUNNING.values(), *agent_api.TASKS.values()]
        for process in processes:
            process.cancel()
        await asyncio.gather(*processes, return_exceptions=True)
        self.configuration.stop()
        self.store.close()
        (agent_api.STORE, agent_api.TASKS, agent_api.CANCELLATIONS, agent_api.PROVIDER_OVERRIDE, main.RUNS, main.CHANGES, main.SOURCES, assistant.RUNNING) = self.saved
        self.temp.cleanup()

    async def task(self, message, request_id="request-test-1"):
        response = await self.client.post(self.root + "/tasks", {"message": message, "request_id": request_id, "expected_content_hash": digest(self.content)})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def settled(self, id):
        process = assistant.RUNNING.get(id)
        if process:
            await asyncio.wait_for(asyncio.shield(process), 8)
        result = await self.client.get(self.root)
        return next(t for t in result.json()["tasks"] if t["id"] == id)

    async def test_check_then_export_runs_real_checker_once(self):
        task = await self.task("检查论文，然后导出")
        result = await self.settled(task["id"])
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual([s["kind"] for s in result["steps"]], ["check", "export"])
        self.assertGreater(result["steps"][0]["result"]["changes_count"], 0)
        self.assertEqual(result["steps"][1]["result"]["action"], "export")
        self.assertEqual(self.store.get_workspace_document(self.doc["id"])["content"], self.content)
        duplicate = await self.task("检查论文，然后导出")
        self.assertEqual(task["id"], duplicate["id"])
        self.assertEqual(len(main.RUNS), 1)

    async def test_layout_proposal_requires_confirmation_and_is_idempotent(self):
        task = await self.task("正文行距改为2倍")
        result = await self.settled(task["id"])
        self.assertEqual(result["status"], "needs_confirmation", result)
        before = (await self.client.get(f"/workspace/documents/{self.doc['id']}/layout")).json()
        self.assertEqual(before["style"]["line_spacing"], 1.5)
        path = f"{self.root}/tasks/{task['id']}/steps/{result['steps'][0]['id']}/apply"
        applied = await self.client.post(path, {})
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertEqual(applied.json()["style"]["line_spacing"], 2)
        self.assertEqual((await self.client.post(path, {})).json(), applied.json())
        self.assertEqual(self.store.get_workspace_document(self.doc["id"])["content"], self.content)

    async def test_changed_draft_blocks_old_proposal(self):
        task = await self.task("正文行距改为2倍")
        result = await self.settled(task["id"])
        self.store.save_workspace_document(self.doc["id"], "练习.md", self.content + "已修改", None, 1)
        path = f"{self.root}/tasks/{task['id']}/steps/{result['steps'][0]['id']}/apply"
        self.assertEqual((await self.client.post(path, {})).status_code, 409)
        self.assertTrue((await self.client.get(self.root)).json()["tasks"][0]["stale"])

    async def test_resume_model_failure_reuses_completed_check(self):
        task = await self.task("深度检查论文")
        failed = await self.settled(task["id"])
        self.assertEqual(failed["status"], "failed", failed)
        run_id = failed["steps"][0]["result"]["run_id"]
        async def provider(role, instructions, context):
            return {"summary": "已检查", "assessments": [], "challenges": []} if role == "judge" else {"summary": "已检查", "findings": []}
        agent_api.PROVIDER_OVERRIDE = provider
        self.assertEqual((await self.client.post(f"{self.root}/tasks/{task['id']}/resume", {})).status_code, 200)
        completed = await self.settled(task["id"])
        self.assertEqual(completed["status"], "completed", completed)
        self.assertEqual(completed["steps"][0]["result"]["run_id"], run_id)
        self.assertEqual(len(main.RUNS), 1)
        self.assertEqual(agent_api.get_review(completed["steps"][1]["result"]["review_id"])["status"], "completed")

    async def test_cancel_then_resume_and_cross_document_isolation(self):
        task = await self.task("检查论文，然后导出")
        await self.client.post(f"{self.root}/tasks/{task['id']}/cancel", {})
        cancelled = await self.settled(task["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        await self.client.post(f"{self.root}/tasks/{task['id']}/resume", {})
        self.assertEqual((await self.settled(task["id"]))["status"], "completed")
        other = self.store.create_workspace_document("另一篇.md", "其他内容")
        response = await self.client.post(f"/workspace-assistant/documents/{other['id']}/tasks/{task['id']}/resume", {})
        self.assertEqual(response.status_code, 404)
        self.assertFalse((await self.client.get("/workspace-assistant/documents/" + other["id"])).json()["tasks"])

    async def test_restart_marks_running_task_resumable(self):
        task = await self.task("检查论文")
        await self.settled(task["id"])
        from app.literature_api import read_record, write_record
        with self.store._transaction() as connection:
            stored = read_record(connection, "assistant_tasks", task["id"])
            stored["status"] = "running"
            write_record(connection, "assistant_tasks", task["id"], stored)
        self.assertEqual((await self.client.get(self.root)).json()["tasks"][0]["status"], "paused")

    async def test_select_source_uses_server_search_and_metadata_has_no_evidence(self):
        source = _source({"title": ["Metadata only"], "DOI": "10.1234/abc"}, "Crossref", "topic")
        response = {"results": [source], "status": "completed", "warnings": [], "has_more": False, "query": "topic", "page": 1}
        route = "/literature/documents/" + self.doc["id"]
        with patch("app.literature_api.search_literature", return_value=response):
            result = (await self.client.post(route + "/search", {"query": "topic"})).json()
        request = {"search_id": result["id"], "source_id": source["id"], "expected_content_hash": digest(self.content)}
        added = await self.client.post(route + "/sources", request)
        self.assertEqual(added.status_code, 200, added.text)
        self.assertTrue((await self.client.post(route + "/sources", request)).json()["already_added"])
        from app.assistance_api import _state
        with self.store._transaction() as connection:
            materials = _state(connection, self.doc["id"])["materials"]
        self.assertEqual(len(materials), 1)
        self.assertEqual(materials[0]["segments"], [])
        self.assertEqual(materials[0]["provenance"]["query"], "topic")
        request["source_id"] = "forged"
        self.assertEqual((await self.client.post(route + "/sources", request)).status_code, 404)

    async def test_mentor_task_is_real_and_retained_on_duplicate_request(self):
        task = await self.task("导师意见：请补充样本选择的依据。")
        result = await self.settled(task["id"])
        self.assertEqual(result["status"], "completed", result)
        state = (await self.client.get(f"/document-assistance/documents/{self.doc['id']}/state")).json()
        self.assertEqual(len(state["mentor_tasks"]), 1, state)
        await self.task("导师意见：请补充样本选择的依据。")
        state = (await self.client.get(f"/document-assistance/documents/{self.doc['id']}/state")).json()
        self.assertEqual(len(state["mentor_tasks"]), 1)


if __name__ == "__main__":
    unittest.main()
