import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class IdempotencyConflictError(Exception):
    pass


class IdempotencyInProgressError(Exception):
    pass


@dataclass(frozen=True)
class WebhookClaim:
    lease_token: str | None = None
    response: dict | None = None


class SQLiteRepository:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connection() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,title TEXT NOT NULL,source TEXT NOT NULL,metadata TEXT NOT NULL,chunk_count INTEGER NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY AUTOINCREMENT,query TEXT NOT NULL,answer TEXT NOT NULL,rating INTEGER NOT NULL,comment TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS webhook_events(idempotency_key TEXT PRIMARY KEY,ticket_id TEXT NOT NULL,request_digest TEXT,response TEXT,status TEXT NOT NULL DEFAULT 'completed',lease_token TEXT,lease_expires_at REAL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                """
            )
            columns = {row["name"] for row in c.execute("PRAGMA table_info(webhook_events)")}
            for name, definition in (
                ("request_digest", "TEXT"),
                ("response", "TEXT"),
                ("status", "TEXT NOT NULL DEFAULT 'completed'"),
                ("lease_token", "TEXT"),
                ("lease_expires_at", "REAL"),
            ):
                if name not in columns:
                    c.execute(f"ALTER TABLE webhook_events ADD COLUMN {name} {definition}")

    def check_ready(self) -> None:
        with self.connection() as c:
            c.execute("SELECT 1").fetchone()

    def add_document(self, doc_id: str, title: str, source: str, metadata: dict, chunk_count: int) -> None:
        with self.connection() as c:
            c.execute(
                "INSERT INTO documents(id,title,source,metadata,chunk_count) VALUES(?,?,?,?,?)",
                (doc_id, title, source, json.dumps(metadata), chunk_count),
            )

    def list_documents(self) -> list[dict]:
        with self.connection() as c:
            rows = c.execute(
                "SELECT id,title,source,metadata,chunk_count FROM documents ORDER BY created_at,id"
            ).fetchall()
        return [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows]

    def add_feedback(self, query: str, answer: str, rating: int, comment: str | None) -> dict:
        with self.connection() as c:
            cursor = c.execute(
                "INSERT INTO feedback(query,answer,rating,comment) VALUES(?,?,?,?)",
                (query, answer, rating, comment),
            )
            row = c.execute(
                "SELECT id,created_at FROM feedback WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
        return dict(row)

    def begin_webhook(
        self, key: str, ticket_id: str, request_digest: str, lease_seconds: float
    ) -> WebhookClaim:
        now = time.time()
        token = str(uuid.uuid4())
        with self.connection() as c:
            cursor = c.execute(
                """INSERT OR IGNORE INTO webhook_events
                (idempotency_key,ticket_id,request_digest,status,lease_token,lease_expires_at)
                VALUES(?,?,?,'processing',?,?)""",
                (key, ticket_id, request_digest, token, now + lease_seconds),
            )
            if cursor.rowcount == 1:
                return WebhookClaim(lease_token=token)
            row = c.execute(
                "SELECT request_digest,response,status,lease_expires_at FROM webhook_events WHERE idempotency_key=?",
                (key,),
            ).fetchone()
            if row["request_digest"] != request_digest:
                raise IdempotencyConflictError
            if row["status"] == "completed":
                return WebhookClaim(response=json.loads(row["response"]))
            if row["lease_expires_at"] is not None and row["lease_expires_at"] <= now:
                reclaimed = c.execute(
                    """UPDATE webhook_events SET ticket_id=?,lease_token=?,lease_expires_at=?
                    WHERE idempotency_key=? AND status='processing' AND request_digest=?
                    AND lease_expires_at<=?""",
                    (ticket_id, token, now + lease_seconds, key, request_digest, now),
                )
                if reclaimed.rowcount == 1:
                    return WebhookClaim(lease_token=token)
            raise IdempotencyInProgressError

    def complete_webhook(self, key: str, lease_token: str, response: dict) -> None:
        with self.connection() as c:
            c.execute(
                """UPDATE webhook_events SET response=?,status='completed',lease_token=NULL,lease_expires_at=NULL
                WHERE idempotency_key=? AND status='processing' AND lease_token=?""",
                (json.dumps(response), key, lease_token),
            )

    def fail_webhook(self, key: str, lease_token: str) -> None:
        with self.connection() as c:
            c.execute(
                "DELETE FROM webhook_events WHERE idempotency_key=? AND status='processing' AND lease_token=?",
                (key, lease_token),
            )
