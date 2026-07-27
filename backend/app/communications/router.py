from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta
from email.utils import parseaddr
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app import platform_db
from app.confirmation import Confirmation
from app.integrations_v1.jobs import IdempotencyConflict, JobStore

from .schema import install_schema


router = APIRouter()
IdempotencyKey = Header(..., alias="Idempotency-Key", min_length=1, max_length=200)
OptionalIdempotencyKey = Header(None, alias="Idempotency-Key", min_length=1, max_length=200)
_TOKEN = re.compile(r"{{\s*([A-Za-z0-9_.-]+)\s*}}")


def database() -> Iterator[sqlite3.Connection]:
    with platform_db.connect() as conn:
        install_schema(conn)
        yield conn


Db = Depends(database)


def _required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


def _utc(value: datetime | None = None, timezone: str = "Europe/London") -> datetime:
    value = value or platform_db.utc_now()
    if value.tzinfo is None:
        try:
            value = value.replace(tzinfo=ZoneInfo(timezone))
        except ZoneInfoNotFoundError:
            raise HTTPException(status_code=422, detail="Unknown timezone") from None
    return value.astimezone(UTC)


def _iso(value: datetime | None = None, timezone: str = "Europe/London") -> str:
    return _utc(value, timezone).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def _decode(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def _plain(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _thread(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["participants"] = _decode(result.pop("participants_json"), [])
    result["unread"] = bool(result["unread"])
    return result


def _message(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for stored, public in (
        ("to_json", "to"),
        ("cc_json", "cc"),
        ("bcc_json", "bcc"),
        ("labels_json", "labels"),
        ("attachments_json", "attachments"),
    ):
        result[public] = _decode(result.pop(stored), [])
    return result


def _sequence(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["business_days"] = _decode(result.pop("business_days_json"), [0, 1, 2, 3, 4])
    return result


def _email_template(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["active"] = bool(result["active"])
    return result


def _document_template(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["merge_schema"] = _decode(result.pop("merge_schema_json"), {})
    return result


def _page(rows: list[sqlite3.Row], limit: int, converter=_plain) -> dict[str, Any]:
    has_more = len(rows) > limit
    visible = rows[:limit]
    return {
        "items": [converter(row) for row in visible],
        "next_cursor": str(visible[-1]["id"]) if has_more and visible else None,
    }


def _not_found(label: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{label} not found")


def _conflict(message: str) -> HTTPException:
    return HTTPException(status_code=409, detail=message)


def _thread_row(conn: sqlite3.Connection, identifier: str) -> sqlite3.Row:
    row = None
    if identifier.isdigit():
        row = conn.execute("SELECT * FROM gmail_threads WHERE id = ?", (int(identifier),)).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM gmail_threads WHERE gmail_thread_id = ?", (identifier,)
        ).fetchone()
    if row is None:
        raise _not_found("Email thread")
    return row


def _record(conn: sqlite3.Connection, table: str, record_id: int, label: str) -> sqlite3.Row:
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
    if row is None:
        raise _not_found(label)
    return row


def _cached(conn: sqlite3.Connection, action: str, key: str) -> Any | None:
    row = conn.execute(
        "SELECT response_json FROM communication_idempotency WHERE action = ? AND key = ?",
        (action, key),
    ).fetchone()
    return _decode(row["response_json"], None) if row else None


def _remember(conn: sqlite3.Connection, action: str, key: str, response: Any) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO communication_idempotency(action, key, response_json, created_at) VALUES (?, ?, ?, ?)",
        (action, key, _json(response), _iso()),
    )


def _render(text: str, values: dict[str, Any]) -> str:
    normalized = {str(key): str(value) for key, value in values.items() if value is not None}
    return _TOKEN.sub(lambda match: normalized.get(match.group(1), ""), text)


def _email_address(value: str) -> str:
    address = parseaddr(value)[1].strip().lower()
    return address if "@" in address else ""


def _is_suppressed(conn: sqlite3.Connection, email: str) -> bool:
    if conn.execute(
        "SELECT 1 FROM email_suppressions WHERE email = ? COLLATE NOCASE", (email,)
    ).fetchone():
        return True
    row = conn.execute(
        "SELECT email_opt_out_at FROM contacts WHERE lower(email) = lower(?) AND archived_at IS NULL",
        (email,),
    ).fetchone()
    return bool(row and row["email_opt_out_at"])


def _cancel_jobs(conn: sqlite3.Connection, job_ids: list[str]) -> None:
    job_ids = [job_id for job_id in job_ids if job_id]
    if not job_ids:
        return
    placeholders = ",".join("?" for _ in job_ids)
    now = _iso()
    conn.execute(
        f"""UPDATE integration_jobs
            SET state = 'cancelled', updated_at = ?, completed_at = ?
            WHERE id IN ({placeholders}) AND state IN ('queued', 'retry_wait')""",
        [now, now, *job_ids],
    )


def _queue_gmail_jobs(
    conn: sqlite3.Connection, sends: list[sqlite3.Row | dict[str, Any]]
) -> list[dict[str, str]]:
    if not sends:
        return []
    conn.commit()  # JobStore owns its connection; release SQLite's write lock first.
    store = JobStore(ensure_schema=False)
    queued: list[dict[str, str]] = []
    for send in sends:
        item = dict(send)
        payload = {
            "to": item["to_email"],
            "subject": item["subject"],
            "body_text": item["body_text"],
            "rfc_message_id": item["rfc_message_id"],
            "thread_id": item.get("gmail_thread_id"),
            "reply_to_message_id": item.get("reply_to_message_id"),
        }
        try:
            job = store.enqueue(
                "google.gmail.send",
                payload,
                idempotency_key=item["idempotency_key"],
                available_at=datetime.fromisoformat(item["scheduled_for"]),
                requires_reconciliation=True,
            )
        except IdempotencyConflict as exc:
            raise _conflict(str(exc)) from exc
        queued.append({"send_id": str(item["id"]), "job_id": job.id, "state": job.state})
    for item in queued:
        conn.execute(
            "UPDATE scheduled_email_sends SET job_id = ?, updated_at = ? WHERE id = ?",
            (item["job_id"], _iso(), int(item["send_id"])),
        )
    return queued


def _queue_drive_job(
    conn: sqlite3.Connection,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str,
) -> str:
    conn.commit()
    try:
        return JobStore(ensure_schema=False).enqueue(
            kind, payload, idempotency_key=idempotency_key, requires_reconciliation=True
        ).id
    except IdempotencyConflict as exc:
        raise _conflict(str(exc)) from exc


class GmailMessageCache(BaseModel):
    gmail_message_id: str = Field(min_length=1)
    rfc_message_id: str = ""
    direction: Literal["inbound", "outbound", "draft"] = "inbound"
    from_email: str = ""
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    subject: str = ""
    snippet: str = ""
    body_text: str = ""
    sent_at: datetime
    labels: list[str] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("gmail_message_id")
    @classmethod
    def message_id_required(cls, value: str) -> str:
        return _required(value)


class GmailThreadCache(BaseModel):
    gmail_thread_id: str = Field(min_length=1)
    history_id: str = ""
    subject: str = ""
    snippet: str = ""
    participants: list[str] = Field(default_factory=list)
    last_message_at: datetime | None = None
    unread: bool = False
    messages: list[GmailMessageCache] = Field(default_factory=list)

    @field_validator("gmail_thread_id")
    @classmethod
    def thread_id_required(cls, value: str) -> str:
        return _required(value)


class ThreadLinkCreate(BaseModel):
    entity_type: str = Field(min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    entity_id: int = Field(gt=0)


class EmailTemplateCreate(BaseModel):
    name: str = Field(min_length=1)
    category: str = ""
    subject: str = ""
    body_text: str
    active: bool = True

    @field_validator("name")
    @classmethod
    def name_required(cls, value: str) -> str:
        return _required(value)


class EmailTemplateUpdate(BaseModel):
    version: int = Field(ge=1)
    name: str | None = None
    category: str | None = None
    subject: str | None = None
    body_text: str | None = None
    active: bool | None = None


class TemplatePreview(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class SequenceStepCreate(BaseModel):
    position: int | None = Field(default=None, ge=0)
    step_type: Literal["email", "delay", "manual_task", "manual-task"]
    template_id: int | None = None
    subject: str = ""
    body_text: str = ""
    delay_minutes: int = Field(default=0, ge=0, le=525600)
    task_title: str = ""
    task_description: str = ""

    @field_validator("step_type")
    @classmethod
    def normalize_step_type(cls, value: str) -> str:
        return value.replace("-", "_")


class SequenceStepUpdate(BaseModel):
    version: int = Field(ge=1)
    position: int | None = Field(default=None, ge=0)
    template_id: int | None = None
    subject: str | None = None
    body_text: str | None = None
    delay_minutes: int | None = Field(default=None, ge=0, le=525600)
    task_title: str | None = None
    task_description: str | None = None


class SequenceCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    timezone: str = "Europe/London"
    business_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    send_window_start: str = "09:00"
    send_window_end: str = "17:00"
    daily_cap: int = Field(default=40, ge=1, le=500)
    steps: list[SequenceStepCreate] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def name_required(cls, value: str) -> str:
        return _required(value)

    @model_validator(mode="after")
    def validate_schedule(self):
        try:
            ZoneInfo(self.timezone)
            start, end = time.fromisoformat(self.send_window_start), time.fromisoformat(self.send_window_end)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("timezone and send windows must be valid") from None
        if start >= end:
            raise ValueError("send_window_end must be after send_window_start")
        if not self.business_days or any(day not in range(7) for day in self.business_days):
            raise ValueError("business_days must contain weekdays 0 through 6")
        self.business_days = sorted(set(self.business_days))
        return self


class SequenceUpdate(BaseModel):
    version: int = Field(ge=1)
    name: str | None = None
    description: str | None = None
    timezone: str | None = None
    business_days: list[int] | None = None
    send_window_start: str | None = None
    send_window_end: str | None = None
    daily_cap: int | None = Field(default=None, ge=1, le=500)


class EnrollmentCreate(BaseModel):
    contact_id: int | None = None
    email: EmailStr | None = None
    start_at: datetime | None = None
    merge_fields: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_recipient(self):
        if self.contact_id is None and self.email is None:
            raise ValueError("contact_id or email is required")
        return self


class EnrollmentAction(BaseModel):
    version: int = Field(ge=1)
    reason: str = ""


class ScheduledSendCreate(BaseModel):
    to: EmailStr
    subject: str = Field(min_length=1)
    body_text: str = ""
    schedule_at: datetime | None = None
    rfc_message_id: str = ""
    thread_id: str | None = None
    reply_to_message_id: str | None = None

    @field_validator("subject")
    @classmethod
    def subject_required(cls, value: str) -> str:
        value = _required(value)
        if "\r" in value or "\n" in value:
            raise ValueError("email subject cannot contain line breaks")
        return value


class SuppressionCreate(BaseModel):
    email: EmailStr
    reason: str = ""
    source: str = "manual"


class DocumentTemplateCreate(BaseModel):
    name: str = Field(min_length=1)
    category: str = ""
    google_file_id: str = ""
    mime_type: str = "application/vnd.google-apps.document"
    merge_schema: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def name_required(cls, value: str) -> str:
        return _required(value)


class DocumentTemplateUpdate(BaseModel):
    version: int = Field(ge=1)
    name: str | None = None
    category: str | None = None
    google_file_id: str | None = None
    mime_type: str | None = None
    merge_schema: dict[str, Any] | None = None


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1)
    template_id: int | None = None
    entity_type: str = ""
    entity_id: int | None = None
    mime_type: str = "application/vnd.google-apps.document"
    google_file_id: str = ""
    drive_url: str = ""
    local_path: str = ""
    checksum_sha256: str = ""
    queue_drive: bool = True
    parent_google_file_id: str = ""
    merge_data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title")
    @classmethod
    def title_required(cls, value: str) -> str:
        return _required(value)


class DocumentUpdate(BaseModel):
    version: int = Field(ge=1)
    title: str | None = None
    entity_type: str | None = None
    entity_id: int | None = None
    google_file_id: str | None = None
    drive_url: str | None = None
    local_path: str | None = None
    checksum_sha256: str | None = None
    sync_state: Literal["Local", "Queued", "Ready", "Error"] | None = None
    last_sync_error: str | None = None


class DocumentVersionCreate(BaseModel):
    google_file_id: str = ""
    local_path: str = ""
    mime_type: str = "application/pdf"
    checksum_sha256: str = Field(min_length=1)
    size_bytes: int = Field(default=0, ge=0)
    issued: bool = False
    source: str = "local"
    queue_drive: bool = False


@router.get("/email/threads")
def list_threads(
    q: str = "",
    entity_type: str = "",
    entity_id: int | None = None,
    unread: bool | None = None,
    cursor: int | None = None,
    limit: int = Query(50, ge=1, le=100),
    conn=Db,
):
    clauses, values = ["t.archived_at IS NULL"], []
    if q:
        clauses.append("(t.subject LIKE ? OR t.snippet LIKE ? OR t.participants_json LIKE ?)")
        values.extend([f"%{q}%"] * 3)
    if entity_type and entity_id is not None:
        clauses.append(
            "EXISTS(SELECT 1 FROM gmail_thread_links l WHERE l.thread_id=t.id AND l.entity_type=? AND l.entity_id=?)"
        )
        values.extend([entity_type, entity_id])
    if unread is not None:
        clauses.append("t.unread = ?")
        values.append(int(unread))
    if cursor is not None:
        clauses.append("t.id < ?")
        values.append(cursor)
    values.append(limit + 1)
    rows = conn.execute(
        f"SELECT t.* FROM gmail_threads t WHERE {' AND '.join(clauses)} ORDER BY t.id DESC LIMIT ?",
        values,
    ).fetchall()
    return _page(rows, limit, _thread)


@router.post("/email/threads/cache")
def cache_thread(payload: GmailThreadCache, conn=Db):
    now = _iso()
    last_message_at = payload.last_message_at
    if payload.messages:
        newest = max(message.sent_at for message in payload.messages)
        last_message_at = max(filter(None, [last_message_at, newest]), default=newest)
    conn.execute(
        """INSERT INTO gmail_threads
           (gmail_thread_id, history_id, subject, snippet, participants_json, last_message_at,
            unread, message_count, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(gmail_thread_id) DO UPDATE SET
             history_id=excluded.history_id, subject=excluded.subject, snippet=excluded.snippet,
             participants_json=excluded.participants_json,
             last_message_at=COALESCE(excluded.last_message_at, gmail_threads.last_message_at),
             unread=excluded.unread, updated_at=excluded.updated_at,
             archived_at=NULL, version=gmail_threads.version+1""",
        (
            payload.gmail_thread_id,
            payload.history_id,
            payload.subject,
            payload.snippet,
            _json(payload.participants),
            _iso(last_message_at) if last_message_at else None,
            int(payload.unread),
            len(payload.messages),
            now,
            now,
        ),
    )
    thread = conn.execute(
        "SELECT * FROM gmail_threads WHERE gmail_thread_id = ?", (payload.gmail_thread_id,)
    ).fetchone()
    for message in payload.messages:
        conn.execute(
            """INSERT INTO gmail_messages
               (thread_id, gmail_message_id, rfc_message_id, direction, from_email, to_json,
                cc_json, bcc_json, subject, snippet, body_text, sent_at, labels_json,
                attachments_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(gmail_message_id) DO UPDATE SET
                 thread_id=excluded.thread_id, rfc_message_id=excluded.rfc_message_id,
                 direction=excluded.direction, from_email=excluded.from_email,
                 to_json=excluded.to_json, cc_json=excluded.cc_json, bcc_json=excluded.bcc_json,
                 subject=excluded.subject, snippet=excluded.snippet, body_text=excluded.body_text,
                 sent_at=excluded.sent_at, labels_json=excluded.labels_json,
                 attachments_json=excluded.attachments_json, updated_at=excluded.updated_at,
                 version=gmail_messages.version+1""",
            (
                thread["id"],
                message.gmail_message_id,
                message.rfc_message_id,
                message.direction,
                message.from_email,
                _json(message.to),
                _json(message.cc),
                _json(message.bcc),
                message.subject,
                message.snippet,
                message.body_text,
                _iso(message.sent_at),
                _json(message.labels),
                _json(message.attachments),
                now,
                now,
            ),
        )
    conn.execute(
        """UPDATE gmail_threads SET
             message_count=(SELECT COUNT(*) FROM gmail_messages WHERE thread_id=?),
             last_message_at=COALESCE((SELECT MAX(sent_at) FROM gmail_messages WHERE thread_id=?), last_message_at)
           WHERE id=?""",
        (thread["id"], thread["id"], thread["id"]),
    )
    candidates = payload.participants[:]
    for message in payload.messages:
        candidates.extend([message.from_email, *message.to, *message.cc, *message.bcc])
    addresses = sorted({_email_address(value) for value in candidates} - {""})
    if addresses:
        placeholders = ",".join("?" for _ in addresses)
        contacts = conn.execute(
            f"SELECT id, account_id FROM contacts WHERE lower(email) IN ({placeholders}) AND archived_at IS NULL",
            addresses,
        ).fetchall()
        for contact in contacts:
            conn.execute(
                "INSERT OR IGNORE INTO gmail_thread_links VALUES (?, 'contact', ?, 'email_match', ?)",
                (thread["id"], contact["id"], now),
            )
            if contact["account_id"]:
                conn.execute(
                    "INSERT OR IGNORE INTO gmail_thread_links VALUES (?, 'account', ?, 'email_match', ?)",
                    (thread["id"], contact["account_id"], now),
                )
    return thread_detail(payload.gmail_thread_id, conn)


@router.put("/email/threads/{gmail_thread_id}")
def cache_thread_at_id(gmail_thread_id: str, payload: GmailThreadCache, conn=Db):
    payload.gmail_thread_id = gmail_thread_id
    return cache_thread(payload, conn)


@router.get("/email/threads/{thread_id}")
def thread_detail(thread_id: str, conn=Db):
    row = _thread_row(conn, thread_id)
    result = _thread(row)
    result["messages"] = [
        _message(message)
        for message in conn.execute(
            "SELECT * FROM gmail_messages WHERE thread_id = ? ORDER BY sent_at", (row["id"],)
        ).fetchall()
    ]
    result["links"] = [
        dict(link)
        for link in conn.execute(
            "SELECT entity_type, entity_id, link_source, created_at FROM gmail_thread_links WHERE thread_id=? ORDER BY entity_type, entity_id",
            (row["id"],),
        ).fetchall()
    ]
    return result


@router.post("/email/threads/{thread_id}/read")
def mark_thread_read(thread_id: str, idempotency_key: str = IdempotencyKey, conn=Db):
    thread = _thread_row(conn, thread_id)
    if thread["unread"]:
        conn.execute(
            "UPDATE gmail_threads SET unread=0, version=version+1, updated_at=? WHERE id=?",
            (_iso(), thread["id"]),
        )
    return thread_detail(thread_id, conn)


@router.post("/email/threads/{thread_id}/links", status_code=201)
def link_thread(thread_id: str, payload: ThreadLinkCreate, conn=Db):
    thread = _thread_row(conn, thread_id)
    conn.execute(
        "INSERT OR REPLACE INTO gmail_thread_links(thread_id, entity_type, entity_id, link_source, created_at) VALUES (?, ?, ?, 'manual', ?)",
        (thread["id"], payload.entity_type, payload.entity_id, _iso()),
    )
    return {"thread_id": thread["id"], **payload.model_dump(), "link_source": "manual"}


@router.delete("/email/threads/{thread_id}/links/{entity_type}/{entity_id}", status_code=204)
def unlink_thread(thread_id: str, entity_type: str, entity_id: int, conn=Db):
    thread = _thread_row(conn, thread_id)
    conn.execute(
        "DELETE FROM gmail_thread_links WHERE thread_id=? AND entity_type=? AND entity_id=?",
        (thread["id"], entity_type, entity_id),
    )
    return Response(status_code=204)


@router.get("/email/templates")
def list_email_templates(
    q: str = "", cursor: int | None = None, limit: int = Query(50, ge=1, le=100), conn=Db
):
    clauses, values = ["archived_at IS NULL"], []
    if q:
        clauses.append("(name LIKE ? OR subject LIKE ? OR category LIKE ?)")
        values.extend([f"%{q}%"] * 3)
    if cursor is not None:
        clauses.append("id < ?")
        values.append(cursor)
    values.append(limit + 1)
    rows = conn.execute(
        f"SELECT * FROM email_templates WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?", values
    ).fetchall()
    return _page(rows, limit, _email_template)


@router.post("/email/templates", status_code=201)
def create_email_template(payload: EmailTemplateCreate, conn=Db):
    now = _iso()
    try:
        cursor = conn.execute(
            "INSERT INTO email_templates(name, category, subject, body_text, active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (payload.name, payload.category, payload.subject, payload.body_text, int(payload.active), now, now),
        )
    except sqlite3.IntegrityError as exc:
        raise _conflict("An active email template already uses this name") from exc
    return _email_template(_record(conn, "email_templates", cursor.lastrowid, "Email template"))


@router.get("/email/templates/{template_id}")
def get_email_template(template_id: int, conn=Db):
    return _email_template(_record(conn, "email_templates", template_id, "Email template"))


@router.patch("/email/templates/{template_id}")
def update_email_template(template_id: int, payload: EmailTemplateUpdate, conn=Db):
    values = payload.model_dump(exclude={"version"}, exclude_none=True)
    if "active" in values:
        values["active"] = int(values["active"])
    if not values:
        return get_email_template(template_id, conn)
    assignments = ", ".join(f"{key}=?" for key in values)
    try:
        cursor = conn.execute(
            f"UPDATE email_templates SET {assignments}, version=version+1, updated_at=? WHERE id=? AND version=? AND archived_at IS NULL",
            [*values.values(), _iso(), template_id, payload.version],
        )
    except sqlite3.IntegrityError as exc:
        raise _conflict("An active email template already uses this name") from exc
    if cursor.rowcount != 1:
        if not conn.execute("SELECT 1 FROM email_templates WHERE id=?", (template_id,)).fetchone():
            raise _not_found("Email template")
        raise _conflict("Email template was changed by another request")
    return get_email_template(template_id, conn)


@router.post("/email/templates/{template_id}/preview")
def preview_email_template(template_id: int, payload: TemplatePreview, conn=Db):
    template = get_email_template(template_id, conn)
    return {
        "subject": _render(template["subject"], payload.values),
        "body_text": _render(template["body_text"], payload.values),
    }


@router.post("/email/templates/{template_id}/archive")
def archive_email_template(template_id: int, payload: EnrollmentAction, conn=Db):
    cursor = conn.execute(
        "UPDATE email_templates SET archived_at=?, active=0, version=version+1, updated_at=? WHERE id=? AND version=? AND archived_at IS NULL",
        (_iso(), _iso(), template_id, payload.version),
    )
    if cursor.rowcount != 1:
        raise _conflict("Email template was changed or archived")
    return get_email_template(template_id, conn)


def _insert_step(conn: sqlite3.Connection, sequence_id: int, payload: SequenceStepCreate) -> int:
    position = payload.position
    if position is None:
        position = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM sequence_steps WHERE sequence_id=?",
            (sequence_id,),
        ).fetchone()[0]
    now = _iso()
    try:
        return conn.execute(
            """INSERT INTO sequence_steps
               (sequence_id, position, step_type, template_id, subject, body_text,
                delay_minutes, task_title, task_description, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sequence_id,
                position,
                payload.step_type,
                payload.template_id,
                payload.subject,
                payload.body_text,
                payload.delay_minutes,
                payload.task_title,
                payload.task_description,
                now,
                now,
            ),
        ).lastrowid
    except sqlite3.IntegrityError as exc:
        raise _conflict("Sequence step position or template is invalid") from exc


def _sequence_detail(conn: sqlite3.Connection, sequence_id: int) -> dict[str, Any]:
    result = _sequence(_record(conn, "sales_sequences", sequence_id, "Sequence"))
    result["steps"] = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM sequence_steps WHERE sequence_id=? ORDER BY position", (sequence_id,)
        ).fetchall()
    ]
    counts = conn.execute(
        "SELECT state, COUNT(*) count FROM sequence_enrollments WHERE sequence_id=? GROUP BY state",
        (sequence_id,),
    ).fetchall()
    result["enrollment_counts"] = {row["state"]: row["count"] for row in counts}
    return result


@router.get("/sequences")
def list_sequences(
    state: str = "", cursor: int | None = None, limit: int = Query(50, ge=1, le=100), conn=Db
):
    clauses, values = ["archived_at IS NULL"], []
    if state:
        clauses.append("state=?")
        values.append(state)
    if cursor is not None:
        clauses.append("id < ?")
        values.append(cursor)
    values.append(limit + 1)
    rows = conn.execute(
        f"SELECT * FROM sales_sequences WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?", values
    ).fetchall()
    return _page(rows, limit, _sequence)


@router.post("/sequences", status_code=201)
def create_sequence(payload: SequenceCreate, conn=Db):
    now = _iso()
    try:
        sequence_id = conn.execute(
            """INSERT INTO sales_sequences
               (name, description, timezone, business_days_json, send_window_start,
                send_window_end, daily_cap, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                payload.name,
                payload.description,
                payload.timezone,
                _json(payload.business_days),
                payload.send_window_start,
                payload.send_window_end,
                payload.daily_cap,
                now,
                now,
            ),
        ).lastrowid
        for index, step in enumerate(payload.steps):
            if step.position is None:
                step.position = index
            _insert_step(conn, sequence_id, step)
    except sqlite3.IntegrityError as exc:
        raise _conflict("An active sequence already uses this name") from exc
    return _sequence_detail(conn, sequence_id)


@router.get("/sequences/{sequence_id}")
def get_sequence(sequence_id: int, conn=Db):
    return _sequence_detail(conn, sequence_id)


@router.patch("/sequences/{sequence_id}")
def update_sequence(sequence_id: int, payload: SequenceUpdate, conn=Db):
    values = payload.model_dump(exclude={"version"}, exclude_none=True)
    if "business_days" in values:
        days = values.pop("business_days")
        if not days or any(day not in range(7) for day in days):
            raise HTTPException(status_code=422, detail="business_days must contain weekdays 0 through 6")
        values["business_days_json"] = _json(sorted(set(days)))
    if "timezone" in values:
        try:
            ZoneInfo(values["timezone"])
        except ZoneInfoNotFoundError:
            raise HTTPException(status_code=422, detail="Unknown timezone") from None
    current = _record(conn, "sales_sequences", sequence_id, "Sequence")
    start = values.get("send_window_start", current["send_window_start"])
    end = values.get("send_window_end", current["send_window_end"])
    try:
        if time.fromisoformat(start) >= time.fromisoformat(end):
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=422, detail="send_window_end must be after send_window_start") from None
    if not values:
        return _sequence_detail(conn, sequence_id)
    assignments = ", ".join(f"{key}=?" for key in values)
    cursor = conn.execute(
        f"UPDATE sales_sequences SET {assignments}, version=version+1, updated_at=? WHERE id=? AND version=? AND archived_at IS NULL",
        [*values.values(), _iso(), sequence_id, payload.version],
    )
    if cursor.rowcount != 1:
        raise _conflict("Sequence was changed by another request")
    return _sequence_detail(conn, sequence_id)


@router.post("/sequences/{sequence_id}/steps", status_code=201)
def add_sequence_step(sequence_id: int, payload: SequenceStepCreate, conn=Db):
    sequence = _record(conn, "sales_sequences", sequence_id, "Sequence")
    if sequence["state"] != "Draft":
        raise _conflict("Only draft sequences can be edited")
    step_id = _insert_step(conn, sequence_id, payload)
    conn.execute(
        "UPDATE sales_sequences SET version=version+1, updated_at=? WHERE id=?", (_iso(), sequence_id)
    )
    return _plain(_record(conn, "sequence_steps", step_id, "Sequence step"))


@router.patch("/sequences/{sequence_id}/steps/{step_id}")
def update_sequence_step(sequence_id: int, step_id: int, payload: SequenceStepUpdate, conn=Db):
    sequence = _record(conn, "sales_sequences", sequence_id, "Sequence")
    if sequence["state"] != "Draft":
        raise _conflict("Only draft sequences can be edited")
    values = payload.model_dump(exclude={"version"}, exclude_none=True)
    if not values:
        return _plain(_record(conn, "sequence_steps", step_id, "Sequence step"))
    assignments = ", ".join(f"{key}=?" for key in values)
    try:
        cursor = conn.execute(
            f"UPDATE sequence_steps SET {assignments}, version=version+1, updated_at=? WHERE id=? AND sequence_id=? AND version=?",
            [*values.values(), _iso(), step_id, sequence_id, payload.version],
        )
    except sqlite3.IntegrityError as exc:
        raise _conflict("Sequence step position or template is invalid") from exc
    if cursor.rowcount != 1:
        raise _conflict("Sequence step was changed by another request")
    return _plain(_record(conn, "sequence_steps", step_id, "Sequence step"))


@router.delete("/sequences/{sequence_id}/steps/{step_id}", status_code=204)
def delete_sequence_step(sequence_id: int, step_id: int, conn=Db):
    sequence = _record(conn, "sales_sequences", sequence_id, "Sequence")
    if sequence["state"] != "Draft":
        raise _conflict("Only draft sequences can be edited")
    cursor = conn.execute(
        "DELETE FROM sequence_steps WHERE id=? AND sequence_id=?", (step_id, sequence_id)
    )
    if cursor.rowcount != 1:
        raise _not_found("Sequence step")
    rows = conn.execute(
        "SELECT id FROM sequence_steps WHERE sequence_id=? ORDER BY position", (sequence_id,)
    ).fetchall()
    conn.execute("UPDATE sequence_steps SET position=position+10000 WHERE sequence_id=?", (sequence_id,))
    for position, row in enumerate(rows):
        conn.execute("UPDATE sequence_steps SET position=? WHERE id=?", (position, row["id"]))
    conn.execute(
        "UPDATE sales_sequences SET version=version+1, updated_at=? WHERE id=?", (_iso(), sequence_id)
    )
    return Response(status_code=204)


@router.post("/sequences/{sequence_id}/activate")
def activate_sequence(sequence_id: int, action: EnrollmentAction, _confirmation: None = Confirmation, conn=Db):
    sequence = _record(conn, "sales_sequences", sequence_id, "Sequence")
    steps = conn.execute(
        "SELECT * FROM sequence_steps WHERE sequence_id=? ORDER BY position", (sequence_id,)
    ).fetchall()
    if not steps:
        raise _conflict("A sequence needs at least one step before activation")
    for step in steps:
        if step["step_type"] == "email":
            template = step["template_id"] and conn.execute(
                "SELECT subject, body_text, active FROM email_templates WHERE id=? AND archived_at IS NULL",
                (step["template_id"],),
            ).fetchone()
            if not template and not (step["subject"].strip() or step["body_text"].strip()):
                raise _conflict("Every email step needs an active template or inline content")
            if template and not template["active"]:
                raise _conflict("Sequence email templates must be active")
        if step["step_type"] == "manual_task" and not step["task_title"].strip():
            raise _conflict("Every manual task step needs a title")
    cursor = conn.execute(
        "UPDATE sales_sequences SET state='Active', version=version+1, updated_at=? WHERE id=? AND version=? AND archived_at IS NULL",
        (_iso(), sequence_id, action.version),
    )
    if cursor.rowcount != 1:
        raise _conflict("Sequence was changed by another request")
    return _sequence_detail(conn, sequence_id)


def _recipient_context(
    conn: sqlite3.Connection, payload: EnrollmentCreate
) -> tuple[int | None, str, dict[str, Any]]:
    context: dict[str, Any] = dict(payload.merge_fields)
    contact_id, email = payload.contact_id, str(payload.email or "").lower()
    if contact_id is not None:
        contact = conn.execute(
            "SELECT * FROM contacts WHERE id=? AND archived_at IS NULL", (contact_id,)
        ).fetchone()
        if contact is None:
            raise _not_found("Contact")
        email = email or contact["email"].strip().lower()
        context = {
            "first_name": contact["first_name"],
            "last_name": contact["last_name"],
            "display_name": contact["display_name"],
            "job_title": contact["job_title"],
            "email": email,
            **context,
        }
        if contact["account_id"]:
            account = conn.execute("SELECT * FROM accounts WHERE id=?", (contact["account_id"],)).fetchone()
            if account:
                context.update(
                    {
                        "account_name": account["name"],
                        "company": account["name"],
                        "domain": account["domain"],
                    }
                )
    if not email:
        raise HTTPException(status_code=422, detail="Recipient has no email address")
    context.setdefault("email", email)
    return contact_id, email, context


def _next_business_slot(
    conn: sqlite3.Connection, sequence: sqlite3.Row, value: datetime
) -> datetime:
    try:
        zone = ZoneInfo(sequence["timezone"])
    except ZoneInfoNotFoundError:
        raise HTTPException(status_code=422, detail="Unknown sequence timezone") from None
    days = set(_decode(sequence["business_days_json"], [0, 1, 2, 3, 4]))
    start, end = time.fromisoformat(sequence["send_window_start"]), time.fromisoformat(sequence["send_window_end"])
    candidate = _utc(value).astimezone(zone)
    for _ in range(15):
        if candidate.weekday() not in days:
            candidate = datetime.combine(candidate.date() + timedelta(days=1), start, zone)
            continue
        if candidate.time() < start:
            candidate = datetime.combine(candidate.date(), start, zone)
        elif candidate.time() >= end:
            candidate = datetime.combine(candidate.date() + timedelta(days=1), start, zone)
            continue
        day_start = datetime.combine(candidate.date(), time.min, zone).astimezone(UTC).isoformat()
        day_end = datetime.combine(candidate.date() + timedelta(days=1), time.min, zone).astimezone(UTC).isoformat()
        count = conn.execute(
            """SELECT COUNT(*) FROM scheduled_email_sends s
               JOIN sequence_enrollments e ON e.id=s.enrollment_id
               WHERE e.sequence_id=? AND s.state IN ('Queued','Sending','Sent')
                 AND s.scheduled_for>=? AND s.scheduled_for<?""",
            (sequence["id"], day_start, day_end),
        ).fetchone()[0]
        if count < sequence["daily_cap"]:
            return candidate.astimezone(UTC)
        candidate = datetime.combine(candidate.date() + timedelta(days=1), start, zone)
    raise _conflict("No available sequence send slot in the next two weeks")


def _prepare_enrollment(
    conn: sqlite3.Connection,
    sequence: sqlite3.Row,
    enrollment_id: int,
    email: str,
    contact_id: int | None,
    context: dict[str, Any],
    start_at: datetime,
    idempotency_key: str,
) -> list[sqlite3.Row]:
    cursor = _utc(start_at, sequence["timezone"])
    next_actions: list[str] = []
    steps = conn.execute(
        """SELECT s.*, t.subject template_subject, t.body_text template_body
           FROM sequence_steps s LEFT JOIN email_templates t ON t.id=s.template_id
           WHERE s.sequence_id=? ORDER BY s.position""",
        (sequence["id"],),
    ).fetchall()
    now = _iso()
    send_ids: list[int] = []
    for step in steps:
        if step["step_type"] == "delay":
            cursor += timedelta(minutes=step["delay_minutes"])
            continue
        if step["step_type"] == "manual_task":
            title = _render(step["task_title"], context)
            due_at = cursor.isoformat()
            conn.execute(
                """INSERT INTO work_tasks
                   (entity_type, entity_id, title, description, status, priority, due_at,
                    created_at, updated_at)
                   VALUES ('sequence_enrollment', ?, ?, ?, 'Open', 'Medium', ?, ?, ?)""",
                (enrollment_id, title, _render(step["task_description"], context), due_at, now, now),
            )
            next_actions.append(due_at)
            continue
        cursor = _next_business_slot(conn, sequence, cursor)
        subject = step["subject"] or step["template_subject"] or ""
        body = step["body_text"] or step["template_body"] or ""
        rfc_id = f"<{uuid.uuid4().hex}@crmworkspace.local>"
        step_key = f"{idempotency_key}:step:{step['id']}"
        send_id = conn.execute(
            """INSERT INTO scheduled_email_sends
               (enrollment_id, sequence_step_id, to_email, subject, body_text, rfc_message_id,
                scheduled_for, idempotency_key, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                enrollment_id,
                step["id"],
                email,
                _render(subject, context),
                _render(body, context),
                rfc_id,
                cursor.isoformat(),
                step_key,
                now,
                now,
            ),
        ).lastrowid
        send_ids.append(send_id)
        next_actions.append(cursor.isoformat())
        cursor += timedelta(minutes=1)
    conn.execute(
        "UPDATE sequence_enrollments SET next_action_at=?, updated_at=? WHERE id=?",
        (min(next_actions) if next_actions else None, now, enrollment_id),
    )
    if not send_ids:
        return []
    placeholders = ",".join("?" for _ in send_ids)
    return conn.execute(
        f"SELECT * FROM scheduled_email_sends WHERE id IN ({placeholders}) ORDER BY scheduled_for, id",
        send_ids,
    ).fetchall()


@router.get("/sequences/{sequence_id}/enrollments")
def list_enrollments(
    sequence_id: int,
    state: str = "",
    cursor: int | None = None,
    limit: int = Query(50, ge=1, le=100),
    conn=Db,
):
    _record(conn, "sales_sequences", sequence_id, "Sequence")
    clauses, values = ["sequence_id=?"], [sequence_id]
    if state:
        clauses.append("state=?")
        values.append(state)
    if cursor is not None:
        clauses.append("id < ?")
        values.append(cursor)
    values.append(limit + 1)
    rows = conn.execute(
        f"SELECT * FROM sequence_enrollments WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?",
        values,
    ).fetchall()
    return _page(rows, limit)


@router.post("/sequences/{sequence_id}/enrollments", status_code=202)
def enroll_sequence(
    sequence_id: int,
    payload: EnrollmentCreate,
    idempotency_key: str = IdempotencyKey,
    conn=Db,
):
    action = f"sequence.enroll:{sequence_id}"
    if cached := _cached(conn, action, idempotency_key):
        return cached
    sequence = _record(conn, "sales_sequences", sequence_id, "Sequence")
    if sequence["state"] != "Active":
        raise _conflict("Only active sequences accept enrollments")
    contact_id, email, context = _recipient_context(conn, payload)
    if _is_suppressed(conn, email):
        raise _conflict("Recipient is suppressed or opted out")
    now = _iso()
    try:
        enrollment_id = conn.execute(
            """INSERT INTO sequence_enrollments
               (sequence_id, contact_id, email, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (sequence_id, contact_id, email, now, now),
        ).lastrowid
    except sqlite3.IntegrityError as exc:
        raise _conflict("Recipient already has an active enrollment in this sequence") from exc
    sends = _prepare_enrollment(
        conn,
        sequence,
        enrollment_id,
        email,
        contact_id,
        context,
        payload.start_at or platform_db.utc_now(),
        idempotency_key,
    )
    jobs = _queue_gmail_jobs(conn, sends)
    result = _plain(_record(conn, "sequence_enrollments", enrollment_id, "Enrollment"))
    result["scheduled_sends"] = jobs
    _remember(conn, action, idempotency_key, result)
    return result


def _get_enrollment(conn: sqlite3.Connection, enrollment_id: int) -> sqlite3.Row:
    return _record(conn, "sequence_enrollments", enrollment_id, "Enrollment")


def _stop_enrollment(
    conn: sqlite3.Connection,
    enrollment_id: int,
    version: int,
    state: str,
    reason: str,
) -> dict[str, Any]:
    now = _iso()
    cursor = conn.execute(
        """UPDATE sequence_enrollments SET state=?, stopped_reason=?, version=version+1,
             updated_at=?, completed_at=CASE WHEN ?='Paused' THEN NULL ELSE ? END,
             replied_at=CASE WHEN ?='Replied' THEN ? ELSE replied_at END,
             bounced_at=CASE WHEN ?='Bounced' THEN ? ELSE bounced_at END,
             opted_out_at=CASE WHEN ?='Opted out' THEN ? ELSE opted_out_at END
           WHERE id=? AND version=? AND state IN ('Active','Paused')""",
        (
            state,
            reason,
            now,
            state,
            now,
            state,
            now,
            state,
            now,
            state,
            now,
            enrollment_id,
            version,
        ),
    )
    if cursor.rowcount != 1:
        raise _conflict("Enrollment was changed or is already stopped")
    pending_states = ("Queued",) if state == "Paused" else ("Queued", "Paused")
    placeholders = ",".join("?" for _ in pending_states)
    sends = conn.execute(
        f"SELECT id, job_id FROM scheduled_email_sends WHERE enrollment_id=? AND state IN ({placeholders})",
        (enrollment_id, *pending_states),
    ).fetchall()
    new_send_state = "Paused" if state == "Paused" else "Cancelled"
    conn.execute(
        f"UPDATE scheduled_email_sends SET state=?, version=version+1, updated_at=? WHERE enrollment_id=? AND state IN ({placeholders})",
        (new_send_state, now, enrollment_id, *pending_states),
    )
    _cancel_jobs(conn, [row["job_id"] for row in sends])
    if state != "Paused":
        conn.execute(
            "UPDATE work_tasks SET status='Cancelled', updated_at=?, version=version+1 WHERE entity_type='sequence_enrollment' AND entity_id=? AND status NOT IN ('Done','Cancelled')",
            (now, enrollment_id),
        )
    return _plain(_get_enrollment(conn, enrollment_id))


@router.post("/sequences/enrollments/{enrollment_id}/pause")
@router.post("/sequence-enrollments/{enrollment_id}/pause", include_in_schema=False)
def pause_enrollment(enrollment_id: int, payload: EnrollmentAction, idempotency_key: str | None = OptionalIdempotencyKey, conn=Db):
    action = f"sequence.enrollment:{enrollment_id}:pause"
    idempotency_key = idempotency_key or f"legacy:{payload.version}"
    if cached := _cached(conn, action, idempotency_key):
        return cached
    result = _stop_enrollment(conn, enrollment_id, payload.version, "Paused", payload.reason)
    _remember(conn, action, idempotency_key, result)
    return result


@router.post("/sequences/enrollments/{enrollment_id}/resume", status_code=202)
@router.post("/sequence-enrollments/{enrollment_id}/resume", status_code=202, include_in_schema=False)
def resume_enrollment(enrollment_id: int, payload: EnrollmentAction, idempotency_key: str | None = OptionalIdempotencyKey, conn=Db):
    action = f"sequence.enrollment:{enrollment_id}:resume"
    idempotency_key = idempotency_key or f"legacy:{payload.version}"
    if cached := _cached(conn, action, idempotency_key):
        return cached
    enrollment = _get_enrollment(conn, enrollment_id)
    if enrollment["state"] != "Paused" or enrollment["version"] != payload.version:
        raise _conflict("Enrollment was changed or is not paused")
    if _is_suppressed(conn, enrollment["email"]):
        raise _conflict("Recipient is suppressed or opted out")
    sequence = _record(conn, "sales_sequences", enrollment["sequence_id"], "Sequence")
    if sequence["state"] != "Active":
        raise _conflict("Sequence is not active")
    now_dt, now = platform_db.utc_now(), _iso()
    sends = conn.execute(
        "SELECT * FROM scheduled_email_sends WHERE enrollment_id=? AND state='Paused' ORDER BY scheduled_for, id",
        (enrollment_id,),
    ).fetchall()
    prepared: list[dict[str, Any]] = []
    for send in sends:
        item = dict(send)
        scheduled = max(datetime.fromisoformat(item["scheduled_for"]), now_dt)
        scheduled = _next_business_slot(conn, sequence, scheduled)
        item["scheduled_for"] = scheduled.isoformat()
        item["idempotency_key"] = f"resume:{enrollment_id}:{payload.version}:{item['id']}"
        conn.execute(
            """UPDATE scheduled_email_sends SET state='Queued', scheduled_for=?, idempotency_key=?,
                 job_id=NULL, version=version+1, updated_at=? WHERE id=?""",
            (item["scheduled_for"], item["idempotency_key"], now, item["id"]),
        )
        prepared.append(item)
    conn.execute(
        "UPDATE sequence_enrollments SET state='Active', stopped_reason='', version=version+1, updated_at=? WHERE id=?",
        (now, enrollment_id),
    )
    jobs = _queue_gmail_jobs(conn, prepared)
    result = _plain(_get_enrollment(conn, enrollment_id))
    result["scheduled_sends"] = jobs
    _remember(conn, action, idempotency_key, result)
    return result


@router.post("/sequences/enrollments/{enrollment_id}/cancel")
@router.post("/sequence-enrollments/{enrollment_id}/cancel", include_in_schema=False)
def cancel_enrollment(enrollment_id: int, payload: EnrollmentAction, idempotency_key: str | None = OptionalIdempotencyKey, conn=Db):
    action = f"sequence.enrollment:{enrollment_id}:cancel"
    idempotency_key = idempotency_key or f"legacy:{payload.version}"
    if cached := _cached(conn, action, idempotency_key):
        return cached
    result = _stop_enrollment(conn, enrollment_id, payload.version, "Cancelled", payload.reason)
    _remember(conn, action, idempotency_key, result)
    return result


@router.post("/sequences/enrollments/{enrollment_id}/reply")
def reply_to_enrollment(enrollment_id: int, payload: EnrollmentAction, idempotency_key: str | None = OptionalIdempotencyKey, conn=Db):
    action = f"sequence.enrollment:{enrollment_id}:reply"
    idempotency_key = idempotency_key or f"legacy:{payload.version}"
    if cached := _cached(conn, action, idempotency_key):
        return cached
    result = _stop_enrollment(conn, enrollment_id, payload.version, "Replied", payload.reason or "Reply received")
    _remember(conn, action, idempotency_key, result)
    return result


@router.post("/sequences/enrollments/{enrollment_id}/bounce")
def bounce_enrollment(enrollment_id: int, payload: EnrollmentAction, idempotency_key: str | None = OptionalIdempotencyKey, conn=Db):
    action = f"sequence.enrollment:{enrollment_id}:bounce"
    idempotency_key = idempotency_key or f"legacy:{payload.version}"
    if cached := _cached(conn, action, idempotency_key):
        return cached
    reason = payload.reason or "Email bounced"
    result = _stop_enrollment(conn, enrollment_id, payload.version, "Bounced", reason)
    now = _iso()
    conn.execute(
        """INSERT INTO email_suppressions(email, reason, source, created_at, updated_at)
           VALUES (?, ?, 'sequence_bounce', ?, ?)
           ON CONFLICT(email) DO UPDATE SET reason=excluded.reason, source=excluded.source, updated_at=excluded.updated_at""",
        (result["email"].lower(), reason, now, now),
    )
    _remember(conn, action, idempotency_key, result)
    return result


@router.get("/email/scheduled-sends")
def list_scheduled_sends(
    state: str = "", cursor: int | None = None, limit: int = Query(50, ge=1, le=100), conn=Db
):
    clauses, values = ["1=1"], []
    if state:
        clauses.append("state=?")
        values.append(state)
    if cursor is not None:
        clauses.append("id < ?")
        values.append(cursor)
    values.append(limit + 1)
    rows = conn.execute(
        f"SELECT * FROM scheduled_email_sends WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?", values
    ).fetchall()
    return _page(rows, limit)


@router.post("/email/send", status_code=202)
def schedule_email(
    payload: ScheduledSendCreate,
    idempotency_key: str = IdempotencyKey,
    _confirmation: None = Confirmation,
    conn=Db,
):
    action = "email.send"
    if cached := _cached(conn, action, idempotency_key):
        return cached
    email = str(payload.to).lower()
    if _is_suppressed(conn, email):
        raise _conflict("Recipient is suppressed or opted out")
    when = _utc(payload.schedule_at or platform_db.utc_now())
    rfc_id = payload.rfc_message_id.strip() or f"<{uuid.uuid4().hex}@crmworkspace.local>"
    now = _iso()
    send_id = conn.execute(
        """INSERT INTO scheduled_email_sends
           (to_email, subject, body_text, rfc_message_id, gmail_thread_id,
            reply_to_message_id, scheduled_for, idempotency_key, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            email,
            payload.subject,
            payload.body_text,
            rfc_id,
            payload.thread_id,
            payload.reply_to_message_id,
            when.isoformat(),
            idempotency_key,
            now,
            now,
        ),
    ).lastrowid
    send = _record(conn, "scheduled_email_sends", send_id, "Scheduled send")
    jobs = _queue_gmail_jobs(conn, [send])
    result = {
        "id": send_id,
        "job_id": jobs[0]["job_id"],
        "state": jobs[0]["state"],
        "scheduled_for": when.isoformat(),
        "rfc_message_id": rfc_id,
    }
    _remember(conn, action, idempotency_key, result)
    return result


@router.get("/email/suppressions")
def list_suppressions(
    cursor: str | None = None, limit: int = Query(50, ge=1, le=100), conn=Db
):
    if cursor:
        rows = conn.execute(
            "SELECT * FROM email_suppressions WHERE email > ? COLLATE NOCASE ORDER BY email LIMIT ?",
            (cursor, limit + 1),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM email_suppressions ORDER BY email LIMIT ?", (limit + 1,)
        ).fetchall()
    has_more = len(rows) > limit
    visible = rows[:limit]
    return {
        "items": [dict(row) for row in visible],
        "next_cursor": visible[-1]["email"] if has_more and visible else None,
    }


@router.post("/email/suppressions", status_code=201)
@router.post("/email/opt-out", status_code=201, include_in_schema=False)
def suppress_email(payload: SuppressionCreate, conn=Db):
    email, now = str(payload.email).lower(), _iso()
    conn.execute(
        """INSERT INTO email_suppressions(email, reason, source, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(email) DO UPDATE SET reason=excluded.reason, source=excluded.source,
             updated_at=excluded.updated_at""",
        (email, payload.reason, payload.source, now, now),
    )
    conn.execute(
        "UPDATE contacts SET email_opt_out_at=?, updated_at=?, version=version+1 WHERE lower(email)=lower(?) AND archived_at IS NULL",
        (now, now, email),
    )
    enrollments = conn.execute(
        "SELECT id, version FROM sequence_enrollments WHERE email=? COLLATE NOCASE AND state IN ('Active','Paused')",
        (email,),
    ).fetchall()
    for enrollment in enrollments:
        _stop_enrollment(conn, enrollment["id"], enrollment["version"], "Opted out", payload.reason or "Opted out")
    return dict(
        conn.execute("SELECT * FROM email_suppressions WHERE email=? COLLATE NOCASE", (email,)).fetchone()
    )


@router.delete("/email/suppressions/{email}", status_code=204)
def remove_suppression(email: str, conn=Db):
    normalized = _email_address(email) or email.strip().lower()
    conn.execute("DELETE FROM email_suppressions WHERE email=? COLLATE NOCASE", (normalized,))
    conn.execute(
        "UPDATE contacts SET email_opt_out_at=NULL, updated_at=?, version=version+1 WHERE lower(email)=lower(?) AND archived_at IS NULL",
        (_iso(), normalized),
    )
    return Response(status_code=204)


@router.get("/document-templates")
def list_document_templates(
    cursor: int | None = None, limit: int = Query(50, ge=1, le=100), conn=Db
):
    if cursor is None:
        rows = conn.execute(
            "SELECT * FROM document_templates WHERE archived_at IS NULL ORDER BY id DESC LIMIT ?", (limit + 1,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM document_templates WHERE archived_at IS NULL AND id<? ORDER BY id DESC LIMIT ?",
            (cursor, limit + 1),
        ).fetchall()
    return _page(rows, limit, _document_template)


@router.post("/document-templates", status_code=201)
def create_document_template(payload: DocumentTemplateCreate, conn=Db):
    now = _iso()
    try:
        template_id = conn.execute(
            """INSERT INTO document_templates
               (name, category, google_file_id, mime_type, merge_schema_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                payload.name,
                payload.category,
                payload.google_file_id,
                payload.mime_type,
                _json(payload.merge_schema),
                now,
                now,
            ),
        ).lastrowid
    except sqlite3.IntegrityError as exc:
        raise _conflict("An active document template already uses this name") from exc
    return _document_template(_record(conn, "document_templates", template_id, "Document template"))


@router.get("/document-templates/{template_id}")
def get_document_template(template_id: int, conn=Db):
    return _document_template(_record(conn, "document_templates", template_id, "Document template"))


@router.patch("/document-templates/{template_id}")
def update_document_template(template_id: int, payload: DocumentTemplateUpdate, conn=Db):
    values = payload.model_dump(exclude={"version"}, exclude_none=True)
    if "merge_schema" in values:
        values["merge_schema_json"] = _json(values.pop("merge_schema"))
    if not values:
        return get_document_template(template_id, conn)
    assignments = ", ".join(f"{key}=?" for key in values)
    cursor = conn.execute(
        f"UPDATE document_templates SET {assignments}, version=version+1, updated_at=? WHERE id=? AND version=? AND archived_at IS NULL",
        [*values.values(), _iso(), template_id, payload.version],
    )
    if cursor.rowcount != 1:
        raise _conflict("Document template was changed by another request")
    return get_document_template(template_id, conn)


@router.post("/document-templates/{template_id}/archive")
def archive_document_template(template_id: int, payload: EnrollmentAction, conn=Db):
    cursor = conn.execute(
        "UPDATE document_templates SET archived_at=?, version=version+1, updated_at=? "
        "WHERE id=? AND version=? AND archived_at IS NULL",
        (_iso(), _iso(), template_id, payload.version),
    )
    if cursor.rowcount != 1:
        if not conn.execute("SELECT 1 FROM document_templates WHERE id=?", (template_id,)).fetchone():
            raise _not_found("Document template")
        raise _conflict("Document template was changed or archived")
    return get_document_template(template_id, conn)


@router.get("/documents")
def list_documents(
    entity_type: str = "",
    entity_id: int | None = None,
    cursor: int | None = None,
    limit: int = Query(50, ge=1, le=100),
    conn=Db,
):
    clauses, values = ["archived_at IS NULL"], []
    if entity_type:
        clauses.append("entity_type=?")
        values.append(entity_type)
    if entity_id is not None:
        clauses.append("entity_id=?")
        values.append(entity_id)
    if cursor is not None:
        clauses.append("id<?")
        values.append(cursor)
    values.append(limit + 1)
    rows = conn.execute(
        f"SELECT * FROM documents WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?", values
    ).fetchall()
    return _page(rows, limit)


@router.post("/documents", status_code=201)
def create_document(
    payload: DocumentCreate,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key", max_length=200),
    conn=Db,
):
    if payload.queue_drive and not idempotency_key:
        raise HTTPException(status_code=422, detail="Idempotency-Key is required when queue_drive is true")
    template = None
    if payload.template_id is not None:
        template = _record(conn, "document_templates", payload.template_id, "Document template")
    now = _iso()
    state = "Queued" if payload.queue_drive else ("Ready" if payload.google_file_id else "Local")
    document_id = conn.execute(
        """INSERT INTO documents
           (template_id, entity_type, entity_id, title, mime_type, google_file_id, drive_url,
            local_path, checksum_sha256, sync_state, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            payload.template_id,
            payload.entity_type,
            payload.entity_id,
            payload.title,
            payload.mime_type,
            payload.google_file_id,
            payload.drive_url,
            payload.local_path,
            payload.checksum_sha256,
            state,
            now,
            now,
        ),
    ).lastrowid
    if payload.queue_drive:
        job_id = _queue_drive_job(
            conn,
            "google.drive.document.create",
            {
                "document_id": document_id,
                "title": payload.title,
                "template_google_file_id": template["google_file_id"] if template else "",
                "parent_google_file_id": payload.parent_google_file_id,
                "mime_type": payload.mime_type,
                "merge_data": payload.merge_data,
            },
            idempotency_key or "",
        )
        conn.execute(
            "UPDATE documents SET drive_job_id=?, updated_at=? WHERE id=?", (job_id, _iso(), document_id)
        )
    return document_detail(document_id, conn)


@router.get("/documents/{document_id}")
def document_detail(document_id: int, conn=Db):
    result = _plain(_record(conn, "documents", document_id, "Document"))
    result["versions"] = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM document_versions WHERE document_id=? ORDER BY version_number DESC",
            (document_id,),
        ).fetchall()
    ]
    return result


@router.patch("/documents/{document_id}")
def update_document(document_id: int, payload: DocumentUpdate, conn=Db):
    values = payload.model_dump(exclude={"version"}, exclude_none=True)
    if not values:
        return document_detail(document_id, conn)
    assignments = ", ".join(f"{key}=?" for key in values)
    cursor = conn.execute(
        f"UPDATE documents SET {assignments}, version=version+1, updated_at=? WHERE id=? AND version=? AND archived_at IS NULL",
        [*values.values(), _iso(), document_id, payload.version],
    )
    if cursor.rowcount != 1:
        raise _conflict("Document was changed by another request")
    return document_detail(document_id, conn)


@router.post("/documents/{document_id}/sync", status_code=202)
def queue_document_sync(
    document_id: int, idempotency_key: str = IdempotencyKey, conn=Db
):
    document = _record(conn, "documents", document_id, "Document")
    job_id = _queue_drive_job(
        conn,
        "google.drive.document.sync",
        {"document_id": document_id, "google_file_id": document["google_file_id"]},
        idempotency_key,
    )
    conn.execute(
        "UPDATE documents SET sync_state='Queued', drive_job_id=?, version=version+1, updated_at=? WHERE id=?",
        (job_id, _iso(), document_id),
    )
    return {"document_id": document_id, "job_id": job_id, "state": "queued"}


@router.post("/documents/{document_id}/versions", status_code=201)
def create_document_version(
    document_id: int,
    payload: DocumentVersionCreate,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key", max_length=200),
    conn=Db,
):
    document = _record(conn, "documents", document_id, "Document")
    if payload.queue_drive and not idempotency_key:
        raise HTTPException(status_code=422, detail="Idempotency-Key is required when queue_drive is true")
    version_number = conn.execute(
        "SELECT COALESCE(MAX(version_number), 0) + 1 FROM document_versions WHERE document_id=?",
        (document_id,),
    ).fetchone()[0]
    version_id = conn.execute(
        """INSERT INTO document_versions
           (document_id, version_number, google_file_id, local_path, mime_type,
            checksum_sha256, size_bytes, issued, source, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            document_id,
            version_number,
            payload.google_file_id,
            payload.local_path,
            payload.mime_type,
            payload.checksum_sha256.lower(),
            payload.size_bytes,
            int(payload.issued),
            payload.source,
            _iso(),
        ),
    ).lastrowid
    if payload.queue_drive:
        job_id = _queue_drive_job(
            conn,
            "google.drive.version.upload",
            {
                "document_id": document_id,
                "version_id": version_id,
                "google_file_id": document["google_file_id"],
                "local_path": payload.local_path,
                "mime_type": payload.mime_type,
                "checksum_sha256": payload.checksum_sha256.lower(),
            },
            idempotency_key or "",
        )
        conn.execute("UPDATE document_versions SET drive_job_id=? WHERE id=?", (job_id, version_id))
    return _plain(_record(conn, "document_versions", version_id, "Document version"))


@router.post("/documents/{document_id}/archive")
def archive_document(document_id: int, payload: EnrollmentAction, conn=Db):
    cursor = conn.execute(
        "UPDATE documents SET archived_at=?, version=version+1, updated_at=? WHERE id=? AND version=? AND archived_at IS NULL",
        (_iso(), _iso(), document_id, payload.version),
    )
    if cursor.rowcount != 1:
        raise _conflict("Document was changed or archived")
    return document_detail(document_id, conn)
