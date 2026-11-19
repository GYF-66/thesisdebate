import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import unittest

from fastapi import HTTPException

from app import main
from app.revision_review import aligned_diff, source_digest


class RevisionReviewTests(unittest.TestCase):
    def setUp(self):
        main.RUNS.clear()
        main.CHANGES.clear()
        main.SOURCES.clear()

    def run_for(self, source, rules=None):
        return main.create_format_run(main.FormatRunCreate(
            document_type="markdown", content=source, requested_rules=rules,
        ))

    def accept(self, change, expected_source_hash=None):
        return main.decide_change(change.id, main.DecisionRequest(
            decision=main.ChangeDecision.accept, expected_source_hash=expected_source_hash,
        ))

    def assert_conflict(self, call):
        with self.assertRaises(HTTPException) as error:
            call()
        self.assertEqual(error.exception.status_code, 409)

    def duplicate(self, change, **updates):
        clone = change.model_copy(update={"id": "conflicting-change", **updates})
        main.CHANGES[clone.id] = clone
        return clone

    def test_reference_and_trailing_spaces_do_not_propose_overlapping_edits(self):
        run = self.run_for("参考文献\n- Entry. 2024.  \n", ["all"])
        changes = main.list_changes(run.id)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].rule_id, "reference.gb7714")
        self.accept(changes[0])
        self.assertEqual(main.get_preview(run.id).preview, "参考文献\n[1] Entry. 2024.\n")
        review = main.get_revision_review(run.id)
        self.assertEqual(review.status, "passed")
        self.assertEqual(review.applied_count, 1)

    def test_overlap_accept_is_rejected_without_state_change(self):
        run = self.run_for("# 标题\n", ["heading.numbering"])
        change = main.list_changes(run.id)[0]
        other = self.duplicate(change, replacement="# 错误覆盖")
        self.accept(change)
        self.assert_conflict(lambda: self.accept(other))
        self.assertEqual(main.CHANGES[other.id].status, main.ChangeStatus.pending)
        self.assertEqual(main.RUNS[run.id].accepted_count, 1)

    def test_corrupt_accepted_overlap_is_reported_and_not_double_applied(self):
        run = self.run_for("# 标题\n", ["heading.numbering"])
        change = main.list_changes(run.id)[0]
        self.accept(change)
        other = self.duplicate(change, status=main.ChangeStatus.accepted, replacement="# 错误覆盖")
        preview = main.get_preview(run.id)
        self.assertEqual(preview.preview, "# 1 标题\n")
        self.assertEqual(preview.applied_count, 1)
        self.assertTrue(any(other.id in note for note in preview.notes))
        review = main.get_revision_review(run.id)
        self.assertEqual(review.status, "blocked")
        self.assertEqual(review.accepted_count, 2)
        self.assertEqual(review.blocked_change_ids, [other.id])

    def test_optional_expected_source_hash_is_a_non_mutating_guard(self):
        run = self.run_for("# 标题\n", ["heading.numbering"])
        change = main.list_changes(run.id)[0]
        self.assert_conflict(lambda: self.accept(change, "0" * 64))
        self.assertEqual(main.CHANGES[change.id].status, main.ChangeStatus.pending)
        self.accept(change, source_digest("# 标题\n"))
        self.assertEqual(main.get_preview(run.id).applied_count, 1)

    def test_stored_source_mutation_blocks_old_proposals_and_review(self):
        run = self.run_for("# 标题\n正文\n", ["heading.numbering"])
        change = main.list_changes(run.id)[0]
        self.accept(change)
        main.SOURCES[run.id] += "新增段落\n"
        self.assert_conflict(lambda: self.accept(change))
        self.assertEqual(main.get_preview(run.id).applied_count, 0)
        review = main.get_revision_review(run.id)
        self.assertEqual(review.status, "blocked")
        self.assertIn(change.id, review.blocked_change_ids)

    def test_invalid_utf16_ranges_quotes_and_context_fail_closed(self):
        for updates in [
            {"start": 1},  # middle of emoji's UTF-16 surrogate pair
            {"start": -1},
            {"end": 99999},
            {"start": 9, "end": 3},
            {"quote": "different"},
            {"context_hash": "0" * 16},
        ]:
            with self.subTest(updates=updates):
                run = self.run_for("😀\n# 标题\n", ["heading.numbering"])
                change = main.list_changes(run.id)[0]
                bad = change.model_copy(update={"anchor": change.anchor.model_copy(update=updates)})
                main.CHANGES[bad.id] = bad
                self.assert_conflict(lambda: self.accept(bad))
                main.CHANGES[bad.id] = bad.model_copy(update={"status": main.ChangeStatus.accepted})
                self.assertEqual(main.get_preview(run.id).applied_count, 0)
                self.assertEqual(main.get_revision_review(run.id).status, "blocked")

    def test_document_version_mismatch_is_rejected(self):
        run = self.run_for("# 标题\n", ["heading.numbering"])
        change = main.list_changes(run.id)[0].model_copy(update={"document_version_id": "other"})
        main.CHANGES[change.id] = change
        self.assert_conflict(lambda: self.accept(change))
        main.CHANGES[change.id] = change.model_copy(update={"status": main.ChangeStatus.accepted})
        self.assertEqual(main.get_preview(run.id).applied_count, 0)
        self.assertEqual(main.get_revision_review(run.id).blocked_change_ids, [change.id])

    def test_concurrent_overlapping_accepts_have_only_one_winner(self):
        run = self.run_for("# 标题\n", ["heading.numbering"])
        change = main.list_changes(run.id)[0]
        other = self.duplicate(change, replacement="# 2 标题")
        def decide(item):
            try:
                self.accept(item)
                return 200
            except HTTPException as error:
                return error.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(decide, [change, other]))
        self.assertEqual(sorted(statuses), [200, 409])
        self.assertEqual(main.get_preview(run.id).applied_count, 1)

    def test_boundary_insertions_work_with_replacement_and_emoji(self):
        run = self.run_for("😀前文\r\n# 标题\r\n正文\r\n", ["heading"])
        for change in main.list_changes(run.id):
            self.accept(change)
        preview = main.get_preview(run.id)
        self.assertEqual(preview.applied_count, 3)
        self.assertIn("# 1 标题", preview.preview)
        self.assertIn("正文\r\n", preview.preview)
        self.assertEqual(main.get_revision_review(run.id).status, "passed")

    def test_pending_decisions_are_visible_and_demo_never_passes(self):
        run = self.run_for("# 标题\n", ["heading.numbering"])
        review = main.get_revision_review(run.id)
        self.assertEqual(review.status, "needs_review")
        self.assertEqual(review.pending_count, 1)
        self.assertFalse(review.llm_enabled)
        demo = main.create_format_run(main.FormatRunCreate())
        self.assertEqual(main.get_revision_review(demo.id).status, "blocked")
        self.assert_conflict(lambda: self.accept(main.list_changes(demo.id)[0]))

    def test_aligned_diff_does_not_treat_shifted_lines_as_edits(self):
        rows = aligned_diff("one\ntwo\nthree\n", "one\ninserted\ntwo\nthree\n")
        self.assertEqual([row.kind for row in rows], ["equal", "insert", "equal", "equal"])
        self.assertEqual((rows[2].source_line_no, rows[2].preview_line_no), (2, 3))
        self.assertEqual(rows[1].before, None)

    def test_tokens_warn_for_real_numeric_formula_and_citation_changes(self):
        source = "正文 42 $x^2$ 引用[2]  \n"
        run = self.run_for(source, ["paragraph.spacing"])
        change = main.list_changes(run.id)[0].model_copy(update={"replacement": "正文 43 $x^3$ 引用[3]"})
        main.CHANGES[change.id] = change
        self.accept(change)
        review = main.get_revision_review(run.id)
        self.assertEqual(review.status, "needs_review")
        self.assertEqual({item.id for item in review.checks if item.status == "warning"},
                         {"tokens.numbers", "tokens.formulas", "tokens.citations"})
        self.assertIn("-正文 42", review.unified_diff)
        self.assertEqual(review.source_hash, source_digest(source))

    def test_unchanged_symbols_do_not_claim_semantic_validation(self):
        run = self.run_for("正文 42 $x^2$ 引用[2]  \n", ["paragraph.spacing"])
        self.accept(main.list_changes(run.id)[0])
        review = main.get_revision_review(run.id)
        self.assertEqual(review.status, "passed")
        self.assertIn("未进行语义推理", review.summary)
        self.assertEqual(review.engine, "deterministic")

    def test_review_does_not_mutate_decisions_and_tracks_undo(self):
        run = self.run_for("# 标题\n", ["heading.numbering"])
        change = main.list_changes(run.id)[0]
        self.accept(change)
        before = main.CHANGES[change.id].model_dump()
        main.get_revision_review(run.id)
        self.assertEqual(main.CHANGES[change.id].model_dump(), before)
        main.decide_change(change.id, main.DecisionRequest(decision=main.ChangeDecision.undo))
        self.assertEqual(main.get_revision_review(run.id).status, "needs_review")
        self.assertEqual(main.get_revision_review(run.id).applied_count, 0)

    def test_http_contract_without_optional_httpx_dependency(self):
        async def request(method, path, payload=None):
            data = json.dumps(payload or {}).encode()
            messages = []
            async def receive():
                return {"type": "http.request", "body": data, "more_body": False}
            async def send(message):
                messages.append(message)
            await main.app({"type": "http", "asgi": {"version": "3.0"},
                            "http_version": "1.1", "method": method, "scheme": "http",
                            "path": path, "raw_path": path.encode(), "query_string": b"",
                            "root_path": "", "headers": [(b"content-type", b"application/json")],
                            "server": ("test", 80), "client": ("test", 123)}, receive, send)
            status = next(item["status"] for item in messages if item["type"] == "http.response.start")
            body = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
            return status, json.loads(body)
        async def scenario():
            code, run = await request("POST", "/format-runs", {"content": "# 标题\n",
                "document_type": "markdown", "requested_rules": ["heading.numbering"]})
            self.assertEqual(code, 200)
            code, changes = await request("GET", f"/format-runs/{run['id']}/changes")
            self.assertEqual(code, 200)
            code, _ = await request("POST", f"/format-changes/{changes[0]['id']}/decision",
                                    {"decision": "accept", "expected_source_hash": "0" * 64})
            self.assertEqual(code, 409)
            code, review = await request("GET", f"/format-runs/{run['id']}/review")
            self.assertEqual(code, 200)
            self.assertEqual(review["engine"], "deterministic")
            self.assertEqual(review["pending_count"], 1)
            self.assertEqual(len(review["source_hash"]), 64)
            code, _ = await request("GET", "/format-runs/missing/review")
            self.assertEqual(code, 404)
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
