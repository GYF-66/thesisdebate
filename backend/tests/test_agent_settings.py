"""Settings persist independently of process env and feed the production SDK path."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from app import agent_engine, agent_settings, main
from tests.test_agent_api import ASGIClient


class AgentSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = TemporaryDirectory()
        self.path = Path(self.temporary.name) / "agent-settings.json"
        self.environment = patch.dict(os.environ, {
            "THESISDEBATE_AGENT_SETTINGS_PATH": str(self.path),
            "AGENT_BASE_URL": "", "AGENT_MODEL": "", "AGENT_API_KEY": "",
            "OPENAI_BASE_URL": "", "OPENAI_MODEL": "", "OPENAI_API_KEY": "",
            "HTTP_PROXY": "", "HTTPS_PROXY": "", "ALL_PROXY": "",
            "http_proxy": "", "https_proxy": "", "all_proxy": "", "NO_PROXY": "127.0.0.1,localhost",
        })
        self.environment.start()
        self.client = ASGIClient(main.app)

    async def asyncTearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def payload(self, **updates):
        return {"base_url": "https://model.example/v1/", "model": "settings-model",
                "api_key": "offline-settings-fixture-secret", **updates}

    async def test_save_reload_blank_key_preservation_and_explicit_clear(self):
        response = await self.client.request("PUT", "/agent-settings", json_body=self.payload())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["configured"])
        self.assertTrue(response.json()["has_api_key"])
        self.assertEqual(response.json()["source"], "local")
        self.assertNotIn(self.payload()["api_key"], response.text)
        self.assertNotIn("api_key", {key for key in response.json() if key != "has_api_key"})
        self.assertTrue(self.path.is_file())
        if os.name == "nt":
            self.assertNotIn(self.payload()["api_key"], self.path.read_text(encoding="utf-8"))
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            self.assertEqual(stored["version"], 2)
            self.assertEqual(stored["channels"][0]["credential"]["encoding"], "windows-dpapi")
        else:
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        # Every read opens the persisted file; there is no in-memory-only config.
        self.assertEqual(agent_settings.resolve_config()["api_key"], self.payload()["api_key"])
        response = await self.client.request("PUT", "/agent-settings", json_body=self.payload(api_key="", model="updated-model"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["has_api_key"])
        self.assertEqual(agent_settings.resolve_config()["model"], "updated-model")
        status = await self.client.get("/agent-config")
        self.assertEqual(status.json()["model"], "updated-model")
        self.assertNotIn(self.payload()["api_key"], status.text)
        # An explicit clear disables the saved config even if env has another key.
        with patch.dict(os.environ, {"AGENT_API_KEY": "environment-key-must-not-reappear"}):
            cleared = await self.client.request("PUT", "/agent-settings", json_body=self.payload(api_key="", clear_api_key=True))
            self.assertEqual(cleared.status_code, 200)
            self.assertFalse(cleared.json()["has_api_key"])
            self.assertFalse(agent_engine.public_config()["configured"])

    async def test_invalid_endpoint_and_external_origin_never_save_or_echo_credentials(self):
        response = await self.client.request("PUT", "/agent-settings", json_body=self.payload(base_url="https://user:offline-secret@model.example/v1"))
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("offline-secret", response.text)
        self.assertFalse(self.path.exists())
        malformed = await self.client.request("PUT", "/agent-settings", json_body=self.payload(api_key={"value": "malformed-secret-never-echo"}))
        self.assertEqual(malformed.status_code, 422)
        self.assertNotIn("malformed-secret-never-echo", malformed.text)
        response = await self.client.request("PUT", "/agent-settings", json_body=self.payload(), headers={"Origin": "https://unrelated.example"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.path.exists())

    async def test_existing_review_adapter_keeps_its_configuration_snapshot(self):
        agent_settings.save_config(agent_settings.SettingsInput(**self.payload()))
        previous = agent_engine._SDKProvider()
        agent_settings.save_config(agent_settings.SettingsInput(**self.payload(model="next-review-model", api_key="next-fixture-secret")))
        following = agent_engine._SDKProvider()
        self.assertEqual(previous._config["model"], "settings-model")
        self.assertEqual(previous._config["api_key"], "offline-settings-fixture-secret")
        self.assertEqual(following._config["model"], "next-review-model")
        self.assertEqual(following._config["api_key"], "next-fixture-secret")

    async def test_saved_configuration_drives_real_sdk_review_and_connection_test(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append({"path": self.path, "model": body.get("model"), "authorization": self.headers.get("Authorization"), "body": body})
                if getattr(self.server, "fail", False):
                    data = json.dumps({"error": {"message": "offline-settings-fixture-secret", "type": "authentication_error"}}).encode()
                    self.send_response(401)
                else:
                    context = json.loads(next(item["content"] for item in body["messages"] if item["role"] == "user"))
                    result = ({"summary": "本地端点裁决完成", "assessments": [], "challenges": []}
                              if context["phase"] == "judgment" else {"summary": "本地端点已连接", "findings": []})
                    data = json.dumps({"id": "chatcmpl-settings", "object": "chat.completion", "created": 1,
                                       "model": "saved-loopback-model", "choices": [{"index": 0, "finish_reason": "stop",
                                       "message": {"role": "assistant", "content": json.dumps(result, ensure_ascii=False)}}],
                                       "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}, ensure_ascii=False).encode()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            form = self.payload(base_url=f"http://127.0.0.1:{server.server_port}/v1", model="saved-loopback-model")
            saved = await self.client.request("PUT", "/agent-settings", json_body=form)
            self.assertEqual(saved.status_code, 200, saved.text)
            # Blank key in the form uses persisted credentials, without echoing them.
            tested = await self.client.post("/agent-settings/test", json={**form, "api_key": ""})
            self.assertEqual(tested.status_code, 200, tested.text)
            self.assertTrue(tested.json()["ok"])
            events = []
            async def emit(name, payload):
                events.append(name)
            snapshot = {"id": "settings-snapshot", "source": "研究方法：样本9人。", "preview": "研究方法：样本10人。", "materials": []}
            snapshot.update({name + "_hash": hashlib.sha256(snapshot[name].encode()).hexdigest() for name in ["source", "preview"]})
            result = await agent_engine.execute_review(snapshot, emit, asyncio.Event())
            self.assertEqual(result["status"], "completed", result)
            self.assertEqual(result["model"], "saved-loopback-model")
            self.assertEqual(len(requests), 6)
            self.assertTrue(all(item["model"] == "saved-loopback-model" for item in requests))
            self.assertTrue(all(item["path"] == "/v1/chat/completions" for item in requests))
            self.assertTrue(all(item["authorization"] == "Bearer offline-settings-fixture-secret" for item in requests))
            self.assertTrue(all(item["body"]["response_format"]["type"] == "json_schema" for item in requests))
            self.assertNotIn(form["api_key"], str(result) + tested.text + saved.text)
            server.fail = True
            failed = await self.client.post("/agent-settings/test", json={**form, "api_key": ""})
            self.assertEqual(failed.status_code, 502)
            self.assertIn("HTTP 401", failed.json()["detail"])
            self.assertNotIn(form["api_key"], failed.text)
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
