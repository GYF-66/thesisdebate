"""Small durable repository for document snapshots and agent-review records.

The store contains only application data. Provider credentials are never accepted
as configuration here and should not be added to review/event payloads.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any, Iterator
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class ReviewStore:
    """Thread-safe SQLite repository; each mutation commits before returning.

    A connection-level lock protects threads. BEGIN IMMEDIATE additionally makes
    version/head and decision writes atomic across distinct store instances.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path) if str(path) == ":memory:" else str(Path(path).expanduser().resolve())
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._closed = False
        self._connection = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None, timeout=10.0,
        )
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA busy_timeout=10000")
            self._connection.execute("PRAGMA foreign_keys=ON")
            if self.path != ":memory:":
                self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.executescript("""
                CREATE TABLE IF NOT EXISTS records (
                    namespace TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (namespace, record_id)
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    id TEXT PRIMARY KEY,
                    format_run_id TEXT,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS reviews_by_run
                    ON reviews(format_run_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    review_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_by_review ON events(review_id, id);
                CREATE TABLE IF NOT EXISTS versions (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    parent_id TEXT REFERENCES versions(id),
                    content TEXT NOT NULL,
                    hash TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS versions_by_document
                    ON versions(document_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS heads (
                    document_id TEXT PRIMARY KEY,
                    version_id TEXT NOT NULL REFERENCES versions(id),
                    hash TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    review_id TEXT NOT NULL,
                    finding_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    result TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (review_id, finding_id)
                );
                CREATE TABLE IF NOT EXISTS workspace_documents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    editor_json TEXT,
                    document_type TEXT NOT NULL CHECK (document_type = 'markdown'),
                    revision INTEGER NOT NULL CHECK (revision > 0),
                    updated_at TEXT NOT NULL,
                    last_run_id TEXT,
                    profile_id TEXT NOT NULL DEFAULT 'generic-thesis'
                );
                CREATE TABLE IF NOT EXISTS workspace_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    active_document_id TEXT REFERENCES workspace_documents(id)
                );
                CREATE TABLE IF NOT EXISTS export_history (
                    id TEXT PRIMARY KEY,
                    document_id TEXT,
                    filename TEXT NOT NULL,
                    format TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    data BLOB NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
        with self._transaction() as connection:
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(workspace_documents)")}
            if "profile_id" not in columns:
                connection.execute("ALTER TABLE workspace_documents ADD COLUMN profile_id TEXT NOT NULL DEFAULT 'generic-thesis'")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    def save_format_state(self, runs: dict, changes: dict, sources: dict[str, str]) -> None:
        with self._transaction() as connection:
            self._save_format_state(connection, runs, changes, sources)

    def _save_format_state(
        self, connection: sqlite3.Connection, runs: dict, changes: dict, sources: dict[str, str],
    ) -> None:
        prepared = [
            (namespace, str(key), _encode(value), _now())
            for namespace, values in (("runs", runs), ("changes", changes), ("sources", sources))
            for key, value in values.items()
        ]
        connection.execute("DELETE FROM records WHERE namespace IN ('runs','changes','sources')")
        connection.executemany(
            "INSERT INTO records(namespace,record_id,payload,updated_at) VALUES (?,?,?,?)", prepared,
        )

    def commit_format_state(
        self, runs: dict, changes: dict, sources: dict[str, str], run_id: str,
        document_id: str, content: str, origin: str,
        expected_head_hash: str | None = None, create_version: bool = True,
        expected_head_absent: bool = False,
    ) -> dict:
        """Commit a proposed format snapshot and its version/head atomically.

        The caller's maps are never changed. It can publish the returned run to
        memory only after this operation commits; any serialization or database
        error rolls back the new version, head and all three record namespaces.
        """
        if run_id not in runs:
            raise ValueError("format run must exist in proposed state")
        committed_run = dict(runs[run_id])
        proposed_runs = {**runs, run_id: committed_run}
        version = None
        with self._transaction() as connection:
            if expected_head_absent and connection.execute(
                "SELECT 1 FROM heads WHERE document_id=?", (document_id,),
            ).fetchone() is not None:
                raise ValueError("version head already exists")
            if create_version:
                version = self._create_version(
                    connection, document_id, content, None, origin, expected_head_hash,
                )
                committed_run["current_version_id"] = version["id"]
                if not committed_run.get("base_version_id"):
                    committed_run["base_version_id"] = version["id"]
            self._save_format_state(connection, proposed_runs, changes, sources)
            result = {"run": json.loads(_encode(committed_run)), "version": version}
        return result

    @staticmethod
    def _workspace_value(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        value = dict(row)
        if "editor_json" in value:
            value["editor_json"] = json.loads(value["editor_json"]) if value["editor_json"] is not None else None
        return value

    def _workspace_document(self, connection: sqlite3.Connection, document_id: str) -> dict | None:
        row = connection.execute(
            """SELECT d.*, h.hash AS head_hash FROM workspace_documents d
               LEFT JOIN heads h ON h.document_id=d.id WHERE d.id=?""", (document_id,),
        ).fetchone()
        return self._workspace_value(row)

    def get_workspace_document(self, document_id: str) -> dict | None:
        with self._lock:
            return self._workspace_document(self._connection, document_id)

    def get_preference(self, key: str) -> dict | None:
        with self._lock:
            row = self._connection.execute("SELECT payload FROM records WHERE namespace='ui_preferences' AND record_id=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_preference(self, key: str, value: dict) -> None:
        with self._transaction() as connection:
            connection.execute("INSERT INTO records(namespace,record_id,payload,updated_at) VALUES ('ui_preferences',?,?,?) ON CONFLICT(namespace,record_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at", (key, _encode(value), _now()))

    def save_export(self, document_id: str | None, filename: str, format: str, scope: str, content: str, media_type: str, data: bytes) -> str:
        identity = 'export-' + uuid4().hex
        with self._transaction() as connection:
            connection.execute('INSERT INTO export_history VALUES (?,?,?,?,?,?,?,?,?)', (identity, document_id, filename, format, scope, hashlib.sha256(content.encode()).hexdigest(), media_type, data, _now()))
        return identity

    def list_exports(self) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._connection.execute('SELECT id,document_id,filename,format,scope,content_hash,length(data) AS size,created_at FROM export_history ORDER BY created_at DESC,id DESC')]

    def get_export(self, identity: str) -> dict | None:
        with self._lock:
            row = self._connection.execute('SELECT * FROM export_history WHERE id=?', (identity,)).fetchone()
            return dict(row) if row else None

    def workspace(self) -> dict:
        with self._transaction() as connection:
            rows = connection.execute(
                """SELECT d.id,d.name,d.document_type,d.updated_at,d.revision,d.last_run_id,
                          d.profile_id,h.hash AS head_hash FROM workspace_documents d
                   LEFT JOIN heads h ON h.document_id=d.id ORDER BY d.updated_at DESC,d.id""",
            ).fetchall()
            active = connection.execute("SELECT active_document_id FROM workspace_state WHERE id=1").fetchone()
            return {"documents": [dict(row) for row in rows],
                    "active_document_id": active[0] if active else None}

    def create_workspace_document(self, name: str, content: str, editor_json: dict | None = None,
                                  profile_id: str = "generic-thesis") -> dict:
        document_id = "doc-" + uuid4().hex
        encoded = _encode(editor_json) if editor_json is not None else None
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO workspace_documents
                   (id,name,content,editor_json,document_type,revision,updated_at,last_run_id,profile_id)
                   VALUES (?,?,?,?,'markdown',1,?,NULL,?)""",
                (document_id, name, content, encoded, _now(), profile_id),
            )
            connection.execute(
                """INSERT INTO workspace_state(id,active_document_id) VALUES (1,?)
                   ON CONFLICT(id) DO UPDATE SET active_document_id=excluded.active_document_id""",
                (document_id,),
            )
            return self._workspace_document(connection, document_id)

    def save_workspace_document(
        self, document_id: str, name: str, content: str, editor_json: dict | None,
        expected_revision: int, last_run_id: str | None = None, update_run: bool = False,
        profile_id: str | None = None,
    ) -> dict:
        encoded = _encode(editor_json) if editor_json is not None else None
        with self._transaction() as connection:
            previous = connection.execute(
                "SELECT revision,last_run_id,profile_id FROM workspace_documents WHERE id=?", (document_id,),
            ).fetchone()
            if previous is None:
                raise KeyError(document_id)
            if previous["revision"] != expected_revision:
                raise ValueError("draft revision conflict")
            selected_run = last_run_id if update_run else previous["last_run_id"]
            selected_profile = profile_id if profile_id is not None else previous["profile_id"]
            if update_run and selected_run is not None:
                run = connection.execute(
                    "SELECT payload FROM records WHERE namespace='runs' AND record_id=?", (selected_run,),
                ).fetchone()
                if run is None or json.loads(run["payload"]).get("document_id") != document_id:
                    raise ValueError("run must belong to the same document")
            connection.execute(
                """UPDATE workspace_documents SET name=?,content=?,editor_json=?,
                   revision=revision+1,updated_at=?,last_run_id=?,profile_id=? WHERE id=? AND revision=?""",
                (name, content, encoded, _now(), selected_run, selected_profile, document_id, expected_revision),
            )
            return self._workspace_document(connection, document_id)

    def set_workspace_active(self, document_id: str | None) -> dict:
        with self._transaction() as connection:
            if document_id is not None and connection.execute(
                "SELECT 1 FROM workspace_documents WHERE id=?", (document_id,),
            ).fetchone() is None:
                raise KeyError(document_id)
            connection.execute(
                """INSERT INTO workspace_state(id,active_document_id) VALUES (1,?)
                   ON CONFLICT(id) DO UPDATE SET active_document_id=excluded.active_document_id""",
                (document_id,),
            )
            return {"active_document_id": document_id}

    def load_format_state(self) -> dict:
        result = {"runs": {}, "changes": {}, "sources": {}}
        with self._lock:
            rows = self._connection.execute(
                "SELECT namespace,record_id,payload FROM records WHERE namespace IN ('runs','changes','sources')"
            ).fetchall()
        for row in rows:
            result[row["namespace"]][row["record_id"]] = json.loads(row["payload"])
        return result

    def put_review(self, review: dict) -> dict:
        value = json.loads(_encode(review))
        review_id = value.get("id")
        if not isinstance(review_id, str) or not review_id:
            raise ValueError("review id is required")
        now = _now()
        with self._transaction() as connection:
            old = connection.execute("SELECT created_at FROM reviews WHERE id=?", (review_id,)).fetchone()
            value.setdefault("created_at", old["created_at"] if old else now)
            value["updated_at"] = now
            connection.execute(
                """INSERT INTO reviews(id,format_run_id,status,payload,created_at,updated_at)
                   VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                   format_run_id=excluded.format_run_id,status=excluded.status,
                   payload=excluded.payload,updated_at=excluded.updated_at""",
                (review_id, value.get("format_run_id", value.get("run_id")), value.get("status", "queued"),
                 _encode(value), value["created_at"], now),
            )
        return value

    def get_review(self, id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute("SELECT payload FROM reviews WHERE id=?", (id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_reviews(self, format_run_id: str | None = None) -> list[dict]:
        query = "SELECT payload FROM reviews"
        params: tuple = ()
        if format_run_id is not None:
            query += " WHERE format_run_id=?"
            params = (format_run_id,)
        query += " ORDER BY created_at DESC,rowid DESC"
        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def list_reviews_for_documents(self, document_ids: list[str]) -> list[dict]:
        """Load only reviews for a bounded batch of workspace document IDs.

        The API layer uses this to show task status beside many documents without
        issuing one query per document or transferring manuscript snapshots.
        """
        if not document_ids:
            return []
        placeholders = ",".join("?" for _ in document_ids)
        query = (
            "SELECT payload FROM reviews "
            f"WHERE json_extract(payload, '$.document_id') IN ({placeholders}) "
            "ORDER BY created_at DESC,rowid DESC"
        )
        with self._lock:
            rows = self._connection.execute(query, tuple(document_ids)).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def add_event(self, review_id: str, type: str, payload: dict) -> dict:
        created_at = _now()
        encoded = _encode(payload)
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO events(review_id,type,payload,created_at) VALUES (?,?,?,?)",
                (review_id, type, encoded, created_at),
            )
            event_id = cursor.lastrowid
        return {"id": event_id, "review_id": review_id, "type": type,
                "payload": json.loads(encoded), "created_at": created_at}

    def events(self, review_id: str, after: int = 0) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM events WHERE review_id=? AND id>? ORDER BY id", (review_id, after),
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def create_version(
        self, document_id: str, content: str, parent_id: str | None = None,
        origin: str = "scan", expected_head_hash: str | None = None,
    ) -> dict:
        with self._transaction() as connection:
            return self._create_version(connection, document_id, content, parent_id, origin, expected_head_hash)

    def _create_version(
        self, connection: sqlite3.Connection, document_id: str, content: str,
        parent_id: str | None, origin: str, expected_head_hash: str | None,
    ) -> dict:
        if not document_id:
            raise ValueError("document id is required")
        if not isinstance(content, str):
            raise TypeError("version content must be text")
        version = {
            "id": f"ver-{uuid4().hex}", "document_id": document_id,
            "parent_id": parent_id, "content": content,
            "hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "origin": origin, "created_at": _now(),
        }
        head = connection.execute("SELECT version_id,hash FROM heads WHERE document_id=?", (document_id,)).fetchone()
        if expected_head_hash is not None and (head is None or head["hash"] != expected_head_hash):
            raise ValueError("version head conflict")
        if parent_id is None and head is not None:
            version["parent_id"] = head["version_id"]
        if version["parent_id"] is not None:
            parent = connection.execute(
                "SELECT document_id FROM versions WHERE id=?", (version["parent_id"],),
            ).fetchone()
            if parent is None or parent["document_id"] != document_id:
                raise ValueError("version parent must exist in the same document")
        connection.execute(
            "INSERT INTO versions(id,document_id,parent_id,content,hash,origin,created_at) VALUES (?,?,?,?,?,?,?)",
            tuple(version[key] for key in ("id", "document_id", "parent_id", "content", "hash", "origin", "created_at")),
        )
        connection.execute(
            """INSERT INTO heads(document_id,version_id,hash,updated_at) VALUES (?,?,?,?)
               ON CONFLICT(document_id) DO UPDATE SET version_id=excluded.version_id,
               hash=excluded.hash,updated_at=excluded.updated_at""",
            (document_id, version["id"], version["hash"], version["created_at"]),
        )
        return version

    def get_version(self, id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM versions WHERE id=?", (id,)).fetchone()
        return dict(row) if row else None

    def list_versions(self, document_id: str) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM versions WHERE document_id=? ORDER BY created_at DESC,rowid DESC", (document_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_head(self, document_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT v.* FROM versions v JOIN heads h ON h.version_id=v.id WHERE h.document_id=?", (document_id,),
            ).fetchone()
        return dict(row) if row else None

    def save_decision(self, review_id: str, finding_id: str, decision: str, result: dict) -> dict:
        encoded = _encode(result)
        with self._transaction() as connection:
            old = connection.execute(
                "SELECT * FROM decisions WHERE review_id=? AND finding_id=?", (review_id, finding_id),
            ).fetchone()
            if old:
                if old["decision"] != decision:
                    raise ValueError("decision conflict")
                return {**dict(old), "result": json.loads(old["result"])}
            created_at = _now()
            connection.execute(
                "INSERT INTO decisions(review_id,finding_id,decision,result,created_at) VALUES (?,?,?,?,?)",
                (review_id, finding_id, decision, encoded, created_at),
            )
        return {"review_id": review_id, "finding_id": finding_id, "decision": decision,
                "result": json.loads(encoded), "created_at": created_at}

    def get_decision(self, review_id: str, finding_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM decisions WHERE review_id=? AND finding_id=?", (review_id, finding_id),
            ).fetchone()
        return {**dict(row), "result": json.loads(row["result"])} if row else None

    def commit_version_decision(
        self, review_id: str, finding_id: str, decision: str, document_id: str,
        content: str, expected_head_hash: str | None, parent_id: str | None = None,
        origin: str = "agent_accept", result: dict | None = None,
    ) -> dict:
        """Commit version, head and idempotent decision in one SQLite transaction.

        On replay the stored result wins, even when the caller still holds the
        pre-commit head hash. No second version is produced after a lost response.
        """
        extra_result = json.loads(_encode(result or {}))
        with self._transaction() as connection:
            old = connection.execute(
                "SELECT * FROM decisions WHERE review_id=? AND finding_id=?", (review_id, finding_id),
            ).fetchone()
            if old:
                if old["decision"] != decision:
                    raise ValueError("decision conflict")
                return {**dict(old), "result": json.loads(old["result"])}
            version = self._create_version(
                connection, document_id, content, parent_id, origin, expected_head_hash,
            )
            stored_result = {**extra_result, "version": version}
            created_at = _now()
            connection.execute(
                "INSERT INTO decisions(review_id,finding_id,decision,result,created_at) VALUES (?,?,?,?,?)",
                (review_id, finding_id, decision, _encode(stored_result), created_at),
            )
        return {"review_id": review_id, "finding_id": finding_id, "decision": decision,
                "result": stored_result, "created_at": created_at}

    def recover_interrupted(self) -> list[dict]:
        """Mark unfinished reviews honestly; no model calls are replayed at startup."""
        recovered = []
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT id,payload FROM reviews WHERE status IN ('queued','running') ORDER BY rowid"
            ).fetchall()
            for row in rows:
                value = json.loads(row["payload"])
                previous_status = value.get("status", "queued")
                timestamp = _now()
                value.update(status="partial", interrupted=True, interrupted_at=timestamp,
                             interruption_reason="process_restart", updated_at=timestamp)
                connection.execute(
                    "UPDATE reviews SET status='partial',payload=?,updated_at=? WHERE id=?",
                    (_encode(value), timestamp, row["id"]),
                )
                connection.execute(
                    "INSERT INTO events(review_id,type,payload,created_at) VALUES (?,?,?,?)",
                    (row["id"], "review.interrupted", _encode({
                        "previous_status": previous_status, "status": "partial", "reason": "process_restart",
                    }), timestamp),
                )
                recovered.append(value)
        return recovered

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True
