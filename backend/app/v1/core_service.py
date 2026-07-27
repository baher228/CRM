from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from app import platform_db
from app.v1 import models


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    def __init__(self, message: str, current_record: dict[str, Any] | None = None):
        super().__init__(message)
        self.current_record = current_record


class DomainError(Exception):
    pass


JSON_FIELDS = {
    "custom_json": "custom",
    "priority_reasons_json": "priority_reasons",
    "cpv_json": "cpv_codes",
    "recurrence_json": "recurrence",
    "config_json": "config",
    "registered_address_json": "registered_address",
    "options_json": "options",
    "value_json": "value",
    "recipe_json": "recipe",
    "result_json": "results",
    "report_json": "report",
}


def record(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for source, target in JSON_FIELDS.items():
        if source in result:
            raw = result.pop(source)
            try:
                result[target] = json.loads(raw or "null")
            except (TypeError, json.JSONDecodeError):
                result[target] = None
    for key in ("all_day", "required", "vat_registered", "tax_codes_approved", "is_primary"):
        if key in result:
            result[key] = bool(result[key])
    return result


def records(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [record(row) or {} for row in rows]


def _cursor(value: str | None) -> int:
    if not value:
        return 0
    try:
        return int(base64.urlsafe_b64decode(value.encode()).decode())
    except (ValueError, UnicodeError):
        raise DomainError("Invalid cursor") from None


def _next_cursor(items: list[dict[str, Any]], limit: int) -> str | None:
    if len(items) < limit or not items:
        return None
    return base64.urlsafe_b64encode(str(items[-1]["id"]).encode()).decode()


def _page(
    conn: sqlite3.Connection,
    table: str,
    *,
    where: list[str] | None = None,
    params: list[Any] | None = None,
    cursor: str | None = None,
    limit: int = 50,
    order: str = "id ASC",
    joins: str = "",
    select: str = "base.*",
) -> dict[str, Any]:
    conditions = list(where or [])
    values = list(params or [])
    cursor_id = _cursor(cursor)
    conditions.append("base.id > ?")
    values.append(cursor_id)
    sql = f"SELECT {select} FROM {table} base {joins}"
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += f" ORDER BY base.{order} LIMIT ?"
    values.append(limit)
    items = records(conn.execute(sql, values))
    return {"items": items, "next_cursor": _next_cursor(items, limit)}


def _require_parent(conn: sqlite3.Connection, table: str, item_id: int | None, label: str) -> None:
    if item_id is None:
        return
    if conn.execute(f"SELECT 1 FROM {table} WHERE id = ? AND archived_at IS NULL", (item_id,)).fetchone() is None:
        raise DomainError(f"{label} does not exist")


def _get(conn: sqlite3.Connection, table: str, item_id: int, *, include_archived: bool = False) -> dict[str, Any]:
    sql = f"SELECT * FROM {table} WHERE id = ?"
    if not include_archived and _has_column(conn, table, "archived_at"):
        sql += " AND archived_at IS NULL"
    item = record(conn.execute(sql, (item_id,)).fetchone())
    if item is None:
        raise NotFoundError(f"{table.rstrip('s').replace('_', ' ').title()} not found")
    return item


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row["name"] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _update_versioned(
    conn: sqlite3.Connection,
    table: str,
    item_id: int,
    version: int,
    values: dict[str, Any],
) -> dict[str, Any]:
    before = _get(conn, table, item_id, include_archived=True)
    values = {key: value for key, value in values.items() if value is not None}
    values["updated_at"] = platform_db.utc_now().isoformat()
    values["version"] = version + 1
    assignments = ", ".join(f"{key} = ?" for key in values)
    cursor = conn.execute(
        f"UPDATE {table} SET {assignments} WHERE id = ? AND version = ?",
        [*values.values(), item_id, version],
    )
    if cursor.rowcount == 0:
        exists = conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (item_id,)).fetchone()
        if not exists:
            raise NotFoundError(f"{table.rstrip('s').title()} not found")
        raise ConflictError(
            "This record changed elsewhere. Refresh and try again.",
            _get(conn, table, item_id, include_archived=True),
        )
    after = _get(conn, table, item_id, include_archived=True)
    platform_db.write_audit(conn, "update", table, item_id, before, after)
    return after


def _archive(
    conn: sqlite3.Connection,
    table: str,
    item_id: int,
    restore: bool = False,
    expected_version: int | None = None,
) -> dict[str, Any]:
    before = _get(conn, table, item_id, include_archived=True)
    if expected_version is not None and before.get("version") != expected_version:
        raise ConflictError("This record changed elsewhere. Refresh and try again.", before)
    archived_at = None if restore else platform_db.utc_now().isoformat()
    version_clause = " AND version = ?" if expected_version is not None else ""
    values: list[Any] = [archived_at, platform_db.utc_now().isoformat(), item_id]
    if expected_version is not None:
        values.append(expected_version)
    cursor = conn.execute(
        f"UPDATE {table} SET archived_at = ?, updated_at = ?, version = version + 1 WHERE id = ?{version_clause}",
        values,
    )
    if cursor.rowcount == 0:
        raise ConflictError(
            "This record changed elsewhere. Refresh and try again.",
            _get(conn, table, item_id, include_archived=True),
        )
    after = _get(conn, table, item_id, include_archived=True)
    platform_db.write_audit(conn, "restore" if restore else "archive", table, item_id, before, after)
    return after


def list_accounts(
    conn: sqlite3.Connection,
    *,
    query: str = "",
    status: str = "",
    health: str = "",
    archived: bool = False,
    include_archived: bool = False,
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    where = ["base.archived_at IS NOT NULL"] if archived else ([] if include_archived else ["base.archived_at IS NULL"])
    params: list[Any] = []
    if query.strip():
        where.append("(base.name LIKE ? OR base.domain LIKE ? OR base.billing_email LIKE ?)")
        needle = f"%{query.strip()}%"
        params.extend([needle, needle, needle])
    if status:
        where.append("base.status = ?")
        params.append(status)
    if health:
        where.append("base.health_status = ?")
        params.append(health)
    page = _page(conn, "accounts", where=where, params=params, cursor=cursor, limit=limit)
    for item in page["items"]:
        item["roles"] = [
            row["role"]
            for row in conn.execute("SELECT role FROM account_roles WHERE account_id = ? ORDER BY role", (item["id"],))
        ]
        item["contact_count"] = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE account_id = ? AND archived_at IS NULL", (item["id"],)
        ).fetchone()[0]
        item["open_pipeline_minor"] = conn.execute(
            """SELECT COALESCE(SUM(o.value_minor), 0) FROM opportunities o
               JOIN pipeline_stages s ON s.id = o.stage_id
               WHERE o.account_id = ? AND o.archived_at IS NULL AND s.kind = 'open'""",
            (item["id"],),
        ).fetchone()[0]
    return page


def create_account(conn: sqlite3.Connection, payload: models.AccountCreate) -> dict[str, Any]:
    now = platform_db.utc_now().isoformat()
    data = payload.model_dump(mode="json")
    roles = data.pop("roles")
    custom = data.pop("custom")
    billing_email = data.get("billing_email") or ""
    if data.get("domain"):
        duplicate = conn.execute(
            "SELECT id FROM accounts WHERE lower(domain) = lower(?) AND archived_at IS NULL", (data["domain"],)
        ).fetchone()
        if duplicate:
            raise ConflictError(f"An account with this domain already exists (#{duplicate['id']})")
    cursor = conn.execute(
        """INSERT INTO accounts
           (name, legal_name, domain, website, phone, billing_email, company_number,
            vat_number, source, payment_terms_days, status, health_status, health_score,
            renewal_date, notes, custom_json, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            data["name"], data["legal_name"], data["domain"], data["website"], data["phone"],
            billing_email, data["company_number"], data["vat_number"], data["source"],
            data["payment_terms_days"], data["status"], data["health_status"], data["health_score"],
            data.get("renewal_date"), data["notes"], json.dumps(custom), now, now,
        ),
    )
    item_id = int(cursor.lastrowid)
    for role in dict.fromkeys(roles):
        conn.execute("INSERT INTO account_roles (account_id, role) VALUES (?, ?)", (item_id, role))
    result = get_account(conn, item_id)
    platform_db.write_audit(conn, "create", "account", item_id, after=result)
    platform_db.index_record(conn, "account", item_id, result["name"], result["domain"], result["notes"])
    add_activity(conn, models.ActivityCreate(entity_type="account", entity_id=item_id, kind="system", subject="Account created"))
    return result


def get_account(conn: sqlite3.Connection, item_id: int, *, include_archived: bool = False) -> dict[str, Any]:
    item = _get(conn, "accounts", item_id, include_archived=include_archived)
    item["roles"] = [row["role"] for row in conn.execute("SELECT role FROM account_roles WHERE account_id = ?", (item_id,))]
    item["addresses"] = records(conn.execute("SELECT * FROM addresses WHERE account_id = ? ORDER BY id", (item_id,)))
    item["contacts"] = records(conn.execute("SELECT * FROM contacts WHERE account_id = ? AND archived_at IS NULL ORDER BY display_name", (item_id,)))
    item["opportunities"] = opportunity_rows(conn, "o.account_id = ? AND o.archived_at IS NULL", [item_id])
    item["activities"] = list_activity_items(conn, "account", item_id, 100)
    item["tasks"] = records(conn.execute("SELECT * FROM work_tasks WHERE entity_type = 'account' AND entity_id = ? AND archived_at IS NULL ORDER BY due_at", (item_id,)))
    item["calendar_events"] = records(conn.execute("SELECT * FROM calendar_events WHERE entity_type = 'account' AND entity_id = ? AND archived_at IS NULL ORDER BY starts_at", (item_id,)))
    if _table_exists(conn, "projects"):
        item["projects"] = records(conn.execute("SELECT * FROM projects WHERE account_id = ? AND archived_at IS NULL ORDER BY id DESC", (item_id,)))
    if _table_exists(conn, "invoices"):
        item["invoices"] = records(conn.execute("SELECT * FROM invoices WHERE account_id = ? ORDER BY id DESC", (item_id,)))
    return item


def update_account(conn: sqlite3.Connection, item_id: int, payload: models.AccountUpdate) -> dict[str, Any]:
    data = payload.model_dump(mode="json", exclude_unset=True)
    version = data.pop("version")
    roles = data.pop("roles", None)
    custom = data.pop("custom", None)
    if custom is not None:
        data["custom_json"] = json.dumps(custom)
    if "billing_email" in data:
        data["billing_email"] = data["billing_email"] or ""
    result = _update_versioned(conn, "accounts", item_id, version, data)
    if roles is not None:
        conn.execute("DELETE FROM account_roles WHERE account_id = ?", (item_id,))
        for role in dict.fromkeys(roles):
            conn.execute("INSERT INTO account_roles (account_id, role) VALUES (?, ?)", (item_id, role))
    platform_db.index_record(conn, "account", item_id, result["name"], result["domain"], result["notes"])
    return get_account(conn, item_id)


def merge_accounts(
    conn: sqlite3.Connection,
    source_id: int,
    target_id: int,
    source_version: int | None = None,
    target_version: int | None = None,
) -> dict[str, Any]:
    source = _get(conn, "accounts", source_id)
    target = _get(conn, "accounts", target_id)
    if source_version is not None and source.get("version") != source_version:
        raise ConflictError("The source account changed elsewhere. Refresh and try again.")
    if target_version is not None and target.get("version") != target_version:
        raise ConflictError("The target account changed elsewhere. Refresh and try again.")
    for table, column in (
        ("contacts", "account_id"), ("sales_leads", "account_id"), ("opportunities", "account_id"),
        ("tender_notices", "buyer_account_id"),
    ):
        conn.execute(f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (target_id, source_id))
    for optional, column in (("projects", "account_id"), ("invoices", "account_id"), ("contracts", "account_id")):
        if _table_exists(conn, optional):
            conn.execute(f"UPDATE {optional} SET {column} = ? WHERE {column} = ?", (target_id, source_id))
    conn.execute("UPDATE activities SET entity_id = ? WHERE entity_type = 'account' AND entity_id = ?", (target_id, source_id))
    conn.execute("INSERT OR IGNORE INTO account_roles (account_id, role) SELECT ?, role FROM account_roles WHERE account_id = ?", (target_id, source_id))
    conn.execute("UPDATE accounts SET archived_at = ?, updated_at = ?, version = version + 1 WHERE id = ?", (platform_db.utc_now().isoformat(), platform_db.utc_now().isoformat(), source_id))
    add_activity(conn, models.ActivityCreate(entity_type="account", entity_id=target_id, kind="system", subject=f"Merged account {source['name']}", body=f"Source account #{source_id}"))
    platform_db.write_audit(conn, "merge", "account", target_id, {"source": source, "target": target}, get_account(conn, target_id))
    return get_account(conn, target_id)


def list_contacts(
    conn: sqlite3.Connection,
    *,
    query: str = "",
    status: str = "",
    account_id: int | None = None,
    include_archived: bool = False,
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    where = [] if include_archived else ["base.archived_at IS NULL"]
    params: list[Any] = []
    if query.strip():
        needle = f"%{query.strip()}%"
        where.append("(base.display_name LIKE ? OR base.email LIKE ? OR base.job_title LIKE ?)")
        params.extend([needle, needle, needle])
    if status:
        where.append("base.status = ?")
        params.append(status)
    if account_id is not None:
        where.append("base.account_id = ?")
        params.append(account_id)
    page = _page(conn, "contacts", where=where, params=params, cursor=cursor, limit=limit)
    for item in page["items"]:
        account = conn.execute("SELECT name FROM accounts WHERE id = ?", (item["account_id"],)).fetchone() if item.get("account_id") else None
        item["account_name"] = account["name"] if account else ""
    return page


def create_contact(conn: sqlite3.Connection, payload: models.ContactCreate) -> dict[str, Any]:
    data = payload.model_dump(mode="json")
    _require_parent(conn, "accounts", data["account_id"], "Account")
    email = data["email"] or ""
    if email:
        duplicate = conn.execute("SELECT id FROM contacts WHERE lower(email) = lower(?) AND archived_at IS NULL", (email,)).fetchone()
        if duplicate:
            raise ConflictError(f"A contact with this email already exists (#{duplicate['id']})")
    now = platform_db.utc_now().isoformat()
    custom = data.pop("custom")
    cursor = conn.execute(
        """INSERT INTO contacts
           (account_id, first_name, last_name, display_name, job_title, email, phone, mobile,
            preferred_channel, source, lawful_basis, status, notes, custom_json, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (data["account_id"], data["first_name"], data["last_name"], data["display_name"], data["job_title"],
         email, data["phone"], data["mobile"], data["preferred_channel"], data["source"], data["lawful_basis"],
         data["status"], data["notes"], json.dumps(custom), now, now),
    )
    item_id = int(cursor.lastrowid)
    result = get_contact(conn, item_id)
    platform_db.write_audit(conn, "create", "contact", item_id, after=result)
    platform_db.index_record(conn, "contact", item_id, result["display_name"], result["email"], result["job_title"])
    add_activity(conn, models.ActivityCreate(entity_type="contact", entity_id=item_id, kind="system", subject="Contact created"))
    return result


def get_contact(conn: sqlite3.Connection, item_id: int, *, include_archived: bool = False) -> dict[str, Any]:
    item = _get(conn, "contacts", item_id, include_archived=include_archived)
    item["account"] = record(conn.execute("SELECT * FROM accounts WHERE id = ?", (item["account_id"],)).fetchone()) if item.get("account_id") else None
    item["opportunities"] = opportunity_rows(conn, "o.primary_contact_id = ? AND o.archived_at IS NULL", [item_id])
    item["activities"] = list_activity_items(conn, "contact", item_id, 100)
    item["tasks"] = records(conn.execute("SELECT * FROM work_tasks WHERE entity_type = 'contact' AND entity_id = ? AND archived_at IS NULL ORDER BY due_at", (item_id,)))
    item["calendar_events"] = records(conn.execute("SELECT * FROM calendar_events WHERE entity_type = 'contact' AND entity_id = ? AND archived_at IS NULL ORDER BY starts_at", (item_id,)))
    return item


def update_contact(conn: sqlite3.Connection, item_id: int, payload: models.ContactUpdate) -> dict[str, Any]:
    data = payload.model_dump(mode="json", exclude_unset=True)
    version = data.pop("version")
    custom = data.pop("custom", None)
    if custom is not None:
        data["custom_json"] = json.dumps(custom)
    if "email" in data:
        data["email"] = data["email"] or ""
    if "email_opt_out" in data:
        opted_out = data.pop("email_opt_out")
        data["email_opt_out_at"] = platform_db.utc_now().isoformat() if opted_out else None
    if "account_id" in data:
        _require_parent(conn, "accounts", data["account_id"], "Account")
    result = _update_versioned(conn, "contacts", item_id, version, data)
    platform_db.index_record(conn, "contact", item_id, result["display_name"], result["email"], result["job_title"])
    return get_contact(conn, item_id)


def merge_contacts(
    conn: sqlite3.Connection,
    source_id: int,
    target_id: int,
    source_version: int | None = None,
    target_version: int | None = None,
) -> dict[str, Any]:
    source = _get(conn, "contacts", source_id)
    target = _get(conn, "contacts", target_id)
    if source_version is not None and source.get("version") != source_version:
        raise ConflictError("The source contact changed elsewhere. Refresh and try again.")
    if target_version is not None and target.get("version") != target_version:
        raise ConflictError("The target contact changed elsewhere. Refresh and try again.")
    for table, column in (("sales_leads", "contact_id"), ("opportunities", "primary_contact_id"), ("tender_notices", "buyer_contact_id")):
        conn.execute(f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (target_id, source_id))
    conn.execute("UPDATE activities SET entity_id = ? WHERE entity_type = 'contact' AND entity_id = ?", (target_id, source_id))
    conn.execute("UPDATE contacts SET archived_at = ?, updated_at = ?, version = version + 1 WHERE id = ?", (platform_db.utc_now().isoformat(), platform_db.utc_now().isoformat(), source_id))
    add_activity(conn, models.ActivityCreate(entity_type="contact", entity_id=target_id, kind="system", subject=f"Merged contact {source['display_name']}", body=f"Source contact #{source_id}"))
    return get_contact(conn, target_id)


def list_leads(conn: sqlite3.Connection, *, query: str = "", status: str = "", cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
    where = ["base.archived_at IS NULL"]
    params: list[Any] = []
    if query:
        needle = f"%{query.strip()}%"
        where.append("(base.title LIKE ? OR base.company LIKE ? OR base.email LIKE ?)")
        params.extend([needle, needle, needle])
    if status:
        where.append("base.status = ?")
        params.append(status)
    return _page(conn, "sales_leads", where=where, params=params, cursor=cursor, limit=limit)


def create_lead(conn: sqlite3.Connection, payload: models.LeadCreate) -> dict[str, Any]:
    data = payload.model_dump(mode="json")
    _require_parent(conn, "accounts", data["account_id"], "Account")
    _require_parent(conn, "contacts", data["contact_id"], "Contact")
    now = platform_db.utc_now().isoformat()
    cursor = conn.execute(
        """INSERT INTO sales_leads
           (account_id, contact_id, title, company, email, phone, source, status, score,
            estimated_value_minor, next_action, notes, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (data["account_id"], data["contact_id"], data["title"], data["company"], data["email"] or "", data["phone"],
         data["source"], data["status"], data["score"], data["estimated_value_minor"], data["next_action"], data["notes"], now, now),
    )
    item_id = int(cursor.lastrowid)
    result = _get(conn, "sales_leads", item_id)
    platform_db.index_record(conn, "lead", item_id, result["title"], result["company"], result["notes"])
    platform_db.write_audit(conn, "create", "lead", item_id, after=result)
    add_activity(conn, models.ActivityCreate(entity_type="lead", entity_id=item_id, kind="system", subject="Lead created"))
    platform_db.enqueue_automation_event(conn, "lead.created", {**result, "type": "lead"})
    return result


def update_lead(conn: sqlite3.Connection, item_id: int, payload: models.LeadUpdate) -> dict[str, Any]:
    data = payload.model_dump(mode="json", exclude_unset=True)
    version = data.pop("version")
    if "email" in data:
        data["email"] = data["email"] or ""
    result = _update_versioned(conn, "sales_leads", item_id, version, data)
    platform_db.index_record(conn, "lead", item_id, result["title"], result["company"], result["notes"])
    return result


def qualify_lead(conn: sqlite3.Connection, item_id: int, payload: models.QualificationRequest) -> dict[str, Any]:
    lead = _get(conn, "sales_leads", item_id)
    if lead.get("converted_opportunity_id"):
        return get_opportunity(conn, int(lead["converted_opportunity_id"]))
    account_id, contact_id = _resolve_qualification(
        conn,
        payload,
        fallback_account=lead.get("company") or lead["title"],
        fallback_contact=lead["title"],
        fallback_email=lead.get("email") or "",
    )
    opportunity = create_opportunity(
        conn,
        models.OpportunityCreate(
            account_id=account_id,
            primary_contact_id=contact_id,
            stage_id=payload.stage_id,
            type="New business",
            title=payload.opportunity_title.strip() or lead["title"],
            value_minor=payload.value_minor or lead["estimated_value_minor"],
            expected_close_date=payload.expected_close_date,
            source=lead["source"],
            next_action=payload.next_action.strip() or lead["next_action"],
            notes=lead["notes"],
        ),
    )
    conn.execute(
        "UPDATE sales_leads SET status = 'Qualified', converted_opportunity_id = ?, updated_at = ?, version = version + 1 WHERE id = ?",
        (opportunity["id"], platform_db.utc_now().isoformat(), item_id),
    )
    add_activity(conn, models.ActivityCreate(entity_type="lead", entity_id=item_id, kind="stage_change", subject="Lead qualified", body=f"Created deal #{opportunity['id']}"))
    qualified = _get(conn, "sales_leads", item_id)
    platform_db.enqueue_automation_event(
        conn,
        "lead.qualified",
        {**qualified, "type": "lead", "opportunity_id": opportunity["id"]},
    )
    return opportunity


def list_pipeline_stages(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return records(conn.execute("SELECT * FROM pipeline_stages WHERE archived_at IS NULL ORDER BY position"))


def opportunity_rows(conn: sqlite3.Connection, condition: str = "o.archived_at IS NULL", params: list[Any] | None = None) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""SELECT o.*, s.name AS stage_name, s.kind AS stage_kind, s.color AS stage_color,
                    a.name AS account_name, c.display_name AS contact_name
             FROM opportunities o
             JOIN pipeline_stages s ON s.id = o.stage_id
             JOIN accounts a ON a.id = o.account_id
             LEFT JOIN contacts c ON c.id = o.primary_contact_id
             WHERE {condition} ORDER BY o.id DESC""",
        params or [],
    )
    return records(rows)


def list_opportunities(conn: sqlite3.Connection, *, query: str = "", stage_id: int | None = None, status: str = "", cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
    conditions = ["o.archived_at IS NULL"]
    params: list[Any] = []
    if query:
        needle = f"%{query.strip()}%"
        conditions.append("(o.title LIKE ? OR a.name LIKE ? OR c.display_name LIKE ?)")
        params.extend([needle, needle, needle])
    if stage_id is not None:
        conditions.append("o.stage_id = ?")
        params.append(stage_id)
    if status:
        conditions.append("o.status = ?")
        params.append(status)
    cursor_id = _cursor(cursor)
    conditions.append("o.id > ?")
    params.append(cursor_id)
    params.append(limit)
    items = records(conn.execute(
        f"""SELECT o.*, s.name AS stage_name, s.kind AS stage_kind, s.color AS stage_color,
                    a.name AS account_name, c.display_name AS contact_name
             FROM opportunities o JOIN pipeline_stages s ON s.id = o.stage_id
             JOIN accounts a ON a.id = o.account_id
             LEFT JOIN contacts c ON c.id = o.primary_contact_id
             WHERE {' AND '.join(conditions)} ORDER BY o.id ASC LIMIT ?""",
        params,
    ))
    return {"items": items, "next_cursor": _next_cursor(items, limit)}


def create_opportunity(conn: sqlite3.Connection, payload: models.OpportunityCreate) -> dict[str, Any]:
    data = payload.model_dump(mode="json")
    _require_parent(conn, "accounts", data["account_id"], "Account")
    _require_parent(conn, "contacts", data["primary_contact_id"], "Contact")
    _require_parent(conn, "tender_notices", data["tender_id"], "Tender")
    stage = _stage(conn, data["stage_id"])
    probability = data["probability_bps"] if data["probability_bps"] is not None else stage["probability_bps"]
    now = platform_db.utc_now().isoformat()
    cursor = conn.execute(
        """INSERT INTO opportunities
           (account_id, primary_contact_id, tender_id, stage_id, type, title, status, value_minor,
            probability_bps, expected_close_date, source, next_action, notes, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (data["account_id"], data["primary_contact_id"], data["tender_id"], stage["id"], data["type"], data["title"],
         "Won" if stage["kind"] == "won" else "Lost" if stage["kind"] == "lost" else "Open", data["value_minor"],
         probability, data["expected_close_date"], data["source"], data["next_action"], data["notes"], now, now),
    )
    item_id = int(cursor.lastrowid)
    if data["tender_id"]:
        conn.execute("UPDATE tender_notices SET linked_opportunity_id = ?, triage_status = 'Qualified', updated_at = ?, version = version + 1 WHERE id = ?", (item_id, now, data["tender_id"]))
    result = get_opportunity(conn, item_id)
    platform_db.index_record(conn, "opportunity", item_id, result["title"], result["account_name"], result["notes"])
    platform_db.write_audit(conn, "create", "opportunity", item_id, after=result)
    add_activity(conn, models.ActivityCreate(entity_type="opportunity", entity_id=item_id, kind="system", subject="Deal created"))
    return result


def get_opportunity(conn: sqlite3.Connection, item_id: int) -> dict[str, Any]:
    rows = opportunity_rows(conn, "o.id = ?", [item_id])
    if not rows:
        raise NotFoundError("Opportunity not found")
    item = rows[0]
    item["activities"] = list_activity_items(conn, "opportunity", item_id, 100)
    item["tasks"] = records(conn.execute("SELECT * FROM work_tasks WHERE entity_type = 'opportunity' AND entity_id = ? AND archived_at IS NULL ORDER BY due_at", (item_id,)))
    item["calendar_events"] = records(conn.execute("SELECT * FROM calendar_events WHERE entity_type = 'opportunity' AND entity_id = ? AND archived_at IS NULL ORDER BY starts_at", (item_id,)))
    item["tender"] = record(conn.execute("SELECT * FROM tender_notices WHERE id = ?", (item["tender_id"],)).fetchone()) if item.get("tender_id") else None
    if _table_exists(conn, "proposals"):
        item["proposals"] = records(conn.execute("SELECT * FROM proposals WHERE opportunity_id = ? ORDER BY id DESC", (item_id,)))
    if _table_exists(conn, "contracts"):
        item["contracts"] = records(conn.execute("SELECT * FROM contracts WHERE opportunity_id = ? ORDER BY id DESC", (item_id,)))
    return item


def update_opportunity(conn: sqlite3.Connection, item_id: int, payload: models.OpportunityUpdate) -> dict[str, Any]:
    data = payload.model_dump(mode="json", exclude_unset=True)
    version = data.pop("version")
    if "account_id" in data:
        _require_parent(conn, "accounts", data["account_id"], "Account")
    if "primary_contact_id" in data:
        _require_parent(conn, "contacts", data["primary_contact_id"], "Contact")
    result = _update_versioned(conn, "opportunities", item_id, version, data)
    joined = get_opportunity(conn, item_id)
    platform_db.index_record(conn, "opportunity", item_id, joined["title"], joined["account_name"], joined["notes"])
    return joined


def transition_opportunity(conn: sqlite3.Connection, item_id: int, payload: models.TransitionRequest) -> dict[str, Any]:
    conn.execute("BEGIN IMMEDIATE")
    before = get_opportunity(conn, item_id)
    stage = _stage(conn, payload.stage_id)
    if stage["kind"] == "lost" and not payload.loss_reason.strip():
        raise DomainError("A loss reason is required")
    now = platform_db.utc_now().isoformat()
    values = {
        "stage_id": stage["id"],
        "status": "Won" if stage["kind"] == "won" else "Lost" if stage["kind"] == "lost" else "Open",
        "probability_bps": payload.probability_bps if payload.probability_bps is not None else stage["probability_bps"],
        "loss_reason": payload.loss_reason.strip() if stage["kind"] == "lost" else "",
        "won_at": now if stage["kind"] == "won" else None,
        "lost_at": now if stage["kind"] == "lost" else None,
    }
    _update_versioned(conn, "opportunities", item_id, payload.version, values)
    if stage["kind"] == "won":
        conn.execute("INSERT OR IGNORE INTO account_roles (account_id, role) VALUES (?, 'client')", (before["account_id"],))
        conn.execute("UPDATE accounts SET status = 'Client', updated_at = ?, version = version + 1 WHERE id = ?", (now, before["account_id"]))
        _ensure_won_project(conn, before, now)
    add_activity(conn, models.ActivityCreate(entity_type="opportunity", entity_id=item_id, kind="stage_change", subject=f"Moved to {stage['name']}", body=payload.loss_reason.strip()))
    result = get_opportunity(conn, item_id)
    platform_db.enqueue_automation_event(
        conn,
        "deal.stage_changed",
        {
            **result,
            "type": "opportunity",
            "previous_stage_id": before["stage_id"],
            "previous_stage_name": before["stage_name"],
        },
    )
    return result


def _ensure_won_project(conn: sqlite3.Connection, opportunity: dict[str, Any], now: str) -> None:
    if not _table_exists(conn, "projects") or not _table_exists(conn, "lifecycle_links"):
        return
    link = conn.execute(
        "SELECT target_id FROM lifecycle_links WHERE kind = 'won_project' AND source_key = ?",
        (str(opportunity["id"]),),
    ).fetchone()
    if link:
        return
    project = conn.execute(
        "SELECT id FROM projects WHERE opportunity_id = ? ORDER BY id LIMIT 1",
        (opportunity["id"],),
    ).fetchone()
    if project is None:
        contract = conn.execute(
            "SELECT * FROM contracts WHERE opportunity_id = ? AND archived_at IS NULL "
            "ORDER BY CASE status WHEN 'Active' THEN 0 WHEN 'Signed' THEN 1 ELSE 2 END, id DESC LIMIT 1",
            (opportunity["id"],),
        ).fetchone() if _table_exists(conn, "contracts") else None
        cursor = conn.execute(
            "INSERT INTO projects(account_id, opportunity_id, contract_id, name, status, billing_type, "
            "budget_pence, currency, starts_on, due_on, notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'Planned', 'fixed', ?, ?, ?, ?, '', ?, ?)",
            (
                opportunity["account_id"],
                opportunity["id"],
                contract["id"] if contract else None,
                contract["title"] if contract else opportunity["title"],
                contract["value_pence"] if contract else opportunity["value_minor"],
                contract["currency"] if contract else "GBP",
                contract["starts_on"] if contract else None,
                contract["ends_on"] if contract else None,
                now,
                now,
            ),
        )
        project = {"id": cursor.lastrowid}
    conn.execute(
        "INSERT OR IGNORE INTO lifecycle_links(kind, source_key, target_type, target_id, created_at) "
        "VALUES ('won_project', ?, 'project', ?, ?)",
        (str(opportunity["id"]), project["id"], now),
    )


def _stage(conn: sqlite3.Connection, stage_id: int | None) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM pipeline_stages WHERE id = ? AND archived_at IS NULL" if stage_id else
        "SELECT * FROM pipeline_stages WHERE kind = 'open' AND archived_at IS NULL ORDER BY position LIMIT 1",
        (stage_id,) if stage_id else (),
    ).fetchone()
    if row is None:
        raise DomainError("Pipeline stage does not exist")
    return dict(row)


def list_tenders(conn: sqlite3.Connection, *, query: str = "", status: str = "", cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
    where = ["base.archived_at IS NULL"]
    params: list[Any] = []
    if query:
        needle = f"%{query.strip()}%"
        where.append("(base.title LIKE ? OR base.buyer_name LIKE ? OR base.portal_name LIKE ?)")
        params.extend([needle, needle, needle])
    if status:
        where.append("base.triage_status = ?")
        params.append(status)
    page = _page(conn, "tender_notices", where=where, params=params, cursor=cursor, limit=limit)
    for item in page["items"]:
        item["sources"] = records(conn.execute("SELECT * FROM tender_sources WHERE tender_id = ? ORDER BY is_primary DESC, id", (item["id"],)))
    return page


def create_tender(conn: sqlite3.Connection, payload: models.TenderCreate) -> dict[str, Any]:
    data = payload.model_dump(mode="json")
    dedupe = data["dedupe_key"].strip() or _tender_key(data["contract_url"], data["buyer_name"], data["title"])
    existing = conn.execute("SELECT id FROM tender_notices WHERE dedupe_key = ?", (dedupe,)).fetchone()
    if existing:
        item_id = int(existing["id"])
        conn.execute(
            "UPDATE tender_notices SET seen_count = seen_count + 1, last_seen_at = ?, updated_at = ?, version = version + 1 WHERE id = ?",
            (platform_db.utc_now().isoformat(), platform_db.utc_now().isoformat(), item_id),
        )
        for source in data["source_urls"]:
            conn.execute("INSERT OR IGNORE INTO tender_sources (tender_id, url, source_kind, is_primary, first_seen_at, last_seen_at) VALUES (?, ?, 'notice', 0, ?, ?)", (item_id, source, platform_db.utc_now().isoformat(), platform_db.utc_now().isoformat()))
        return get_tender(conn, item_id)
    now = platform_db.utc_now().isoformat()
    cursor = conn.execute(
        """INSERT INTO tender_notices
           (title, buyer_name, portal_name, notice_reference, contract_url, contract_value_text,
            estimated_value_minor, deadline, procurement_stage, contract_status, availability_status,
            availability_reason, niche, region, location, confidence_score, priority_score,
            priority_reasons_json, outreach_angle, dedupe_key, first_seen_at, last_seen_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (data["title"], data["buyer_name"], data["portal_name"], data["notice_reference"], data["contract_url"],
         data["contract_value_text"], data["estimated_value_minor"], data["deadline"], data["procurement_stage"],
         data["contract_status"], data["availability_status"], data["availability_reason"], data["niche"], data["region"],
         data["location"], data["confidence_score"], data["priority_score"], json.dumps(data["priority_reasons"]),
         data["outreach_angle"], dedupe, now, now, now, now),
    )
    item_id = int(cursor.lastrowid)
    all_sources = [data["contract_url"], *data["source_urls"]]
    for source in dict.fromkeys(value for value in all_sources if value):
        conn.execute("INSERT OR IGNORE INTO tender_sources (tender_id, url, source_kind, is_primary, first_seen_at, last_seen_at) VALUES (?, ?, 'notice', ?, ?, ?)", (item_id, source, int(source == data["contract_url"]), now, now))
    platform_db.index_record(conn, "tender", item_id, data["title"], data["buyer_name"], data["outreach_angle"])
    platform_db.write_audit(conn, "create", "tender", item_id, after=get_tender(conn, item_id))
    return get_tender(conn, item_id)


def get_tender(conn: sqlite3.Connection, item_id: int) -> dict[str, Any]:
    item = _get(conn, "tender_notices", item_id)
    item["sources"] = records(conn.execute("SELECT * FROM tender_sources WHERE tender_id = ? ORDER BY is_primary DESC, id", (item_id,)))
    item["activities"] = list_activity_items(conn, "tender", item_id, 100)
    item["opportunity"] = get_opportunity(conn, item["linked_opportunity_id"]) if item.get("linked_opportunity_id") else None
    return item


def decide_tender(conn: sqlite3.Connection, item_id: int, status: str, payload: models.TenderDecision) -> dict[str, Any]:
    if status == "Rejected" and not payload.reason.strip():
        raise DomainError("A rejection reason is required")
    if status == "Snoozed" and payload.snoozed_until is None:
        raise DomainError("A return date is required")
    values = {
        "triage_status": status,
        "rejection_reason": payload.reason.strip() if status == "Rejected" else "",
        "snoozed_until": payload.snoozed_until.isoformat() if payload.snoozed_until else None,
    }
    _update_versioned(conn, "tender_notices", item_id, payload.version, values)
    add_activity(conn, models.ActivityCreate(entity_type="tender", entity_id=item_id, kind="stage_change", subject=f"Tender {status.lower()}", body=payload.reason.strip()))
    return get_tender(conn, item_id)


def qualify_tender(conn: sqlite3.Connection, item_id: int, payload: models.QualificationRequest) -> dict[str, Any]:
    tender = _get(conn, "tender_notices", item_id)
    if tender.get("linked_opportunity_id"):
        return get_opportunity(conn, tender["linked_opportunity_id"])
    account_id, contact_id = _resolve_qualification(conn, payload, tender["buyer_name"] or tender["title"], tender["buyer_name"], "")
    opportunity = create_opportunity(conn, models.OpportunityCreate(
        account_id=account_id, primary_contact_id=contact_id, tender_id=item_id, stage_id=payload.stage_id,
        type="Tender", title=payload.opportunity_title.strip() or tender["title"],
        value_minor=payload.value_minor or tender["estimated_value_minor"], expected_close_date=payload.expected_close_date,
        source=tender["portal_name"] or "Tender Radar", next_action=payload.next_action,
        notes=tender["outreach_angle"],
    ))
    add_activity(conn, models.ActivityCreate(entity_type="tender", entity_id=item_id, kind="stage_change", subject="Tender qualified", body=f"Created deal #{opportunity['id']}"))
    qualified = _get(conn, "tender_notices", item_id)
    platform_db.enqueue_automation_event(
        conn,
        "tender.qualified",
        {**qualified, "type": "tender", "opportunity_id": opportunity["id"]},
    )
    return opportunity


def _resolve_qualification(conn: sqlite3.Connection, payload: models.QualificationRequest, fallback_account: str, fallback_contact: str, fallback_email: str) -> tuple[int, int | None]:
    if payload.account_id:
        _require_parent(conn, "accounts", payload.account_id, "Account")
        account_id = payload.account_id
    else:
        account = create_account(conn, models.AccountCreate(name=payload.account_name.strip() or fallback_account, source="Qualification"))
        account_id = account["id"]
    contact_id = payload.contact_id
    if contact_id:
        _require_parent(conn, "contacts", contact_id, "Contact")
    elif payload.contact_name.strip() or payload.contact_email or fallback_email:
        email = str(payload.contact_email or fallback_email or "") or None
        existing = conn.execute("SELECT id FROM contacts WHERE lower(email) = lower(?) AND archived_at IS NULL", (email,)).fetchone() if email else None
        if existing:
            contact_id = int(existing["id"])
        else:
            contact = create_contact(conn, models.ContactCreate(account_id=account_id, display_name=payload.contact_name.strip() or fallback_contact or email or "Decision maker", email=email, source="Qualification"))
            contact_id = contact["id"]
    return account_id, contact_id


def _tender_key(url: str, buyer: str, title: str) -> str:
    value = "|".join((url.strip().lower(), buyer.strip().lower(), title.strip().lower()))
    return hashlib.sha256(value.encode()).hexdigest()


def add_activity(conn: sqlite3.Connection, payload: models.ActivityCreate) -> dict[str, Any]:
    now = platform_db.utc_now().isoformat()
    occurred = payload.occurred_at.isoformat() if payload.occurred_at else now
    cursor = conn.execute(
        "INSERT INTO activities (entity_type, entity_id, kind, subject, body, occurred_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (payload.entity_type, payload.entity_id, payload.kind, payload.subject, payload.body.strip(), occurred, now),
    )
    return record(conn.execute("SELECT * FROM activities WHERE id = ?", (cursor.lastrowid,)).fetchone()) or {}


def list_activity_items(conn: sqlite3.Connection, entity_type: str, entity_id: int, limit: int = 100) -> list[dict[str, Any]]:
    return records(conn.execute("SELECT * FROM activities WHERE entity_type = ? AND entity_id = ? ORDER BY occurred_at DESC LIMIT ?", (entity_type, entity_id, limit)))


def list_activities(conn: sqlite3.Connection, *, entity_type: str = "", entity_id: int | None = None, cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
    where: list[str] = []
    params: list[Any] = []
    if entity_type:
        where.append("base.entity_type = ?")
        params.append(entity_type)
    if entity_id is not None:
        where.append("base.entity_id = ?")
        params.append(entity_id)
    return _page(conn, "activities", where=where, params=params, cursor=cursor, limit=limit)


def list_tasks(conn: sqlite3.Connection, *, query: str = "", status: str = "", entity_type: str = "", entity_id: int | None = None, cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
    where = ["base.archived_at IS NULL"]
    params: list[Any] = []
    if query.strip():
        needle = f"%{query.strip()}%"
        where.append("(base.title LIKE ? OR base.description LIKE ?)")
        params.extend([needle, needle])
    if status:
        where.append("base.status = ?")
        params.append(status)
    if entity_type:
        where.append("base.entity_type = ?")
        params.append(entity_type)
    if entity_id is not None:
        where.append("base.entity_id = ?")
        params.append(entity_id)
    return _page(conn, "work_tasks", where=where, params=params, cursor=cursor, limit=limit)


def create_task(conn: sqlite3.Connection, payload: models.TaskCreate) -> dict[str, Any]:
    data = payload.model_dump(mode="json")
    now = platform_db.utc_now().isoformat()
    completed = now if data["status"] == "Done" else None
    cursor = conn.execute(
        """INSERT INTO work_tasks
           (entity_type, entity_id, title, description, status, priority, due_at, completed_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (data["entity_type"], data["entity_id"], data["title"], data["description"], data["status"], data["priority"], data["due_at"], completed, now, now),
    )
    item = _get(conn, "work_tasks", int(cursor.lastrowid))
    if data["entity_type"] and data["entity_id"]:
        add_activity(conn, models.ActivityCreate(entity_type=data["entity_type"], entity_id=data["entity_id"], kind="task", subject=data["title"], body=data["description"]))
    return item


def update_task(conn: sqlite3.Connection, item_id: int, payload: models.TaskUpdate) -> dict[str, Any]:
    data = payload.model_dump(mode="json", exclude_unset=True)
    version = data.pop("version")
    if data.get("status") == "Done":
        data["completed_at"] = platform_db.utc_now().isoformat()
    elif "status" in data:
        data["completed_at"] = None
    return _update_versioned(conn, "work_tasks", item_id, version, data)


def list_events(conn: sqlite3.Connection, *, start: date | None = None, end: date | None = None, cursor: str | None = None, limit: int = 100) -> dict[str, Any]:
    where = ["base.archived_at IS NULL"]
    params: list[Any] = []
    if start:
        where.append("base.starts_at >= ?")
        params.append(start.isoformat())
    if end:
        where.append("base.starts_at < ?")
        params.append((end + timedelta(days=1)).isoformat())
    return _page(conn, "calendar_events", where=where, params=params, cursor=cursor, limit=limit)


def create_event(conn: sqlite3.Connection, payload: models.CalendarEventCreate) -> dict[str, Any]:
    data = payload.model_dump(mode="json")
    now = platform_db.utc_now().isoformat()
    cursor = conn.execute(
        """INSERT INTO calendar_events
           (entity_type, entity_id, title, body, location, starts_at, ends_at, timezone, all_day,
            recurrence_json, sync_state, local_updated_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?, ?, ?)""",
        (data["entity_type"], data["entity_id"], data["title"], data["body"], data["location"], data["starts_at"],
         data["ends_at"], data["timezone"], int(data["all_day"]), json.dumps(data["recurrence"]), now, now, now),
    )
    item = _get(conn, "calendar_events", int(cursor.lastrowid))
    if data["entity_type"] and data["entity_id"]:
        add_activity(conn, models.ActivityCreate(entity_type=data["entity_type"], entity_id=data["entity_id"], kind="meeting", subject=data["title"], body=data["body"], occurred_at=payload.starts_at))
    _enqueue_optional(conn, "google.calendar.push", {"event_id": item["id"], "version": item["version"]}, f"calendar:{item['id']}:v{item['version']}")
    return item


def update_event(conn: sqlite3.Connection, item_id: int, payload: models.CalendarEventUpdate) -> dict[str, Any]:
    data = payload.model_dump(mode="json", exclude_unset=True)
    version = data.pop("version")
    if "all_day" in data:
        data["all_day"] = int(data["all_day"])
    if "recurrence" in data:
        data["recurrence_json"] = json.dumps(data.pop("recurrence"))
    data["sync_state"] = "Pending"
    data["local_updated_at"] = platform_db.utc_now().isoformat()
    result = _update_versioned(conn, "calendar_events", item_id, version, data)
    _enqueue_optional(conn, "google.calendar.push", {"event_id": item_id, "version": result["version"]}, f"calendar:{item_id}:v{result['version']}")
    return result


def _today_signal(
    signal_type: str,
    item_id: int | str,
    title: str,
    reason: str,
    route: str,
    **details: Any,
) -> dict[str, Any]:
    return {
        "type": signal_type,
        "id": item_id,
        "title": title.strip() or "Untitled record",
        "reason": reason.strip() or "Review this record",
        "route": route,
        **details,
    }


def dashboard(conn: sqlite3.Connection) -> dict[str, Any]:
    now = platform_db.utc_now()
    local_today = now.astimezone(ZoneInfo("Europe/London")).date()
    today = local_today.isoformat()
    tomorrow = (local_today + timedelta(days=1)).isoformat()
    tender_cutoff = (local_today + timedelta(days=7)).isoformat()
    renewal_cutoff = (local_today + timedelta(days=90)).isoformat()
    meeting_cutoff = (now + timedelta(days=7)).isoformat()
    stale_cutoff = (now - timedelta(days=14)).isoformat()

    open_tasks = conn.execute(
        "SELECT COUNT(*) FROM work_tasks WHERE status NOT IN ('Done','Cancelled') AND archived_at IS NULL"
    ).fetchone()[0]
    overdue_tasks = conn.execute(
        """SELECT COUNT(*) FROM work_tasks
           WHERE status NOT IN ('Done','Cancelled') AND due_at IS NOT NULL
             AND due_at < ? AND archived_at IS NULL""",
        (today,),
    ).fetchone()[0]
    due_work_count = conn.execute(
        """SELECT COUNT(*) FROM work_tasks
           WHERE status NOT IN ('Done','Cancelled') AND due_at IS NOT NULL
             AND due_at < ? AND archived_at IS NULL""",
        (tomorrow,),
    ).fetchone()[0]
    overdue_work = []
    for row in conn.execute(
        """SELECT id,title,description,priority,due_at FROM work_tasks
           WHERE status NOT IN ('Done','Cancelled') AND due_at IS NOT NULL
             AND due_at < ? AND archived_at IS NULL
           ORDER BY due_at,id LIMIT 12""",
        (tomorrow,),
    ):
        overdue = row["due_at"][:10] < today
        timing = f"Overdue since {row['due_at'][:10]}" if overdue else "Due today"
        reason = f"{timing} - {row['description']}" if row["description"] else f"{timing} - {row['priority']} priority"
        overdue_work.append(_today_signal(
            "task", row["id"], row["title"], reason, f"/tasks/{row['id']}",
            due_at=row["due_at"], priority="high" if overdue or row["priority"] in {"High", "Urgent"} else "normal",
        ))

    tender_filter = """deadline BETWEEN ? AND ? AND triage_status NOT IN ('Rejected','Expired')
                       AND (triage_status != 'Snoozed' OR snoozed_until IS NULL OR snoozed_until <= ?)
                       AND archived_at IS NULL"""
    tender_params = (today, tender_cutoff, today)
    open_tenders = conn.execute(
        "SELECT COUNT(*) FROM tender_notices WHERE triage_status IN ('New','Reviewing','Snoozed') AND archived_at IS NULL"
    ).fetchone()[0]
    tender_count = conn.execute(f"SELECT COUNT(*) FROM tender_notices WHERE {tender_filter}", tender_params).fetchone()[0]
    tender_deadline_items = []
    for row in conn.execute(
        f"""SELECT id,title,buyer_name,deadline,priority_score,triage_status
            FROM tender_notices WHERE {tender_filter}
            ORDER BY deadline,priority_score DESC,id LIMIT 12""",
        tender_params,
    ):
        buyer = f" for {row['buyer_name']}" if row["buyer_name"] else ""
        tender_deadline_items.append(_today_signal(
            "tender", row["id"], row["title"], f"Deadline {row['deadline']}{buyer}", f"/tenders/{row['id']}",
            deadline=row["deadline"], buyer_name=row["buyer_name"], status=row["triage_status"],
            priority="high" if row["priority_score"] >= 75 else "normal",
        ))

    open_deal_sql = """FROM opportunities o JOIN pipeline_stages s ON s.id=o.stage_id
                       WHERE s.kind='open' AND o.archived_at IS NULL"""
    open_deals = conn.execute(f"SELECT COUNT(*) {open_deal_sql}").fetchone()[0]
    pipeline_value_minor = conn.execute(f"SELECT COALESCE(SUM(o.value_minor),0) {open_deal_sql}").fetchone()[0]
    weighted_pipeline_minor = conn.execute(
        f"SELECT COALESCE(SUM(o.value_minor * o.probability_bps / 10000),0) {open_deal_sql}"
    ).fetchone()[0]
    deal_risk_where = """s.kind='open' AND o.archived_at IS NULL AND
                         (o.expected_close_date < ? OR TRIM(o.next_action) = '' OR o.updated_at < ?)"""
    deal_risk_params = (today, stale_cutoff)
    deal_risk_count = conn.execute(
        f"""SELECT COUNT(*) FROM opportunities o
            JOIN pipeline_stages s ON s.id=o.stage_id WHERE {deal_risk_where}""",
        deal_risk_params,
    ).fetchone()[0]
    deal_risks = []
    for row in conn.execute(
        f"""SELECT o.id,o.title,o.value_minor,o.expected_close_date,o.next_action,o.updated_at,a.name AS account_name
            FROM opportunities o JOIN pipeline_stages s ON s.id=o.stage_id
            JOIN accounts a ON a.id=o.account_id
            WHERE {deal_risk_where}
            ORDER BY (o.expected_close_date IS NULL),o.expected_close_date,o.updated_at,o.id LIMIT 12""",
        deal_risk_params,
    ):
        reasons = []
        if row["expected_close_date"] and row["expected_close_date"] < today:
            reasons.append(f"Expected close was {row['expected_close_date']}")
        if not row["next_action"].strip():
            reasons.append("No next action is set")
        if row["updated_at"] < stale_cutoff:
            reasons.append("No update for at least 14 days")
        deal_risks.append(_today_signal(
            "opportunity", row["id"], row["title"], " - ".join(reasons), f"/opportunities/{row['id']}",
            account_name=row["account_name"], value_minor=row["value_minor"],
            expected_close_date=row["expected_close_date"], priority="high",
        ))

    upcoming_meetings = []
    meeting_count = conn.execute(
        """SELECT COUNT(*) FROM calendar_events
           WHERE starts_at >= ? AND starts_at < ? AND archived_at IS NULL""",
        (now.isoformat(), meeting_cutoff),
    ).fetchone()[0]
    for row in conn.execute(
        """SELECT id,title,body,location,starts_at,ends_at FROM calendar_events
           WHERE starts_at >= ? AND starts_at < ? AND archived_at IS NULL
           ORDER BY starts_at,id LIMIT 12""",
        (now.isoformat(), meeting_cutoff),
    ):
        reason = row["location"] or row["body"] or f"Starts {row['starts_at']}"
        upcoming_meetings.append(_today_signal(
            "meeting", row["id"], row["title"], reason, f"/calendar?event={row['id']}",
            starts_at=row["starts_at"], ends_at=row["ends_at"], location=row["location"],
        ))

    unread_replies: list[dict[str, Any]] = []
    reply_count = 0
    if _table_exists(conn, "gmail_threads") and _table_exists(conn, "gmail_messages"):
        reply_from = """FROM gmail_threads t JOIN gmail_messages m ON m.id = (
                            SELECT latest.id FROM gmail_messages latest
                            WHERE latest.thread_id=t.id ORDER BY latest.sent_at DESC,latest.id DESC LIMIT 1
                        )"""
        reply_where = "t.unread=1 AND t.archived_at IS NULL AND m.direction='inbound'"
        reply_count = conn.execute(f"SELECT COUNT(*) {reply_from} WHERE {reply_where}").fetchone()[0]
        for row in conn.execute(
            f"""SELECT t.id,t.subject,t.snippet,t.last_message_at,m.from_email,m.subject AS message_subject
                {reply_from} WHERE {reply_where}
                ORDER BY t.last_message_at DESC,t.id DESC LIMIT 12"""
        ):
            context = row["snippet"].strip()[:240] or "Unread inbound reply"
            sender = f"Reply from {row['from_email']} - " if row["from_email"] else ""
            unread_replies.append(_today_signal(
                "email_thread", row["id"], row["subject"] or row["message_subject"] or "Email reply",
                f"{sender}{context}", f"/inbox?thread={row['id']}", last_message_at=row["last_message_at"],
                from_email=row["from_email"], priority="high",
            ))

    project_blockers: list[dict[str, Any]] = []
    blocked_project_count = 0
    if _table_exists(conn, "projects"):
        blocked_filter = "status='Blocked' AND archived_at IS NULL"
        blocked_project_count = conn.execute(f"SELECT COUNT(*) FROM projects WHERE {blocked_filter}").fetchone()[0]
        for row in conn.execute(
            f"""SELECT id,name,notes,due_on FROM projects WHERE {blocked_filter}
                ORDER BY (due_on IS NULL),due_on,id LIMIT 12"""
        ):
            detail = row["notes"].strip()[:240] if row["notes"] else "Delivery cannot progress"
            project_blockers.append(_today_signal(
                "project", row["id"], row["name"], f"Blocked - {detail}", f"/projects/{row['id']}",
                due_on=row["due_on"], priority="high",
            ))

    unpaid_invoice_items: list[dict[str, Any]] = []
    overdue_invoice_items: list[dict[str, Any]] = []
    unpaid_invoice_count = 0
    overdue_invoice_count = 0
    outstanding_minor = 0
    if _table_exists(conn, "invoices"):
        status_column = "status" if _has_column(conn, "invoices", "status") else "state"
        balance_expression = "balance_minor" if _has_column(conn, "invoices", "balance_minor") else "MAX(total_pence-paid_pence-credited_pence,0)"
        archive_filter = " AND archived_at IS NULL" if _has_column(conn, "invoices", "archived_at") else ""
        unpaid_filter = f"{status_column} IN ('Sent','Part-paid','Overdue') AND {balance_expression} > 0{archive_filter}"
        finance_row = conn.execute(
            f"""SELECT COUNT(*) AS unpaid_count,
                       COALESCE(SUM({balance_expression}),0) AS outstanding,
                       COALESCE(SUM(CASE WHEN due_on < ? OR {status_column}='Overdue' THEN 1 ELSE 0 END),0) AS overdue_count
                FROM invoices WHERE {unpaid_filter}""",
            (today,),
        ).fetchone()
        unpaid_invoice_count = finance_row["unpaid_count"]
        overdue_invoice_count = finance_row["overdue_count"]
        outstanding_minor = finance_row["outstanding"]
        for row in conn.execute(
            f"""SELECT id,number,customer_name,due_on,currency,{status_column} AS status,
                       {balance_expression} AS balance_minor
                FROM invoices WHERE {unpaid_filter}
                ORDER BY CASE WHEN due_on < ? OR {status_column}='Overdue' THEN 0 ELSE 1 END,due_on,id LIMIT 12""",
            (today,),
        ):
            overdue = row["due_on"] < today or row["status"] == "Overdue"
            timing = f"Overdue since {row['due_on']}" if overdue else f"Due {row['due_on']}"
            amount = f"{row['currency']} {row['balance_minor'] / 100:,.2f} outstanding"
            item = _today_signal(
                "invoice", row["id"], row["number"] or f"Invoice {row['id']}", f"{timing} - {amount}",
                f"/invoices/{row['id']}", balance_minor=row["balance_minor"], currency=row["currency"],
                due_on=row["due_on"], customer_name=row["customer_name"], status=row["status"],
                priority="high" if overdue else "normal",
            )
            unpaid_invoice_items.append(item)
            if overdue:
                overdue_invoice_items.append(item)

    renewals: list[dict[str, Any]] = []
    renewal_count = 0
    if _table_exists(conn, "client_success"):
        renewal_filter = """cs.renewal_on BETWEEN ? AND ? AND cs.archived_at IS NULL
                            AND a.archived_at IS NULL"""
        renewal_params = (today, renewal_cutoff)
        renewal_count = conn.execute(
            f"""SELECT COUNT(*) FROM client_success cs JOIN accounts a ON a.id=cs.account_id
                WHERE {renewal_filter}""",
            renewal_params,
        ).fetchone()[0]
        for row in conn.execute(
            f"""SELECT cs.id,cs.account_id,cs.renewal_on,cs.manual_health,cs.open_risks,a.name
                FROM client_success cs JOIN accounts a ON a.id=cs.account_id
                WHERE {renewal_filter} ORDER BY cs.renewal_on,cs.id LIMIT 12""",
            renewal_params,
        ):
            days = (date.fromisoformat(row["renewal_on"]) - local_today).days
            timing = "Renews today" if days == 0 else f"Renews in {days} days"
            if row["open_risks"]:
                timing += f" - {row['open_risks']} open risk{'s' if row['open_risks'] != 1 else ''}"
            renewals.append(_today_signal(
                "renewal", row["id"], row["name"], timing, f"/client-success/{row['account_id']}",
                account_id=row["account_id"], renewal_on=row["renewal_on"], health=row["manual_health"],
                priority="high" if days <= 30 or row["open_risks"] else "normal",
            ))

    counts = {
        "unread_replies": reply_count,
        "overdue_tasks": overdue_tasks,
        "due_today_tasks": max(due_work_count - overdue_tasks, 0),
        "overdue_work": due_work_count,
        "tender_deadlines": tender_count,
        "risky_deals": deal_risk_count,
        "upcoming_meetings": meeting_count,
        "blocked_projects": blocked_project_count,
        "unpaid_invoices": unpaid_invoice_count,
        "overdue_invoices": overdue_invoice_count,
        "renewals": renewal_count,
    }
    counts["needs_action"] = sum(counts[key] for key in (
        "unread_replies", "overdue_work", "tender_deadlines", "risky_deals",
        "blocked_projects", "unpaid_invoices", "renewals",
    ))

    priority_order = {
        "email_thread": 0, "task": 1, "invoice": 2, "tender": 3,
        "project": 4, "opportunity": 5, "renewal": 6,
    }
    priorities = sorted(
        unread_replies + overdue_work + unpaid_invoice_items + tender_deadline_items + deal_risks + project_blockers + renewals,
        key=lambda item: (0 if item.get("priority") == "high" else 1, priority_order.get(item["type"], 99), str(item.get("due_at") or item.get("deadline") or item.get("due_on") or item.get("renewal_on") or ""), str(item["id"])),
    )[:20]
    risk_signals = deal_risks[:3] + project_blockers[:3] + overdue_invoice_items[:3] + renewals[:3]
    stage_summary = records(conn.execute(
        """SELECT s.id,s.name,s.position,s.kind,s.color,COUNT(o.id) AS count,
                  COALESCE(SUM(o.value_minor),0) AS value_minor
           FROM pipeline_stages s LEFT JOIN opportunities o ON o.stage_id=s.id AND o.archived_at IS NULL
           WHERE s.archived_at IS NULL GROUP BY s.id ORDER BY s.position"""
    ))
    recent = records(conn.execute("SELECT * FROM activities ORDER BY occurred_at DESC LIMIT 20"))
    active_clients = conn.execute("SELECT COUNT(DISTINCT account_id) FROM account_roles WHERE role='client'").fetchone()[0]
    briefing = f"{counts['needs_action']} active signal{'s' if counts['needs_action'] != 1 else ''} across replies, deadlines, deals, delivery, cash and renewals."
    return {
        "generated_at": now.isoformat(),
        "counts": counts,
        "briefing": briefing,
        "needs_action": counts["needs_action"],
        "open_tasks": open_tasks,
        "overdue_tasks": overdue_tasks,
        "open_tenders": open_tenders,
        "tenders_due_soon": tender_count,
        "tender_deadlines": tender_count,
        "open_deals": open_deals,
        "pipeline_value_minor": pipeline_value_minor,
        "weighted_pipeline_minor": weighted_pipeline_minor,
        "active_clients": active_clients,
        "outstanding_minor": outstanding_minor,
        "overdue_invoices": overdue_invoice_count,
        "unread_replies": unread_replies,
        "overdue_work": overdue_work,
        "tender_deadline_items": tender_deadline_items,
        "deal_risks": deal_risks,
        "upcoming_meetings": upcoming_meetings,
        "upcoming_events": upcoming_meetings,
        "project_blockers": project_blockers,
        "unpaid_invoice_items": unpaid_invoice_items,
        "overdue_invoice_items": overdue_invoice_items,
        "renewals": renewals,
        "risk_signals": risk_signals,
        "priorities": priorities,
        "action_items": priorities,
        "pipeline": stage_summary,
        "recent_activity": recent,
    }


def global_search(conn: sqlite3.Connection, query: str, limit: int = 30) -> list[dict[str, Any]]:
    cleaned = query.strip()
    if not cleaned:
        return []
    try:
        rows = conn.execute("SELECT entity_type, entity_id, title, subtitle, detail, bm25(search_fts) AS rank FROM search_fts WHERE search_fts MATCH ? ORDER BY rank LIMIT ?", (f'"{cleaned.replace(chr(34), chr(34)*2)}"*', limit))
        return records(rows)
    except sqlite3.OperationalError:
        needle = f"%{cleaned}%"
        return records(conn.execute("SELECT entity_type, entity_id, title, subtitle, detail FROM search_fts WHERE title LIKE ? OR subtitle LIKE ? OR detail LIKE ? LIMIT ?", (needle, needle, needle, limit)))


def create_tag(conn: sqlite3.Connection, payload: models.TagCreate) -> dict[str, Any]:
    cursor = conn.execute("INSERT INTO tags (name, color, created_at) VALUES (?, ?, ?)", (payload.name, payload.color.strip() or "blue", platform_db.utc_now().isoformat()))
    return record(conn.execute("SELECT * FROM tags WHERE id = ?", (cursor.lastrowid,)).fetchone()) or {}


def list_tags(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return records(conn.execute("SELECT * FROM tags ORDER BY name COLLATE NOCASE"))


def get_entity_tags(conn: sqlite3.Connection, entity_type: str, entity_id: int) -> list[dict[str, Any]]:
    return records(conn.execute(
        "SELECT t.* FROM tags t JOIN entity_tags e ON e.tag_id=t.id WHERE e.entity_type=? AND e.entity_id=? ORDER BY t.name",
        (entity_type, entity_id),
    ))


def set_entity_tags(conn: sqlite3.Connection, entity_type: str, entity_id: int, tag_ids: list[int]) -> list[dict[str, Any]]:
    conn.execute("DELETE FROM entity_tags WHERE entity_type = ? AND entity_id = ?", (entity_type, entity_id))
    for tag_id in dict.fromkeys(tag_ids):
        if not conn.execute("SELECT 1 FROM tags WHERE id = ?", (tag_id,)).fetchone():
            raise DomainError(f"Tag #{tag_id} does not exist")
        conn.execute("INSERT INTO entity_tags (tag_id, entity_type, entity_id) VALUES (?, ?, ?)", (tag_id, entity_type, entity_id))
    return records(conn.execute("SELECT t.* FROM tags t JOIN entity_tags e ON e.tag_id=t.id WHERE e.entity_type=? AND e.entity_id=? ORDER BY t.name", (entity_type, entity_id)))


def create_custom_field(conn: sqlite3.Connection, payload: models.CustomFieldCreate) -> dict[str, Any]:
    data = payload.model_dump(mode="json")
    now = platform_db.utc_now().isoformat()
    cursor = conn.execute("INSERT INTO custom_fields (entity_type,name,field_type,options_json,required,position,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", (data["entity_type"], data["name"], data["field_type"], json.dumps(data["options"]), int(data["required"]), data["position"], now, now))
    return record(conn.execute("SELECT * FROM custom_fields WHERE id=?", (cursor.lastrowid,)).fetchone()) or {}


def list_custom_fields(conn: sqlite3.Connection, entity_type: str = "") -> list[dict[str, Any]]:
    if entity_type:
        return records(conn.execute("SELECT * FROM custom_fields WHERE entity_type=? AND archived_at IS NULL ORDER BY position,id", (entity_type,)))
    return records(conn.execute("SELECT * FROM custom_fields WHERE archived_at IS NULL ORDER BY entity_type,position,id"))


def create_saved_view(conn: sqlite3.Connection, payload: models.SavedViewCreate) -> dict[str, Any]:
    data = payload.model_dump(mode="json")
    now = platform_db.utc_now().isoformat()
    cursor = conn.execute("INSERT INTO saved_views (entity_type,name,config_json,created_at,updated_at) VALUES (?,?,?,?,?)", (data["entity_type"], data["name"], json.dumps(data["config"]), now, now))
    return record(conn.execute("SELECT * FROM saved_views WHERE id=?", (cursor.lastrowid,)).fetchone()) or {}


def list_saved_views(conn: sqlite3.Connection, entity_type: str = "") -> list[dict[str, Any]]:
    if entity_type:
        return records(conn.execute("SELECT * FROM saved_views WHERE entity_type=? AND archived_at IS NULL ORDER BY name", (entity_type,)))
    return records(conn.execute("SELECT * FROM saved_views WHERE archived_at IS NULL ORDER BY entity_type,name"))


def get_business_profile(conn: sqlite3.Connection) -> dict[str, Any]:
    return record(conn.execute("SELECT * FROM business_profile WHERE id=1").fetchone()) or {}


def update_business_profile(conn: sqlite3.Connection, payload: models.BusinessProfileUpdate) -> dict[str, Any]:
    data = payload.model_dump(mode="json", exclude_unset=True)
    version = data.pop("version")
    if "registered_address" in data:
        data["registered_address_json"] = json.dumps(data.pop("registered_address"))
    if "vat_registered" in data:
        data["vat_registered"] = int(data["vat_registered"])
    if "tax_codes_approved" in data:
        data["tax_codes_approved"] = int(data["tax_codes_approved"])
    current = get_business_profile(conn)
    candidate = {**current, **data}
    if candidate.get("vat_registered"):
        missing = []
        if not str(candidate.get("legal_name") or "").strip():
            missing.append("legal name")
        if not str(candidate.get("vat_number") or "").strip():
            missing.append("VAT number")
        if not str(candidate.get("vat_scheme") or "").strip():
            missing.append("VAT scheme")
        if not candidate.get("vat_effective_from"):
            missing.append("VAT effective date")
        if not candidate.get("tax_codes_approved"):
            missing.append("approved tax codes")
        raw_address = candidate.get("registered_address_json")
        if raw_address is None:
            address = candidate.get("registered_address") or {}
        else:
            try:
                address = json.loads(raw_address or "{}")
            except (TypeError, json.JSONDecodeError):
                address = {}
        if not any(str(value).strip() for value in address.values()):
            missing.append("registered address")
        if missing:
            raise DomainError("Complete VAT setup before enabling VAT: " + ", ".join(missing))
    return _update_versioned(conn, "business_profile", 1, version, data)


def archive_record(
    conn: sqlite3.Connection,
    resource: str,
    item_id: int,
    restore: bool = False,
    expected_version: int | None = None,
) -> dict[str, Any]:
    table = {
        "accounts": "accounts", "contacts": "contacts", "leads": "sales_leads", "opportunities": "opportunities",
        "tenders": "tender_notices", "tasks": "work_tasks", "events": "calendar_events",
    }.get(resource)
    if not table:
        raise DomainError("This resource cannot be archived")
    return _archive(conn, table, item_id, restore, expected_version)


def _enqueue_optional(conn: sqlite3.Connection, kind: str, payload: dict[str, Any], idempotency_key: str) -> None:
    if not _table_exists(conn, "integration_jobs"):
        return
    now = platform_db.utc_now().isoformat()
    conn.execute(
        """INSERT OR IGNORE INTO integration_jobs
           (id, kind, payload_json, state, available_at, max_attempts, idempotency_key,
            requires_reconciliation, reconciliation_state, created_at, updated_at)
           VALUES (lower(hex(randomblob(16))), ?, ?, 'queued', ?, 8, ?, 1, 'pending', ?, ?)""",
        (kind, json.dumps(payload, separators=(",", ":"), sort_keys=True), now, idempotency_key, now, now),
    )


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None
