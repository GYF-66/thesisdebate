"""Regression coverage for duplicate heading prefixes and uploaded school rules."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from app import agent_api, main
from app.heading_numbering import format_heading, heading_body, parse_prefix
from app.revision_review import _protected_tokens
from app.school_templates import SchoolTemplateDraft, import_template
from app.storage import ReviewStore
from tests.test_document_import import OFFICE, package, paragraph, W
from tests.test_agent_api import ASGIClient, ASGIResponse


async def raw_upload(target, body):
    path, _, query = target.partition("?")
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
             "http_version": "1.1", "scheme": "http", "method": "POST", "path": path,
             "raw_path": path.encode(), "query_string": query.encode(), "root_path": "",
             "headers": [(b"content-type", b"application/octet-stream")],
             "client": ("127.0.0.1", 1), "server": ("test", 80)}
    sent, messages = False, []

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        messages.append(message)

    await main.app(scope, receive, send)
    start = next(item for item in messages if item["type"] == "http.response.start")
    return ASGIResponse(start["status"], b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body"), start["headers"])


class HeadingProfileTests(unittest.TestCase):
    def setUp(self):
        self.previous_store = agent_api.STORE
        self.previous_state = tuple(dict(value) for value in (main.RUNS, main.CHANGES, main.SOURCES))
        agent_api.STORE = ReviewStore(":memory:")
        for value in (main.RUNS, main.CHANGES, main.SOURCES):
            value.clear()

    def tearDown(self):
        agent_api.STORE.close()
        agent_api.STORE = self.previous_store
        for value, previous in zip((main.RUNS, main.CHANGES, main.SOURCES), self.previous_state):
            value.clear()
            value.update(previous)

    def changes(self, content, styles=None):
        return main.make_content_changes("heading-test", "v1", content, ["heading.numbering"], styles)

    def apply(self, content, styles=None):
        changes = self.changes(content, styles)
        accepted = [change.model_copy(update={"status": main.ChangeStatus.accepted}) for change in changes]
        return main.build_preview(content, accepted, "v1")[0]

    def test_existing_chinese_parenthesized_decimal_and_bold_prefixes_are_kept(self):
        content = "\n\n".join(["# 一、绪论", "# 二、核心接口清单", "## （一）背景", "### （1）设计", "### (2) 方法", "## 1.1 研究方法", "# 第三章 结果", "# **一、设计范围**", "# **二、** 范围", "## 2.1. 标题", "# 3. 标题"])
        self.assertEqual(self.changes(content), [])

    def test_old_arabic_chinese_double_prefix_is_repaired_and_rescan_is_idempotent(self):
        source = "# 2 二、核心接口清单\n\n# 4 **三、** 设计范围\n\n# **5 四、整节加粗**\n\n# 2 **二、整节加粗**\n"
        expected = "# 二、核心接口清单\n\n# **三、** 设计范围\n\n# **四、整节加粗**\n\n# **二、整节加粗**\n"
        self.assertEqual(self.apply(source), expected)
        self.assertEqual(self.changes(expected), [])
        self.assertEqual(_protected_tokens(source), _protected_tokens(expected))

    def test_numeric_subjects_are_not_mistaken_for_structural_prefixes(self):
        for title in ("2026年趋势", "3D 模型", "2型系统", "1.5米设备"):
            with self.subTest(title=title):
                self.assertIsNone(parse_prefix(title))
                result = self.apply("# " + title + "\n")
                self.assertEqual(result, "# 1 " + title + "\n")
                self.assertEqual(_protected_tokens("# " + title), _protected_tokens(result))

    def test_school_rules_replace_prefix_and_reset_nested_counts(self):
        styles = ["chinese", "chinese-paren", "arabic-paren", "decimal", "preserve", "preserve"]
        source = "# 2 二、绪论\n\n## 1.1 背景\n\n### （3）方法\n\n#### 实验\n\n# 第二章 结果\n\n## 7.3 讨论\n"
        expected = "# 一、 绪论\n\n## （一） 背景\n\n### （1） 方法\n\n#### 1.1.1.1 实验\n\n# 二、 结果\n\n## （一） 讨论\n"
        self.assertEqual(self.apply(source, styles), expected)
        self.assertEqual(self.changes(expected, styles), [])
        self.assertEqual(_protected_tokens(source), _protected_tokens(expected))

    def test_abstract_references_and_acknowledgements_do_not_get_chapter_numbers(self):
        source = "# 摘要\n\n# Abstract\n\n# 正文\n\n# **参考文献**\n\n# 致谢\n\n# 附录A 调查问卷\n"
        result = self.apply(source, ["chinese"] * 6)
        self.assertIn("# 一、 正文", result)
        self.assertEqual(len(self.changes(source, ["chinese"] * 6)), 1)

    def test_none_policy_removes_only_structure_and_keeps_subject_numbers(self):
        source = "# 二、2026年结果\n\n## 1.1 数值0.82\n"
        expected = "# 2026年结果\n\n## 数值0.82\n"
        self.assertEqual(self.apply(source, ["none"] * 6), expected)
        self.assertEqual(_protected_tokens(source), _protected_tokens(expected))
        self.assertNotEqual(_protected_tokens(expected), _protected_tokens(expected.replace("0.82", "0.83")))

    def test_unambiguous_existing_number_sets_missing_successor_in_preserve_mode(self):
        self.assertEqual(self.apply("# 三、已有\n\n# 后续\n"), "# 三、已有\n\n# 4 后续\n")

    def test_json_import_is_a_draft_and_rejects_invalid_styles_or_level_count(self):
        payload = {"name": "学校甲", "institution": "甲校", "heading_numbering": ["chinese", "chinese-paren", "arabic-paren", "decimal", "preserve", "none"]}
        draft = import_template("甲校.json", json.dumps(payload, ensure_ascii=False).encode())
        self.assertEqual(draft.source_name, "甲校.json")
        self.assertEqual({profile.id for profile in main.list_profiles()}, set(main.PROFILES))
        for styles in (["chinese"], ["run-code"] * 6):
            with self.assertRaises(ValidationError):
                import_template("bad.json", json.dumps({**payload, "heading_numbering": styles}).encode())

    def test_docx_manual_heading_samples_infer_styles_and_warn_on_unknown_levels(self):
        data = package(paragraph("一、一级标题", "Heading1") + paragraph("（一）二级标题", "Heading2") + paragraph("（1）三级标题", "Heading3"))
        draft = import_template("示例学校.docx", data)
        self.assertEqual(draft.heading_numbering[:3], ["chinese", "chinese-paren", "arabic-paren"])
        self.assertEqual(draft.heading_numbering[3:], ["preserve"] * 3)
        self.assertTrue(any("第 4 级" in warning for warning in draft.warnings))

    def test_docx_automatic_word_numbering_is_inferred_from_heading_style(self):
        styles = '<w:styles xmlns:w="' + W + '"><w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:pPr><w:numPr><w:numId w:val="7"/><w:ilvl w:val="0"/></w:numPr></w:pPr></w:style></w:styles>'
        numbering = '<w:numbering xmlns:w="' + W + '"><w:abstractNum w:abstractNumId="3"><w:lvl w:ilvl="0"><w:numFmt w:val="chineseCounting"/><w:lvlText w:val="%1、"/></w:lvl></w:abstractNum><w:num w:numId="7"><w:abstractNumId w:val="3"/></w:num></w:numbering>'
        relationships = '<Relationship Id="styles" Type="' + OFFICE + 'styles" Target="styles.xml"/><Relationship Id="numbers" Type="' + OFFICE + 'numbering" Target="numbering.xml"/>'
        draft = import_template("自动编号.docx", package(paragraph("一级标题", "Heading1"), {"word/styles.xml": styles, "word/numbering.xml": numbering}, relationships))
        self.assertEqual(draft.heading_numbering[0], "chinese")

    def test_docx_direct_heading_numbering_is_inferred(self):
        numbering = '<w:numbering xmlns:w="' + W + '"><w:abstractNum w:abstractNumId="3"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/><w:lvlText w:val="（%1）"/></w:lvl></w:abstractNum><w:num w:numId="7"><w:abstractNumId w:val="3"/></w:num></w:numbering>'
        body = '<w:p><w:pPr><w:pStyle w:val="Heading1"/><w:numPr><w:numId w:val="7"/><w:ilvl w:val="0"/></w:numPr></w:pPr><w:r><w:t>一级标题</w:t></w:r></w:p>'
        relationships = '<Relationship Id="numbers" Type="' + OFFICE + 'numbering" Target="numbering.xml"/>'
        draft = import_template("直接编号.docx", package(body, {"word/numbering.xml": numbering}, relationships))
        self.assertEqual(draft.heading_numbering[0], "arabic-paren")

    def test_profile_persists_and_selected_profile_changes_actual_format_output(self):
        with tempfile.TemporaryDirectory() as directory:
            agent_api.STORE.close()
            path = Path(directory) / "profiles.sqlite3"
            agent_api.STORE = ReviewStore(path)
            created = main.create_school_template(SchoolTemplateDraft(name="甲校", heading_numbering=["chinese"] * 6))
            agent_api.STORE.close()
            agent_api.STORE = ReviewStore(path)
            loaded = main.resolve_format_profile(created.id)
            self.assertEqual(loaded.heading_numbering, ["chinese"] * 6)
            run = main.create_format_run(main.FormatRunCreate(profile_id=created.id, content="# 研究方法\n", requested_rules=["heading.numbering"]))
            self.assertEqual(main.list_changes(run.id)[0].after, "# 一、 研究方法")
            self.assertEqual(main.resolve_format_profile(created.id).name, "甲校")
            agent_api.STORE.close()
            agent_api.STORE = ReviewStore(":memory:")

    def test_http_upload_confirm_and_scan_use_the_confirmed_rule(self):
        async def scenario():
            client = ASGIClient(main.app)
            payload = {"name": "乙校", "heading_numbering": ["chapter"] * 6}
            response = await raw_upload("/format-profiles/import?filename=school.json", json.dumps(payload).encode())
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual({profile["id"] for profile in (await client.get("/format-profiles")).json()}, set(main.PROFILES))
            saved = await client.post("/format-profiles", json=response.json())
            self.assertEqual(saved.status_code, 201, saved.text)
            run = await client.post("/format-runs", json={"profile_id": saved.json()["id"], "content": "# 2 二、核心接口清单\n", "requested_rules": ["heading.numbering"]})
            changes = await client.get("/format-runs/" + run.json()["id"] + "/changes")
            self.assertEqual(changes.json()[0]["after"], "# 第一章 核心接口清单")
            invalid = await raw_upload("/format-profiles/import?filename=school.pdf", b"test")
            self.assertEqual(invalid.status_code, 422)
        asyncio.run(scenario())

    def test_suggested_preview_shows_pending_difference_without_changing_decisions_or_head(self):
        source = "# 2 二、核心接口清单\n\n正文  \n"
        run = main.create_format_run(main.FormatRunCreate(content=source, requested_rules=["heading.numbering", "paragraph.spacing"]))
        before_head = agent_api.STORE.get_head(run.document_id)
        before_changes = [change.model_dump() for change in main.list_changes(run.id)]
        suggested = main.get_suggested_preview(run.id)
        self.assertEqual(suggested.source, source)
        self.assertIn("# 二、核心接口清单", suggested.preview)
        self.assertEqual(suggested.applied_count, 0)
        self.assertEqual(suggested.proposed_count, 2)
        self.assertTrue(any(row.kind != "equal" for row in suggested.diff_rows))
        self.assertEqual([change.model_dump() for change in main.list_changes(run.id)], before_changes)
        self.assertEqual(agent_api.STORE.get_head(run.document_id), before_head)
        self.assertEqual(main.get_preview(run.id).preview, source)
        changes = main.list_changes(run.id)
        main.decide_change(changes[0].id, main.DecisionRequest(decision=main.ChangeDecision.accept))
        suggested = main.get_suggested_preview(run.id)
        self.assertEqual((suggested.applied_count, suggested.proposed_count), (1, 1))
        main.decide_change(changes[1].id, main.DecisionRequest(decision=main.ChangeDecision.reject))
        suggested = main.get_suggested_preview(run.id)
        self.assertEqual((suggested.applied_count, suggested.proposed_count), (1, 0))
        self.assertTrue(suggested.preview.endswith("正文  \n"))

    def test_suggested_preview_keeps_accepted_edits_and_filters_conflicting_pending_edits(self):
        run = main.create_format_run(main.FormatRunCreate(content="# 标题\n", requested_rules=["heading.numbering"]))
        original = main.list_changes(run.id)[0]
        main.decide_change(original.id, main.DecisionRequest(decision=main.ChangeDecision.accept))
        conflict = original.model_copy(update={"id": "proposal-conflict", "status": main.ChangeStatus.pending, "replacement": "# 不应覆盖"})
        main.CHANGES[conflict.id] = conflict
        head = agent_api.STORE.get_head(run.document_id)
        suggested = main.get_suggested_preview(run.id)
        self.assertEqual(suggested.preview, "# 1 标题\n")
        self.assertEqual((suggested.applied_count, suggested.proposed_count), (1, 0))
        self.assertIn(conflict.id, suggested.blocked_change_ids)
        self.assertTrue(any("冲突" in note for note in suggested.notes))
        self.assertEqual(agent_api.STORE.get_head(run.document_id), head)
        self.assertEqual(main.CHANGES[conflict.id].status, main.ChangeStatus.pending)

    def test_suggested_preview_stops_on_stale_source_without_writing_anything(self):
        run = main.create_format_run(main.FormatRunCreate(content="# 标题\n", requested_rules=["heading.numbering"]))
        before = agent_api.STORE.get_head(run.document_id)
        main.SOURCES[run.id] += "外部改动\n"
        suggested = main.get_suggested_preview(run.id)
        self.assertEqual(suggested.proposed_count, 0)
        self.assertEqual(suggested.preview, main.SOURCES[run.id])
        self.assertEqual(len(suggested.blocked_change_ids), 1)
        self.assertEqual(agent_api.STORE.get_head(run.document_id), before)


if __name__ == "__main__":
    unittest.main()
