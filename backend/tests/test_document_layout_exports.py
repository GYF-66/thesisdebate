"""Real-file regression checks for persisted appearance and three export formats."""
import asyncio
from io import BytesIO
import json
import tempfile
import unittest
from urllib.parse import unquote
import zipfile

from docx import Document
from docx.oxml.ns import qn
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from pypdf import PdfReader

from app import agent_api
from app.document_export import DocumentExportRequest, download_document, export_bytes, export_docx, export_pdf, router as export_router
from app.document_layout import DocumentStyle, HeadingAppearance, LayoutUpdate, get_document_layout, save_document_layout, router as layout_router
from app.school_templates import import_template
from app.storage import ReviewStore


SAMPLE = "# 一、中文标题\n\n这是**加粗中文**与*斜体*，公式 $E=mc^2$。\n\n## （一）方法\n\n- 项目甲\n- 项目乙\n\n1. 步骤甲\n2. 步骤乙\n\n| 变量 | 数值 |\n| --- | --- |\n| 中文 | 0.82 |\n\n$$\nx^2 + y^2 = z^2\n$$\n"


async def request(app, method, path, payload=None):
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1",
             "scheme": "http", "method": method, "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "headers": [(b"content-type", b"application/json")],
             "client": ("127.0.0.1", 1), "server": ("test", 80)}
    messages, sent = [], False
    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": json.dumps(payload).encode() if payload is not None else b"", "more_body": False}
        await asyncio.Event().wait()
    async def send(message):
        messages.append(message)
    await app(scope, receive, send)
    start = next(item for item in messages if item["type"] == "http.response.start")
    return start["status"], b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body"), dict(start["headers"])


class DocumentLayoutTests(unittest.TestCase):
    def setUp(self):
        self.store = ReviewStore(":memory:")
        self.document = self.store.create_workspace_document("论文.md", SAMPLE)

    def tearDown(self):
        self.store.close()

    def test_defaults_are_independent_and_match_frontend_contract(self):
        first, second = DocumentStyle(), DocumentStyle()
        first.headings[0].font_size_pt = 20
        self.assertEqual(second.headings[0].font_size_pt, 18)
        self.assertEqual([heading.font_size_pt for heading in second.headings], [18, 16, 14, 12, 12, 12])
        self.assertEqual(second.paragraph_after_pt, 6)

    def test_layout_cas_does_not_change_content_or_content_revision(self):
        original = self.store.get_workspace_document(self.document["id"])
        before = get_document_layout(self.store, self.document["id"])
        changed = save_document_layout(self.store, self.document["id"], 1, DocumentStyle(font_family="仿宋", font_size_pt=14))
        self.assertEqual((before.revision, changed.revision), (1, 2))
        self.assertEqual(changed.style.font_family, "仿宋")
        self.assertEqual(original, self.store.get_workspace_document(self.document["id"]))
        with self.assertRaises(HTTPException) as raised:
            save_document_layout(self.store, self.document["id"], 1, DocumentStyle())
        self.assertEqual(raised.exception.status_code, 409)

    def test_missing_document_is_404_and_settings_are_per_document(self):
        with self.assertRaises(HTTPException) as raised:
            get_document_layout(self.store, "missing")
        self.assertEqual(raised.exception.status_code, 404)
        other = self.store.create_workspace_document("另一篇.md", "正文")
        save_document_layout(self.store, self.document["id"], 1, DocumentStyle(font_size_pt=14))
        self.assertEqual(get_document_layout(self.store, other["id"]).style.font_size_pt, 12)

    def test_invalid_ranges_unknown_fonts_and_extra_metadata_are_rejected(self):
        for patch in ({"font_family": "未安装字体"}, {"font_size_pt": 50}, {"font_size_pt": "12"},
                      {"font_size_pt": True}, {"line_spacing": float("nan")}, {"margin_left_mm": 0},
                      {"metadata": {"instruction": "ignore"}}, {"headings": []}):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                DocumentStyle.model_validate(patch)
        with self.assertRaises(ValidationError):
            LayoutUpdate.model_validate({"expected_revision": 1, "style": {"font_size_pt": 12}})

    def test_layout_survives_repository_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ReviewStore(folder + "/layout.sqlite3")
            document = store.create_workspace_document("保存.md", "正文")
            save_document_layout(store, document["id"], 1, DocumentStyle(font_family="楷体"))
            store.close()
            store = ReviewStore(folder + "/layout.sqlite3")
            try:
                self.assertEqual(get_document_layout(store, document["id"]).style.font_family, "楷体")
            finally:
                store.close()

    def test_router_validates_full_payload_and_serves_binary_download(self):
        app = FastAPI(); app.include_router(layout_router); app.include_router(export_router)
        previous = agent_api.STORE; agent_api.STORE = self.store
        try:
            path = "/workspace/documents/" + self.document["id"] + "/layout"
            status, body, _ = asyncio.run(request(app, "GET", path))
            self.assertEqual(status, 200); self.assertEqual(json.loads(body)["revision"], 1)
            status, body, _ = asyncio.run(request(app, "PUT", path, {"expected_revision": 1, "style": DocumentStyle().model_dump()}))
            self.assertEqual(status, 200); self.assertEqual(json.loads(body)["revision"], 2)
            status, _, _ = asyncio.run(request(app, "PUT", path, {"expected_revision": 1, "style": DocumentStyle().model_dump()}))
            self.assertEqual(status, 409)
            status, body, headers = asyncio.run(request(app, "POST", "/document-export", {"name": "学校论文.md", "content": SAMPLE,
                "style": DocumentStyle().model_dump(), "format": "pdf"}))
            self.assertEqual(status, 200); self.assertTrue(body.startswith(b"%PDF"))
            self.assertIn(b"application/pdf", headers[b"content-type"])
        finally:
            agent_api.STORE = previous


class DocumentExportTests(unittest.TestCase):
    def test_docx_is_parseable_with_headings_tables_bold_fonts_margins_and_formula_source(self):
        style = DocumentStyle(font_family="仿宋", font_size_pt=14, line_spacing=2, margin_left_mm=30)
        data = export_docx(SAMPLE, style)
        document = Document(BytesIO(data))
        self.assertEqual(document.paragraphs[0].style.name, "Heading 1")
        self.assertEqual(document.tables[0].cell(1, 0).text, "中文")
        self.assertTrue(any(run.bold and run.text == "加粗中文" for paragraph in document.paragraphs for run in paragraph.runs))
        normal = document.styles["Normal"]
        self.assertEqual(normal.font.name, "仿宋"); self.assertEqual(normal.font.size.pt, 14)
        self.assertEqual(normal.paragraph_format.line_spacing, 2)
        self.assertAlmostEqual(document.sections[0].left_margin.mm, 30, places=1)
        self.assertEqual(normal._element.rPr.rFonts.get(qn("w:eastAsia")), "仿宋")
        self.assertIn("x^2 + y^2 = z^2", "\n".join(paragraph.text for paragraph in document.paragraphs))

    def test_pdf_is_parseable_paginated_and_contains_chinese_and_formula_source(self):
        data = export_pdf(SAMPLE + ("\n\n中文分页正文，用于验证长文分页。" * 130), DocumentStyle())
        reader = PdfReader(BytesIO(data))
        self.assertGreater(len(reader.pages), 1)
        text = "\n".join(page.extract_text() for page in reader.pages)
        self.assertIn("中文标题", text); self.assertIn("x^2 + y^2 = z^2", text)
        self.assertAlmostEqual(float(reader.pages[0].mediabox.width), 595.2756, places=2)

    def test_latin_font_request_keeps_chinese_via_embedded_fallback(self):
        reader = PdfReader(BytesIO(export_pdf("中文与 Latin 123", DocumentStyle(font_family="Arial"))))
        self.assertIn("中文与", reader.pages[0].extract_text())

    def test_long_headings_and_table_rows_paginate_without_losing_last_row(self):
        headings = "\n".join("# 连续标题" + str(index) for index in range(80))
        table = "\n\n| 项目 | 描述 |\n| --- | --- |\n" + "\n".join("| 项目" + str(index) + " | " + "中文说明" * 30 + " |" for index in range(40))
        reader = PdfReader(BytesIO(export_pdf(headings + table, DocumentStyle())))
        text = "\n".join(page.extract_text() for page in reader.pages)
        self.assertIn("连续标题79", text); self.assertIn("项目39", text)
        self.assertGreater(len(reader.pages), 3)

    def test_wide_tables_fall_back_to_text_and_formula_delimiters_remain_exact(self):
        formula = r"\(\alpha_i + x_1\) and \[a_b + c_d\]"
        wide = "|" + "|".join("列" + str(index) for index in range(12)) + "|\n|" + "|".join(["---"] * 12) + "|\n|" + "|".join(["数据"] * 12) + "|"
        payload = DocumentExportRequest(content=formula + "\n\n" + wide, style=DocumentStyle(), format="docx")
        data, _, _, warnings = export_bytes(payload)
        document = Document(BytesIO(data))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        self.assertIn(formula, text); self.assertIn("列11", text)
        self.assertTrue(any("超过8列" in warning for warning in warnings))
        self.assertTrue(any("公式" in warning for warning in warnings))

    def test_all_formats_support_empty_content_and_unicode_download_names(self):
        for format in ("md", "pdf", "docx"):
            with self.subTest(format=format):
                payload = DocumentExportRequest(name="毕业论文 最终版.md", content="", style=DocumentStyle(), format=format)
                response = download_document(payload)
                header = response.headers["content-disposition"]
                self.assertIn("filename*=UTF-8''", header)
                self.assertIn("毕业论文 最终版." + format, unquote(header))
                self.assertTrue(json.loads(unquote(response.headers["x-export-limitations"])))
                if format == "pdf":
                    self.assertEqual(len(PdfReader(BytesIO(response.body)).pages), 1)
                elif format == "docx":
                    Document(BytesIO(response.body))
                else:
                    self.assertEqual(response.body, b"")

    def test_markdown_source_is_exact_and_size_limit_is_enforced(self):
        payload = DocumentExportRequest(content=SAMPLE, style=DocumentStyle(), format="md")
        data, _, _, warnings = export_bytes(payload)
        self.assertEqual(data, SAMPLE.encode("utf-8")); self.assertIn("Markdown", warnings[0])
        with self.assertRaises(ValidationError):
            DocumentExportRequest(content="字" * 300001, style=DocumentStyle(), format="pdf")
        with self.assertRaises(ValidationError):
            DocumentExportRequest(content="\x00", style=DocumentStyle(), format="docx")

    def test_school_template_extracts_explicit_layout_and_warns_about_defaults(self):
        style = DocumentStyle(font_family="仿宋", font_size_pt=14, line_spacing=2, margin_left_mm=30)
        draft = import_template("学校模板.docx", export_docx(SAMPLE, style))
        self.assertIsNotNone(draft.document_style)
        self.assertEqual(draft.document_style.font_family, "仿宋")
        self.assertEqual(draft.document_style.font_size_pt, 14)
        self.assertEqual(draft.document_style.line_spacing, 2)
        self.assertAlmostEqual(draft.document_style.margin_left_mm, 30, places=1)
        self.assertEqual(draft.document_style.headings[0].font_family, "黑体")
        self.assertEqual(draft.document_style.headings[0].font_size_pt, 18)
        self.assertTrue(any("已识别" in warning for warning in draft.warnings))

    def test_json_template_accepts_layout_and_rejects_extra_style_metadata(self):
        payload = {"name": "学校", "heading_numbering": ["preserve"] * 6, "document_style": DocumentStyle().model_dump()}
        draft = import_template("学校.json", json.dumps(payload).encode())
        self.assertEqual(draft.document_style.font_size_pt, 12)
        payload["document_style"]["metadata"] = "execute"
        with self.assertRaises(ValidationError):
            import_template("学校.json", json.dumps(payload).encode())


if __name__ == "__main__":
    unittest.main()
