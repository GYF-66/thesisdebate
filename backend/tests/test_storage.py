import hashlib
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
import unittest

from app.storage import ReviewStore


class ReviewStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "nested" / "reviews.sqlite3"
        self.store = ReviewStore(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def reopen(self) -> None:
        self.store.close()
        self.store = ReviewStore(self.path)

    def test_native_schema_and_missing_records(self) -> None:
        with closing(sqlite3.connect(self.path)) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"records", "reviews", "events", "versions", "heads", "decisions"} <= tables)
        self.assertEqual(self.store.load_format_state(), {"runs": {}, "changes": {}, "sources": {}})
        self.assertIsNone(self.store.get_review("missing"))
        self.assertIsNone(self.store.get_version("missing"))
        self.assertIsNone(self.store.get_head("missing"))
        self.assertIsNone(self.store.get_decision("missing", "missing"))
        self.assertEqual(self.store.list_reviews(), [])
        self.assertEqual(self.store.list_versions("missing"), [])
        self.assertEqual(self.store.events("missing"), [])

    def test_format_snapshot_survives_reopen_and_replaces_removed_records(self) -> None:
        values = ({"run-1": {"id": "run-1", "status": "completed"}},
                  {"change-1": {"before": "😀原文", "status": "accepted"}}, {"run-1": "原文\n"})
        self.store.save_format_state(*values)
        self.reopen()
        self.assertEqual(self.store.load_format_state(), dict(zip(("runs", "changes", "sources"), values)))
        self.store.save_format_state({}, {}, {})
        self.reopen()
        self.assertEqual(self.store.load_format_state(), {"runs": {}, "changes": {}, "sources": {}})

    def test_invalid_json_does_not_clear_previous_state(self) -> None:
        self.store.save_format_state({"old": {"id": "old"}}, {}, {})
        with self.assertRaises(TypeError):
            self.store.save_format_state({"new": object()}, {}, {})
        self.assertEqual(set(self.store.load_format_state()["runs"]), {"old"})

    def test_review_filter_and_updates_survive_reopen(self) -> None:
        saved = self.store.put_review({"id": "a", "format_run_id": "f1", "status": "running", "roles": []})
        self.store.put_review({"id": "b", "format_run_id": "f2", "status": "completed"})
        self.store.put_review({"id": "a", "format_run_id": "f1", "status": "completed", "roles": [{"id": "judge"}]})
        self.reopen()
        actual = self.store.get_review("a")
        self.assertEqual(actual["status"], "completed")
        self.assertEqual(actual["created_at"], saved["created_at"])
        self.assertEqual(actual["roles"], [{"id": "judge"}])
        self.assertEqual([r["id"] for r in self.store.list_reviews("f1")], ["a"])
        self.assertEqual([r["id"] for r in self.store.list_reviews()], ["b", "a"])

    def test_format_commit_atomically_sets_version_ids_without_mutating_input(self) -> None:
        runs = {"r": {"id": "r", "base_version_id": None, "current_version_id": None}}
        first = self.store.commit_format_state(runs, {}, {"r": "before"}, "r", "doc", "before", "scan")
        self.assertIsNone(runs["r"]["current_version_id"])
        self.assertIsNone(runs["r"]["base_version_id"])
        self.assertEqual(first["run"]["base_version_id"], first["version"]["id"])
        changed = {"r": {**first["run"], "accepted_count": 1}}
        second = self.store.commit_format_state(
            changed, {"c": {"decision": "accepted"}}, {"r": "before"},
            "r", "doc", "after", "format_accept", expected_head_hash=first["version"]["hash"],
        )
        self.reopen()
        self.assertEqual(second["run"]["base_version_id"], first["version"]["id"])
        self.assertEqual(second["run"]["current_version_id"], second["version"]["id"])
        self.assertEqual(changed["r"]["current_version_id"], first["version"]["id"])
        self.assertEqual(self.store.get_head("doc"), second["version"])
        self.assertEqual(self.store.load_format_state()["runs"]["r"], second["run"])
        self.assertEqual(self.store.load_format_state()["changes"], {"c": {"decision": "accepted"}})

    def test_format_commit_without_version_preserves_head(self) -> None:
        initial = self.store.commit_format_state({"r": {"id": "r"}}, {}, {"r": "before"}, "r", "doc", "before", "scan")
        runs = {"r": {**initial["run"], "rejected_count": 1}}
        result = self.store.commit_format_state(
            runs, {"c": {"decision": "rejected"}}, {"r": "before"},
            "r", "doc", "before", "format_reject", create_version=False,
        )
        self.assertIsNone(result["version"])
        self.assertEqual(result["run"], runs["r"])
        self.assertEqual(self.store.get_head("doc"), initial["version"])
        self.assertEqual(len(self.store.list_versions("doc")), 1)
        self.assertEqual(self.store.load_format_state()["runs"], runs)

    def test_format_commit_json_failure_rolls_back_head_version_and_snapshot(self) -> None:
        initial = self.store.commit_format_state({"r": {"id": "r"}}, {}, {"r": "before"}, "r", "doc", "before", "scan")
        previous = self.store.load_format_state()
        proposed = {"r": {**initial["run"], "accepted_count": 1}}
        with self.assertRaises(TypeError):
            self.store.commit_format_state(
                proposed, {"bad": object()}, {"r": "before"}, "r", "doc", "after", "format_accept",
                expected_head_hash=initial["version"]["hash"],
            )
        self.reopen()
        self.assertEqual(self.store.load_format_state(), previous)
        self.assertEqual(self.store.get_head("doc"), initial["version"])
        self.assertEqual(self.store.list_versions("doc"), [initial["version"]])
        self.assertEqual(proposed["r"]["current_version_id"], initial["version"]["id"])

    def test_format_commit_head_conflict_leaves_snapshot_unchanged(self) -> None:
        initial = self.store.commit_format_state({"r": {"id": "r"}}, {}, {"r": "before"}, "r", "doc", "before", "scan")
        previous = self.store.load_format_state()
        with self.assertRaisesRegex(ValueError, "head conflict"):
            self.store.commit_format_state({"r": initial["run"]}, {}, {"r": "stale"}, "r", "doc", "after", "format_accept",
                                           expected_head_hash="0" * 64)
        self.assertEqual(self.store.load_format_state(), previous)
        self.assertEqual(self.store.get_head("doc"), initial["version"])
        self.assertEqual(len(self.store.list_versions("doc")), 1)

    def test_event_cursor_order_is_durable_and_scoped(self) -> None:
        first = self.store.add_event("r1", "role.started", {"role": "方法"})
        other = self.store.add_event("r2", "review.created", {})
        second = self.store.add_event("r1", "role.completed", {"result": [1, 2]})
        self.reopen()
        self.assertEqual(self.store.events("r1", first["id"]), [second])
        self.assertEqual(self.store.events("r2"), [other])
        self.assertGreater(second["id"], first["id"])

    def test_versions_are_immutable_and_head_history_survive_reopen(self) -> None:
        first = self.store.create_version("doc", "😀第一版\n")
        second = self.store.create_version("doc", "😀第二版\n", origin="manual_accept", expected_head_hash=first["hash"])
        self.reopen()
        self.assertEqual(first["hash"], hashlib.sha256("😀第一版\n".encode()).hexdigest())
        self.assertEqual(self.store.get_version(first["id"]), first)
        self.assertEqual(second["parent_id"], first["id"])
        self.assertEqual(self.store.get_head("doc"), second)
        self.assertEqual(self.store.list_versions("doc"), [second, first])

    def test_head_conflict_is_atomic_and_does_not_add_version(self) -> None:
        head = self.store.create_version("doc", "first")
        with self.assertRaisesRegex(ValueError, "head conflict"):
            self.store.create_version("doc", "stale", expected_head_hash="0" * 64)
        self.assertEqual(self.store.get_head("doc"), head)
        self.assertEqual(len(self.store.list_versions("doc")), 1)
        with self.assertRaisesRegex(ValueError, "head conflict"):
            self.store.create_version("new", "new", expected_head_hash=head["hash"])
        self.assertEqual(self.store.list_versions("new"), [])

    def test_cross_document_or_missing_parent_is_rejected(self) -> None:
        parent = self.store.create_version("doc1", "first")
        for parent_id in (parent["id"], "missing"):
            with self.assertRaisesRegex(ValueError, "parent"):
                self.store.create_version("doc2", "second", parent_id=parent_id)
        self.assertIsNone(self.store.get_head("doc2"))

    def test_two_store_connections_cannot_both_accept_stale_head(self) -> None:
        head = self.store.create_version("doc", "initial")
        other = ReviewStore(self.path)
        try:
            def update(store, content):
                try:
                    return store.create_version("doc", content, expected_head_hash=head["hash"])
                except ValueError as exc:
                    return str(exc)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda p: update(*p), ((self.store, "a"), (other, "b"))))
            self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
            self.assertEqual(sum(result == "version head conflict" for result in results), 1)
            self.assertEqual(len(self.store.list_versions("doc")), 2)
        finally:
            other.close()

    def test_decisions_are_idempotent_and_conflicting_choice_is_rejected(self) -> None:
        first = self.store.save_decision("review", "finding", "accept", {"version_id": "v1"})
        duplicate = self.store.save_decision("review", "finding", "accept", {"version_id": "v2"})
        self.assertEqual(duplicate, first)
        with self.assertRaisesRegex(ValueError, "decision conflict"):
            self.store.save_decision("review", "finding", "reject", {})
        self.reopen()
        self.assertEqual(self.store.get_decision("review", "finding"), first)

    def test_composed_decision_survives_lost_response_without_second_version(self) -> None:
        head = self.store.create_version("doc", "before")
        committed = self.store.commit_version_decision(
            "r", "f", "accept", "doc", "after", head["hash"], result={"accepted": True},
        )
        self.reopen()
        replay = self.store.commit_version_decision(
            "r", "f", "accept", "doc", "after", head["hash"], result={"accepted": False},
        )
        self.assertEqual(replay, committed)
        self.assertTrue(replay["result"]["accepted"])
        self.assertEqual(self.store.get_head("doc"), committed["result"]["version"])
        self.assertEqual(len(self.store.list_versions("doc")), 2)
        with self.assertRaisesRegex(ValueError, "decision conflict"):
            self.store.commit_version_decision("r", "f", "reject", "doc", "unused", head["hash"])

    def test_composed_decision_head_conflict_rolls_back_every_record(self) -> None:
        head = self.store.create_version("doc", "before")
        with self.assertRaisesRegex(ValueError, "head conflict"):
            self.store.commit_version_decision("r", "f", "accept", "doc", "after", "0" * 64)
        self.assertIsNone(self.store.get_decision("r", "f"))
        self.assertEqual(self.store.get_head("doc"), head)
        self.assertEqual(self.store.list_versions("doc"), [head])

    def test_recovery_preserves_completed_roles_and_is_idempotent(self) -> None:
        roles = [{"id": "method", "status": "completed", "result": {"findings": ["f"]}},
                 {"id": "judge", "status": "running"}]
        self.store.put_review({"id": "r", "status": "running", "roles": roles})
        self.store.put_review({"id": "q", "status": "queued"})
        self.store.put_review({"id": "done", "status": "completed", "result": {"valid": True}})
        self.reopen()
        recovered = self.store.recover_interrupted()
        self.assertEqual({r["id"] for r in recovered}, {"r", "q"})
        self.assertEqual(self.store.get_review("r")["roles"], roles)
        self.assertEqual(self.store.get_review("r")["status"], "partial")
        self.assertTrue(self.store.get_review("r")["interrupted"])
        self.assertEqual(self.store.get_review("done")["status"], "completed")
        self.assertEqual(self.store.events("r")[0]["type"], "review.interrupted")
        self.assertEqual(self.store.recover_interrupted(), [])
        self.assertEqual(len(self.store.events("r")), 1)


if __name__ == "__main__":
    unittest.main()
