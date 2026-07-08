from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel

from app.data import CALENDAR, CLIENTS, LEADS
from app.schemas import CalendarItem, Client, Lead, Note, Task
from app.services.local_store import load_model_list


DB_PATH = Path(__file__).resolve().parents[2] / "crm.sqlite3"
LEGACY_CLIENTS_PATH = Path(__file__).resolve().parents[2] / "manual_clients.json"
LEGACY_CALENDAR_PATH = Path(__file__).resolve().parents[2] / "manual_calendar.json"
LEGACY_LEADS_PATH = Path(__file__).resolve().parents[2] / "discovered_leads.json"


def db_path() -> Path:
    return Path(os.getenv("CRM_DB_PATH") or DB_PATH)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def list_clients() -> list[Client]:
    _bootstrap()
    with _connect() as conn:
        rows = conn.execute("SELECT payload FROM clients ORDER BY lower(name)").fetchall()
    return [Client.model_validate_json(row["payload"]) for row in rows]


def get_client(client_id: int) -> Client | None:
    _bootstrap()
    with _connect() as conn:
        row = conn.execute("SELECT payload FROM clients WHERE id = ?", (client_id,)).fetchone()
    return Client.model_validate_json(row["payload"]) if row else None


def save_client(client: Client) -> Client:
    _bootstrap()
    now = utc_now()
    if client.created_at is None:
        client = client.model_copy(update={"created_at": now})
    client = client.model_copy(update={"updated_at": now})
    with _connect() as conn:
        _write_client(conn, client)
    return client


def create_client(client: Client) -> Client:
    client_id = client.id or _next_id("clients")
    return save_client(client.model_copy(update={"id": client_id}))


def delete_client(client_id: int) -> bool:
    return _delete("clients", client_id)


def list_leads() -> list[Lead]:
    _bootstrap()
    with _connect() as conn:
        rows = conn.execute("SELECT payload FROM leads ORDER BY priority_score DESC, created_at DESC").fetchall()
    return [Lead.model_validate_json(row["payload"]) for row in rows]


def get_lead(lead_id: int) -> Lead | None:
    _bootstrap()
    with _connect() as conn:
        row = conn.execute("SELECT payload FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return Lead.model_validate_json(row["payload"]) if row else None


def save_lead(lead: Lead) -> Lead:
    _bootstrap()
    now = utc_now()
    lead = lead.model_copy(update={"updated_at": now})
    with _connect() as conn:
        _write_lead(conn, lead)
    return lead


def create_lead(lead: Lead) -> Lead:
    lead_id = lead.id or _next_id("leads")
    return save_lead(lead.model_copy(update={"id": lead_id}))


def replace_leads(leads: list[Lead]) -> None:
    _bootstrap()
    with _connect() as conn:
        conn.execute("DELETE FROM leads")
    for lead in leads:
        save_lead(lead)


def delete_lead(lead_id: int) -> bool:
    return _delete("leads", lead_id)


def list_calendar_items() -> list[CalendarItem]:
    _bootstrap()
    with _connect() as conn:
        rows = conn.execute("SELECT payload FROM calendar_items ORDER BY date, start_time, lower(title)").fetchall()
    return [CalendarItem.model_validate_json(row["payload"]) for row in rows]


def save_calendar_item(item: CalendarItem) -> CalendarItem:
    _bootstrap()
    now = utc_now()
    if item.created_at is None:
        item = item.model_copy(update={"created_at": now})
    item = item.model_copy(update={"updated_at": now})
    with _connect() as conn:
        _write_calendar_item(conn, item)
    return item


def create_calendar_item(item: CalendarItem) -> CalendarItem:
    item_id = item.id or _next_id("calendar_items")
    return save_calendar_item(item.model_copy(update={"id": item_id}))


def list_tasks(status: str | None = None) -> list[Task]:
    _bootstrap()
    sql = "SELECT payload FROM tasks"
    params: tuple[Any, ...] = ()
    if status:
        sql += " WHERE status = ?"
        params = (status,)
    sql += " ORDER BY COALESCE(due_date, '9999-12-31'), lower(title)"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [Task.model_validate_json(row["payload"]) for row in rows]


def get_task(task_id: int) -> Task | None:
    _bootstrap()
    with _connect() as conn:
        row = conn.execute("SELECT payload FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return Task.model_validate_json(row["payload"]) if row else None


def save_task(task: Task) -> Task:
    _bootstrap()
    now = utc_now()
    if task.created_at is None:
        task = task.model_copy(update={"created_at": now})
    task = task.model_copy(update={"updated_at": now})
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO tasks (id, title, status, due_date, related_type, related_id, related_to, priority, sync_status, created_at, updated_at, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                status=excluded.status,
                due_date=excluded.due_date,
                related_type=excluded.related_type,
                related_id=excluded.related_id,
                related_to=excluded.related_to,
                priority=excluded.priority,
                sync_status=excluded.sync_status,
                updated_at=excluded.updated_at,
                payload=excluded.payload
            """,
            (
                task.id,
                task.title,
                task.status,
                task.due_date.isoformat() if task.due_date else "",
                task.related_type,
                task.related_id,
                task.related_to,
                task.priority.value if hasattr(task.priority, "value") else str(task.priority),
                task.sync_status,
                _iso(task.created_at),
                _iso(task.updated_at),
                _dump(task),
            ),
        )
    return task


def create_task(task: Task) -> Task:
    task_id = task.id or _next_id("tasks")
    return save_task(task.model_copy(update={"id": task_id}))


def delete_task(task_id: int) -> bool:
    return _delete("tasks", task_id)


def list_notes(related_type: str | None = None, related_id: int | None = None) -> list[Note]:
    _bootstrap()
    where = []
    params: list[Any] = []
    if related_type:
        where.append("related_type = ?")
        params.append(related_type)
    if related_id is not None:
        where.append("related_id = ?")
        params.append(related_id)
    sql = "SELECT * FROM notes"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_note_from_row(row) for row in rows]


def create_note(related_type: str, related_id: int, body: str) -> Note:
    _bootstrap()
    now = utc_now()
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO notes (related_type, related_id, body, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (related_type, related_id, body, _iso(now), _iso(now)),
        )
        note_id = int(cursor.lastrowid)
    return Note(id=note_id, related_type=related_type, related_id=related_id, body=body, created_at=now, updated_at=now)


def delete_note(note_id: int) -> bool:
    return _delete("notes", note_id)


def search(query: str) -> list[dict[str, Any]]:
    _bootstrap()
    needle = f"%{query.lower()}%"
    results: list[dict[str, Any]] = []
    with _connect() as conn:
        for row in conn.execute(
            "SELECT id, name, company, email, payload FROM clients WHERE lower(name || ' ' || company || ' ' || email || ' ' || owner || ' ' || payload) LIKE ? LIMIT 20",
            (needle,),
        ):
            results.append({"type": "client", "id": row["id"], "title": row["name"], "subtitle": row["company"], "detail": row["email"]})
        for row in conn.execute(
            "SELECT id, name, company, status, payload FROM leads WHERE lower(name || ' ' || company || ' ' || source || ' ' || status || ' ' || payload) LIKE ? LIMIT 20",
            (needle,),
        ):
            results.append({"type": "lead", "id": row["id"], "title": row["name"], "subtitle": row["company"], "detail": row["status"]})
        for row in conn.execute(
            "SELECT id, title, related_to, due_date FROM tasks WHERE lower(title || ' ' || related_to || ' ' || payload) LIKE ? LIMIT 20",
            (needle,),
        ):
            results.append({"type": "task", "id": row["id"], "title": row["title"], "subtitle": row["related_to"], "detail": row["due_date"]})
        for row in conn.execute(
            "SELECT id, title, related_to, date FROM calendar_items WHERE lower(title || ' ' || related_to || ' ' || payload) LIKE ? LIMIT 20",
            (needle,),
        ):
            results.append({"type": "calendar", "id": row["id"], "title": row["title"], "subtitle": row["related_to"], "detail": row["date"]})
        for row in conn.execute(
            "SELECT id, related_type, related_id, body FROM notes WHERE lower(body) LIKE ? LIMIT 20",
            (needle,),
        ):
            title = row["body"][:60]
            results.append({"type": "note", "id": row["id"], "title": title, "subtitle": f"{row['related_type']} #{row['related_id']}", "detail": row["body"]})
    return results


def dashboard_counts() -> dict[str, Any]:
    _bootstrap()
    today = date.today().isoformat()
    with _connect() as conn:
        open_leads = conn.execute("SELECT COUNT(*) FROM leads WHERE status NOT IN ('Confirmed', 'Rejected')").fetchone()[0]
        hot_leads = conn.execute("SELECT COUNT(*) FROM leads WHERE priority_score >= 80 AND status != 'Rejected'").fetchone()[0]
        overdue_tasks = conn.execute("SELECT COUNT(*) FROM tasks WHERE status != 'Done' AND due_date != '' AND due_date < ?", (today,)).fetchone()[0]
        upcoming_calendar = conn.execute("SELECT COUNT(*) FROM calendar_items WHERE date >= ?", (today,)).fetchone()[0]
    return {
        "open_leads": int(open_leads),
        "hot_leads": int(hot_leads),
        "overdue_tasks": int(overdue_tasks),
        "upcoming_calendar": int(upcoming_calendar),
    }


def get_setting(key: str) -> str:
    _bootstrap()
    with _connect() as conn:
        return _meta(conn, key)


def save_settings(values: dict[str, str]) -> None:
    _bootstrap()
    with _connect() as conn:
        for key, value in values.items():
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))


def reset_for_tests() -> None:
    path = db_path()
    if path.exists():
        path.unlink()


def _bootstrap() -> None:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        _create_schema(conn)
        _import_legacy(conn)
        _import_seed_data(conn)


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            company TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '',
            owner TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Active',
            source TEXT NOT NULL DEFAULT '',
            value INTEGER NOT NULL DEFAULT 0,
            last_contact TEXT NOT NULL DEFAULT '',
            next_action TEXT NOT NULL DEFAULT '',
            sync_status TEXT NOT NULL DEFAULT 'Local',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            company TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'New',
            source TEXT NOT NULL DEFAULT '',
            priority_score INTEGER NOT NULL DEFAULT 0,
            priority_label TEXT NOT NULL DEFAULT 'Low',
            estimated_value INTEGER NOT NULL DEFAULT 0,
            deadline TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            sync_status TEXT NOT NULL DEFAULT 'Local',
            payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS calendar_items (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            related_to TEXT NOT NULL DEFAULT '',
            related_client_id INTEGER,
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Open',
            due_date TEXT NOT NULL DEFAULT '',
            related_type TEXT NOT NULL DEFAULT '',
            related_id INTEGER,
            related_to TEXT NOT NULL DEFAULT '',
            priority TEXT NOT NULL DEFAULT 'Medium',
            sync_status TEXT NOT NULL DEFAULT 'Local',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            related_type TEXT NOT NULL,
            related_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )


def _import_legacy(conn: sqlite3.Connection) -> None:
    if _meta(conn, "legacy_imported"):
        return
    for client in load_model_list(LEGACY_CLIENTS_PATH, Client):
        _write_client(conn, client)
    for item in load_model_list(LEGACY_CALENDAR_PATH, CalendarItem):
        _write_calendar_item(conn, item)
    for lead in load_model_list(LEGACY_LEADS_PATH, Lead):
        _write_lead(conn, lead)
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('legacy_imported', '1')")


def _import_seed_data(conn: sqlite3.Connection) -> None:
    if not _truthy(os.getenv("CRM_INCLUDE_DEMO_DATA")):
        return
    if _meta(conn, "seed_imported"):
        return
    for client in CLIENTS:
        _write_client(conn, client)
    for item in CALENDAR:
        _write_calendar_item(conn, item)
    if _truthy(os.getenv("CRM_INCLUDE_DEMO_LEADS")):
        for lead in LEADS:
            _write_lead(conn, lead)
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('seed_imported', '1')")


def _write_client(conn: sqlite3.Connection, client: Client) -> None:
    payload = _dump(client)
    conn.execute(
        """
        INSERT INTO clients (id, name, company, email, owner, status, source, value, last_contact, next_action, sync_status, created_at, updated_at, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            company=excluded.company,
            email=excluded.email,
            owner=excluded.owner,
            status=excluded.status,
            source=excluded.source,
            value=excluded.value,
            last_contact=excluded.last_contact,
            next_action=excluded.next_action,
            sync_status=excluded.sync_status,
            updated_at=excluded.updated_at,
            payload=excluded.payload
        """,
        (
            client.id,
            client.name,
            client.company,
            str(client.email) if client.email else "",
            client.owner,
            client.status,
            client.source,
            client.value,
            client.last_contact.isoformat(),
            client.next_action,
            client.sync_status,
            _iso(client.created_at),
            _iso(client.updated_at),
            payload,
        ),
    )


def _write_lead(conn: sqlite3.Connection, lead: Lead) -> None:
    conn.execute(
        """
        INSERT INTO leads (id, name, company, status, source, priority_score, priority_label, estimated_value, deadline, created_at, updated_at, sync_status, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            company=excluded.company,
            status=excluded.status,
            source=excluded.source,
            priority_score=excluded.priority_score,
            priority_label=excluded.priority_label,
            estimated_value=excluded.estimated_value,
            deadline=excluded.deadline,
            updated_at=excluded.updated_at,
            sync_status=excluded.sync_status,
            payload=excluded.payload
        """,
        (
            lead.id,
            lead.name,
            lead.company,
            lead.status.value if hasattr(lead.status, "value") else str(lead.status),
            lead.source,
            lead.priority_score,
            lead.priority_label,
            lead.estimated_value,
            lead.deadline,
            lead.created_at.isoformat(),
            _iso(lead.updated_at),
            lead.sync_status,
            _dump(lead),
        ),
    )


def _write_calendar_item(conn: sqlite3.Connection, item: CalendarItem) -> None:
    conn.execute(
        """
        INSERT INTO calendar_items (id, title, date, start_time, end_time, related_to, related_client_id, created_at, updated_at, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title,
            date=excluded.date,
            start_time=excluded.start_time,
            end_time=excluded.end_time,
            related_to=excluded.related_to,
            related_client_id=excluded.related_client_id,
            updated_at=excluded.updated_at,
            payload=excluded.payload
        """,
        (
            item.id,
            item.title,
            item.date.isoformat(),
            item.start_time.isoformat(),
            item.end_time.isoformat(),
            item.related_to,
            item.related_client_id,
            _iso(item.created_at),
            _iso(item.updated_at),
            _dump(item),
        ),
    )


def _meta(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else ""


def _next_id(table: str) -> int:
    _bootstrap()
    with _connect() as conn:
        value = conn.execute(f"SELECT COALESCE(MAX(id), 0) + 1 FROM {table}").fetchone()[0]
    return int(value)


def _delete(table: str, item_id: int) -> bool:
    _bootstrap()
    with _connect() as conn:
        cursor = conn.execute(f"DELETE FROM {table} WHERE id = ?", (item_id,))
    return cursor.rowcount > 0


def _note_from_row(row: sqlite3.Row) -> Note:
    return Note(
        id=row["id"],
        related_type=row["related_type"],
        related_id=row["related_id"],
        body=row["body"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _dump(model: BaseModel) -> str:
    return model.model_dump_json()


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
