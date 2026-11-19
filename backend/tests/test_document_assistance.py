"""Offline integration tests: real SQLite, real uploads and mocked metadata boundary."""
import asyncio
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi import FastAPI

from app import agent_api
from app.assistance_api import router
from app.document_assistance import analyze_document, content_hash, extract_comments, extract_material, verify_reference
from app.storage import ReviewStore


class Response:
    def __init__(self, status, body, headers):
        self.status_code, self.body, self.headers = status, body, headers
        self.text = body.decode("utf-8")

    def json(self):
        return json.loads(self.text)


def commented_docx():
    from docx import Document
    document = Document()
    document.add_paragraph("研究对象为大学生。")
    original = BytesIO()
    document.save(original)
    result = BytesIO()
    with ZipFile(original) as source, ZipFile(result, "w", ZIP_DEFLATED) as target:
        for entry in source.infolist():
            data = source.read(entry.filename)
            if entry.filename == "word/document.xml":
                data = data.replace(b"<w:p>", b'<w:p><w:commentRangeStart w:id="0"/>', 1).replace(b"</w:p>", b'<w:commentRangeEnd w:id="0"/></w:p>', 1)
            elif entry.filename == "word/_rels/document.xml.rels":
                data = data.replace(b"</Relationships>", b'<Relationship Id="rIdMentor" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" Target="comments.xml"/></Relationships>')
            elif entry.filename == "[Content_Types].xml":
                data = data.replace(b"</Types>", b'<Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/></Types>')
            target.writestr(entry.filename, data)
        target.writestr("word/comments.xml", '<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:comment w:id="0" w:author="测试导师"><w:p><w:r><w:t>请统一研究对象的表述。</w:t></w:r></w:p></w:comment></w:comments>')
    return result.getvalue()


class PureAssistanceTests(unittest.TestCase):
    def test_sections_are_headings_and_nonpaper_does_not_require_thesis(self):
        generic = analyze_document("本报告讨论项目过程。", "generic")
        self.assertEqual(generic["required_sections"], [])
        self.assertFalse(generic["findings"])
        self.assertFalse(any(item["id"] == "references" for item in generic["submission_checklist"]))
        thesis = analyze_document("这里提到了摘要、绪论、研究方法、结论、参考文献，但它们不是标题。", "thesis")
        self.assertEqual(len([item for item in thesis["findings"] if item["category"] == "structure"]), 5)
        course = analyze_document("# 项目概述\n内容\n# 主要内容\n正文\n# 总结\n完成", "course_report")
        self.assertFalse(course["findings"])

    def test_consistency_candidates_have_exact_anchors(self):
        content = "# 摘要\n样本量为100人。\n样本量为120人。\n卷积神经网络（CNN）。循环神经网络（CNN）。"
        result = analyze_document(content, "generic")
        self.assertEqual(len(result["findings"]), 4)
        for item in result["findings"]:
            self.assertEqual(content[item["start"]:item["end"]], item["quote"])
            self.assertTrue(item["requires_confirmation"])

    def test_both_citation_directions_duplicates_and_ranges(self):
        content = "本文引用[1-2]和[4]。\n# 参考文献\n[1] 相同文献[J]. 2025.\n[2] 相同文献[J]. 2025.\n[2] 第二篇[J]. 2024.\n[3] 未引用文献[J]. 2023."
        result = analyze_document(content, "generic")
        titles = [item["title"] for item in result["findings"]]
        self.assertIn("参考文献编号 [2] 重复", titles)
        self.assertIn("疑似重复参考文献", titles)
        self.assertIn("正文引用未找到对应条目", titles)
        self.assertIn("文献 [3] 未识别到正文引用", titles)
        self.assertTrue(all(item["verification"]["existence"] == "not_verified" for item in result["references"]))

    def test_real_docx_comments_preserve_anchor_without_modifying_docx(self):
        data = commented_docx()
        before = content_hash(data.hex())
        tasks = extract_comments(data, "研究对象为大学生。", "导师批注.docx")
        self.assertEqual(tasks[0]["quote"], "研究对象为大学生。")
        self.assertEqual(tasks[0]["comment"], "请统一研究对象的表述。")
        self.assertEqual(content_hash(data.hex()), before)
        mismatch = extract_comments(data, "新版本没有该句。", "导师批注.docx")
        self.assertEqual(mismatch[0]["quote"], "")
        self.assertEqual(mismatch[0]["location_status"], "needs_confirmation")

    def test_pdf_extraction_has_page_numbers_and_no_ocr_claim(self):
        from reportlab.pdfgen.canvas import Canvas
        stream = BytesIO()
        pdf = Canvas(stream)
        pdf.drawString(40, 800, "Cognitive load describes working memory.")
        pdf.showPage()
        pdf.drawString(40, 800, "A second evidence page.")
        pdf.save()
        result = extract_material("evidence.pdf", stream.getvalue())
        self.assertEqual({part["page"] for part in result["segments"]}, {1, 2})
        self.assertIn("Cognitive load", result["segments"][0]["text"])

    def test_metadata_matching_does_not_verify_claim_support(self):
        data = {"message": {"DOI": "10.1234/example", "title": ["A real metadata title"]}}
        with patch("app.document_assistance._fetch_json", return_value=data) as request:
            result = verify_reference("[1] A real metadata title. DOI:10.1234/example")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result["existence_status"], "metadata_found")
        self.assertEqual(result["claim_support_status"], "not_checked")
        self.assertEqual(result["format_status"], "not_checked")

    def test_network_failure_and_unmatched_titles_are_only_unverified(self):
        with patch("app.document_assistance._fetch_json", side_effect=TimeoutError()):
            result = verify_reference("[1] DOI:10.1234/example")
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(result["existence_status"], "not_verified")
        self.assertIn("不代表", result["message"])
        with patch("app.document_assistance._fetch_json", return_value={"message": {"items": [{"title": ["Entirely unrelated result"], "DOI": "10.1111/other"}]}}):
            mismatch = verify_reference("大学生学习动机与任务策略")
        self.assertEqual(mismatch["status"], "candidates_only")
        self.assertEqual(mismatch["existence_status"], "not_verified")
        with patch("app.document_assistance._fetch_json", return_value={"message": []}):
            malformed = verify_reference("大学生学习动机与任务策略")
        self.assertEqual(malformed["status"], "unverified")

    def test_equivalent_thesis_headings_and_balanced_doi_parentheses(self):
        content = "# 摘要\n内容\n# 引言\n内容\n# 研究设计\n内容\n# 总结\n内容\n# 参考文献\n"
        self.assertFalse(analyze_document(content, "thesis")["findings"])
        with patch("app.document_assistance._fetch_json", return_value={"message": {"DOI": "10.1234/example(2020)", "title": ["A metadata title"]}}):
            result = verify_reference("[1] DOI:10.1234/example(2020)")
        self.assertEqual(result["doi"], "10.1234/example(2020)")
        self.assertEqual(result["status"], "metadata_matched")


class AssistanceAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "assistance.sqlite3"
        self.store = ReviewStore(self.path)
        self.previous_store = agent_api.STORE
        agent_api.STORE = self.store
        self.app = FastAPI()
        self.app.include_router(router)
        self.document = self.store.create_workspace_document("论文.md", "# 绪论\n研究对象为大学生。\n# 参考文献\n[1] Test study. DOI:10.1234/example")
        self.other = self.store.create_workspace_document("另一个.md", "其他报告")

    async def asyncTearDown(self):
        self.store.close()
        agent_api.STORE = self.previous_store
        self.temp.cleanup()

    def guard(self, document=None):
        value = document or self.document
        return {"document_id": value["id"], "content": value["content"], "expected_content_hash": content_hash(value["content"])}

    async def request(self, method, target, payload=None, raw=None, declared=None):
        path, _, query = target.partition("?")
        body = raw if raw is not None else json.dumps(payload, ensure_ascii=False).encode() if payload is not None else b""
        headers = [(b"content-type", b"application/octet-stream" if raw is not None else b"application/json")]
        if declared is not None:
            headers.append((b"content-length", str(declared).encode()))
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1", "scheme": "http", "method": method, "path": path, "raw_path": path.encode(), "query_string": query.encode(), "root_path": "", "headers": headers, "client": ("127.0.0.1", 1000), "server": ("testserver", 80)}
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
        result = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
        return Response(start["status"], result, start["headers"])

    async def add_task(self):
        response = await self.request("POST", "/document-assistance/mentor-comments", {**self.guard(), "comments": '1. 统一“研究对象为大学生。”的表述\n2. 补充局限'})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["mentor_tasks"][0]

    async def test_analyze_course_report_and_stale_hash(self):
        result = await self.request("POST", "/document-assistance/analyze", {**self.guard(), "document_kind": "course_report"})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertNotIn("参考文献", result.json()["required_sections"])
        wrong = await self.request("POST", "/document-assistance/analyze", {**self.guard(), "expected_content_hash": "0" * 64})
        self.assertEqual(wrong.status_code, 409)
        state = await self.request("GET", f"/document-assistance/documents/{self.document['id']}/state")
        self.assertEqual(state.json()["revision"], 1)

    async def test_profile_scene_is_resolved_and_explicit_generic_never_becomes_thesis(self):
        generic = await self.request("POST", "/document-assistance/analyze", {**self.guard(), "profile_id": "preset-generic"})
        self.assertEqual(generic.status_code, 200, generic.text)
        self.assertEqual(generic.json()["document_kind"], "generic")
        self.assertEqual(generic.json()["required_sections"], [])
        explicit = await self.request("POST", "/document-assistance/analyze", {**self.guard(), "profile_id": "generic-thesis", "document_kind": "generic"})
        self.assertEqual(explicit.status_code, 200, explicit.text)
        self.assertEqual(explicit.json()["required_sections"], [])

    async def test_actual_unresolved_mentor_task_is_in_checklist_and_questions(self):
        task = await self.add_task()
        result = await self.request("POST", "/document-assistance/analyze", {**self.guard(), "document_kind": "generic"})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(any(item["id"] == "mentor_tasks" and item["status"] == "attention" for item in result.json()["submission_checklist"]))
        self.assertTrue(any(task["comment"] in item["question"] for item in result.json()["defense_questions"]))

    async def test_tasks_persist_isolate_and_require_completion_explanation(self):
        task = await self.add_task()
        self.assertEqual(task["quote"], "研究对象为大学生。")
        route = f"/document-assistance/documents/{self.document['id']}/mentor-tasks/{task['id']}"
        payload = {"content": self.document["content"], "expected_content_hash": content_hash(self.document["content"]), "expected_task_version": 1, "status": "done"}
        invalid = await self.request("PATCH", route, payload)
        self.assertEqual(invalid.status_code, 422)
        completed = await self.request("PATCH", route, {**payload, "reply": "已在绪论统一表述。"})
        self.assertEqual(completed.status_code, 200, completed.text)
        conflict = await self.request("PATCH", route, {**payload, "reply": "旧版本覆盖"})
        self.assertEqual(conflict.status_code, 409)
        other_route = f"/document-assistance/documents/{self.other['id']}/mentor-tasks/{task['id']}"
        isolated = await self.request("PATCH", other_route, {**payload, "content": self.other["content"], "expected_content_hash": content_hash(self.other["content"])})
        self.assertEqual(isolated.status_code, 404)
        self.store.close()
        self.store = ReviewStore(self.path)
        agent_api.STORE = self.store
        state = await self.request("GET", f"/document-assistance/documents/{self.document['id']}/state")
        self.assertEqual(state.json()["mentor_tasks"][0]["status"], "done")

    async def test_mentor_task_requires_explicit_rebase_after_body_change(self):
        task = await self.add_task()
        old_content = self.document["content"]
        self.document = self.store.save_workspace_document(self.document["id"], self.document["name"], old_content + "\n新增内容", None, 1)
        route = f"/document-assistance/documents/{self.document['id']}/mentor-tasks/{task['id']}"
        payload = {"content": self.document["content"], "expected_content_hash": content_hash(self.document["content"]), "expected_task_version": 1, "status": "in_progress"}
        conflict = await self.request("PATCH", route, payload)
        self.assertEqual(conflict.status_code, 409)
        rebased = await self.request("PATCH", route, {**payload, "rebase": True, "quote": "研究对象为大学生。"})
        self.assertEqual(rebased.status_code, 200, rebased.text)
        self.assertFalse(rebased.json()["mentor_tasks"][0]["stale"])

    async def test_raw_docx_comment_import_and_csv_export(self):
        query = urlencode({"document_id": self.document["id"], "filename": "批注.docx", "expected_content_hash": content_hash(self.document["content"])})
        imported = await self.request("POST", "/document-assistance/mentor-comments/import?" + query, raw=commented_docx())
        self.assertEqual(imported.status_code, 201, imported.text)
        exported = await self.request("GET", f"/document-assistance/documents/{self.document['id']}/mentor-replies.csv")
        self.assertEqual(exported.status_code, 200)
        self.assertTrue(exported.text.startswith("\ufeff"))
        self.assertIn("请统一研究对象的表述。", exported.text)
        self.assertIn("研究对象为大学生。", exported.text)

    async def test_material_retrieval_cites_real_paragraphs_and_isolates_ids(self):
        query = urlencode({"document_id": self.document["id"], "filename": "认知负荷.md", "expected_content_hash": content_hash(self.document["content"])})
        imported = await self.request("POST", "/document-assistance/materials?" + query, raw="# 定义\n\n认知负荷指工作记忆承担的加工负担。\n\n这是一段其他内容。".encode())
        self.assertEqual(imported.status_code, 201, imported.text)
        material = imported.json()["materials"][0]
        self.assertNotIn("segments", material)
        route = f"/document-assistance/documents/{self.document['id']}/questions"
        result = await self.request("POST", route, {**self.guard(), "question": "认知负荷如何定义"})
        self.assertEqual(result.status_code, 200, result.text)
        answer = result.json()
        self.assertFalse(answer["model_used"])
        self.assertIn("认知负荷指", answer["citations"][0]["quote"])
        self.assertEqual(answer["citations"][0]["source_name"], "认知负荷.md")
        self.assertIsNone(answer["citations"][0]["page"])
        other_route = f"/document-assistance/documents/{self.other['id']}/questions"
        denied = await self.request("POST", other_route, {**self.guard(self.other), "question": "认知负荷", "material_ids": [material["id"]]})
        self.assertEqual(denied.status_code, 404)
        duplicate = await self.request("POST", "/document-assistance/materials?" + query, raw="# 定义\n\n认知负荷指工作记忆承担的加工负担。\n\n这是一段其他内容。".encode())
        self.assertEqual(duplicate.status_code, 409)

    async def test_material_limits_hash_guard_and_no_hit_answer(self):
        base = {"document_id": self.document["id"], "filename": "资料.txt", "expected_content_hash": content_hash(self.document["content"])}
        large = await self.request("POST", "/document-assistance/materials?" + urlencode(base), raw=b"x", declared=10 * 1024 * 1024 + 1)
        self.assertEqual(large.status_code, 413)
        wrong = await self.request("POST", "/document-assistance/materials?" + urlencode({**base, "expected_content_hash": "0" * 64}), raw=b"data")
        self.assertEqual(wrong.status_code, 409)
        accepted = await self.request("POST", "/document-assistance/materials?" + urlencode(base), raw=b"alpha beta")
        self.assertEqual(accepted.status_code, 201)
        response = await self.request("POST", f"/document-assistance/documents/{self.document['id']}/questions", {**self.guard(), "question": "quantumxyz"})
        self.assertEqual(response.json()["citations"], [])
        self.assertIn("不代表", response.json()["answer"])

    async def test_metadata_api_is_scoped_and_does_not_call_network_in_test(self):
        reference = "[1] Test study. DOI:10.1234/example"
        with patch("app.document_assistance._fetch_json", return_value={"message": {"DOI": "10.1234/example", "title": ["Test study"]}}) as lookup:
            result = await self.request("POST", "/document-assistance/references/verify", {**self.guard(), "reference": reference})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(lookup.call_count, 1)
        self.assertEqual(result.json()["claim_support_status"], "not_checked")
        with patch("app.document_assistance._fetch_json") as unexpected:
            foreign = await self.request("POST", "/document-assistance/references/verify", {**self.guard(self.other), "reference": reference})
        self.assertEqual(foreign.status_code, 422)
        unexpected.assert_not_called()

    async def test_report_recomputes_saved_body_and_escapes_csv_formulas(self):
        await self.request("POST", "/document-assistance/analyze", {**self.guard(), "document_kind": "generic", "required_sections": []})
        result = await self.request("POST", "/document-assistance/mentor-comments", {**self.guard(), "comments": "=HYPERLINK(unsafe)"})
        self.assertEqual(result.status_code, 201)
        csv_result = await self.request("GET", f"/document-assistance/documents/{self.document['id']}/mentor-replies.csv")
        self.assertIn("'=HYPERLINK(unsafe)", csv_result.text)
        report = await self.request("GET", f"/document-assistance/documents/{self.document['id']}/report.md")
        self.assertEqual(report.status_code, 200)
        self.assertIn(content_hash(self.document["content"]), report.text)
        self.assertIn("汇报准备问题", report.text)
        self.assertNotIn("未识别到章节：摘要", report.text)
        stale_export = await self.request("GET", f"/document-assistance/documents/{self.document['id']}/report.md?expected_content_hash={'0' * 64}")
        self.assertEqual(stale_export.status_code, 409)


if __name__ == "__main__":
    unittest.main()
