from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping

from app.platform_db import connect, utc_now

from .schema import install_schema


TERMINAL_JOB_STATES = {"succeeded", "failed", "cancelled"}
TERMINAL_OUTBOX_STATES = {"delivered", "dead_letter", "cancelled"}


class RetryableJobError(RuntimeError):
    """A handler failure that is known to be safe to retry."""


class UnknownExternalOutcome(RuntimeError):
    """An external request may have succeeded and must be reconciled first."""


class PermanentJobError(RuntimeError):
    """A validated failure that retrying cannot fix."""


class IdempotencyConflict(RuntimeError):
    """The same idempotency key was reused for different input."""


_SENSITIVE_KEY = re.compile(
    r"(?:^|_)(?:password|secret|api_key|access_token|refresh_token|authorization)(?:$|_)", re.I
)
_SENSITIVE_VALUE = re.compile(
    r"(?i)(password|secret|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|authorization)"
    r"(\s*[=:]\s*)([^\s,;}]+)"
)


@dataclass(frozen=True)
class Job:
    id: str
    kind: str
    payload: dict[str, Any]
    state: str
    priority: int
    available_at: str
    attempts: int
    max_attempts: int
    lease_owner: str | None
    lease_expires_at: str | None
    idempotency_key: str | None
    requires_reconciliation: bool
    reconciliation_state: str
    result: Any
    last_error: str
    created_at: str
    updated_at: str
    completed_at: str | None


@dataclass(frozen=True)
class OutboxMessage:
    id: str
    destination: str
    event_type: str
    payload: dict[str, Any]
    state: str
    available_at: str
    attempts: int
    max_attempts: int
    lease_owner: str | None
    lease_expires_at: str | None
    idempotency_key: str
    external_id: str | None
    reconciliation_state: str
    last_error: str
    created_at: str
    updated_at: str
    delivered_at: str | None


def _iso(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _load(value: str | None) -> Any:
    return json.loads(value) if value else None


def _reject_secrets(value: Any, key: str = "") -> None:
    if key and _SENSITIVE_KEY.search(key):
        raise ValueError("Secrets must be stored in Windows Credential Manager, not job payloads")
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            _reject_secrets(child, str(child_key))
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_secrets(child, key)


def _safe_error(error: str) -> str:
    return _SENSITIVE_VALUE.sub(r"\1\2[redacted]", str(error))[:4000]


def _job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        kind=row["kind"],
        payload=_load(row["payload_json"]),
        state=row["state"],
        priority=row["priority"],
        available_at=row["available_at"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        idempotency_key=row["idempotency_key"],
        requires_reconciliation=bool(row["requires_reconciliation"]),
        reconciliation_state=row["reconciliation_state"],
        result=_load(row["result_json"]),
        last_error=row["last_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


def _outbox(row: sqlite3.Row) -> OutboxMessage:
    return OutboxMessage(
        id=row["id"],
        destination=row["destination"],
        event_type=row["event_type"],
        payload=_load(row["payload_json"]),
        state=row["state"],
        available_at=row["available_at"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        idempotency_key=row["idempotency_key"],
        external_id=row["external_id"],
        reconciliation_state=row["reconciliation_state"],
        last_error=row["last_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        delivered_at=row["delivered_at"],
    )


class JobStore:
    def __init__(self, *, ensure_schema: bool = True) -> None:
        if ensure_schema:
            install_schema()

    def enqueue(
        self,
        kind: str,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
        available_at: datetime | None = None,
        priority: int = 0,
        max_attempts: int = 5,
        requires_reconciliation: bool = False,
    ) -> Job:
        if not kind.strip():
            raise ValueError("Job kind is required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        _reject_secrets(payload)
        job_id, now = str(uuid.uuid4()), _iso()
        payload_json = _json(dict(payload))
        with connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO integration_jobs
                    (id, kind, payload_json, priority, available_at, max_attempts,
                     idempotency_key, requires_reconciliation, reconciliation_state,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    kind,
                    payload_json,
                    priority,
                    _iso(available_at),
                    max_attempts,
                    idempotency_key,
                    int(requires_reconciliation),
                    "pending" if requires_reconciliation else "not_required",
                    now,
                    now,
                ),
            )
            if idempotency_key is not None:
                row = conn.execute(
                    "SELECT * FROM integration_jobs WHERE kind = ? AND idempotency_key = ?",
                    (kind, idempotency_key),
                ).fetchone()
                if row is not None and row["payload_json"] != payload_json:
                    raise IdempotencyConflict(
                        "Idempotency key was already used with different job input"
                    )
            else:
                row = conn.execute("SELECT * FROM integration_jobs WHERE id = ?", (job_id,)).fetchone()
        assert row is not None
        return _job(row)

    def get(self, job_id: str) -> Job | None:
        with connect() as conn:
            row = conn.execute("SELECT * FROM integration_jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(row) if row else None

    def list(self, *, state: str | None = None, limit: int = 100) -> list[Job]:
        limit = max(1, min(limit, 100))
        with connect() as conn:
            if state:
                rows = conn.execute(
                    "SELECT * FROM integration_jobs WHERE state = ? ORDER BY created_at DESC LIMIT ?",
                    (state, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM integration_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
        return [_job(row) for row in rows]

    def recover_expired(self, *, now: datetime | None = None) -> int:
        timestamp = _iso(now)
        recovered = 0
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT * FROM integration_jobs
                WHERE state = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?
                """,
                (timestamp,),
            ).fetchall()
            for row in rows:
                if row["requires_reconciliation"]:
                    state, reconciliation, error = "unknown", "required", "Worker lease expired; reconcile before retry"
                elif row["attempts"] >= row["max_attempts"]:
                    state, reconciliation, error = "failed", row["reconciliation_state"], "Worker lease expired"
                else:
                    state, reconciliation, error = "retry_wait", row["reconciliation_state"], "Worker lease expired"
                conn.execute(
                    """
                    UPDATE integration_jobs
                    SET state = ?, reconciliation_state = ?, available_at = ?, lease_owner = NULL,
                        lease_expires_at = NULL, last_error = ?, updated_at = ?,
                        completed_at = CASE WHEN ? = 'failed' THEN ? ELSE NULL END
                    WHERE id = ? AND state = 'running'
                    """,
                    (state, reconciliation, timestamp, error, timestamp, state, timestamp, row["id"]),
                )
                self._finish_attempt(conn, row["id"], "lost_lease", error, timestamp)
                recovered += 1
        return recovered

    def claim(
        self,
        worker_id: str,
        *,
        limit: int = 1,
        lease_seconds: int = 60,
        kinds: list[str] | None = None,
        now: datetime | None = None,
    ) -> list[Job]:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        current = now or utc_now()
        self.recover_expired(now=current)
        timestamp, expires = _iso(current), _iso(current + timedelta(seconds=lease_seconds))
        limit = max(1, min(limit, 100))
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sql = """
                SELECT * FROM integration_jobs
                WHERE state IN ('queued', 'retry_wait') AND available_at <= ?
            """
            params: list[Any] = [timestamp]
            if kinds:
                sql += f" AND kind IN ({','.join('?' for _ in kinds)})"
                params.extend(kinds)
            sql += " ORDER BY priority DESC, available_at, created_at LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            claimed: list[Job] = []
            for row in rows:
                attempt = row["attempts"] + 1
                cursor = conn.execute(
                    """
                    UPDATE integration_jobs
                    SET state = 'running', attempts = ?, lease_owner = ?, lease_expires_at = ?,
                        reconciliation_state = CASE WHEN requires_reconciliation = 1 THEN 'pending' ELSE reconciliation_state END,
                        updated_at = ?
                    WHERE id = ? AND state IN ('queued', 'retry_wait')
                    """,
                    (attempt, worker_id, expires, timestamp, row["id"]),
                )
                if cursor.rowcount != 1:
                    continue
                conn.execute(
                    """
                    INSERT INTO integration_delivery_attempts
                        (queue_type, item_id, attempt_number, worker_id, started_at)
                    VALUES ('job', ?, ?, ?, ?)
                    """,
                    (row["id"], attempt, worker_id, timestamp),
                )
                current_row = conn.execute(
                    "SELECT * FROM integration_jobs WHERE id = ?", (row["id"],)
                ).fetchone()
                claimed.append(_job(current_row))
        return claimed

    def complete(self, job_id: str, result: Any = None, *, worker_id: str | None = None) -> Job:
        now = _iso()
        with connect() as conn:
            cursor = conn.execute(
                """
                UPDATE integration_jobs
                SET state = 'succeeded', result_json = ?, lease_owner = NULL, lease_expires_at = NULL,
                    reconciliation_state = CASE WHEN requires_reconciliation = 1 THEN 'resolved' ELSE reconciliation_state END,
                    last_error = '', updated_at = ?, completed_at = ?
                WHERE id = ? AND state = 'running' AND (? IS NULL OR lease_owner = ?)
                """,
                (_json(result), now, now, job_id, worker_id, worker_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Job is not leased by this worker")
            self._finish_attempt(conn, job_id, "succeeded", "", now)
            row = conn.execute("SELECT * FROM integration_jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(row)

    def fail(
        self,
        job_id: str,
        error: str,
        *,
        worker_id: str | None = None,
        retryable: bool = True,
        retry_delay_seconds: int | None = None,
    ) -> Job:
        error = _safe_error(error)
        with connect() as conn:
            row = conn.execute("SELECT * FROM integration_jobs WHERE id = ?", (job_id,)).fetchone()
            if not row or row["state"] != "running" or (worker_id and row["lease_owner"] != worker_id):
                raise ValueError("Job is not leased by this worker")
            retry = retryable and row["attempts"] < row["max_attempts"]
            now_dt = utc_now()
            delay = retry_delay_seconds if retry_delay_seconds is not None else min(3600, 2 ** row["attempts"])
            state = "retry_wait" if retry else "failed"
            conn.execute(
                """
                UPDATE integration_jobs
                SET state = ?, available_at = ?, lease_owner = NULL, lease_expires_at = NULL,
                    last_error = ?, updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    state,
                    _iso(now_dt + timedelta(seconds=max(0, delay))),
                    error,
                    _iso(now_dt),
                    None if retry else _iso(now_dt),
                    job_id,
                ),
            )
            self._finish_attempt(conn, job_id, state, error, _iso(now_dt))
            updated = conn.execute("SELECT * FROM integration_jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(updated)

    def mark_unknown(self, job_id: str, error: str, *, worker_id: str | None = None) -> Job:
        error = _safe_error(error)
        now = _iso()
        with connect() as conn:
            cursor = conn.execute(
                """
                UPDATE integration_jobs
                SET state = 'unknown', reconciliation_state = 'required', lease_owner = NULL,
                    lease_expires_at = NULL, last_error = ?, updated_at = ?
                WHERE id = ? AND state = 'running' AND (? IS NULL OR lease_owner = ?)
                """,
                (error, now, job_id, worker_id, worker_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Job is not leased by this worker")
            self._finish_attempt(conn, job_id, "unknown", error, now)
            row = conn.execute("SELECT * FROM integration_jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(row)

    def resolve_unknown(self, job_id: str, *, succeeded: bool, result: Any = None) -> Job:
        now = _iso()
        with connect() as conn:
            row = conn.execute("SELECT * FROM integration_jobs WHERE id = ?", (job_id,)).fetchone()
            if not row or row["state"] != "unknown":
                raise ValueError("Job is not awaiting reconciliation")
            state = "succeeded" if succeeded else (
                "retry_wait" if row["attempts"] < row["max_attempts"] else "failed"
            )
            conn.execute(
                """
                UPDATE integration_jobs
                SET state = ?, reconciliation_state = 'resolved', result_json = ?, available_at = ?,
                    last_error = '', updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    state,
                    _json(result),
                    now,
                    now,
                    now if state in TERMINAL_JOB_STATES else None,
                    job_id,
                ),
            )
            updated = conn.execute("SELECT * FROM integration_jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(updated)

    def retry_failed(self, job_id: str) -> Job:
        """Queue one deliberate retry without bypassing unknown-outcome reconciliation."""
        now = _iso()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM integration_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Job not found")
            if row["state"] in {"queued", "retry_wait"}:
                return _job(row)
            if row["state"] == "unknown":
                raise ValueError("Unknown outcomes must be reconciled before retry")
            if row["state"] != "failed":
                raise ValueError("Only a failed job can be retried")
            max_attempts = max(int(row["max_attempts"]), int(row["attempts"]) + 1)
            conn.execute(
                """
                UPDATE integration_jobs
                SET state = 'retry_wait', available_at = ?, max_attempts = ?,
                    lease_owner = NULL, lease_expires_at = NULL, completed_at = NULL,
                    updated_at = ?
                WHERE id = ? AND state = 'failed'
                """,
                (now, max_attempts, now, job_id),
            )
            updated = conn.execute(
                "SELECT * FROM integration_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return _job(updated)

    @staticmethod
    def _finish_attempt(
        conn: sqlite3.Connection, job_id: str, outcome: str, error: str, finished_at: str
    ) -> None:
        conn.execute(
            """
            UPDATE integration_delivery_attempts
            SET finished_at = ?, outcome = ?, error = ?
            WHERE id = (
                SELECT id FROM integration_delivery_attempts
                WHERE queue_type = 'job' AND item_id = ? AND finished_at IS NULL
                ORDER BY id DESC LIMIT 1
            )
            """,
            (finished_at, outcome, error, job_id),
        )


class OutboxStore:
    def __init__(self, *, ensure_schema: bool = True) -> None:
        if ensure_schema:
            install_schema()

    def enqueue(
        self,
        destination: str,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
        available_at: datetime | None = None,
        max_attempts: int = 5,
    ) -> OutboxMessage:
        if not destination.strip() or not event_type.strip() or not idempotency_key.strip():
            raise ValueError("destination, event_type and idempotency_key are required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        _reject_secrets(payload)
        message_id, now = str(uuid.uuid4()), _iso()
        payload_json = _json(dict(payload))
        with connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO integration_outbox
                    (id, destination, event_type, payload_json, available_at, max_attempts,
                     idempotency_key, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    destination,
                    event_type,
                    payload_json,
                    _iso(available_at),
                    max_attempts,
                    idempotency_key,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM integration_outbox WHERE destination = ? AND idempotency_key = ?",
                (destination, idempotency_key),
            ).fetchone()
            if row is not None and (
                row["event_type"] != event_type or row["payload_json"] != payload_json
            ):
                raise IdempotencyConflict(
                    "Idempotency key was already used with different outbox input"
                )
        return _outbox(row)

    def get(self, message_id: str) -> OutboxMessage | None:
        with connect() as conn:
            row = conn.execute("SELECT * FROM integration_outbox WHERE id = ?", (message_id,)).fetchone()
        return _outbox(row) if row else None

    def recover_expired(self, *, now: datetime | None = None) -> int:
        timestamp = _iso(now)
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT id FROM integration_outbox
                WHERE state = 'processing' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?
                """,
                (timestamp,),
            ).fetchall()
            for row in rows:
                error = "Worker lease expired; reconcile before retry"
                conn.execute(
                    """
                    UPDATE integration_outbox
                    SET state = 'unknown', reconciliation_state = 'required', lease_owner = NULL,
                        lease_expires_at = NULL, last_error = ?, updated_at = ?
                    WHERE id = ? AND state = 'processing'
                    """,
                    (error, timestamp, row["id"]),
                )
                self._finish_attempt(conn, row["id"], "unknown", error, timestamp)
        return len(rows)

    def claim(
        self,
        worker_id: str,
        *,
        limit: int = 1,
        lease_seconds: int = 60,
        destinations: list[str] | None = None,
        now: datetime | None = None,
    ) -> list[OutboxMessage]:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        current = now or utc_now()
        self.recover_expired(now=current)
        timestamp, expires = _iso(current), _iso(current + timedelta(seconds=lease_seconds))
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sql = """
                SELECT * FROM integration_outbox
                WHERE state IN ('pending', 'retry_wait') AND available_at <= ?
            """
            params: list[Any] = [timestamp]
            if destinations:
                sql += f" AND destination IN ({','.join('?' for _ in destinations)})"
                params.extend(destinations)
            sql += " ORDER BY available_at, created_at LIMIT ?"
            params.append(max(1, min(limit, 100)))
            rows = conn.execute(sql, params).fetchall()
            claimed: list[OutboxMessage] = []
            for row in rows:
                attempt = row["attempts"] + 1
                cursor = conn.execute(
                    """
                    UPDATE integration_outbox
                    SET state = 'processing', attempts = ?, lease_owner = ?, lease_expires_at = ?,
                        reconciliation_state = 'pending', updated_at = ?
                    WHERE id = ? AND state IN ('pending', 'retry_wait')
                    """,
                    (attempt, worker_id, expires, timestamp, row["id"]),
                )
                if cursor.rowcount != 1:
                    continue
                conn.execute(
                    """
                    INSERT INTO integration_delivery_attempts
                        (queue_type, item_id, attempt_number, worker_id, started_at)
                    VALUES ('outbox', ?, ?, ?, ?)
                    """,
                    (row["id"], attempt, worker_id, timestamp),
                )
                current_row = conn.execute(
                    "SELECT * FROM integration_outbox WHERE id = ?", (row["id"],)
                ).fetchone()
                claimed.append(_outbox(current_row))
        return claimed

    def delivered(
        self, message_id: str, *, external_id: str | None = None, worker_id: str | None = None
    ) -> OutboxMessage:
        now = _iso()
        with connect() as conn:
            cursor = conn.execute(
                """
                UPDATE integration_outbox
                SET state = 'delivered', external_id = COALESCE(?, external_id),
                    reconciliation_state = 'resolved', lease_owner = NULL, lease_expires_at = NULL,
                    last_error = '', updated_at = ?, delivered_at = ?
                WHERE id = ? AND state = 'processing' AND (? IS NULL OR lease_owner = ?)
                """,
                (external_id, now, now, message_id, worker_id, worker_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Outbox message is not leased by this worker")
            self._finish_attempt(conn, message_id, "delivered", "", now)
            row = conn.execute("SELECT * FROM integration_outbox WHERE id = ?", (message_id,)).fetchone()
        return _outbox(row)

    def fail(
        self,
        message_id: str,
        error: str,
        *,
        worker_id: str | None = None,
        retryable: bool = True,
        retry_delay_seconds: int | None = None,
    ) -> OutboxMessage:
        error = _safe_error(error)
        with connect() as conn:
            row = conn.execute("SELECT * FROM integration_outbox WHERE id = ?", (message_id,)).fetchone()
            if not row or row["state"] != "processing" or (worker_id and row["lease_owner"] != worker_id):
                raise ValueError("Outbox message is not leased by this worker")
            retry = retryable and row["attempts"] < row["max_attempts"]
            now_dt = utc_now()
            delay = retry_delay_seconds if retry_delay_seconds is not None else min(3600, 2 ** row["attempts"])
            state = "retry_wait" if retry else "dead_letter"
            conn.execute(
                """
                UPDATE integration_outbox
                SET state = ?, available_at = ?, lease_owner = NULL, lease_expires_at = NULL,
                    last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (state, _iso(now_dt + timedelta(seconds=max(0, delay))), error, _iso(now_dt), message_id),
            )
            self._finish_attempt(conn, message_id, state, error, _iso(now_dt))
            updated = conn.execute("SELECT * FROM integration_outbox WHERE id = ?", (message_id,)).fetchone()
        return _outbox(updated)

    def mark_unknown(
        self, message_id: str, error: str, *, worker_id: str | None = None
    ) -> OutboxMessage:
        error = _safe_error(error)
        now = _iso()
        with connect() as conn:
            cursor = conn.execute(
                """
                UPDATE integration_outbox
                SET state = 'unknown', reconciliation_state = 'required', lease_owner = NULL,
                    lease_expires_at = NULL, last_error = ?, updated_at = ?
                WHERE id = ? AND state = 'processing' AND (? IS NULL OR lease_owner = ?)
                """,
                (error, now, message_id, worker_id, worker_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Outbox message is not leased by this worker")
            self._finish_attempt(conn, message_id, "unknown", error, now)
            row = conn.execute("SELECT * FROM integration_outbox WHERE id = ?", (message_id,)).fetchone()
        return _outbox(row)

    def resolve_unknown(
        self, message_id: str, *, delivered: bool, external_id: str | None = None
    ) -> OutboxMessage:
        now = _iso()
        with connect() as conn:
            row = conn.execute("SELECT * FROM integration_outbox WHERE id = ?", (message_id,)).fetchone()
            if not row or row["state"] != "unknown":
                raise ValueError("Outbox message is not awaiting reconciliation")
            state = "delivered" if delivered else (
                "retry_wait" if row["attempts"] < row["max_attempts"] else "dead_letter"
            )
            conn.execute(
                """
                UPDATE integration_outbox
                SET state = ?, reconciliation_state = 'resolved', external_id = COALESCE(?, external_id),
                    available_at = ?, last_error = '', updated_at = ?, delivered_at = ?
                WHERE id = ?
                """,
                (
                    state,
                    external_id,
                    now,
                    now,
                    now if state == "delivered" else None,
                    message_id,
                ),
            )
            updated = conn.execute("SELECT * FROM integration_outbox WHERE id = ?", (message_id,)).fetchone()
        return _outbox(updated)

    @staticmethod
    def _finish_attempt(
        conn: sqlite3.Connection, message_id: str, outcome: str, error: str, finished_at: str
    ) -> None:
        conn.execute(
            """
            UPDATE integration_delivery_attempts
            SET finished_at = ?, outcome = ?, error = ?
            WHERE id = (
                SELECT id FROM integration_delivery_attempts
                WHERE queue_type = 'outbox' AND item_id = ? AND finished_at IS NULL
                ORDER BY id DESC LIMIT 1
            )
            """,
            (finished_at, outcome, error, message_id),
        )


JobHandler = Callable[[dict[str, Any], Job], Any]
OutboxHandler = Callable[[dict[str, Any], OutboxMessage], Any]


class JobWorker:
    def __init__(self, worker_id: str, store: JobStore, handlers: Mapping[str, JobHandler]) -> None:
        self.worker_id, self.store, self.handlers = worker_id, store, dict(handlers)

    def run_once(self, *, limit: int = 1) -> int:
        claimed = self.store.claim(self.worker_id, limit=limit, kinds=list(self.handlers))
        for job in claimed:
            handler = self.handlers[job.kind]
            try:
                self.store.complete(job.id, handler(job.payload, job), worker_id=self.worker_id)
            except UnknownExternalOutcome as exc:
                self.store.mark_unknown(job.id, str(exc), worker_id=self.worker_id)
            except RetryableJobError as exc:
                self.store.fail(job.id, str(exc), worker_id=self.worker_id, retryable=True)
            except PermanentJobError as exc:
                self.store.fail(job.id, str(exc), worker_id=self.worker_id, retryable=False)
            except Exception as exc:
                if job.requires_reconciliation:
                    self.store.mark_unknown(job.id, str(exc), worker_id=self.worker_id)
                else:
                    self.store.fail(job.id, str(exc), worker_id=self.worker_id, retryable=False)
        return len(claimed)


class OutboxWorker:
    def __init__(
        self, worker_id: str, store: OutboxStore, handlers: Mapping[str, OutboxHandler]
    ) -> None:
        self.worker_id, self.store, self.handlers = worker_id, store, dict(handlers)

    def run_once(self, *, limit: int = 1) -> int:
        claimed = self.store.claim(self.worker_id, limit=limit, destinations=list(self.handlers))
        for message in claimed:
            handler = self.handlers[message.destination]
            try:
                result = handler(message.payload, message)
                external_id = result.get("external_id") if isinstance(result, dict) else result
                self.store.delivered(
                    message.id,
                    external_id=str(external_id) if external_id is not None else None,
                    worker_id=self.worker_id,
                )
            except UnknownExternalOutcome as exc:
                self.store.mark_unknown(message.id, str(exc), worker_id=self.worker_id)
            except RetryableJobError as exc:
                self.store.fail(message.id, str(exc), worker_id=self.worker_id, retryable=True)
            except PermanentJobError as exc:
                self.store.fail(message.id, str(exc), worker_id=self.worker_id, retryable=False)
            except Exception as exc:
                self.store.mark_unknown(message.id, str(exc), worker_id=self.worker_id)
        return len(claimed)
