import unittest

from app import main


class FormatApiTests(unittest.TestCase):
    def setUp(self) -> None:
        main.RUNS.clear()
        main.CHANGES.clear()
        main.SOURCES.clear()

    def test_content_run_proposes_numbering_and_reference_changes(self) -> None:
        payload = main.FormatRunCreate(
            profile_id="nju-2026",
            document_version_id="v-content",
            document_name="论文.md",
            document_type="markdown",
            requested_rules=["heading.numbering", "reference.gb7714"],
            content="# 研究背景\n正文\n## 研究方法\n采用问卷法。\n参考文献\n- Smith. Learning systems. 2024.\n",
        )
        run = main.create_format_run(payload)
        changes = main.list_changes(run.id)

        self.assertEqual(run.status, "completed")
        self.assertEqual(run.requested_rules, ["heading.numbering", "reference.gb7714"])
        self.assertTrue(any(item.rule_id == "heading.numbering" for item in changes))
        reference = next(item for item in changes if item.rule_id == "reference.gb7714")
        self.assertEqual(reference.replacement, "[1] Smith. Learning systems. 2024.")

        main.decide_change(reference.id, main.DecisionRequest(decision=main.ChangeDecision.accept))
        heading = next(item for item in changes if item.rule_id == "heading.numbering")
        main.decide_change(heading.id, main.DecisionRequest(decision=main.ChangeDecision.accept))
        preview = main.get_preview(run.id)

        self.assertIn("# 1 研究背景", preview.preview)
        self.assertIn("[1] Smith. Learning systems. 2024.", preview.preview)
        self.assertEqual(preview.applied_count, 2)
        self.assertEqual(len(preview.accepted_change_ids), 2)

    def test_profiles_expose_versioned_rule_catalog(self) -> None:
        profile = next(item for item in main.list_profiles() if item.id == "nju-2026")
        self.assertEqual(profile.version, "2026.1")
        self.assertEqual(profile.rules_count, len(profile.rules))
        self.assertIn("heading.numbering", {rule.id for rule in profile.rules})

    def test_run_records_selected_profile_version(self) -> None:
        run = main.create_format_run(
            main.FormatRunCreate(
                profile_id="generic-thesis",
                document_type="markdown",
                content="# Draft\n",
            )
        )

        self.assertEqual(run.profile_id, "generic-thesis")
        self.assertEqual(run.profile_version, "1.0")

    def test_requested_rules_filter_is_deterministic(self) -> None:
        run = main.create_format_run(
            main.FormatRunCreate(
                content="# 未编号标题\n参考文献\n- A. 2024.\n",
                requested_rules=["paragraph.spacing"],
                document_type="markdown",
            )
        )
        self.assertEqual(main.list_changes(run.id), [])

    def test_heading_spacing_and_utf16_anchors_preview_correctly(self) -> None:
        source = "😀前文\n# 标题\n正文\n"
        run = main.create_format_run(
            main.FormatRunCreate(
                content=source,
                requested_rules=["heading"],
                document_type="markdown",
            )
        )
        changes = main.list_changes(run.id)
        self.assertEqual(len(changes), 3)
        heading = next(item for item in changes if item.rule_id == "heading.numbering")
        self.assertEqual(heading.anchor.start, 5)

        for change in changes:
            main.decide_change(change.id, main.DecisionRequest(decision=main.ChangeDecision.accept))
        preview = main.get_preview(run.id)

        self.assertEqual(preview.preview, "😀前文\n\n# 1 标题\n\n正文\n")
        self.assertEqual(preview.applied_count, 3)

    def test_heading_numbering_handles_missing_parent_and_chinese_chapter_numbers(self) -> None:
        run = main.create_format_run(
            main.FormatRunCreate(
                content="## 研究方法\n## 第三章 已有章节编号\n## 3.1 已有小节编号\n",
                requested_rules=["heading.numbering"],
                document_type="markdown",
            )
        )

        changes = main.list_changes(run.id)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].replacement, "## 1.1 研究方法")

    def test_no_content_keeps_demo_behavior(self) -> None:
        run = main.create_format_run(main.FormatRunCreate())
        self.assertEqual(run.changes_count, 4)
        self.assertEqual(len(main.list_changes(run.id)), 4)
        preview = main.get_preview(run.id)
        self.assertEqual(preview.source, "")
        self.assertTrue(preview.notes)

    def test_latex_endpoint_is_preview_validation_only(self) -> None:
        valid = main.compile_latex(main.LatexCompileRequest(source="\\section{Intro}"))
        invalid = main.compile_latex(main.LatexCompileRequest(source="\\section{Intro"))
        self.assertEqual(valid.status, "preview_validated")
        self.assertIsNone(valid.pdf_url)
        self.assertEqual(invalid.status, "preview_rejected")
        self.assertTrue(invalid.errors)

    def test_undo_decision_and_restore_run_clear_applied_changes(self) -> None:
        run = main.create_format_run(
            main.FormatRunCreate(
                content="# 标题\n正文\n",
                document_type="markdown",
                requested_rules=["heading.numbering"],
            )
        )
        change = main.list_changes(run.id)[0]
        main.decide_change(change.id, main.DecisionRequest(decision=main.ChangeDecision.accept))
        self.assertEqual(main.get_preview(run.id).applied_count, 1)
        main.decide_change(change.id, main.DecisionRequest(decision=main.ChangeDecision.undo))
        self.assertEqual(main.get_preview(run.id).applied_count, 0)
        main.decide_change(change.id, main.DecisionRequest(decision=main.ChangeDecision.accept))
        restored = main.restore_run(run.id)
        self.assertEqual(restored.applied_count, 0)
        self.assertEqual(restored.pending_change_ids, [change.id])


if __name__ == "__main__":
    unittest.main()
