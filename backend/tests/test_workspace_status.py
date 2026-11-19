"""Compact batched task status for document-library badges."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi import HTTPException

from app import agent_api
from app.storage import ReviewStore


class WorkspaceStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = ReviewStore(Path(self.temp.name) / "status.sqlite3")
        self.previous_store = agent_api.STORE
        agent_api.STORE = self.store

    def tearDown(self):
        agent_api.STORE = self.previous_store
        self.store.close()
        self.temp.cleanup()

    def add_review(self, review_id, document_id, run_id, status, findings=None, **extra):
        return self.store.put_review({
            "id": review_id,
            "document_id": document_id,
            "format_run_id": run_id,
            "status": status,
            "findings": findings or [],
            **extra,
        })

    def test_active_task_wins_and_response_excludes_document_payload(self):
        self.add_review("older-running", "doc-a", "run-a", "running", [
            {"id": "f1", "decision": "pending"},
            {"id": "f2", "decision": "accepted"},
        ], snapshot={"source": "PRIVATE MANUSCRIPT"}, materials=[{"content": "private"}])
        self.add_review("newer-completed", "doc-a", "run-a2", "completed", [
            {"id": "f3", "decision": "pending"},
        ])
        self.add_review("failed", "doc-b", "run-b", "failed", [])

        result = agent_api.workspace_review_status("doc-a,doc-b,doc-missing")

        self.assertEqual(len(result["tasks"]), 2)
        doc_a, doc_b = result["tasks"]
        self.assertEqual(doc_a["review_id"], "older-running")
        self.assertEqual(doc_a["status"], "running")
        self.assertEqual(doc_a["pending_count"], 1)
        self.assertEqual(doc_a["finding_count"], 2)
        self.assertEqual(doc_b["status"], "failed")
        self.assertEqual(set(doc_a), {
            "document_id", "review_id", "format_run_id", "status",
            "pending_count", "finding_count", "updated_at",
        })
        self.assertNotIn("snapshot", str(result))
        self.assertNotIn("PRIVATE MANUSCRIPT", str(result))

    def test_returns_empty_list_for_unknown_documents(self):
        self.assertEqual(agent_api.workspace_review_status("unknown")["tasks"], [])

    def test_rejects_empty_or_unbounded_ids(self):
        for supplied in ("", "doc-a,,doc-b", ",doc-a", "x" * 101):
            with self.subTest(supplied=supplied[:12]):
                with self.assertRaises(HTTPException) as caught:
                    agent_api.workspace_review_status(supplied)
                self.assertEqual(caught.exception.status_code, 422)

    def test_rejects_more_than_one_hundred_ids(self):
        with self.assertRaises(HTTPException) as caught:
            agent_api.workspace_review_status(",".join(f"doc-{i}" for i in range(101)))
        self.assertEqual(caught.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
