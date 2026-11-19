"""Chat proposal integration checks; HTTP model responses are loopback fixtures.

These tests exercise the installed SDK and strict output schema, not a remote
model's understanding or academic/typographic judgment quality.
"""
import asyncio
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from app import agent_api, main
from app.document_layout import DocumentStyle, HeadingAppearance, save_document_layout
from app.storage import ReviewStore
from tests.test_agent_api import ASGIClient


FIXTURE_KEY = "offline-chat-fixture-not-a-real-key"
FIXTURE_PRIVATE_TEXT = "offline-private-manuscript-error-must-not-echo"


class AgentChatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.settings_path = Path(self.temp.name) / "agent-settings.json"
        self.environment = patch.dict(os.environ, {
            "THESISDEBATE_AGENT_SETTINGS_PATH": str(self.settings_path),
            "AGENT_BASE_URL": "", "AGENT_MODEL": "", "AGENT_API_KEY": "",
            "OPENAI_BASE_URL": "", "OPENAI_MODEL": "", "OPENAI_API_KEY": "",
            "HTTP_PROXY": "", "HTTPS_PROXY": "", "ALL_PROXY": "",
            "http_proxy": "", "https_proxy": "", "all_proxy": "",
            "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
        })
        self.environment.start()
        self.store = ReviewStore(Path(self.temp.name) / "workspace.sqlite3")
        self.saved = agent_api.STORE, main.RUNS, main.CHANGES, main.SOURCES
        agent_api.STORE = self.store
        main.RUNS, main.CHANGES, main.SOURCES = {}, {}, {}
        self.client = ASGIClient(main.app)
        self.document = self.store.create_workspace_document(
            "对话隔离检查.md", "# 原始标题\n准确率0.82，公式 E=mc^2，参考文献保持原样。\n")
        self.style = DocumentStyle(font_size_pt=14, line_spacing=1.2, paragraph_after_pt=3)
        save_document_layout(self.store, self.document["id"], 1, self.style)

    async def asyncTearDown(self):
        try:
            await self.client.aclose()
            self.store.close()
        finally:
            agent_api.STORE, main.RUNS, main.CHANGES, main.SOURCES = self.saved
            self.environment.stop()
            self.temp.cleanup()

    def request(self, message, **updates):
        return {"document_id": self.document["id"], "message": message,
                "style": self.style.model_dump(mode="json"), "history": [],
                "content": self.document["content"], **updates}

    def persistent_snapshot(self):
        return "\n".join(self.store._connection.iterdump())

    async def proposal(self, message, *, provider="local", **updates):
        before = self.persistent_snapshot()
        request = self.request(message, **updates)
        original_request = deepcopy(request)
        response = await self.client.post("/agent-chat", json=request)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["provider"], provider)
        self.assertIsNotNone(result["proposed_style"], response.text)
        proposed = result["proposed_style"]
        self.assertEqual(set(proposed), set(DocumentStyle.model_fields))
        self.assertEqual(len(proposed["headings"]), 6)
        for heading in proposed["headings"]:
            self.assertEqual(set(heading), set(HeadingAppearance.model_fields))
        DocumentStyle.model_validate(proposed)
        self.assertTrue(result["changes"])
        self.assertEqual(request, original_request)
        self.assertEqual(self.persistent_snapshot(), before, "A proposal must never write manuscript, versions, or persisted layout")
        return result

    async def no_proposal(self, message, **updates):
        before = self.persistent_snapshot()
        response = await self.client.post("/agent-chat", json=self.request(message, **updates))
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertIsNone(result["proposed_style"], response.text)
        self.assertEqual(result["changes"], [])
        self.assertEqual(self.persistent_snapshot(), before)
        return result

    async def test_local_body_font_size_spacing_indent_and_margin_command(self):
        result = await self.proposal("正文仿宋三号，行距2倍，段前6磅，段后12磅，首行缩进2字符；上页边距3厘米，左右页边距2厘米")
        style = result["proposed_style"]
        self.assertEqual(style["font_family"], "仿宋")
        self.assertEqual(style["font_size_pt"], 16)
        self.assertEqual(style["line_spacing"], 2)
        self.assertEqual(style["paragraph_before_pt"], 6)
        self.assertEqual(style["paragraph_after_pt"], 12)
        self.assertEqual(style["first_line_indent_pt"], 32)
        self.assertEqual(style["margin_top_mm"], 30)
        self.assertEqual(style["margin_left_mm"], 20)
        self.assertEqual(style["margin_right_mm"], 20)
        self.assertEqual(style["margin_bottom_mm"], self.style.margin_bottom_mm)
        self.assertEqual(style["headings"], self.style.model_dump(mode="json")["headings"])
        self.assertIn("未调用远程模型", "".join(result["warnings"]))

    async def test_first_heading_and_body_scopes_are_independent(self):
        result = await self.proposal("一级标题宋体三号，行距2倍，段前1行；正文仿宋小四，行距1.5倍，段后0磅")
        style = result["proposed_style"]
        self.assertEqual((style["font_family"], style["font_size_pt"], style["line_spacing"], style["paragraph_after_pt"]),
                         ("仿宋", 12, 1.5, 0))
        first = style["headings"][0]
        self.assertEqual((first["font_family"], first["font_size_pt"], first["line_spacing"], first["space_before_pt"]),
                         ("宋体", 16, 2, 16))
        self.assertEqual(style["headings"][1:], self.style.model_dump(mode="json")["headings"][1:])
        fields = {item["field"] for item in result["changes"]}
        self.assertIn("headings.0.font_size_pt", fields)
        self.assertIn("font_size_pt", fields)
        self.assertFalse(any(field.startswith("headings.1.") for field in fields))

    async def test_all_heading_scope_does_not_change_body(self):
        style = (await self.proposal("标题宋体小三，双倍行距"))["proposed_style"]
        original = self.style.model_dump(mode="json")
        self.assertEqual({key: value for key, value in style.items() if key != "headings"},
                         {key: value for key, value in original.items() if key != "headings"})
        self.assertTrue(all((heading["font_family"], heading["font_size_pt"], heading["line_spacing"]) ==
                            ("宋体", 15, 2) for heading in style["headings"]))

    async def test_full_document_scope_explicitly_changes_body_and_all_headings(self):
        style = (await self.proposal("全文楷体小四，1.5倍行距"))["proposed_style"]
        self.assertEqual((style["font_family"], style["font_size_pt"], style["line_spacing"]), ("楷体", 12, 1.5))
        self.assertTrue(all((heading["font_family"], heading["font_size_pt"], heading["line_spacing"]) ==
                            ("楷体", 12, 1.5) for heading in style["headings"]))

    async def test_chinese_small_four_and_number_three_map_to_twelve_and_sixteen_points(self):
        result = await self.proposal("正文宋体小四号，一级标题黑体三号")
        self.assertEqual(result["proposed_style"]["font_size_pt"], 12)
        self.assertEqual(result["proposed_style"]["headings"][0]["font_size_pt"], 16)

    async def test_out_of_range_values_return_no_proposal(self):
        for command in ("正文字号37", "正文行距0.5倍", "正文段前73磅",
                        "页边距9毫米", "一级标题字号49", "正文首行缩进73磅"):
            with self.subTest(command=command):
                await self.no_proposal(command)

    async def test_invalid_heading_level_does_not_silently_target_all_headings(self):
        for command in ("第七级标题字号16", "第0级标题字号16", "第9级标题字号16"):
            with self.subTest(command=command):
                await self.no_proposal(command)

    async def test_negative_or_unsupported_line_spacing_is_not_reinterpreted(self):
        for command in ("正文行距-2倍", "正文行距2厘米"):
            with self.subTest(command=command):
                await self.no_proposal(command)

    async def test_ambiguous_and_negative_requests_leave_document_unchanged(self):
        for command in ("帮我优化排版并符合学校要求", "不要修改正文字体", "正文换成未知字体"):
            with self.subTest(command=command):
                await self.no_proposal(command)

    async def test_profile_style_is_used_only_when_provided(self):
        await self.no_proposal("按学校模板排版")
        profile = DocumentStyle(font_family="仿宋", font_size_pt=12, margin_top_mm=30).model_dump(mode="json")
        result = await self.proposal("按学校模板排版", profile_style=profile)
        self.assertEqual(result["proposed_style"], profile)

    async def test_matching_style_and_blank_message_have_no_write(self):
        await self.no_proposal("正文字号14，行距1.2倍，段后3磅")
        before = self.persistent_snapshot()
        response = await self.client.post("/agent-chat", json=self.request("   "))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.persistent_snapshot(), before)

    async def test_unsaved_or_missing_document_returns_404_before_model_call(self):
        before = self.persistent_snapshot()
        response = await self.client.post("/agent-chat", json=self.request("正文宋体小四", document_id="doc-not-saved"))
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.persistent_snapshot(), before)

    async def start_loopback(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(handler):
                body = json.loads(handler.rfile.read(int(handler.headers["Content-Length"])))
                requests.append({"path": handler.path, "authorization": handler.headers.get("Authorization"), "body": body})
                if getattr(handler.server, "fail_status", None):
                    status = handler.server.fail_status
                    data = json.dumps({"error": {"message": FIXTURE_KEY + " " + FIXTURE_PRIVATE_TEXT,
                                                  "type": "authentication_error"}}).encode()
                else:
                    status = 200
                    context = json.loads(next(message["content"] for message in body["messages"] if message["role"] == "user"))
                    proposed = deepcopy(context["style"])
                    proposed["font_family"] = "仿宋"
                    proposed["headings"][0]["font_size_pt"] = 16
                    result = {"reply": "已读取本轮请求和历史，生成待确认排版。", "proposed_style": proposed}
                    if getattr(handler.server, "invalid_output", False):
                        result["reply"] = FIXTURE_PRIVATE_TEXT
                        result["proposed_style"]["font_size_pt"] = 999
                    if getattr(handler.server, "partial_style", None) == "body":
                        result["proposed_style"] = {"font_family": "仿宋"}
                    elif getattr(handler.server, "partial_style", None) == "heading":
                        result["proposed_style"]["headings"][0] = {"font_size_pt": 16}
                    if getattr(handler.server, "extra_style_field", False):
                        result["proposed_style"]["content"] = FIXTURE_PRIVATE_TEXT
                    data = json.dumps({"id": "chatcmpl-local-chat-fixture", "object": "chat.completion",
                                       "created": 1, "model": "saved-chat-loopback-model",
                                       "choices": [{"index": 0, "finish_reason": "stop",
                                                    "message": {"role": "assistant", "content": json.dumps(result, ensure_ascii=False)}}],
                                       "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}, ensure_ascii=False).encode()
                handler.send_response(status)
                handler.send_header("Content-Type", "application/json")
                handler.send_header("Content-Length", str(len(data)))
                handler.end_headers()
                handler.wfile.write(data)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        async def cleanup():
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join(timeout=1)
        self.addAsyncCleanup(cleanup)
        saved = await self.client.request("PUT", "/agent-settings", json_body={
            "base_url": f"http://127.0.0.1:{server.server_port}/v1",
            "model": "saved-chat-loopback-model", "api_key": FIXTURE_KEY})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["source"], "local")
        self.assertTrue(saved.json()["configured"])
        self.assertNotIn(FIXTURE_KEY, saved.text)
        return server, requests

    async def test_saved_settings_drive_real_sdk_schema_and_multiturn_context(self):
        server, requests = await self.start_loopback()
        history = [{"role": "user", "content": "正文保持不变，先讨论一级标题。"},
                   {"role": "assistant", "content": "请说明一级标题字号。"}]
        excerpt = "尚未保存的上下文片段，仅供对话参考，不应写回正文。"
        profile = DocumentStyle(margin_top_mm=30).model_dump(mode="json")
        response = await self.proposal("沿用刚才的范围，设为三号，并把正文设为仿宋。", provider="configured",
                                       history=history, content=excerpt, profile_style=profile)
        self.assertEqual(response["warnings"], [])
        self.assertEqual(response["proposed_style"]["font_family"], "仿宋")
        self.assertEqual(response["proposed_style"]["headings"][0]["font_size_pt"], 16)
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request["path"], "/v1/chat/completions")
        self.assertEqual(request["authorization"], "Bearer " + FIXTURE_KEY)
        body = request["body"]
        self.assertEqual(body["model"], "saved-chat-loopback-model")
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertTrue(body["response_format"]["json_schema"]["strict"])
        schema = body["response_format"]["json_schema"]["schema"]
        self.assertEqual(set(schema["required"]), {"reply", "proposed_style"})
        self.assertFalse(schema["additionalProperties"])
        document_schema = next(value for value in schema["$defs"].values()
                               if set(value.get("properties", {})) == set(DocumentStyle.model_fields))
        self.assertEqual(set(document_schema["required"]), set(DocumentStyle.model_fields))
        context = json.loads(next(message["content"] for message in body["messages"] if message["role"] == "user"))
        self.assertEqual(context["history"], history)
        self.assertEqual(context["manuscript_excerpt"], excerpt)
        self.assertEqual(context["profile_style"], profile)
        self.assertEqual(context["style"], self.style.model_dump(mode="json"))
        self.assertIn("三号", context["message"])
        self.assertNotIn(FIXTURE_KEY, json.dumps(context) + json.dumps(response))

    async def test_configured_missing_document_returns_404_without_http_call(self):
        server, requests = await self.start_loopback()
        before = self.persistent_snapshot()
        response = await self.client.post("/agent-chat", json=self.request("设为三号", document_id="unsaved-document"))
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(requests, [])
        self.assertEqual(self.persistent_snapshot(), before)

    async def test_configured_http_error_does_not_echo_key_or_private_error_text(self):
        server, requests = await self.start_loopback()
        server.fail_status = 401
        before = self.persistent_snapshot()
        response = await self.client.post("/agent-chat", json=self.request("把正文改成仿宋"))
        self.assertEqual(response.status_code, 502, response.text)
        self.assertIn("HTTP 401", response.json()["detail"])
        self.assertNotIn(FIXTURE_KEY, response.text)
        self.assertNotIn(FIXTURE_PRIVATE_TEXT, response.text)
        self.assertEqual(len(requests), 1)
        self.assertEqual(self.persistent_snapshot(), before)

    async def test_configured_invalid_structured_output_is_sanitized_and_never_saved(self):
        server, requests = await self.start_loopback()
        server.invalid_output = True
        before = self.persistent_snapshot()
        response = await self.client.post("/agent-chat", json=self.request("把正文改成仿宋"))
        self.assertEqual(response.status_code, 502, response.text)
        self.assertNotIn(FIXTURE_KEY, response.text)
        self.assertNotIn(FIXTURE_PRIVATE_TEXT, response.text)
        self.assertEqual(len(requests), 1)
        self.assertEqual(self.persistent_snapshot(), before)

    async def test_configured_partial_style_is_rejected_without_filling_unrequested_defaults(self):
        server, requests = await self.start_loopback()
        for omitted_fields in ("body", "heading"):
            with self.subTest(omitted_fields=omitted_fields):
                server.partial_style = omitted_fields
                before = self.persistent_snapshot()
                response = await self.client.post("/agent-chat", json=self.request("只修改正文字体，不改字号行距或标题。"))
                self.assertEqual(response.status_code, 502, response.text)
                self.assertNotIn(FIXTURE_KEY, response.text)
                self.assertEqual(self.persistent_snapshot(), before)
        self.assertEqual(len(requests), 2)

    async def test_configured_non_style_field_is_rejected_and_never_written(self):
        server, requests = await self.start_loopback()
        server.extra_style_field = True
        before = self.persistent_snapshot()
        response = await self.client.post("/agent-chat", json=self.request("只修改正文字体。"))
        self.assertEqual(response.status_code, 502, response.text)
        self.assertNotIn(FIXTURE_PRIVATE_TEXT, response.text)
        self.assertEqual(len(requests), 1)
        self.assertEqual(self.persistent_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
