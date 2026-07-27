from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterator

from fastapi import HTTPException

from app.operations.models import (
    CatalogItemCreate,
    CatalogItemUpdate,
    ClientSuccessUpsert,
    CommercialLineInput,
    ContractCreate,
    ContractSign,
    ContractUpdate,
    CreditNoteCreate,
    ExpenseCreate,
    ExpenseUpdate,
    InvoiceCreate,
    InvoiceUpdate,
    MilestoneCreate,
    MilestoneUpdate,
    PaymentAllocationCreate,
    PaymentCreate,
    PaymentRefundCreate,
    ProjectCreate,
    ProjectUpdate,
    ProposalCreate,
    ProposalUpdate,
    TimeEntryCreate,
    TimeEntryUpdate,
)
from app.operations.schema import install_schema
from app.platform_db import connect, enqueue_automation_event, index_record, next_number, utc_now


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    with connect() as conn:
        install_schema(conn)
        yield conn


def _now() -> str:
    return utc_now().isoformat()


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return aware.isoformat()
    return value.isoformat() if hasattr(value, "isoformat") else value


def _pence(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _quantity(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _assert_output_vat_ready(conn: sqlite3.Connection, vat_pence: int) -> None:
    if vat_pence <= 0:
        return
    profile = conn.execute("SELECT * FROM business_profile WHERE id=1").fetchone()
    today = date.today().isoformat()
    ready = bool(
        profile
        and profile["vat_registered"]
        and str(profile["legal_name"] or "").strip()
        and str(profile["vat_number"] or "").strip()
        and str(profile["vat_scheme"] or "").strip()
        and profile["vat_effective_from"]
        and profile["vat_effective_from"] <= today
        and (not profile["vat_effective_to"] or profile["vat_effective_to"] >= today)
        and profile["tax_codes_approved"]
    )
    if not ready:
        raise HTTPException(
            409,
            "VAT is disabled until the legal, scheme, effective-date and approved-tax-code setup is complete",
        )


def calculate_line(line: CommercialLineInput) -> dict[str, Any]:
    """Calculate one commercial line in integer pence with half-up VAT rounding."""
    discounted = Decimal(line.unit_price_pence) * line.quantity
    discounted *= Decimal(10_000 - line.discount_bps) / Decimal(10_000)
    net = _pence(discounted)
    vat = _pence(Decimal(net) * Decimal(line.tax_rate_bps) / Decimal(10_000))
    return {
        "catalog_item_id": line.catalog_item_id,
        "description": line.description,
        "quantity": _quantity(line.quantity),
        "unit_price_pence": line.unit_price_pence,
        "discount_bps": line.discount_bps,
        "tax_rate_bps": line.tax_rate_bps,
        "net_pence": net,
        "vat_pence": vat,
        "total_pence": net + vat,
    }


def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def _required(conn: sqlite3.Connection, table: str, item_id: int) -> dict[str, Any]:
    row = _dict(conn.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,)).fetchone())
    if not row:
        raise HTTPException(404, f"{table.replace('_', ' ').rstrip('s').title()} not found")
    return row


def _page(
    conn: sqlite3.Connection,
    table: str,
    cursor: int | None,
    limit: int,
    where: str = "1=1",
    params: tuple[Any, ...] = (),
    order: str = "id",
) -> dict[str, Any]:
    limit = min(max(limit, 1), 100)
    cursor_clause = " AND id > ?" if cursor is not None else ""
    query_params = (*params, cursor) if cursor is not None else params
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE {where}{cursor_clause} ORDER BY {order} LIMIT ?",
        (*query_params, limit + 1),
    ).fetchall()
    more = len(rows) > limit
    items = [dict(row) for row in rows[:limit]]
    return {"items": items, "next_cursor": str(items[-1]["id"]) if more else None}


def _versioned_update(
    conn: sqlite3.Connection,
    table: str,
    item_id: int,
    version: int,
    changes: dict[str, Any],
) -> dict[str, Any]:
    current = _required(conn, table, item_id)
    clean = {key: _iso(value) for key, value in changes.items()}
    clean["updated_at"] = _now()
    assignments = ", ".join(f"{key} = ?" for key in clean)
    cursor = conn.execute(
        f"UPDATE {table} SET {assignments}, version = version + 1 WHERE id = ? AND version = ? AND archived_at IS NULL",
        (*clean.values(), item_id, version),
    )
    if cursor.rowcount != 1:
        latest = _required(conn, table, item_id)
        raise HTTPException(409, {
            "message": "Record was changed or archived; refresh and retry",
            "current_record": latest,
            "current_version": latest.get("version"),
        })
    return _required(conn, table, item_id)


def _archive(table: str, item_id: int, version: int) -> dict[str, Any]:
    with _db() as conn:
        current = _required(conn, table, item_id)
        now = _now()
        cursor = conn.execute(
            f"UPDATE {table} SET archived_at = ?, updated_at = ?, version = version + 1 "
            "WHERE id = ? AND version = ? AND archived_at IS NULL",
            (now, now, item_id, version),
        )
        if cursor.rowcount != 1:
            latest = _required(conn, table, item_id)
            raise HTTPException(409, {
                "message": "Record was changed or archived; refresh and retry",
                "current_record": latest,
                "current_version": latest.get("version"),
            })
        return _required(conn, table, item_id)


def _lines(
    conn: sqlite3.Connection,
    table: str,
    foreign_key: str,
    parent_id: int,
    lines: list[CommercialLineInput],
    include_catalog: bool = True,
) -> tuple[int, int, int]:
    conn.execute(f"DELETE FROM {table} WHERE {foreign_key} = ?", (parent_id,))
    totals = [calculate_line(line) for line in lines]
    for index, line in enumerate(totals):
        columns = [foreign_key]
        if include_catalog:
            columns.append("catalog_item_id")
        columns += [
            "description", "quantity", "unit_price_pence", "discount_bps",
            "tax_rate_bps", "net_pence", "vat_pence", "total_pence", "sort_order",
        ]
        values = [parent_id]
        if include_catalog:
            values.append(line["catalog_item_id"])
        values += [
            line["description"], line["quantity"], line["unit_price_pence"],
            line["discount_bps"], line["tax_rate_bps"], line["net_pence"],
            line["vat_pence"], line["total_pence"], index,
        ]
        placeholders = ",".join("?" for _ in values)
        conn.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})", values)
    return (
        sum(line["net_pence"] for line in totals),
        sum(line["vat_pence"] for line in totals),
        sum(line["total_pence"] for line in totals),
    )


def _document(
    conn: sqlite3.Connection,
    table: str,
    line_table: str,
    foreign_key: str,
    item_id: int,
) -> dict[str, Any]:
    item = _required(conn, table, item_id)
    item["lines"] = [
        dict(row)
        for row in conn.execute(
            f"SELECT * FROM {line_table} WHERE {foreign_key} = ? ORDER BY sort_order, id", (item_id,)
        )
    ]
    if table == "invoices":
        item["outstanding_pence"] = max(
            item["total_pence"] - item["paid_pence"] - item["credited_pence"], 0
        )
    return item


def _cached(conn: sqlite3.Connection, action: str, key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT response_json FROM operation_idempotency WHERE action = ? AND key = ?", (action, key)
    ).fetchone()
    return json.loads(row["response_json"]) if row else None


def _remember(conn: sqlite3.Connection, action: str, key: str, response: dict[str, Any]) -> dict[str, Any]:
    conn.execute(
        "INSERT INTO operation_idempotency(action, key, response_json, created_at) VALUES (?, ?, ?, ?)",
        (action, key, json.dumps(response), _now()),
    )
    return response


def _post_journal(
    conn: sqlite3.Connection,
    source_type: str,
    source_id: int,
    description: str,
    lines: list[tuple[str, int, int]],
    *,
    allow_empty: bool = False,
) -> int:
    lines = [line for line in lines if line[1] or line[2]]
    debit = sum(line[1] for line in lines)
    credit = sum(line[2] for line in lines)
    if debit != credit or (not lines and not allow_empty):
        raise HTTPException(409, "Journal is not balanced")
    cursor = conn.execute(
        "INSERT OR IGNORE INTO journals(source_type, source_id, description, posted_at) VALUES (?, ?, ?, ?)",
        (source_type, source_id, description, _now()),
    )
    if cursor.rowcount == 0:
        return int(conn.execute(
            "SELECT id FROM journals WHERE source_type = ? AND source_id = ?",
            (source_type, source_id),
        ).fetchone()["id"])
    journal_id = int(cursor.lastrowid)
    conn.executemany(
        "INSERT INTO journal_lines(journal_id, account_code, debit_pence, credit_pence) VALUES (?, ?, ?, ?)",
        [(journal_id, *line) for line in lines],
    )
    return journal_id


# Catalog
def list_catalog(cursor: int | None = None, limit: int = 50, include_inactive: bool = False) -> dict[str, Any]:
    with _db() as conn:
        where = "archived_at IS NULL" + ("" if include_inactive else " AND active = 1")
        return _page(conn, "catalog_items", cursor, limit, where)


def create_catalog_item(request: CatalogItemCreate) -> dict[str, Any]:
    now = _now()
    with _db() as conn:
        cursor = conn.execute(
            "INSERT INTO catalog_items(name, description, unit, unit_price_pence, tax_rate_bps, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (request.name, request.description, request.unit, request.unit_price_pence, request.tax_rate_bps, request.active, now, now),
        )
        return _required(conn, "catalog_items", int(cursor.lastrowid))


def get_catalog_item(item_id: int) -> dict[str, Any]:
    with _db() as conn:
        return _required(conn, "catalog_items", item_id)


def update_catalog_item(item_id: int, request: CatalogItemUpdate) -> dict[str, Any]:
    with _db() as conn:
        return _versioned_update(conn, "catalog_items", item_id, request.version, request.model_dump(exclude={"version"}, exclude_unset=True))


# Projects and delivery
def list_projects(cursor: int | None = None, limit: int = 50, status: str | None = None, query: str = "") -> dict[str, Any]:
    with _db() as conn:
        where, params = "archived_at IS NULL", ()
        if status:
            where += " AND status = ?"
            params = (status,)
        if query.strip():
            needle = f"%{query.strip()}%"
            where += " AND (name LIKE ? OR notes LIKE ?)"
            params += (needle, needle)
        return _page(conn, "projects", cursor, limit, where, params)


def get_project(project_id: int) -> dict[str, Any]:
    with _db() as conn:
        project = _required(conn, "projects", project_id)
        project["milestones"] = [dict(row) for row in conn.execute(
            "SELECT * FROM milestones WHERE project_id = ? AND archived_at IS NULL ORDER BY sort_order, id", (project_id,)
        )]
        summary = conn.execute(
            "SELECT COALESCE(SUM(minutes), 0) minutes, COALESCE(SUM(CASE WHEN billable THEN minutes ELSE 0 END), 0) billable_minutes "
            "FROM time_entries WHERE project_id = ? AND archived_at IS NULL", (project_id,)
        ).fetchone()
        expenses = conn.execute(
            "SELECT COALESCE(SUM(total_pence), 0) total FROM expenses WHERE project_id = ? AND archived_at IS NULL", (project_id,)
        ).fetchone()
        project["time_minutes"] = int(summary["minutes"])
        project["billable_minutes"] = int(summary["billable_minutes"])
        project["expense_pence"] = int(expenses["total"])
        return project


def create_project(request: ProjectCreate) -> dict[str, Any]:
    now = _now()
    with _db() as conn:
        cursor = conn.execute(
            "INSERT INTO projects(account_id, opportunity_id, contract_id, name, status, billing_type, budget_pence, currency, starts_on, due_on, notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (request.account_id, request.opportunity_id, request.contract_id, request.name, request.status,
             request.billing_type, request.budget_pence, request.currency.upper(), _iso(request.starts_on),
             _iso(request.due_on), request.notes, now, now),
        )
        return _required(conn, "projects", int(cursor.lastrowid))


def update_project(project_id: int, request: ProjectUpdate) -> dict[str, Any]:
    with _db() as conn:
        before = _required(conn, "projects", project_id)
        result = _versioned_update(conn, "projects", project_id, request.version, request.model_dump(exclude={"version"}, exclude_unset=True))
        if result["status"] == "Blocked" and before["status"] != "Blocked":
            enqueue_automation_event(conn, "project.blocked", {**result, "type": "project"})
        return result


def create_project_from_contract(contract_id: int, key: str) -> dict[str, Any]:
    action = f"contract:{contract_id}:project"
    with _db() as conn:
        if cached := _cached(conn, action, key):
            return cached
        contract = _required(conn, "contracts", contract_id)
        if contract["status"] not in {"Signed", "Active"}:
            raise HTTPException(409, "Contract must be signed before project creation")
        existing = conn.execute("SELECT * FROM projects WHERE contract_id = ? AND archived_at IS NULL", (contract_id,)).fetchone()
        if existing:
            return _remember(conn, action, key, dict(existing))
        now = _now()
        cursor = conn.execute(
            "INSERT INTO projects(account_id, opportunity_id, contract_id, name, status, billing_type, budget_pence, currency, starts_on, due_on, notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'Planned', 'fixed', ?, ?, ?, ?, '', ?, ?)",
            (contract["account_id"], contract["opportunity_id"], contract_id, contract["title"], contract["value_pence"],
             contract["currency"], contract["starts_on"], contract["ends_on"], now, now),
        )
        return _remember(conn, action, key, _required(conn, "projects", int(cursor.lastrowid)))


def create_milestone(project_id: int, request: MilestoneCreate) -> dict[str, Any]:
    now = _now()
    with _db() as conn:
        _required(conn, "projects", project_id)
        cursor = conn.execute(
            "INSERT INTO milestones(project_id, title, due_on, amount_pence, status, sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (project_id, request.title, _iso(request.due_on), request.amount_pence, request.status, request.sort_order, now, now),
        )
        return _required(conn, "milestones", int(cursor.lastrowid))


def list_milestones(cursor: int | None = None, limit: int = 50, project_id: int | None = None, query: str = "") -> dict[str, Any]:
    with _db() as conn:
        where, params = "archived_at IS NULL", ()
        if project_id is not None:
            where += " AND project_id = ?"
            params = (project_id,)
        if query.strip():
            where += " AND title LIKE ?"
            params += (f"%{query.strip()}%",)
        return _page(conn, "milestones", cursor, limit, where, params)


def get_milestone(milestone_id: int) -> dict[str, Any]:
    with _db() as conn:
        return _required(conn, "milestones", milestone_id)


def update_milestone(milestone_id: int, request: MilestoneUpdate) -> dict[str, Any]:
    with _db() as conn:
        return _versioned_update(conn, "milestones", milestone_id, request.version, request.model_dump(exclude={"version"}, exclude_unset=True))


def list_time_entries(cursor: int | None = None, limit: int = 50, project_id: int | None = None, query: str = "") -> dict[str, Any]:
    with _db() as conn:
        where, params = "archived_at IS NULL", ()
        if project_id is not None:
            where += " AND project_id = ?"
            params = (project_id,)
        if query.strip():
            where += " AND description LIKE ?"
            params += (f"%{query.strip()}%",)
        return _page(conn, "time_entries", cursor, limit, where, params)


def create_time_entry(request: TimeEntryCreate) -> dict[str, Any]:
    now = _now()
    with _db() as conn:
        _required(conn, "projects", request.project_id)
        cursor = conn.execute(
            "INSERT INTO time_entries(project_id, entry_date, minutes, description, billable, hourly_rate_pence, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (request.project_id, _iso(request.entry_date), request.minutes, request.description, request.billable, request.hourly_rate_pence, now, now),
        )
        return _required(conn, "time_entries", int(cursor.lastrowid))


def get_time_entry(entry_id: int) -> dict[str, Any]:
    with _db() as conn:
        return _required(conn, "time_entries", entry_id)


def update_time_entry(entry_id: int, request: TimeEntryUpdate) -> dict[str, Any]:
    with _db() as conn:
        return _versioned_update(conn, "time_entries", entry_id, request.version, request.model_dump(exclude={"version"}, exclude_unset=True))


def list_expenses(cursor: int | None = None, limit: int = 50, project_id: int | None = None) -> dict[str, Any]:
    with _db() as conn:
        where, params = "archived_at IS NULL", ()
        if project_id is not None:
            where += " AND project_id = ?"
            params = (project_id,)
        return _page(conn, "expenses", cursor, limit, where, params)


def _expense_amounts(net_pence: int, tax_rate_bps: int) -> tuple[int, int]:
    vat = _pence(Decimal(net_pence) * Decimal(tax_rate_bps) / Decimal(10_000))
    return vat, net_pence + vat


def _expense_position(conn: sqlite3.Connection, expense: dict[str, Any]) -> dict[str, int]:
    profile = conn.execute("SELECT * FROM business_profile WHERE id = 1").fetchone()
    expense_date = expense["expense_date"]
    reclaimable_vat = int(expense["vat_pence"]) if (
        expense["vat_pence"]
        and profile
        and profile["vat_registered"]
        and str(profile["legal_name"] or "").strip()
        and str(profile["vat_number"] or "").strip()
        and str(profile["vat_scheme"] or "").strip()
        and profile["vat_effective_from"]
        and profile["vat_effective_from"] <= expense_date
        and (not profile["vat_effective_to"] or profile["vat_effective_to"] >= expense_date)
        and profile["tax_codes_approved"]
    ) else 0
    return {
        "1200": -int(expense["total_pence"]),
        "1300": reclaimable_vat,
        "5000": int(expense["total_pence"]) - reclaimable_vat,
    }


def _post_expense_position(
    conn: sqlite3.Connection,
    expense: dict[str, Any],
    source_type: str,
    description: str,
    target: dict[str, int] | None = None,
) -> int:
    rows = conn.execute(
        "SELECT l.account_code, COALESCE(SUM(l.debit_pence - l.credit_pence), 0) balance "
        "FROM journals j JOIN journal_lines l ON l.journal_id = j.id "
        "WHERE j.source_id = ? AND (j.source_type = 'expense' "
        "OR j.source_type LIKE 'expense_adjustment:%' OR j.source_type LIKE 'expense_reversal:%') "
        "GROUP BY l.account_code",
        (expense["id"],),
    ).fetchall()
    current = {row["account_code"]: int(row["balance"]) for row in rows}
    wanted = _expense_position(conn, expense) if target is None else target
    lines: list[tuple[str, int, int]] = []
    for account in sorted(current.keys() | wanted.keys()):
        delta = wanted.get(account, 0) - current.get(account, 0)
        if delta:
            lines.append((account, max(delta, 0), max(-delta, 0)))
    return _post_journal(
        conn,
        source_type,
        expense["id"],
        description,
        lines,
        allow_empty=True,
    )


def create_expense(request: ExpenseCreate) -> dict[str, Any]:
    now = _now()
    vat, total = _expense_amounts(request.net_pence, request.tax_rate_bps)
    with _db() as conn:
        if request.project_id is not None:
            _required(conn, "projects", request.project_id)
        cursor = conn.execute(
            "INSERT INTO expenses(project_id, account_id, expense_date, vendor, description, net_pence, tax_rate_bps, vat_pence, total_pence, billable, receipt_file_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (request.project_id, request.account_id, _iso(request.expense_date), request.vendor, request.description,
             request.net_pence, request.tax_rate_bps, vat, total, request.billable, request.receipt_file_id, now, now),
        )
        expense = _required(conn, "expenses", int(cursor.lastrowid))
        _post_expense_position(conn, expense, "expense", f"Expense {expense['id']}: {expense['description']}")
        return expense


def get_expense(expense_id: int) -> dict[str, Any]:
    with _db() as conn:
        return _required(conn, "expenses", expense_id)


def update_expense(expense_id: int, request: ExpenseUpdate) -> dict[str, Any]:
    with _db() as conn:
        current = _required(conn, "expenses", expense_id)
        changes = request.model_dump(exclude={"version"}, exclude_unset=True)
        net = changes.get("net_pence", current["net_pence"])
        rate = changes.get("tax_rate_bps", current["tax_rate_bps"])
        if "net_pence" in changes or "tax_rate_bps" in changes:
            changes["vat_pence"], changes["total_pence"] = _expense_amounts(net, rate)
        expense = _versioned_update(conn, "expenses", expense_id, request.version, changes)
        _post_expense_position(
            conn,
            expense,
            f"expense_adjustment:{expense['version']}",
            f"Expense {expense_id} adjustment (version {expense['version']})",
        )
        return expense


def archive_expense(expense_id: int, version: int) -> dict[str, Any]:
    with _db() as conn:
        expense = _required(conn, "expenses", expense_id)
        now = _now()
        cursor = conn.execute(
            "UPDATE expenses SET archived_at = ?, updated_at = ?, version = version + 1 "
            "WHERE id = ? AND version = ? AND archived_at IS NULL",
            (now, now, expense_id, version),
        )
        if cursor.rowcount != 1:
            raise HTTPException(409, "Record was changed or archived; refresh and retry")
        archived = _required(conn, "expenses", expense_id)
        _post_expense_position(
            conn,
            archived,
            f"expense_reversal:{archived['version']}",
            f"Expense {expense_id} reversal (version {archived['version']})",
            {},
        )
        return archived


# Proposals and contracts
def list_proposals(cursor: int | None = None, limit: int = 50, status: str | None = None, query: str = "") -> dict[str, Any]:
    with _db() as conn:
        where, params = "archived_at IS NULL", ()
        if status:
            where += " AND status = ?"
            params = (status,)
        if query.strip():
            needle = f"%{query.strip()}%"
            where += " AND (title LIKE ? OR number LIKE ? OR notes LIKE ?)"
            params += (needle, needle, needle)
        return _page(conn, "proposals", cursor, limit, where, params)


def get_proposal(proposal_id: int) -> dict[str, Any]:
    with _db() as conn:
        return _document(conn, "proposals", "proposal_lines", "proposal_id", proposal_id)


def create_proposal(request: ProposalCreate) -> dict[str, Any]:
    now = _now()
    with _db() as conn:
        cursor = conn.execute(
            "INSERT INTO proposals(account_id, opportunity_id, title, currency, valid_until, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (request.account_id, request.opportunity_id, request.title, request.currency.upper(), _iso(request.valid_until), request.notes, now, now),
        )
        proposal_id = int(cursor.lastrowid)
        net, vat, total = _lines(conn, "proposal_lines", "proposal_id", proposal_id, request.lines)
        _assert_output_vat_ready(conn, vat)
        conn.execute("UPDATE proposals SET net_pence = ?, vat_pence = ?, total_pence = ? WHERE id = ?", (net, vat, total, proposal_id))
        return _document(conn, "proposals", "proposal_lines", "proposal_id", proposal_id)


def update_proposal(proposal_id: int, request: ProposalUpdate) -> dict[str, Any]:
    with _db() as conn:
        current = _required(conn, "proposals", proposal_id)
        if current["status"] != "Draft":
            raise HTTPException(409, "Only draft proposals can be edited")
        changes = request.model_dump(exclude={"version", "lines"}, exclude_unset=True)
        if request.lines is not None:
            net, vat, total = _lines(conn, "proposal_lines", "proposal_id", proposal_id, request.lines)
            _assert_output_vat_ready(conn, vat)
            changes.update(net_pence=net, vat_pence=vat, total_pence=total)
        _versioned_update(conn, "proposals", proposal_id, request.version, changes)
        return _document(conn, "proposals", "proposal_lines", "proposal_id", proposal_id)


def send_proposal(proposal_id: int, key: str) -> dict[str, Any]:
    action = f"proposal:{proposal_id}:send"
    with _db() as conn:
        if cached := _cached(conn, action, key):
            return cached
        item = _required(conn, "proposals", proposal_id)
        if item["status"] != "Draft":
            raise HTTPException(409, "Only a draft proposal can be sent")
        now = _now()
        conn.execute(
            "UPDATE proposals SET number = ?, status = 'Sent', sent_at = ?, updated_at = ?, version = version + 1 WHERE id = ?",
            (next_number(conn, "PROP"), now, now, proposal_id),
        )
        return _remember(conn, action, key, _document(conn, "proposals", "proposal_lines", "proposal_id", proposal_id))


def accept_proposal(proposal_id: int, key: str) -> dict[str, Any]:
    action = f"proposal:{proposal_id}:accept"
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if cached := _cached(conn, action, key):
            return cached
        proposal = _required(conn, "proposals", proposal_id)
        if proposal["status"] != "Sent":
            raise HTTPException(409, "Only a sent proposal can be accepted")
        now = _now()
        conn.execute("UPDATE proposals SET status = 'Accepted', accepted_at = ?, updated_at = ?, version = version + 1 WHERE id = ?", (now, now, proposal_id))
        existing = conn.execute(
            "SELECT * FROM contracts WHERE proposal_id = ? ORDER BY id LIMIT 1",
            (proposal_id,),
        ).fetchone()
        if existing:
            contract = dict(existing)
        else:
            cursor = conn.execute(
                "INSERT INTO contracts(account_id, proposal_id, opportunity_id, title, value_pence, currency, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (proposal["account_id"], proposal_id, proposal["opportunity_id"], proposal["title"], proposal["total_pence"], proposal["currency"], proposal["notes"], now, now),
            )
            contract = _required(conn, "contracts", int(cursor.lastrowid))
        conn.execute(
            "INSERT OR IGNORE INTO lifecycle_links(kind, source_key, target_type, target_id, created_at) VALUES ('proposal_contract', ?, 'contract', ?, ?)",
            (str(proposal_id), contract["id"], now),
        )
        _advance_opportunity_to_contract(conn, proposal["opportunity_id"], now)
        response = {
            "proposal": _document(conn, "proposals", "proposal_lines", "proposal_id", proposal_id),
            "contract": contract,
        }
        enqueue_automation_event(
            conn,
            "proposal.accepted",
            {**response["proposal"], "type": "proposal", "contract_id": contract["id"]},
        )
        return _remember(conn, action, key, response)


def _advance_opportunity_to_contract(conn: sqlite3.Connection, opportunity_id: int | None, now: str) -> None:
    if opportunity_id is None:
        return
    current = conn.execute(
        "SELECT o.id, o.stage_id, s.kind, s.position FROM opportunities o "
        "JOIN pipeline_stages s ON s.id = o.stage_id WHERE o.id = ? AND o.archived_at IS NULL",
        (opportunity_id,),
    ).fetchone()
    target = conn.execute(
        "SELECT id, probability_bps, position FROM pipeline_stages "
        "WHERE name = 'Contract' AND archived_at IS NULL LIMIT 1"
    ).fetchone()
    if not current or not target or current["kind"] != "open" or current["position"] >= target["position"]:
        return
    conn.execute(
        "UPDATE opportunities SET stage_id = ?, status = 'Open', probability_bps = ?, "
        "updated_at = ?, version = version + 1 WHERE id = ?",
        (target["id"], target["probability_bps"], now, opportunity_id),
    )
    conn.execute(
        "INSERT INTO activities(entity_type, entity_id, kind, subject, body, occurred_at, created_at) "
        "VALUES ('opportunity', ?, 'stage_change', 'Moved to Contract', 'Proposal accepted', ?, ?)",
        (opportunity_id, now, now),
    )
    updated = conn.execute("SELECT * FROM opportunities WHERE id=?", (opportunity_id,)).fetchone()
    if updated:
        enqueue_automation_event(
            conn,
            "deal.stage_changed",
            {**dict(updated), "type": "opportunity", "previous_stage_id": current["stage_id"]},
        )


def reject_proposal(proposal_id: int, reason: str, key: str) -> dict[str, Any]:
    action = f"proposal:{proposal_id}:reject"
    with _db() as conn:
        if cached := _cached(conn, action, key):
            return cached
        item = _required(conn, "proposals", proposal_id)
        if item["status"] not in {"Draft", "Sent"}:
            raise HTTPException(409, "Proposal cannot be rejected from its current state")
        now = _now()
        notes = f"{item['notes']}\nRejection: {reason}".strip()
        conn.execute("UPDATE proposals SET status = 'Rejected', rejected_at = ?, notes = ?, updated_at = ?, version = version + 1 WHERE id = ?", (now, notes, now, proposal_id))
        return _remember(conn, action, key, _document(conn, "proposals", "proposal_lines", "proposal_id", proposal_id))


def list_contracts(cursor: int | None = None, limit: int = 50, status: str | None = None, query: str = "") -> dict[str, Any]:
    with _db() as conn:
        where, params = "archived_at IS NULL", ()
        if status:
            where += " AND status = ?"
            params = (status,)
        if query.strip():
            needle = f"%{query.strip()}%"
            where += " AND (title LIKE ? OR number LIKE ? OR notes LIKE ?)"
            params += (needle, needle, needle)
        return _page(conn, "contracts", cursor, limit, where, params)


def get_contract(contract_id: int) -> dict[str, Any]:
    with _db() as conn:
        return _required(conn, "contracts", contract_id)


def create_contract(request: ContractCreate) -> dict[str, Any]:
    now = _now()
    with _db() as conn:
        cursor = conn.execute(
            "INSERT INTO contracts(account_id, proposal_id, opportunity_id, title, starts_on, ends_on, value_pence, currency, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (request.account_id, request.proposal_id, request.opportunity_id, request.title, _iso(request.starts_on),
             _iso(request.ends_on), request.value_pence, request.currency.upper(), request.notes, now, now),
        )
        return _required(conn, "contracts", int(cursor.lastrowid))


def update_contract(contract_id: int, request: ContractUpdate) -> dict[str, Any]:
    with _db() as conn:
        current = _required(conn, "contracts", contract_id)
        if current["status"] != "Draft":
            raise HTTPException(409, "Only draft contracts can be edited")
        return _versioned_update(conn, "contracts", contract_id, request.version, request.model_dump(exclude={"version"}, exclude_unset=True))


def contract_action(contract_id: int, target: str, key: str, sign: ContractSign | None = None) -> dict[str, Any]:
    action = f"contract:{contract_id}:{target.lower()}"
    transitions = {"Sent": ("Draft", "sent_at"), "Signed": ("Sent", "signed_at"), "Active": ("Signed", "activated_at")}
    with _db() as conn:
        if cached := _cached(conn, action, key):
            return cached
        contract = _required(conn, "contracts", contract_id)
        required, timestamp_column = transitions[target]
        if contract["status"] != required:
            raise HTTPException(409, f"Contract must be {required.lower()} before becoming {target.lower()}")
        now = _now()
        number = contract["number"] or next_number(conn, "CON")
        signed_at = _iso(sign.signed_at) if sign and sign.signed_at else now
        stamp = signed_at if target == "Signed" else now
        extra = ", signed_file_id = ?" if target == "Signed" and sign else ""
        params: list[Any] = [number, target, stamp]
        if extra:
            params.append(sign.signed_file_id)
        params += [now, contract_id]
        conn.execute(
            f"UPDATE contracts SET number = ?, status = ?, {timestamp_column} = ?{extra}, updated_at = ?, version = version + 1 WHERE id = ?",
            params,
        )
        result = _required(conn, "contracts", contract_id)
        if target == "Active":
            enqueue_automation_event(conn, "contract.activated", {**result, "type": "contract"})
        return _remember(conn, action, key, result)


# Invoicing, credit notes and payments
def list_invoices(cursor: int | None = None, limit: int = 50, status: str | None = None, query: str = "") -> dict[str, Any]:
    with _db() as conn:
        _refresh_overdue(conn)
        where, params = "1=1", ()
        if status:
            where += " AND status = ?"
            params = (status,)
        if query.strip():
            needle = f"%{query.strip()}%"
            where += " AND (number LIKE ? OR customer_name LIKE ? OR notes LIKE ?)"
            params += (needle, needle, needle)
        result = _page(conn, "invoices", cursor, limit, where, params)
        for item in result["items"]:
            item["outstanding_pence"] = max(item["total_pence"] - item["paid_pence"] - item["credited_pence"], 0)
        return result


def get_invoice(invoice_id: int) -> dict[str, Any]:
    with _db() as conn:
        _refresh_overdue(conn)
        return _document(conn, "invoices", "invoice_lines", "invoice_id", invoice_id)


def create_invoice(request: InvoiceCreate) -> dict[str, Any]:
    now = _now()
    with _db() as conn:
        if request.project_id is not None:
            _required(conn, "projects", request.project_id)
        cursor = conn.execute(
            "INSERT INTO invoices(account_id, project_id, contract_id, currency, due_on, customer_name, customer_address, notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (request.account_id, request.project_id, request.contract_id, request.currency.upper(), _iso(request.due_on),
             request.customer_name, request.customer_address, request.notes, now, now),
        )
        invoice_id = int(cursor.lastrowid)
        net, vat, total = _lines(conn, "invoice_lines", "invoice_id", invoice_id, request.lines)
        _assert_output_vat_ready(conn, vat)
        conn.execute("UPDATE invoices SET net_pence = ?, vat_pence = ?, total_pence = ? WHERE id = ?", (net, vat, total, invoice_id))
        return _document(conn, "invoices", "invoice_lines", "invoice_id", invoice_id)


def update_invoice(invoice_id: int, request: InvoiceUpdate) -> dict[str, Any]:
    with _db() as conn:
        current = _required(conn, "invoices", invoice_id)
        if current["status"] != "Draft":
            raise HTTPException(409, "Issued invoice snapshots are immutable")
        changes = request.model_dump(exclude={"version", "lines"}, exclude_unset=True)
        if request.lines is not None:
            net, vat, total = _lines(conn, "invoice_lines", "invoice_id", invoice_id, request.lines)
            _assert_output_vat_ready(conn, vat)
            changes.update(net_pence=net, vat_pence=vat, total_pence=total)
        _versioned_update(conn, "invoices", invoice_id, request.version, changes)
        return _document(conn, "invoices", "invoice_lines", "invoice_id", invoice_id)


def issue_invoice(invoice_id: int, key: str) -> dict[str, Any]:
    action = f"invoice:{invoice_id}:issue"
    newly_issued = False
    with _db() as conn:
        if not _cached(conn, action, key):
            invoice = _required(conn, "invoices", invoice_id)
            if invoice["status"] != "Draft":
                raise HTTPException(409, "Only a draft invoice can be issued")
            if invoice["total_pence"] <= 0:
                raise HTTPException(409, "Invoice total must be greater than zero")
            _assert_output_vat_ready(conn, invoice["vat_pence"])
            number = next_number(conn, "INV")
            today, now = date.today().isoformat(), _now()
            conn.execute(
                "UPDATE invoices SET number = ?, status = 'Sent', issued_on = ?, updated_at = ?, version = version + 1 WHERE id = ?",
                (number, today, now, invoice_id),
            )
            _post_journal(conn, "invoice", invoice_id, f"Invoice {number}", [
                ("1100", invoice["total_pence"], 0),
                ("4000", 0, invoice["net_pence"]),
                ("2100", 0, invoice["vat_pence"]),
            ])
            _remember(conn, action, key, _document(conn, "invoices", "invoice_lines", "invoice_id", invoice_id))
            newly_issued = True
    if newly_issued:
        try:
            from app.operations.invoice_pdf import render_invoice

            render_invoice(invoice_id)
        except Exception:
            # Issuance and numbering remain authoritative; the PDF can be retried independently.
            pass
    return get_invoice(invoice_id)


def void_invoice(invoice_id: int, key: str) -> dict[str, Any]:
    action = f"invoice:{invoice_id}:void"
    with _db() as conn:
        if cached := _cached(conn, action, key):
            return cached
        invoice = _required(conn, "invoices", invoice_id)
        if invoice["status"] not in {"Sent", "Overdue"} or invoice["paid_pence"] or invoice["credited_pence"]:
            raise HTTPException(409, "Only an unpaid, uncredited issued invoice can be voided")
        _post_journal(conn, "invoice_void", invoice_id, f"Void {invoice['number']}", [
            ("4000", invoice["net_pence"], 0),
            ("2100", invoice["vat_pence"], 0),
            ("1100", 0, invoice["total_pence"]),
        ])
        now = _now()
        conn.execute("UPDATE invoices SET status = 'Void', updated_at = ?, version = version + 1 WHERE id = ?", (now, invoice_id))
        return _remember(conn, action, key, _document(conn, "invoices", "invoice_lines", "invoice_id", invoice_id))


def list_credit_notes(cursor: int | None = None, limit: int = 50) -> dict[str, Any]:
    with _db() as conn:
        return _page(conn, "credit_notes", cursor, limit)


def get_credit_note(credit_id: int) -> dict[str, Any]:
    with _db() as conn:
        return _document(conn, "credit_notes", "credit_note_lines", "credit_note_id", credit_id)


def create_credit_note(request: CreditNoteCreate) -> dict[str, Any]:
    now = _now()
    with _db() as conn:
        invoice = _required(conn, "invoices", request.invoice_id)
        if invoice["status"] in {"Draft", "Void"}:
            raise HTTPException(409, "Credit notes require an issued invoice")
        cursor = conn.execute(
            "INSERT INTO credit_notes(invoice_id, reason, currency, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (request.invoice_id, request.reason, invoice["currency"], now, now),
        )
        credit_id = int(cursor.lastrowid)
        net, vat, total = _lines(conn, "credit_note_lines", "credit_note_id", credit_id, request.lines, include_catalog=False)
        conn.execute("UPDATE credit_notes SET net_pence = ?, vat_pence = ?, total_pence = ? WHERE id = ?", (net, vat, total, credit_id))
        return _document(conn, "credit_notes", "credit_note_lines", "credit_note_id", credit_id)


def issue_credit_note(credit_id: int, key: str) -> dict[str, Any]:
    action = f"credit_note:{credit_id}:issue"
    with _db() as conn:
        if cached := _cached(conn, action, key):
            return cached
        credit = _required(conn, "credit_notes", credit_id)
        if credit["status"] != "Draft":
            raise HTTPException(409, "Only a draft credit note can be issued")
        invoice = _required(conn, "invoices", credit["invoice_id"])
        outstanding = invoice["total_pence"] - invoice["paid_pence"] - invoice["credited_pence"]
        if credit["total_pence"] <= 0 or credit["total_pence"] > outstanding:
            raise HTTPException(409, "Credit note exceeds the invoice outstanding balance")
        number, today, now = next_number(conn, "CN"), date.today().isoformat(), _now()
        conn.execute("UPDATE credit_notes SET number = ?, status = 'Issued', issued_on = ?, updated_at = ?, version = version + 1 WHERE id = ?", (number, today, now, credit_id))
        _post_journal(conn, "credit_note", credit_id, f"Credit note {number}", [
            ("4000", credit["net_pence"], 0),
            ("2100", credit["vat_pence"], 0),
            ("1100", 0, credit["total_pence"]),
        ])
        conn.execute("UPDATE invoices SET credited_pence = credited_pence + ?, updated_at = ?, version = version + 1 WHERE id = ?", (credit["total_pence"], now, invoice["id"]))
        _recompute_invoice(conn, invoice["id"])
        response = {
            "credit_note": _document(conn, "credit_notes", "credit_note_lines", "credit_note_id", credit_id),
            "invoice": _document(conn, "invoices", "invoice_lines", "invoice_id", invoice["id"]),
        }
        return _remember(conn, action, key, response)


def list_payments(cursor: int | None = None, limit: int = 50) -> dict[str, Any]:
    with _db() as conn:
        result = _page(conn, "payments", cursor, limit)
        result["items"] = [_payment(conn, item["id"]) for item in result["items"]]
        return result


def get_payment(payment_id: int) -> dict[str, Any]:
    with _db() as conn:
        return _payment(conn, payment_id)


def _payment(conn: sqlite3.Connection, payment_id: int) -> dict[str, Any]:
    payment = _required(conn, "payments", payment_id)
    payment["allocations"] = [dict(row) for row in conn.execute("SELECT * FROM payment_allocations WHERE payment_id = ? ORDER BY id", (payment_id,))]
    payment["refunds"] = [dict(row) for row in conn.execute("SELECT * FROM payment_refunds WHERE payment_id = ? ORDER BY id", (payment_id,))]
    allocated = sum(item["amount_pence"] for item in payment["allocations"])
    refunded = sum(item["amount_pence"] for item in payment["refunds"])
    payment["allocated_pence"] = allocated
    payment["refunded_pence"] = refunded
    payment["unallocated_pence"] = max(payment["amount_pence"] - allocated - sum(
        item["amount_pence"] for item in payment["refunds"] if item["invoice_id"] is None
    ), 0)
    return payment


def create_payment(request: PaymentCreate, key: str) -> dict[str, Any]:
    action = "payment:create"
    with _db() as conn:
        if cached := _cached(conn, action, key):
            return cached
        now = _now()
        cursor = conn.execute(
            "INSERT INTO payments(amount_pence, currency, received_at, method, reference, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (request.amount_pence, request.currency.upper(), _iso(request.received_at) if request.received_at else now, request.method, request.reference, now),
        )
        payment_id = int(cursor.lastrowid)
        _post_journal(conn, "payment_receipt", payment_id, f"Payment {request.reference or payment_id}", [
            ("1200", request.amount_pence, 0), ("2200", 0, request.amount_pence)
        ])
        if request.invoice_id is not None:
            _allocate(conn, payment_id, PaymentAllocationCreate(invoice_id=request.invoice_id, amount_pence=request.amount_pence))
        result = _payment(conn, payment_id)
        enqueue_automation_event(conn, "payment.received", {**result, "type": "payment"})
        return _remember(conn, action, key, result)


def allocate_payment(payment_id: int, request: PaymentAllocationCreate, key: str) -> dict[str, Any]:
    action = f"payment:{payment_id}:allocate"
    with _db() as conn:
        if cached := _cached(conn, action, key):
            return cached
        _allocate(conn, payment_id, request)
        return _remember(conn, action, key, _payment(conn, payment_id))


def _allocate(conn: sqlite3.Connection, payment_id: int, request: PaymentAllocationCreate) -> None:
    payment = _required(conn, "payments", payment_id)
    invoice = _required(conn, "invoices", request.invoice_id)
    if invoice["status"] in {"Draft", "Void"} or invoice["currency"] != payment["currency"]:
        raise HTTPException(409, "Payment and issued invoice must use the same currency")
    allocated = conn.execute("SELECT COALESCE(SUM(amount_pence), 0) value FROM payment_allocations WHERE payment_id = ?", (payment_id,)).fetchone()["value"]
    unallocated_refunds = conn.execute("SELECT COALESCE(SUM(amount_pence), 0) value FROM payment_refunds WHERE payment_id = ? AND invoice_id IS NULL", (payment_id,)).fetchone()["value"]
    if request.amount_pence > payment["amount_pence"] - allocated - unallocated_refunds:
        raise HTTPException(409, "Allocation exceeds the unallocated payment balance")
    outstanding = invoice["total_pence"] - invoice["paid_pence"] - invoice["credited_pence"]
    if request.amount_pence > outstanding:
        raise HTTPException(409, "Allocation exceeds the invoice outstanding balance")
    now = _now()
    cursor = conn.execute(
        "INSERT INTO payment_allocations(payment_id, invoice_id, amount_pence, created_at) VALUES (?, ?, ?, ?)",
        (payment_id, invoice["id"], request.amount_pence, now),
    )
    allocation_id = int(cursor.lastrowid)
    _post_journal(conn, "payment_allocation", allocation_id, f"Allocate payment {payment_id}", [
        ("2200", request.amount_pence, 0), ("1100", 0, request.amount_pence)
    ])
    conn.execute("UPDATE invoices SET paid_pence = paid_pence + ?, updated_at = ?, version = version + 1 WHERE id = ?", (request.amount_pence, now, invoice["id"]))
    _recompute_invoice(conn, invoice["id"])


def refund_payment(payment_id: int, request: PaymentRefundCreate, key: str) -> dict[str, Any]:
    action = f"payment:{payment_id}:refund"
    with _db() as conn:
        if cached := _cached(conn, action, key):
            return cached
        payment = _required(conn, "payments", payment_id)
        refunded = conn.execute("SELECT COALESCE(SUM(amount_pence), 0) value FROM payment_refunds WHERE payment_id = ?", (payment_id,)).fetchone()["value"]
        if request.amount_pence > payment["amount_pence"] - refunded:
            raise HTTPException(409, "Refund exceeds the remaining payment amount")
        invoice_id = request.invoice_id
        if invoice_id is None:
            allocated_invoices = conn.execute("SELECT DISTINCT invoice_id FROM payment_allocations WHERE payment_id = ?", (payment_id,)).fetchall()
            if len(allocated_invoices) == 1:
                invoice_id = int(allocated_invoices[0]["invoice_id"])
            elif len(allocated_invoices) > 1:
                raise HTTPException(409, "invoice_id is required for a payment allocated to multiple invoices")
        if invoice_id is not None:
            invoice = _required(conn, "invoices", invoice_id)
            allocated = conn.execute("SELECT COALESCE(SUM(amount_pence), 0) value FROM payment_allocations WHERE payment_id = ? AND invoice_id = ?", (payment_id, invoice_id)).fetchone()["value"]
            prior = conn.execute("SELECT COALESCE(SUM(amount_pence), 0) value FROM payment_refunds WHERE payment_id = ? AND invoice_id = ?", (payment_id, invoice_id)).fetchone()["value"]
            if request.amount_pence > allocated - prior:
                raise HTTPException(409, "Refund exceeds this invoice allocation")
        now = _now()
        cursor = conn.execute(
            "INSERT INTO payment_refunds(payment_id, invoice_id, amount_pence, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            (payment_id, invoice_id, request.amount_pence, request.reason, now),
        )
        refund_id = int(cursor.lastrowid)
        if invoice_id is None:
            lines = [("2200", request.amount_pence, 0), ("1200", 0, request.amount_pence)]
        else:
            lines = [("1100", request.amount_pence, 0), ("1200", 0, request.amount_pence)]
            conn.execute("UPDATE invoices SET paid_pence = paid_pence - ?, updated_at = ?, version = version + 1 WHERE id = ?", (request.amount_pence, now, invoice_id))
            _recompute_invoice(conn, invoice_id)
        _post_journal(conn, "payment_refund", refund_id, f"Refund payment {payment_id}", lines)
        return _remember(conn, action, key, _payment(conn, payment_id))


def _recompute_invoice(conn: sqlite3.Connection, invoice_id: int) -> None:
    invoice = _required(conn, "invoices", invoice_id)
    if invoice["status"] in {"Draft", "Void"}:
        return
    outstanding = invoice["total_pence"] - invoice["paid_pence"] - invoice["credited_pence"]
    if outstanding <= 0:
        status = "Paid"
    elif invoice["paid_pence"] or invoice["credited_pence"]:
        status = "Part-paid"
    elif invoice["due_on"] < date.today().isoformat():
        status = "Overdue"
    else:
        status = "Sent"
    conn.execute("UPDATE invoices SET status = ? WHERE id = ?", (status, invoice_id))
    if status == "Overdue" and invoice["status"] != "Overdue":
        current = _required(conn, "invoices", invoice_id)
        enqueue_automation_event(
            conn,
            "invoice.overdue",
            {
                **current,
                "type": "invoice",
                "amount_due_minor": max(current["total_pence"] - current["paid_pence"] - current["credited_pence"], 0),
            },
        )


def _refresh_overdue(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT * FROM invoices WHERE status = 'Sent' AND due_on < ?",
        (date.today().isoformat(),),
    ).fetchall()
    now = _now()
    for row in rows:
        conn.execute(
            "UPDATE invoices SET status = 'Overdue', updated_at = ?, version = version + 1 WHERE id = ? AND status = 'Sent'",
            (now, row["id"]),
        )
        current = _required(conn, "invoices", row["id"])
        enqueue_automation_event(
            conn,
            "invoice.overdue",
            {
                **current,
                "type": "invoice",
                "amount_due_minor": max(current["total_pence"] - current["paid_pence"] - current["credited_pence"], 0),
            },
        )


# Client success and reports
def upsert_client_success(request: ClientSuccessUpsert) -> dict[str, Any]:
    now = _now()
    if request.account_id is None:
        raise HTTPException(422, "account_id is required")
    with _db() as conn:
        current = conn.execute("SELECT * FROM client_success WHERE account_id = ?", (request.account_id,)).fetchone()
        values = request.model_dump(exclude={"version"})
        if current:
            if request.version is None or request.version != current["version"]:
                raise HTTPException(409, "Record was changed; refresh and retry")
            _versioned_update(conn, "client_success", current["id"], request.version, values)
            return _success_record(conn, request.account_id)
        cursor = conn.execute(
            "INSERT INTO client_success(account_id, manual_health, open_risks, onboarding_status, next_review_on, renewal_on, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (request.account_id, request.manual_health, request.open_risks, request.onboarding_status,
             _iso(request.next_review_on), _iso(request.renewal_on), request.notes, now, now),
        )
        return _success_record(conn, request.account_id)


def get_client_success(account_id: int) -> dict[str, Any]:
    with _db() as conn:
        return _success_record(conn, account_id)


def list_client_success(cursor: int | None = None, limit: int = 50) -> dict[str, Any]:
    with _db() as conn:
        result = _page(conn, "client_success", cursor, limit, "archived_at IS NULL")
        result["items"] = [_success_record(conn, item["account_id"]) for item in result["items"]]
        return result


def _success_record(conn: sqlite3.Connection, account_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM client_success WHERE account_id = ? AND archived_at IS NULL", (account_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Client success record not found")
    item = dict(row)
    account = conn.execute(
        "SELECT name, status, domain FROM accounts WHERE id = ? AND archived_at IS NULL",
        (account_id,),
    ).fetchone()
    if account:
        item.update(
            name=account["name"],
            account_name=account["name"],
            account_status=account["status"],
            account_domain=account["domain"],
        )
    reasons: list[str] = []
    watch_reasons: list[str] = []
    blocked = conn.execute("SELECT COUNT(*) value FROM projects WHERE account_id = ? AND status = 'Blocked' AND archived_at IS NULL", (account_id,)).fetchone()["value"]
    overdue = conn.execute(
        "SELECT COUNT(*) value FROM invoices WHERE account_id = ? AND due_on < ? AND status NOT IN ('Draft', 'Paid', 'Void')",
        (account_id, date.today().isoformat()),
    ).fetchone()["value"]
    if blocked:
        reasons.append("Blocked project")
    if overdue:
        reasons.append("Overdue invoice")
    if item["open_risks"]:
        reasons.append("Open client risk")
    latest_values = [
        conn.execute(
            "SELECT MAX(occurred_at) value FROM activities WHERE "
            "(entity_type='account' AND entity_id=?) OR "
            "(entity_type='opportunity' AND entity_id IN (SELECT id FROM opportunities WHERE account_id=?))",
            (account_id, account_id),
        ).fetchone()["value"],
        conn.execute(
            "SELECT MAX(starts_at) value FROM calendar_events WHERE entity_type='account' AND entity_id=?",
            (account_id,),
        ).fetchone()["value"],
        conn.execute(
            "SELECT MAX(t.last_message_at) value FROM gmail_threads t JOIN gmail_thread_links l ON l.thread_id=t.id "
            "WHERE l.entity_type='account' AND l.entity_id=?",
            (account_id,),
        ).fetchone()["value"],
    ]
    parsed_latest = []
    for value in latest_values:
        if not value:
            continue
        try:
            parsed_latest.append(datetime.fromisoformat(str(value).replace("Z", "+00:00")).date())
        except ValueError:
            continue
    if parsed_latest:
        inactive_days = (date.today() - max(parsed_latest)).days
        item["last_client_activity_on"] = max(parsed_latest).isoformat()
        item["inactive_days"] = max(inactive_days, 0)
        if inactive_days >= 60:
            reasons.append("No client activity for 60 days")
        elif inactive_days >= 30:
            watch_reasons.append("No client activity for 30 days")
    if item["next_review_on"] and item["next_review_on"] < date.today().isoformat():
        watch_reasons.append("Review overdue")
    if item["renewal_on"]:
        renewal_days = (date.fromisoformat(item["renewal_on"]) - date.today()).days
        item["renewal_days"] = renewal_days
        if 0 <= renewal_days <= 30:
            watch_reasons.append("Renewal due within 30 days")
    if item["manual_health"]:
        health = item["manual_health"]
    elif reasons:
        health = "At risk"
    elif watch_reasons:
        health = "Watch"
    else:
        health = "Healthy"
    item["computed_health"] = health
    item["health_reasons"] = reasons + watch_reasons
    return item


def finance_report() -> dict[str, Any]:
    with _db() as conn:
        _refresh_overdue(conn)
        totals = conn.execute(
            "SELECT COALESCE(SUM(total_pence), 0) invoiced, COALESCE(SUM(vat_pence), 0) output_vat, "
            "COALESCE(SUM(paid_pence), 0) paid, COALESCE(SUM(credited_pence), 0) credited, "
            "COALESCE(SUM(MAX(total_pence - paid_pence - credited_pence, 0)), 0) outstanding "
            "FROM invoices WHERE status NOT IN ('Draft', 'Void')"
        ).fetchone()
        credit_vat = conn.execute("SELECT COALESCE(SUM(vat_pence), 0) value FROM credit_notes WHERE status = 'Issued'").fetchone()["value"]
        input_vat = conn.execute(
            "SELECT COALESCE(SUM(debit_pence - credit_pence), 0) value "
            "FROM journal_lines WHERE account_code = '1300'"
        ).fetchone()["value"]
        aging = {"current": 0, "1_30": 0, "31_60": 0, "61_90": 0, "90_plus": 0}
        for row in conn.execute("SELECT due_on, total_pence - paid_pence - credited_pence outstanding FROM invoices WHERE status NOT IN ('Draft', 'Paid', 'Void')"):
            amount = max(int(row["outstanding"]), 0)
            days = (date.today() - date.fromisoformat(row["due_on"])).days
            bucket = "current" if days <= 0 else "1_30" if days <= 30 else "31_60" if days <= 60 else "61_90" if days <= 90 else "90_plus"
            aging[bucket] += amount
        return {
            "currency": "GBP",
            "invoiced_pence": int(totals["invoiced"]),
            "collected_pence": int(totals["paid"]),
            "credited_pence": int(totals["credited"]),
            "outstanding_pence": int(totals["outstanding"]),
            "vat": {
                "output_pence": int(totals["output_vat"]) - int(credit_vat),
                "input_pence": int(input_vat),
                "net_due_pence": int(totals["output_vat"]) - int(credit_vat) - int(input_vat),
            },
            "receivables_aging_pence": aging,
        }


def project_report() -> dict[str, Any]:
    with _db() as conn:
        items: list[dict[str, Any]] = []
        for row in conn.execute("SELECT * FROM projects WHERE archived_at IS NULL ORDER BY id"):
            project = dict(row)
            invoices = conn.execute("SELECT COALESCE(SUM(net_pence), 0) value FROM invoices WHERE project_id = ? AND status NOT IN ('Draft', 'Void')", (row["id"],)).fetchone()["value"]
            credits = conn.execute("SELECT COALESCE(SUM(c.net_pence), 0) value FROM credit_notes c JOIN invoices i ON i.id = c.invoice_id WHERE i.project_id = ? AND c.status = 'Issued'", (row["id"],)).fetchone()["value"]
            expenses = conn.execute("SELECT COALESCE(SUM(total_pence), 0) value FROM expenses WHERE project_id = ? AND archived_at IS NULL", (row["id"],)).fetchone()["value"]
            time = conn.execute("SELECT COALESCE(SUM(minutes), 0) minutes, COALESCE(SUM(CASE WHEN billable THEN minutes ELSE 0 END), 0) billable FROM time_entries WHERE project_id = ? AND archived_at IS NULL", (row["id"],)).fetchone()
            revenue = int(invoices) - int(credits)
            project.update(
                revenue_pence=revenue,
                expense_pence=int(expenses),
                margin_pence=revenue - int(expenses),
                time_minutes=int(time["minutes"]),
                billable_minutes=int(time["billable"]),
            )
            items.append(project)
        return {"items": items, "next_cursor": None}


def renewal_report(days: int = 90) -> dict[str, Any]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM client_success WHERE renewal_on IS NOT NULL AND archived_at IS NULL AND renewal_on BETWEEN ? AND date(?, '+' || ? || ' days') ORDER BY renewal_on",
            (date.today().isoformat(), date.today().isoformat(), min(max(days, 1), 365)),
        ).fetchall()
        return {"items": [_success_record(conn, row["account_id"]) for row in rows], "next_cursor": None}


def process_renewals(days: int, key: str) -> dict[str, Any]:
    action = f"renewals:process:{days}"
    cutoff = (date.today() + timedelta(days=days)).isoformat()
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if cached := _cached(conn, action, key):
            return cached
        rows = conn.execute(
            "SELECT cs.*, a.name account_name FROM client_success cs "
            "LEFT JOIN accounts a ON a.id = cs.account_id "
            "WHERE cs.archived_at IS NULL AND cs.renewal_on IS NOT NULL "
            "AND date(cs.renewal_on) <= date(?) ORDER BY cs.renewal_on, cs.id",
            (cutoff,),
        ).fetchall()
        items: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        created_count = 0
        now = _now()
        stage = conn.execute(
            "SELECT id, probability_bps FROM pipeline_stages "
            "WHERE kind = 'open' AND archived_at IS NULL ORDER BY position LIMIT 1"
        ).fetchone()
        if stage is None:
            raise HTTPException(409, "An open pipeline stage is required")
        for row in rows:
            if not row["account_name"]:
                skipped.append({"client_success_id": row["id"], "reason": "Account not found"})
                continue
            source_key = f"{row['id']}:{row['renewal_on']}"
            link = conn.execute(
                "SELECT target_id FROM lifecycle_links WHERE kind = 'renewal_opportunity' AND source_key = ?",
                (source_key,),
            ).fetchone()
            opportunity = conn.execute(
                "SELECT * FROM opportunities WHERE id = ?",
                (link["target_id"],),
            ).fetchone() if link else None
            if opportunity is None:
                opportunity = conn.execute(
                    "SELECT * FROM opportunities WHERE account_id = ? AND type = 'Renewal' "
                    "AND expected_close_date = ? AND archived_at IS NULL ORDER BY id LIMIT 1",
                    (row["account_id"], row["renewal_on"]),
                ).fetchone()
            created = False
            if opportunity is None:
                prior = conn.execute(
                    "SELECT value_minor, primary_contact_id FROM opportunities "
                    "WHERE account_id = ? AND status = 'Won' AND archived_at IS NULL "
                    "ORDER BY won_at DESC, id DESC LIMIT 1",
                    (row["account_id"],),
                ).fetchone()
                value_minor = int(prior["value_minor"]) if prior else 0
                contact_id = prior["primary_contact_id"] if prior else None
                title = f"{row['account_name']} renewal"
                cursor = conn.execute(
                    "INSERT INTO opportunities(account_id, primary_contact_id, stage_id, type, title, status, "
                    "value_minor, probability_bps, expected_close_date, source, next_action, notes, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'Renewal', ?, 'Open', ?, ?, ?, 'Client success', "
                    "'Prepare renewal review', '', ?, ?)",
                    (row["account_id"], contact_id, stage["id"], title, value_minor,
                     stage["probability_bps"], row["renewal_on"], now, now),
                )
                opportunity = conn.execute(
                    "SELECT * FROM opportunities WHERE id = ?", (cursor.lastrowid,)
                ).fetchone()
                index_record(conn, "opportunity", opportunity["id"], title, row["account_name"], "")
                conn.execute(
                    "INSERT INTO activities(entity_type, entity_id, kind, subject, body, occurred_at, created_at) "
                    "VALUES ('opportunity', ?, 'system', 'Renewal deal created', ?, ?, ?)",
                    (opportunity["id"], f"Renewal due {row['renewal_on']}", now, now),
                )
                created = True
                created_count += 1
            conn.execute(
                "INSERT OR IGNORE INTO lifecycle_links(kind, source_key, target_type, target_id, created_at) "
                "VALUES ('renewal_opportunity', ?, 'opportunity', ?, ?)",
                (source_key, opportunity["id"], now),
            )
            item = dict(opportunity)
            item.update(client_success_id=row["id"], renewal_on=row["renewal_on"], created=created)
            items.append(item)
        return _remember(conn, action, key, {
            "items": items,
            "created_count": created_count,
            "existing_count": len(items) - created_count,
            "skipped": skipped,
        })


def ledger_report(cursor: int | None = None, limit: int = 50, query: str = "", source_type: str = "") -> dict[str, Any]:
    with _db() as conn:
        where, params = "1=1", ()
        if query.strip():
            needle = f"%{query.strip()}%"
            where += " AND (description LIKE ? OR source_type LIKE ? OR CAST(source_id AS TEXT) LIKE ?)"
            params += (needle, needle, needle)
        if source_type.strip():
            where += " AND source_type LIKE ?"
            params += (f"{source_type.strip()}%",)
        result = _page(conn, "journals", cursor, limit, where, params)
        for item in result["items"]:
            item["lines"] = [dict(row) for row in conn.execute("SELECT * FROM journal_lines WHERE journal_id = ? ORDER BY id", (item["id"],))]
            item["debit_pence"] = sum(line["debit_pence"] for line in item["lines"])
            item["credit_pence"] = sum(line["credit_pence"] for line in item["lines"])
            item["linked_type"], item["linked_id"], item["payment_status"] = "", None, ""
            source_type, source_id = item["source_type"], item["source_id"]
            if source_type.startswith("invoice"):
                invoice = conn.execute("SELECT id, status FROM invoices WHERE id=?", (source_id,)).fetchone()
                if invoice:
                    item.update(linked_type="invoice", linked_id=invoice["id"], payment_status=invoice["status"])
            elif source_type == "credit_note":
                item.update(linked_type="credit_note", linked_id=source_id)
            elif source_type == "payment_receipt":
                item.update(linked_type="payment", linked_id=source_id, payment_status="Recorded")
            elif source_type == "payment_allocation":
                allocation = conn.execute(
                    "SELECT a.payment_id, i.status invoice_status FROM payment_allocations a "
                    "JOIN invoices i ON i.id=a.invoice_id WHERE a.id=?",
                    (source_id,),
                ).fetchone()
                if allocation:
                    item.update(linked_type="payment", linked_id=allocation["payment_id"], payment_status=allocation["invoice_status"])
            elif source_type == "payment_refund":
                refund = conn.execute("SELECT payment_id FROM payment_refunds WHERE id=?", (source_id,)).fetchone()
                if refund:
                    item.update(linked_type="payment", linked_id=refund["payment_id"], payment_status="Refunded")
            elif source_type.startswith("expense"):
                item.update(linked_type="expense", linked_id=source_id)
        balances = [dict(row) for row in conn.execute(
            "SELECT a.code, a.name, a.kind, COALESCE(SUM(l.debit_pence), 0) debit_pence, COALESCE(SUM(l.credit_pence), 0) credit_pence "
            "FROM ledger_accounts a LEFT JOIN journal_lines l ON l.account_code = a.code GROUP BY a.code ORDER BY a.code"
        )]
        result["balances"] = balances
        return result


def reports_overview() -> dict[str, Any]:
    return {
        "finance": finance_report(),
        "projects": project_report()["items"],
        "renewals": renewal_report()["items"],
    }


# Public archive helpers keep financial records out of the generic archive path.
archive_catalog_item = lambda item_id, version: _archive("catalog_items", item_id, version)
archive_project = lambda item_id, version: _archive("projects", item_id, version)
archive_milestone = lambda item_id, version: _archive("milestones", item_id, version)
archive_time_entry = lambda item_id, version: _archive("time_entries", item_id, version)
archive_proposal = lambda item_id, version: _archive("proposals", item_id, version)
archive_contract = lambda item_id, version: _archive("contracts", item_id, version)
