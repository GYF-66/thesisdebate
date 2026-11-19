"""Offline API integration tests with a real temporary durable repository.

Only the provider boundary is injected. Routing, guards, engine execution, event
replay, immutable versions, decisions and follow-up scheduling stay production.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app import agent_api as api
from app import main
from app.agent_engine import AGENT_ROLES
from app.storage import ReviewStore


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ASGIResponse:
    def __init__(self, status_code, body, headers):
        self.status_code = status_code
        self.text = body.decode("utf-8")
        self.headers = {key.decode("latin1"): value.decode("latin1") for key, value in headers}

    def json(self):
        return json.loads(self.text)


class ASGIClient:
    """Minimal in-process HTTP transport; no network or optional httpx dependency."""
    def __init__(self, app):
        self.app = app

    async def request(self, method, target, json_body=None, headers=None):
        path, _, query = target.partition("?")
        body = json.dumps(json_body, ensure_ascii=False).encode("utf-8") if json_body is not None else b""
        request_headers = [(b"content-type", b"application/json")]
        request_headers.extend((key.lower().encode("latin1"), value.encode("latin1"))
                               for key, value in (headers or {}).items())
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "http_version": "1.1", "scheme": "http", "method": method,
                 "path": path, "raw_path": path.encode(), "query_string": query.encode(),
                 "root_path": "", "headers": request_headers,
                 "client": ("127.0.0.1", 12345), "server": ("testserver", 80)}
        sent, messages = False, []

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.Event().wait()

        async def send(message):
            messages.append(message)

        await self.app(scope, receive, send)
        start = next(item for item in messages if item["type"] == "http.response.start")
        content = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
        return ASGIResponse(start["status"], content, start["headers"])

    async def post(self, target, json=None):
        return await self.request("POST", target, json_body=json)

    async def get(self, target, headers=None):
        return await self.request("GET", target, headers=headers)

    async def aclose(self):
        pass


class AgentAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "reviews.sqlite3"
        self.store = ReviewStore(self.path)
        self.saved = (api.STORE, api.TASKS, api.CANCELLATIONS, api.PROVIDER_OVERRIDE,
                      main.RUNS, main.CHANGES, main.SOURCES)
        api.STORE, api.TASKS, api.CANCELLATIONS = self.store, {}, {}
        api.PROVIDER_OVERRIDE = self.provider
        main.RUNS, main.CHANGES, main.SOURCES = {}, {}, {}
        self.calls = []
        self.client = ASGIClient(main.app)

    async def asyncTearDown(self):
        try:
            for cancel in list(api.CANCELLATIONS.values()):
                cancel.set()
            tasks = list(api.TASKS.values())
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5)
            await self.client.aclose()
            self.store.close()
        finally:
            (api.STORE, api.TASKS, api.CANCELLATIONS, api.PROVIDER_OVERRIDE,
             main.RUNS, main.CHANGES, main.SOURCES) = self.saved
            self.temp.cleanup()

    async def provider(self, role, instructions, context):
        self.calls.append((role, context["phase"], context.get("window_index")))
        if role == "judge":
            return {"summary": "已按字面证据复核。", "challenges": [],
                    "assessments": [{"finding_id": item["id"], "verdict": "supported",
                                     "rationale": "证据与锚点一致。"}
                                    for item in context["findings"]]}
        findings = []
        if role == "methods" and "研究方法" in context["manuscript"]["content"]:
            findings.append({"title": "明确研究设计", "severity": "medium",
                             "quote": "研究方法", "replacement": "研究设计",
                             "rationale": "统一此处描述。", "verdict": "supported",
                             "evidence": [{"source_id": "manuscript", "quote": "研究方法"}]})
        return {"summary": role + "已检查当前窗口。", "findings": findings}

    async def finish(self):
        while api.TASKS:
            await asyncio.wait_for(asyncio.gather(*list(api.TASKS.values())), timeout=5)

    async def create_run(self, source="😀研究方法：样本10人。\n", accept_spacing=False):
        response = await self.client.post("/format-runs", json={
            "document_type": "markdown", "content": source,
            "requested_rules": ["paragraph.spacing"],
        })
        self.assertEqual(response.status_code, 200, response.text)
        run = response.json()
        if accept_spacing:
            changes = (await self.client.get(f"/format-runs/{run['id']}/changes")).json()
            self.assertEqual(len(changes), 1)
            accepted = await self.client.post(f"/format-changes/{changes[0]['id']}/decision",
                json={"decision": "accept", "expected_source_hash": digest(source)})
            self.assertEqual(accepted.status_code, 200, accepted.text)
            run = (await self.client.get(f"/format-runs/{run['id']}")).json()
        return run

    async def guard(self, run_id):
        response = await self.client.get(f"/format-runs/{run_id}/review")
        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()
        return {"expected_source_hash": report["source_hash"],
                "expected_preview_hash": report["preview_hash"]}

    async def create_review(self, run, finish=True, materials=None):
        guard = await self.guard(run["id"])
        response = await self.client.post("/agent-reviews", json={
            "format_run_id": run["id"], **guard, "materials": materials or [],
        })
        self.assertEqual(response.status_code, 202, response.text)
        review = response.json()
        if finish:
            await self.finish()
            review = (await self.client.get(f"/agent-reviews/{review['id']}")).json()
        return review, guard

    async def accept(self, review, guard):
        self.assertEqual(review["status"], "completed", review)
        self.assertEqual(len(review["findings"]), 1, review)
        finding = review["findings"][0]
        response = await self.client.post(
            f"/agent-reviews/{review['id']}/findings/{finding['id']}/decision",
            json={**guard, "decision": "accept"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_unconfigured_review_creation_is_503_without_task(self):
        run = await self.create_run()
        guard = await self.guard(run["id"])
        api.PROVIDER_OVERRIDE = None
        with patch.dict(os.environ, {key: "" for key in ("AGENT_API_KEY", "OPENAI_API_KEY",
                "AGENT_BASE_URL", "OPENAI_BASE_URL", "AGENT_MODEL", "OPENAI_MODEL")}):
            config = (await self.client.get("/agent-config")).json()
            response = await self.client.post("/agent-reviews", json={"format_run_id": run["id"], **guard})
        self.assertFalse(config["configured"])
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.store.list_reviews(), [])
        self.assertEqual(api.TASKS, {})
        self.assertEqual(self.calls, [])

    async def test_creation_rejects_each_stale_snapshot_hash(self):
        run = await self.create_run()
        guard = await self.guard(run["id"])
        for key in guard:
            response = await self.client.post("/agent-reviews", json={
                "format_run_id": run["id"], **guard, key: "0" * 64})
            self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(api.TASKS, {})

    async def test_all_five_stages_and_durable_sse_cursor_replay(self):
        run = await self.create_run("😀研究方法：样本10人。  \n", accept_spacing=True)
        review, _ = await self.create_review(run, materials=[{"name": "实验记录", "content": "样本10人。"}])
        self.assertEqual(review["status"], "completed")
        self.assertEqual([item["id"] for item in review["agents"]], [item["id"] for item in AGENT_ROLES])
        self.assertTrue(all(item["status"] == "completed" for item in review["agents"]))
        self.assertEqual(set(item[0] for item in self.calls), {item["id"] for item in AGENT_ROLES})
        self.assertTrue(review["coverage"]["complete"])
        self.assertNotIn("snapshot", review)
        self.assertNotIn("role_results", review)
        records = self.store.events(review["id"])
        self.assertEqual(records[0]["type"], "review_queued")
        self.assertEqual(records[-1]["type"], "review_completed")
        self.assertTrue(any(item["type"] == "role_checkpoint" for item in records))
        cursor = records[len(records) // 2]["id"]
        response = await self.client.get(f"/agent-reviews/{review['id']}/events?after=0",
                                          headers={"Last-Event-ID": str(cursor)})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("text/event-stream", response.headers["content-type"])
        replayed = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        self.assertEqual(replayed, [item for item in records if item["id"] > cursor])
        self.assertTrue(all(f"id: {item['id']}\n" in response.text for item in replayed))
        invalid = await self.client.get(f"/agent-reviews/{review['id']}/events", headers={"Last-Event-ID": "bad"})
        self.assertEqual(invalid.status_code, 422)

    async def test_accept_creates_immutable_version_run_and_comparison_followup(self):
        run = await self.create_run()
        old_version = self.store.get_head(run["document_id"])
        review, guard = await self.create_review(run)
        old_preview = self.store.get_review(review["id"])["snapshot"]["preview"]
        result = await self.accept(review, guard)
        await self.finish()
        version = result["version"]
        self.assertNotEqual(result["format_run_id"], run["id"])
        self.assertNotEqual(version["id"], old_version["id"])
        self.assertEqual(version["content"], old_preview.replace("研究方法", "研究设计"))
        self.assertEqual(version["parent_id"], old_version["id"])
        self.assertEqual(version["origin"], "agent_accept")
        self.assertEqual(self.store.get_version(old_version["id"]), old_version)
        self.assertEqual(self.store.get_head(run["document_id"]), version)
        new_run = main.RUNS[result["format_run_id"]]
        self.assertEqual(new_run.current_version_id, version["id"])
        self.assertEqual(main.SOURCES[new_run.id], version["content"])
        followup = self.store.get_review(result["followup_review"]["id"])
        self.assertEqual(followup["status"], "completed")
        self.assertEqual(followup["snapshot"]["source"], old_preview)
        self.assertEqual(followup["comparison_base_hash"], digest(old_preview))
        self.assertEqual(followup["snapshot"]["preview"], version["content"])
        self.assertEqual(followup["findings"], [])

    async def test_same_decision_replay_is_stable_and_opposite_is_409(self):
        run = await self.create_run()
        review, guard = await self.create_review(run)
        first = await self.accept(review, guard)
        await self.finish()
        versions_before = self.store.list_versions(run["document_id"])
        second = await self.accept(review, guard)
        for key in ("version", "format_run_id"):
            self.assertEqual(first[key], second[key])
        self.assertEqual(first["followup_review"]["id"], second["followup_review"]["id"])
        self.assertEqual(self.store.list_versions(run["document_id"]), versions_before)
        self.assertEqual(api.TASKS, {})
        response = await self.client.post(
            f"/agent-reviews/{review['id']}/findings/{review['findings'][0]['id']}/decision",
            json={**guard, "decision": "reject"})
        self.assertEqual(response.status_code, 409)

    async def test_accept_checks_source_and_preview_guards_before_mutation(self):
        run = await self.create_run()
        review, guard = await self.create_review(run)
        original_head = self.store.get_head(run["document_id"])
        for key in guard:
            response = await self.client.post(
                f"/agent-reviews/{review['id']}/findings/{review['findings'][0]['id']}/decision",
                json={**guard, key: "0" * 64, "decision": "accept"})
            self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.store.get_head(run["document_id"]), original_head)
        self.assertEqual(len(self.store.list_versions(run["document_id"])), 1)
        self.assertIsNone(self.store.get_decision(review["id"], review["findings"][0]["id"]))

    async def test_modified_source_fails_integrity_guard(self):
        run = await self.create_run()
        review, guard = await self.create_review(run)
        main.SOURCES[run["id"]] += "外部写入。"
        response = await self.client.post(
            f"/agent-reviews/{review['id']}/findings/{review['findings'][0]['id']}/decision",
            json={**guard, "decision": "accept"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(self.store.list_versions(run["document_id"])), 1)

    async def test_stale_format_run_cannot_start_review_after_new_head(self):
        run = await self.create_run()
        review, guard = await self.create_review(run)
        await self.accept(review, guard)
        await self.finish()
        response = await self.client.post("/agent-reviews", json={"format_run_id": run["id"], **guard})
        self.assertEqual(response.status_code, 409, response.text)

    async def test_reject_is_durable_idempotent_and_does_not_create_version(self):
        run = await self.create_run()
        review, guard = await self.create_review(run)
        url = f"/agent-reviews/{review['id']}/findings/{review['findings'][0]['id']}/decision"
        first = await self.client.post(url, json={**guard, "decision": "reject"})
        second = await self.client.post(url, json={**guard, "decision": "reject"})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["review"]["findings"][0]["decision"], "rejected")
        self.assertIsNone(second.json()["version"])
        self.assertIsNone(second.json()["followup_review"])
        self.assertEqual(len(self.store.list_versions(run["document_id"])), 1)
        self.assertEqual(self.store.get_decision(review["id"], review["findings"][0]["id"])["decision"], "reject")

    async def test_restore_creates_new_version_and_followup_against_previous_preview(self):
        run = await self.create_run()
        baseline = self.store.get_head(run["document_id"])
        review, guard = await self.create_review(run)
        accepted = await self.accept(review, guard)
        await self.finish()
        current_guard = await self.guard(accepted["format_run_id"])
        response = await self.client.post(
            f"/format-runs/{accepted['format_run_id']}/versions/{baseline['id']}/restore", json=current_guard)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        await self.finish()
        self.assertNotEqual(result["version"]["id"], baseline["id"])
        self.assertEqual(result["version"]["content"], baseline["content"])
        self.assertEqual(result["version"]["parent_id"], accepted["version"]["id"])
        self.assertEqual(result["version"]["origin"], "history_restore")
        self.assertEqual(self.store.get_head(run["document_id"]), result["version"])
        followup = self.store.get_review(result["followup_review"]["id"])
        self.assertEqual(followup["snapshot"]["source"], accepted["version"]["content"])
        self.assertEqual(followup["snapshot"]["preview"], baseline["content"])
        history = (await self.client.get(f"/format-runs/{result['format_run_id']}/versions")).json()
        self.assertEqual(len(history), 3)
        self.assertEqual(history[0], result["version"])

    async def test_restore_write_failure_rolls_back_head_run_and_survives_restart_retry(self):
        run = await self.create_run()
        baseline = self.store.get_head(run["document_id"])
        review, guard = await self.create_review(run)
        accepted = await self.accept(review, guard)
        await self.finish()
        current_guard = await self.guard(accepted["format_run_id"])
        url = f"/format-runs/{accepted['format_run_id']}/versions/{baseline['id']}/restore"
        before = ({key: item.model_dump(mode="json") for key, item in main.RUNS.items()},
                  {key: item.model_dump(mode="json") for key, item in main.CHANGES.items()},
                  dict(main.SOURCES))
        durable_before = self.store.load_format_state()
        versions_before = self.store.list_versions(run["document_id"])
        # Fail inside commit_format_state after its version/head insertion, so
        # this checks SQLite rollback rather than a failure before any write.
        with patch.object(self.store, "_save_format_state", side_effect=RuntimeError("simulated disk failure")):
            with self.assertRaisesRegex(RuntimeError, "simulated disk failure"):
                await self.client.post(url, json=current_guard)
        after = ({key: item.model_dump(mode="json") for key, item in main.RUNS.items()},
                 {key: item.model_dump(mode="json") for key, item in main.CHANGES.items()},
                 dict(main.SOURCES))
        self.assertEqual(after, before)
        self.assertEqual(self.store.get_head(run["document_id"]), accepted["version"])
        self.assertEqual(self.store.list_versions(run["document_id"]), versions_before)
        self.assertEqual(self.store.load_format_state(), durable_before)
        self.assertEqual(api.TASKS, {})
        self.store.close()
        self.store = ReviewStore(self.path)
        api.STORE = self.store
        main.RUNS, main.CHANGES, main.SOURCES = {}, {}, {}
        api.restore_format_state()
        self.assertEqual(self.store.get_head(run["document_id"]), accepted["version"])
        self.assertEqual(self.store.list_versions(run["document_id"]), versions_before)
        self.assertEqual(self.store.load_format_state(), durable_before)
        self.assertEqual({key: item.model_dump(mode="json") for key, item in main.RUNS.items()}, before[0])
        self.assertEqual({key: item.model_dump(mode="json") for key, item in main.CHANGES.items()}, before[1])
        self.assertEqual(main.SOURCES, before[2])
        response = await self.client.post(url, json=current_guard)
        self.assertEqual(response.status_code, 200, response.text)
        restored = response.json()
        await self.finish()
        self.assertEqual(restored["version"]["content"], baseline["content"])
        self.assertEqual(self.store.get_head(run["document_id"]), restored["version"])
        self.assertEqual(main.RUNS[restored["format_run_id"]].current_version_id, restored["version"]["id"])
        self.assertEqual(len(self.store.list_versions(run["document_id"])), len(versions_before) + 1)

    async def test_fulltext_deletion_fails_followup_honestly_and_history_can_restore(self):
        async def deletion_provider(role, instructions, context):
            result = await self.provider(role, instructions, context)
            if role == "methods":
                text = context["manuscript"]["content"]
                result["findings"] = [{"title": "删除当前段落", "severity": "medium",
                    "quote": text, "replacement": "", "rationale": "显式删除当前完整段落。",
                    "evidence": [{"source_id": "manuscript", "quote": text}], "verdict": "supported"}]
            return result
        api.PROVIDER_OVERRIDE = deletion_provider
        # Plain text can still be explicitly deleted; protected numeric data
        # is covered by the separate manual-verification acceptance test.
        run = await self.create_run("研究方法：采用质性访谈。\n")
        baseline = self.store.get_head(run["document_id"])
        review, guard = await self.create_review(run)
        calls_before_accept = len(self.calls)
        accepted = await self.accept(review, guard)
        await self.finish()
        self.assertEqual(accepted["version"]["content"], "")
        empty_run_id = accepted["format_run_id"]
        self.assertEqual(main.SOURCES[empty_run_id], "")
        followup = self.store.get_review(accepted["followup_review"]["id"])
        self.assertEqual(followup["status"], "failed")
        self.assertFalse(followup["coverage"]["complete"])
        self.assertFalse(any(item["status"] == "completed" for item in followup["agents"]))
        self.assertEqual(len(self.calls), calls_before_accept)
        current_guard = await self.guard(empty_run_id)
        response = await self.client.post(
            f"/format-runs/{empty_run_id}/versions/{baseline['id']}/restore", json=current_guard)
        self.assertEqual(response.status_code, 200, response.text)
        restored = response.json()
        await self.finish()
        self.assertEqual(restored["version"]["content"], baseline["content"])
        self.assertEqual(main.SOURCES[restored["format_run_id"]], baseline["content"])
        self.assertEqual(self.store.get_head(run["document_id"]), restored["version"])
        self.assertEqual(self.store.get_version(baseline["id"]), baseline)
        self.assertEqual(restored["version"]["parent_id"], accepted["version"]["id"])
        recovery_review = self.store.get_review(restored["followup_review"]["id"])
        self.assertEqual(recovery_review["snapshot"]["source"], "")
        self.assertEqual(recovery_review["snapshot"]["preview"], baseline["content"])
        self.assertEqual(recovery_review["status"], "completed")

    async def test_cancel_retry_reuses_durable_completed_chunk(self):
        blocked, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def blocking_provider(role, instructions, context):
            calls.append((role, context.get("window_index")))
            if role == "methods" and context.get("window_index") == 1 and not release.is_set():
                blocked.set()
                await release.wait()
            return await self.provider(role, instructions, context)
        api.PROVIDER_OVERRIDE = blocking_provider
        run = await self.create_run("😀研究方法：" + "文" * 4200 + "\n")
        review, guard = await self.create_review(run, finish=False)
        await asyncio.wait_for(blocked.wait(), timeout=5)
        duplicate = await self.client.post("/agent-reviews", json={"format_run_id": run["id"], **guard})
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        cancelled = await self.client.post(f"/agent-reviews/{review['id']}/cancel")
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        saved = self.store.get_review(review["id"])
        self.assertIn("0", saved["role_results"]["methods"]["chunks"])
        self.assertEqual(api.TASKS, {})
        release.set()
        retried = await self.client.post(f"/agent-reviews/{review['id']}/retry", json=guard)
        self.assertEqual(retried.status_code, 202, retried.text)
        await self.finish()
        result = (await self.client.get(f"/agent-reviews/{review['id']}")).json()
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(calls.count(("methods", 0)), 1)
        self.assertEqual(calls.count(("methods", 1)), 2)
        self.assertIn(0, result["coverage"]["roles"]["methods"]["reused_windows"])
        self.assertTrue(any(item["type"] == "review_retry" for item in self.store.events(review["id"])))

    async def test_restart_restores_head_decision_followup_and_format_state(self):
        run = await self.create_run()
        review, guard = await self.create_review(run)
        accepted = await self.accept(review, guard)
        await self.finish()
        events = self.store.events(review["id"])
        self.store.close()
        self.store = ReviewStore(self.path)
        api.STORE = self.store
        main.RUNS, main.CHANGES, main.SOURCES = {}, {}, {}
        api.restore_format_state()
        self.assertEqual(self.store.get_head(run["document_id"]), accepted["version"])
        self.assertIn(accepted["format_run_id"], main.RUNS)
        self.assertEqual(main.SOURCES[accepted["format_run_id"]], accepted["version"]["content"])
        self.assertEqual(self.store.events(review["id"]), events)
        replay = await self.accept(review, guard)
        self.assertEqual(replay["version"], accepted["version"])
        self.assertEqual(replay["format_run_id"], accepted["format_run_id"])
        self.assertEqual(replay["followup_review"]["id"], accepted["followup_review"]["id"])
        self.assertEqual(len(self.store.list_versions(run["document_id"])), 2)
        self.assertEqual(api.TASKS, {})

    async def test_startup_marks_interrupted_review_partial_preserving_checkpoint(self):
        run = await self.create_run()
        review, _ = await self.create_review(run)
        saved = self.store.get_review(review["id"])
        checkpoints = saved["role_results"]
        saved["status"] = "running"
        self.store.put_review(saved)
        self.store.close()
        api.STORE = None
        with patch.dict(os.environ, {"THESISDEBATE_DB_PATH": str(self.path)}):
            self.store = api.get_store()
        recovered = self.store.get_review(review["id"])
        self.assertEqual(recovered["status"], "partial")
        self.assertTrue(recovered["interrupted"])
        self.assertEqual(recovered["role_results"], checkpoints)
        self.assertEqual(self.store.events(review["id"])[-1]["type"], "review.interrupted")
        self.assertEqual(api.TASKS, {})

    async def test_progress_is_persisted_while_later_window_is_running(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def provider(role, instructions, context):
            if role == "methods" and context.get("window_index") == 1:
                entered.set()
                await release.wait()
            return await self.provider(role, instructions, context)

        api.PROVIDER_OVERRIDE = provider
        run = await self.create_run("甲" * 9000)
        review, _ = await self.create_review(run, finish=False)
        await asyncio.wait_for(entered.wait(), timeout=5)
        current = (await self.client.get(f"/agent-reviews/{review['id']}")).json()
        role = next(item for item in current["agents"] if item["id"] == "methods")
        self.assertEqual(current["status"], "running")
        self.assertEqual(role["status"], "running")
        self.assertEqual(role["progress"]["completed_units"], 1)
        self.assertEqual(role["progress"]["total_units"], 3)
        self.assertTrue(role["started_at"])
        self.assertTrue(role["progress"]["updated_at"])
        replay = (await self.client.get(f"/agent-reviews/{review['id']}/progress-events")).json()
        self.assertTrue(any(item["type"] == "role_checkpoint" and
                            item["payload"].get("agent_id") == "methods" for item in replay))
        release.set()
        await self.finish()

    async def test_terminal_progress_replays_every_event_with_safe_metadata(self):
        run = await self.create_run("甲" * 40000)
        review, _ = await self.create_review(run)
        self.assertEqual(review["status"], "completed")
        records = self.store.events(review["id"])
        self.assertGreater(len(records), 40)
        self.assertTrue(any("role_result" in item["payload"] for item in records))
        response = await self.client.get(f"/agent-reviews/{review['id']}/progress-events")
        self.assertEqual(response.status_code, 200, response.text)
        replay = response.json()
        self.assertEqual([item["id"] for item in replay], [item["id"] for item in records])
        self.assertTrue(all(item["created_at"] for item in replay))
        self.assertTrue(all("role_result" not in item["payload"] and
                            "role_results" not in item["payload"] for item in replay))
        cursor = replay[20]["id"]
        resumed = (await self.client.get(f"/agent-reviews/{review['id']}/progress-events?after={cursor}")).json()
        self.assertEqual(resumed, [item for item in replay if item["id"] > cursor])

    async def test_general_context_goal_and_original_template_survive_accept_and_restore(self):
        run = await self.create_run()
        baseline = self.store.get_head(run["document_id"])
        template = {"name": "课程实验报告", "heading_numbering": ["preserve"] * 6,
                    "required_sections": ["实验目的", "实验结果"]}
        original = main.RUNS[run["id"]]
        main.RUNS[run["id"]] = original.model_copy(update={
            "document_kind": "lab_report", "review_profile_id": "general", "template_snapshot": template})
        api.persist_format_state()
        context = (await self.client.get("/agent-review-context?format_run_id=" + run["id"])).json()
        self.assertEqual(context["roles"][0]["name"], "结构完整性审查官")
        self.assertEqual(context["template_name"], template["name"])
        self.assertNotIn("confirmed_template", context)
        goal, seen = "保留事实，按实验报告模板改善结构", []

        async def provider(role, instructions, model_context):
            seen.append(model_context)
            return await self.provider(role, instructions, model_context)

        api.PROVIDER_OVERRIDE = provider
        guard = await self.guard(run["id"])
        response = await self.client.post("/agent-reviews", json={"format_run_id": run["id"],
            **guard, "user_goal": goal})
        self.assertEqual(response.status_code, 202, response.text)
        review_id = response.json()["id"]
        await self.finish()
        review = (await self.client.get(f"/agent-reviews/{review_id}")).json()
        self.assertEqual(review["user_goal"], goal)
        self.assertEqual(review["review_profile_id"], "general")
        self.assertTrue(seen)
        self.assertTrue(all(value["user_goal"] == goal and value["confirmed_template"] == template for value in seen))
        with patch.object(main, "make_content_changes", wraps=main.make_content_changes) as make_changes:
            accepted = await self.accept(review, guard)
            self.assertEqual(make_changes.call_args.args[4], template["heading_numbering"])
        await self.finish()
        accepted_run = main.RUNS[accepted["format_run_id"]]
        self.assertEqual(accepted_run.template_snapshot, template)
        current_guard = await self.guard(accepted_run.id)
        with patch.object(main, "make_content_changes", wraps=main.make_content_changes) as make_changes:
            response = await self.client.post(
                f"/format-runs/{accepted_run.id}/versions/{baseline['id']}/restore", json=current_guard)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(make_changes.call_args.args[4], template["heading_numbering"])
        restored = response.json()
        await self.finish()
        restored_run = main.RUNS[restored["format_run_id"]]
        self.assertEqual(restored_run.template_snapshot, template)
        self.assertEqual(restored_run.document_kind, "lab_report")
        self.assertEqual(restored_run.review_profile_id, "general")
        followup = self.store.get_review(restored["followup_review"]["id"])
        self.assertEqual(followup["user_goal"], goal)
        self.assertEqual(followup["snapshot"]["confirmed_template"], template)
        api.restore_format_state()
        self.assertEqual(main.RUNS[restored_run.id].template_snapshot, template)

    async def test_legacy_numeric_suggestion_cannot_bypass_manual_verification(self):
        run = await self.create_run()
        review, guard = await self.create_review(run)
        saved = self.store.get_review(review["id"])
        finding = saved["findings"][0]
        preview = saved["snapshot"]["preview"]
        start = preview.index("样本10人")
        finding.update(quote="样本10人", replacement="样本20人", applicable=True,
                       verdict="supported", start=main.utf16_length(preview[:start]),
                       end=main.utf16_length(preview[:start] + "样本10人"))
        self.store.put_review(saved)
        before = self.store.list_versions(run["document_id"])
        response = await self.client.post(
            f"/agent-reviews/{review['id']}/findings/{finding['id']}/decision",
            json={**guard, "decision": "accept"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("人工核对", response.json()["detail"])
        self.assertEqual(self.store.list_versions(run["document_id"]), before)


if __name__ == "__main__":
    unittest.main()
