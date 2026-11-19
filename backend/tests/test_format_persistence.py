"""Regression coverage for durable format state and visible memory consistency."""
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app import agent_api, main
from app.storage import ReviewStore


class FormatPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.previous_store = agent_api.STORE
        self.previous_state = (dict(main.RUNS), dict(main.CHANGES), dict(main.SOURCES))
        agent_api.STORE = ReviewStore(":memory:")
        main.RUNS.clear()
        main.CHANGES.clear()
        main.SOURCES.clear()

    def tearDown(self):
        agent_api.STORE.close()
        agent_api.STORE = self.previous_store
        for target, previous in zip((main.RUNS, main.CHANGES, main.SOURCES), self.previous_state):
            target.clear()
            target.update(previous)

    def scan(self, **kwargs):
        return main.create_format_run(main.FormatRunCreate(
            content="# 标题😀\n段落  \n", document_type="markdown", **kwargs))

    def decide(self, change, decision):
        return main.decide_change(change.id, main.DecisionRequest(decision=decision))

    def state(self):
        return ({key: value.model_dump() for key, value in main.RUNS.items()},
                {key: value.model_dump() for key, value in main.CHANGES.items()}, dict(main.SOURCES))

    def test_failed_scan_publishes_no_partial_memory_or_durable_state(self):
        before = self.state()
        durable = agent_api.STORE.load_format_state()
        with patch.object(agent_api.STORE, "commit_format_state", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                self.scan()
        self.assertEqual(self.state(), before)
        self.assertEqual(agent_api.STORE.load_format_state(), durable)

    def test_failed_decision_preserves_preview_counts_and_decision(self):
        run = self.scan()
        before = self.state()
        head = agent_api.STORE.get_head(run.document_id)
        with patch.object(agent_api.STORE, "commit_format_state", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                self.decide(main.list_changes(run.id)[0], "accept")
        self.assertEqual(self.state(), before)
        self.assertEqual(agent_api.STORE.get_head(run.document_id), head)

    def test_head_tracks_exact_preview_through_accept_undo_restore(self):
        run = self.scan()
        changes = main.list_changes(run.id)
        first = changes[0]
        self.decide(first, "accept")
        self.assertEqual(agent_api.STORE.get_head(run.document_id)["content"], main.get_preview(run.id).preview)
        self.decide(first, "undo")
        self.assertEqual(agent_api.STORE.get_head(run.document_id)["content"], main.get_preview(run.id).source)
        self.decide(first, "accept")
        main.restore_run(run.id)
        self.assertEqual(agent_api.STORE.get_head(run.document_id)["content"], main.get_preview(run.id).source)
        self.assertEqual(len(agent_api.STORE.list_versions(run.document_id)), 5)

    def test_reject_persists_decision_without_duplicate_content_version(self):
        run = self.scan()
        change = main.list_changes(run.id)[0]
        self.decide(change, "reject")
        self.assertEqual(len(agent_api.STORE.list_versions(run.document_id)), 1)
        main.RUNS.clear()
        main.CHANGES.clear()
        main.SOURCES.clear()
        agent_api.restore_format_state()
        self.assertEqual(main.CHANGES[change.id].status, main.ChangeStatus.rejected)
        self.assertEqual(main.RUNS[run.id].rejected_count, 1)

    def test_failed_restore_preserves_accepted_state(self):
        run = self.scan()
        self.decide(main.list_changes(run.id)[0], "accept")
        before = self.state()
        with patch.object(agent_api.STORE, "commit_format_state", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                main.restore_run(run.id)
        self.assertEqual(self.state(), before)

    def test_rescan_requires_current_head_and_invalidates_old_run(self):
        run = self.scan()
        for expected in (None, "0" * 64):
            with self.assertRaises(HTTPException) as error:
                self.scan(document_id=run.document_id, expected_head_hash=expected)
            self.assertEqual(error.exception.status_code, 409)
        current = self.scan(document_id=run.document_id, expected_head_hash=run.source_hash)
        self.assertEqual(current.document_id, run.document_id)
        self.assertNotEqual(current.current_version_id, run.current_version_id)
        with self.assertRaises(HTTPException) as error:
            self.decide(main.list_changes(run.id)[0], "accept")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(agent_api.STORE.get_head(run.document_id)["id"], current.current_version_id)


if __name__ == "__main__":
    unittest.main()
