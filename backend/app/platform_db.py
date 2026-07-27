from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


APP_NAME = "CRMWorkspace"
REPO_BACKEND = Path(__file__).resolve().parents[1]
LEGACY_DB_PATH = REPO_BACKEND / "crm.sqlite3"
_BOOTSTRAP_LOCK = threading.Lock()
_BOOTSTRAPPED: set[Path] = set()
_AUTOMATION_CONTEXT = threading.local()


def utc_now() -> datetime:
    return datetime.now(UTC)


def data_root() -> Path:
    configured = os.getenv("CRM_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    local = os.getenv("LOCALAPPDATA", "").strip()
    if local:
        return Path(local) / APP_NAME
    return REPO_BACKEND / ".crm-data"


def db_path() -> Path:
    configured = os.getenv("CRM_DB_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return data_root() / "crm.sqlite3"


def documents_root() -> Path:
    return data_root() / "documents"


def backups_root() -> Path:
    return data_root() / "backups"


def _raw_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def bootstrap() -> Path:
    path = db_path()
    with _BOOTSTRAP_LOCK:
        if path in _BOOTSTRAPPED:
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        documents_root().mkdir(parents=True, exist_ok=True)
        backups_root().mkdir(parents=True, exist_ok=True)

        if not path.exists() and not os.getenv("CRM_DB_PATH") and LEGACY_DB_PATH.exists():
            source = sqlite3.connect(LEGACY_DB_PATH)
            target = sqlite3.connect(path)
            try:
                source.backup(target)
            finally:
                source.close()
                target.close()

        conn = _raw_connect(path)
        try:
            install_core_schema(conn)
            migrate_legacy_data(conn)
            normalize_migrated_data(conn)
            upgrade_business_profile(conn)
            _install_optional_schemas(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        _BOOTSTRAPPED.add(path)
    return path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = bootstrap()
    conn = _raw_connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reset_bootstrap_for_tests() -> None:
    with _BOOTSTRAP_LOCK:
        _BOOTSTRAPPED.clear()


def install_core_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS number_sequences (
            prefix TEXT PRIMARY KEY,
            year INTEGER NOT NULL,
            next_value INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS business_profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            legal_name TEXT NOT NULL DEFAULT '',
            trading_name TEXT NOT NULL DEFAULT '',
            company_number TEXT NOT NULL DEFAULT '',
            vat_registered INTEGER NOT NULL DEFAULT 0,
            vat_number TEXT NOT NULL DEFAULT '',
            vat_scheme TEXT NOT NULL DEFAULT '',
            vat_effective_from TEXT,
            vat_effective_to TEXT,
            tax_codes_approved INTEGER NOT NULL DEFAULT 0,
            registered_address_json TEXT NOT NULL DEFAULT '{}',
            invoice_email TEXT NOT NULL DEFAULT '',
            invoice_phone TEXT NOT NULL DEFAULT '',
            bank_details TEXT NOT NULL DEFAULT '',
            currency TEXT NOT NULL DEFAULT 'GBP',
            timezone TEXT NOT NULL DEFAULT 'Europe/London',
            default_vat_bps INTEGER NOT NULL DEFAULT 2000,
            default_payment_terms_days INTEGER NOT NULL DEFAULT 14,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            legal_name TEXT NOT NULL DEFAULT '',
            domain TEXT NOT NULL DEFAULT '',
            website TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            billing_email TEXT NOT NULL DEFAULT '',
            company_number TEXT NOT NULL DEFAULT '',
            vat_number TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            payment_terms_days INTEGER NOT NULL DEFAULT 14,
            status TEXT NOT NULL DEFAULT 'Prospect',
            health_status TEXT NOT NULL DEFAULT 'Healthy',
            health_score INTEGER NOT NULL DEFAULT 100,
            renewal_date TEXT,
            notes TEXT NOT NULL DEFAULT '',
            custom_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            archived_at TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_domain
            ON accounts(lower(domain)) WHERE domain <> '' AND archived_at IS NULL;
        CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts(status, archived_at);
        CREATE TABLE IF NOT EXISTS account_roles (
            account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK(role IN ('prospect','client','supplier','partner')),
            PRIMARY KEY(account_id, role)
        );
        CREATE TABLE IF NOT EXISTS addresses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            kind TEXT NOT NULL DEFAULT 'billing',
            line1 TEXT NOT NULL DEFAULT '',
            line2 TEXT NOT NULL DEFAULT '',
            city TEXT NOT NULL DEFAULT '',
            region TEXT NOT NULL DEFAULT '',
            postcode TEXT NOT NULL DEFAULT '',
            country_code TEXT NOT NULL DEFAULT 'GB',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
            first_name TEXT NOT NULL DEFAULT '',
            last_name TEXT NOT NULL DEFAULT '',
            display_name TEXT NOT NULL,
            job_title TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            mobile TEXT NOT NULL DEFAULT '',
            preferred_channel TEXT NOT NULL DEFAULT 'Email',
            source TEXT NOT NULL DEFAULT '',
            lawful_basis TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Active',
            last_contact_at TEXT,
            email_opt_out_at TEXT,
            notes TEXT NOT NULL DEFAULT '',
            custom_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            archived_at TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_email
            ON contacts(lower(email)) WHERE email <> '' AND archived_at IS NULL;
        CREATE INDEX IF NOT EXISTS idx_contacts_account ON contacts(account_id, archived_at);
        CREATE TABLE IF NOT EXISTS sales_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
            contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
            title TEXT NOT NULL,
            company TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'Manual',
            status TEXT NOT NULL DEFAULT 'New',
            score INTEGER NOT NULL DEFAULT 0,
            estimated_value_minor INTEGER NOT NULL DEFAULT 0,
            next_action TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            converted_opportunity_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            archived_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sales_leads_status ON sales_leads(status, archived_at);
        CREATE TABLE IF NOT EXISTS pipeline_stages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            position INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('open','won','lost')),
            probability_bps INTEGER NOT NULL CHECK(probability_bps BETWEEN 0 AND 10000),
            color TEXT NOT NULL DEFAULT 'blue',
            archived_at TEXT
        );
        CREATE TABLE IF NOT EXISTS discovery_runs (
            id TEXT PRIMARY KEY,
            recipe_json TEXT NOT NULL,
            state TEXT NOT NULL,
            phase TEXT NOT NULL DEFAULT 'Queued',
            message TEXT NOT NULL DEFAULT '',
            progress INTEGER NOT NULL DEFAULT 0,
            result_json TEXT NOT NULL DEFAULT '[]',
            error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS discovery_recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            config_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            archived_at TEXT
        );
        CREATE TABLE IF NOT EXISTS tender_notices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discovery_run_id TEXT REFERENCES discovery_runs(id) ON DELETE SET NULL,
            buyer_account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
            buyer_contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
            linked_opportunity_id INTEGER,
            title TEXT NOT NULL,
            buyer_name TEXT NOT NULL DEFAULT '',
            portal_name TEXT NOT NULL DEFAULT '',
            notice_reference TEXT NOT NULL DEFAULT '',
            contract_url TEXT NOT NULL DEFAULT '',
            contract_value_text TEXT NOT NULL DEFAULT '',
            estimated_value_minor INTEGER NOT NULL DEFAULT 0,
            deadline TEXT,
            procurement_stage TEXT NOT NULL DEFAULT '',
            contract_status TEXT NOT NULL DEFAULT '',
            availability_status TEXT NOT NULL DEFAULT 'Unverified',
            availability_reason TEXT NOT NULL DEFAULT '',
            availability_checked_at TEXT,
            niche TEXT NOT NULL DEFAULT '',
            region TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            cpv_json TEXT NOT NULL DEFAULT '[]',
            confidence_score INTEGER NOT NULL DEFAULT 0,
            priority_score INTEGER NOT NULL DEFAULT 0,
            priority_reasons_json TEXT NOT NULL DEFAULT '[]',
            outreach_angle TEXT NOT NULL DEFAULT '',
            dedupe_key TEXT NOT NULL,
            seen_count INTEGER NOT NULL DEFAULT 1,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            triage_status TEXT NOT NULL DEFAULT 'New',
            snoozed_until TEXT,
            rejection_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            archived_at TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tenders_dedupe ON tender_notices(dedupe_key);
        CREATE INDEX IF NOT EXISTS idx_tenders_triage ON tender_notices(triage_status, deadline, archived_at);
        CREATE INDEX IF NOT EXISTS idx_tenders_deadline ON tender_notices(deadline, archived_at, triage_status);
        CREATE TABLE IF NOT EXISTS tender_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tender_id INTEGER NOT NULL REFERENCES tender_notices(id) ON DELETE CASCADE,
            url TEXT NOT NULL,
            source_kind TEXT NOT NULL DEFAULT 'notice',
            content_hash TEXT NOT NULL DEFAULT '',
            is_primary INTEGER NOT NULL DEFAULT 0,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            UNIQUE(tender_id, url)
        );
        CREATE TABLE IF NOT EXISTS opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
            primary_contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
            tender_id INTEGER REFERENCES tender_notices(id) ON DELETE SET NULL,
            stage_id INTEGER NOT NULL REFERENCES pipeline_stages(id) ON DELETE RESTRICT,
            type TEXT NOT NULL DEFAULT 'New business',
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Open',
            value_minor INTEGER NOT NULL DEFAULT 0,
            probability_bps INTEGER NOT NULL DEFAULT 1000,
            expected_close_date TEXT,
            source TEXT NOT NULL DEFAULT '',
            next_action TEXT NOT NULL DEFAULT '',
            loss_reason TEXT NOT NULL DEFAULT '',
            won_at TEXT,
            lost_at TEXT,
            notes TEXT NOT NULL DEFAULT '',
            custom_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            archived_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_opportunities_stage ON opportunities(stage_id, archived_at);
        CREATE INDEX IF NOT EXISTS idx_opportunities_account ON opportunities(account_id, archived_at);
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            color TEXT NOT NULL DEFAULT 'blue',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS entity_tags (
            tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            PRIMARY KEY(tag_id, entity_type, entity_id)
        );
        CREATE TABLE IF NOT EXISTS custom_fields (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            name TEXT NOT NULL,
            field_type TEXT NOT NULL,
            options_json TEXT NOT NULL DEFAULT '[]',
            required INTEGER NOT NULL DEFAULT 0,
            position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            archived_at TEXT,
            UNIQUE(entity_type, name)
        );
        CREATE TABLE IF NOT EXISTS custom_field_values (
            field_id INTEGER NOT NULL REFERENCES custom_fields(id) ON DELETE CASCADE,
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(field_id, entity_type, entity_id)
        );
        CREATE TABLE IF NOT EXISTS saved_views (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            name TEXT NOT NULL,
            config_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            archived_at TEXT
        );
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL DEFAULT '',
            occurred_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_activities_entity
            ON activities(entity_type, entity_id, occurred_at DESC);
        CREATE TABLE IF NOT EXISTS work_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL DEFAULT '',
            entity_id INTEGER,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Open',
            priority TEXT NOT NULL DEFAULT 'Medium',
            due_at TEXT,
            completed_at TEXT,
            originating_rule_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            archived_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_work_tasks_due ON work_tasks(status, due_at, archived_at);
        CREATE INDEX IF NOT EXISTS idx_work_tasks_dashboard ON work_tasks(due_at, archived_at, status);
        CREATE TABLE IF NOT EXISTS calendar_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL DEFAULT '',
            entity_id INTEGER,
            title TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            starts_at TEXT NOT NULL,
            ends_at TEXT NOT NULL,
            timezone TEXT NOT NULL DEFAULT 'Europe/London',
            all_day INTEGER NOT NULL DEFAULT 0,
            recurrence_json TEXT NOT NULL DEFAULT '{}',
            google_event_id TEXT NOT NULL DEFAULT '',
            google_etag TEXT NOT NULL DEFAULT '',
            google_html_link TEXT NOT NULL DEFAULT '',
            sync_state TEXT NOT NULL DEFAULT 'Local',
            local_updated_at TEXT,
            remote_updated_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            archived_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_calendar_starts ON calendar_events(starts_at, archived_at);
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            before_json TEXT NOT NULL DEFAULT '{}',
            after_json TEXT NOT NULL DEFAULT '{}',
            request_id TEXT NOT NULL DEFAULT '',
            occurred_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS import_jobs (
            id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            filename TEXT NOT NULL,
            state TEXT NOT NULL,
            report_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS export_jobs (
            id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            state TEXT NOT NULL,
            path TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            completed_at TEXT
        );
        """
    )
    now = utc_now().isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO business_profile (id, created_at, updated_at) VALUES (1, ?, ?)",
        (now, now),
    )
    stages = [
        ("Discovery", 10, "open", 1000, "cyan"),
        ("Qualified", 20, "open", 2500, "blue"),
        ("Contacted", 30, "open", 3500, "blue"),
        ("Meeting", 40, "open", 5000, "purple"),
        ("Proposal", 50, "open", 7000, "yellow"),
        ("Negotiation", 60, "open", 8500, "orange"),
        ("Contract", 70, "open", 9000, "purple"),
        ("Won", 80, "won", 10000, "green"),
        ("Lost", 90, "lost", 0, "red"),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO pipeline_stages (name, position, kind, probability_bps, color) VALUES (?, ?, ?, ?, ?)",
        stages,
    )
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, name, applied_at) VALUES (1, 'core-v1', ?)",
        (now,),
    )
    _install_fts(conn)


def _install_fts(conn: sqlite3.Connection) -> None:
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5("
            "entity_type UNINDEXED, entity_id UNINDEXED, title, subtitle, detail)"
        )
    except sqlite3.OperationalError:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS search_fts (
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                title TEXT NOT NULL,
                subtitle TEXT NOT NULL,
                detail TEXT NOT NULL
            )"""
        )


def _install_optional_schemas(conn: sqlite3.Connection) -> None:
    for module_name in (
        "app.operations.schema",
        "app.integrations_v1.schema",
        "app.communications.schema",
        "app.workflows_v1.schema",
    ):
        try:
            module = __import__(module_name, fromlist=["install_schema"])
        except ImportError:
            continue
        installer = getattr(module, "install_schema", None)
        if installer:
            installer(conn)


def next_number(conn: sqlite3.Connection, prefix: str) -> str:
    cleaned = "".join(char for char in prefix.upper() if char.isalnum() or char == "-").strip("-")
    if not cleaned:
        raise ValueError("A numbering prefix is required")
    year = utc_now().year
    row = conn.execute(
        "SELECT year, next_value FROM number_sequences WHERE prefix = ?",
        (cleaned,),
    ).fetchone()
    if row is None:
        value = 1
        conn.execute(
            "INSERT INTO number_sequences (prefix, year, next_value) VALUES (?, ?, ?)",
            (cleaned, year, 2),
        )
    else:
        value = 1 if row["year"] != year else row["next_value"]
        conn.execute(
            "UPDATE number_sequences SET year = ?, next_value = ? WHERE prefix = ?",
            (year, value + 1, cleaned),
        )
    return f"{cleaned}-{year}-{value:04d}"


def write_audit(
    conn: sqlite3.Connection,
    action: str,
    entity_type: str,
    entity_id: int | str,
    before: Any = None,
    after: Any = None,
    request_id: str = "",
) -> None:
    conn.execute(
        """INSERT INTO audit_log
           (action, entity_type, entity_id, before_json, after_json, request_id, occurred_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            action,
            entity_type,
            str(entity_id),
            json.dumps(before or {}, default=str),
            json.dumps(after or {}, default=str),
            request_id,
            utc_now().isoformat(),
        ),
    )


def enqueue_automation_event(
    conn: sqlite3.Connection,
    trigger_name: str,
    record: dict[str, Any],
    *,
    correlation_id: str = "",
) -> str | None:
    """Persist a domain automation event in the business transaction.

    Integrations are installed after the platform schema during first bootstrap,
    so an absent jobs table is intentionally treated as "automation not ready".
    """
    if getattr(_AUTOMATION_CONTEXT, "suppressed", False):
        return None
    record_type = str(record.get("type") or record.get("record_type") or "record")
    record_id = str(record.get("id") or record.get("record_id") or "unknown")
    version = str(record.get("version") or record.get("updated_at") or "1")
    key = correlation_id or f"automation:{trigger_name}:{record_type}:{record_id}:{version}"
    job_id = str(uuid.uuid4())
    now = utc_now().isoformat()
    payload = {
        "trigger_name": trigger_name,
        "record": record,
        "correlation_id": key,
    }
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO integration_jobs
                (id, kind, payload_json, state, priority, available_at, max_attempts,
                 idempotency_key, requires_reconciliation, reconciliation_state,
                 created_at, updated_at)
            VALUES (?, 'automation.event', ?, 'queued', 10, ?, 3, ?, 0,
                    'not_required', ?, ?)
            """,
            (job_id, json.dumps(payload, default=str), now, key, now, now),
        )
        row = conn.execute(
            "SELECT id FROM integration_jobs WHERE kind='automation.event' AND idempotency_key=?",
            (key,),
        ).fetchone()
        return str(row["id"]) if row else None
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
        return None


@contextmanager
def suppress_automation_events() -> Iterator[None]:
    previous = getattr(_AUTOMATION_CONTEXT, "suppressed", False)
    _AUTOMATION_CONTEXT.suppressed = True
    try:
        yield
    finally:
        _AUTOMATION_CONTEXT.suppressed = previous


def index_record(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: int | str,
    title: str,
    subtitle: str = "",
    detail: str = "",
) -> None:
    try:
        conn.execute(
            "DELETE FROM search_fts WHERE entity_type = ? AND entity_id = ?",
            (entity_type, str(entity_id)),
        )
        conn.execute(
            "INSERT INTO search_fts (entity_type, entity_id, title, subtitle, detail) VALUES (?, ?, ?, ?, ?)",
            (entity_type, str(entity_id), title, subtitle, detail),
        )
    except sqlite3.OperationalError:
        return


def rebuild_search_index(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM search_fts")
    for row in conn.execute("SELECT id, name, domain, notes FROM accounts WHERE archived_at IS NULL"):
        index_record(conn, "account", row["id"], row["name"], row["domain"], row["notes"])
    for row in conn.execute(
        "SELECT id, display_name, email, job_title FROM contacts WHERE archived_at IS NULL"
    ):
        index_record(conn, "contact", row["id"], row["display_name"], row["email"], row["job_title"])
    for row in conn.execute(
        "SELECT id, title, company, notes FROM sales_leads WHERE archived_at IS NULL"
    ):
        index_record(conn, "lead", row["id"], row["title"], row["company"], row["notes"])
    for row in conn.execute(
        "SELECT id, title, buyer_name, outreach_angle FROM tender_notices WHERE archived_at IS NULL"
    ):
        index_record(conn, "tender", row["id"], row["title"], row["buyer_name"], row["outreach_angle"])
    for row in conn.execute(
        "SELECT id, title, source, notes FROM opportunities WHERE archived_at IS NULL"
    ):
        index_record(conn, "opportunity", row["id"], row["title"], row["source"], row["notes"])


def migrate_legacy_data(conn: sqlite3.Connection) -> None:
    marker = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version = 2"
    ).fetchone()
    if marker:
        return
    now = utc_now().isoformat()
    if _table_exists(conn, "clients"):
        for row in conn.execute("SELECT * FROM clients ORDER BY id"):
            payload = _row_payload(row)
            company = _known(payload.get("company"))
            person = _known(payload.get("name")) or company or f"Legacy contact {row['id']}"
            account_name = company or person
            account_id = _find_or_create_account(
                conn,
                account_name,
                domain=_domain(payload.get("website")),
                website=_known(payload.get("website")),
                source=_known(payload.get("source")) or "Legacy",
                status=_known(payload.get("status")) or "Prospect",
                notes=_known(payload.get("notes")),
                now=now,
            )
            contact_id = _find_or_create_contact(
                conn,
                account_id,
                person,
                _known(payload.get("email")),
                _known(payload.get("phone")),
                _known(payload.get("source")) or "Legacy",
                now,
            )
            next_action = _known(payload.get("next_action"))
            if next_action:
                conn.execute(
                    """INSERT INTO work_tasks
                       (entity_type, entity_id, title, status, priority, created_at, updated_at)
                       VALUES ('contact', ?, ?, 'Open', 'Medium', ?, ?)""",
                    (contact_id, next_action, now, now),
                )
            notes = _known(payload.get("notes"))
            if notes:
                _activity(conn, "contact", contact_id, "note", "Legacy note", notes, now)

    if _table_exists(conn, "leads"):
        for row in conn.execute("SELECT * FROM leads ORDER BY id"):
            payload = _row_payload(row)
            title = _first_known(payload.get("contract_title"), payload.get("name"), f"Legacy opportunity {row['id']}")
            buyer = _first_known(payload.get("buyer_name"), payload.get("company"), title)
            account_id = _find_or_create_account(
                conn,
                buyer,
                domain=_first_known(payload.get("company_domain"), _domain(payload.get("buyer_website"))),
                website=_first_known(payload.get("buyer_website"), payload.get("website")),
                source=_known(payload.get("source")) or "Legacy tender",
                status="Prospect",
                notes="",
                now=now,
            )
            contact_name = _first_known(payload.get("contact_name"), payload.get("name"))
            contact_email = _first_known(payload.get("contact_email"), payload.get("email"))
            contact_id = None
            if contact_name or contact_email:
                contact_id = _find_or_create_contact(
                    conn,
                    account_id,
                    contact_name or contact_email,
                    contact_email,
                    _known(payload.get("contact_phone")),
                    "Legacy tender",
                    now,
                )
            dedupe = _first_known(payload.get("dedupe_key"), payload.get("contract_url"), f"legacy-{row['id']}")
            tender_id = conn.execute(
                "SELECT id FROM tender_notices WHERE dedupe_key = ?", (dedupe,)
            ).fetchone()
            if tender_id:
                tender_pk = tender_id["id"]
            else:
                cursor = conn.execute(
                    """INSERT INTO tender_notices
                       (buyer_account_id, buyer_contact_id, title, buyer_name, portal_name,
                        contract_url, contract_value_text, estimated_value_minor, deadline,
                        procurement_stage, contract_status, availability_status,
                        availability_reason, confidence_score, priority_score,
                        priority_reasons_json, outreach_angle, dedupe_key, seen_count,
                        first_seen_at, last_seen_at, triage_status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        account_id,
                        contact_id,
                        title,
                        buyer,
                        _known(payload.get("portal_name")),
                        _first_known(payload.get("contract_url"), payload.get("website")),
                        _known(payload.get("contract_value")),
                        int(payload.get("estimated_value") or 0) * 100,
                        _nullable(payload.get("deadline")),
                        _known(payload.get("procurement_stage")),
                        _known(payload.get("contract_status")),
                        _known(payload.get("availability_status")) or "Unverified",
                        _known(payload.get("availability_reason")),
                        int(payload.get("confidence_score") or 0),
                        int(payload.get("priority_score") or 0),
                        json.dumps(payload.get("priority_reasons") or []),
                        _known(payload.get("outreach_angle")),
                        dedupe,
                        int(payload.get("seen_count") or 1),
                        _first_known(payload.get("first_seen_at"), now),
                        _first_known(payload.get("last_seen_at"), now),
                        _legacy_tender_status(payload.get("status")),
                        now,
                        now,
                    ),
                )
                tender_pk = cursor.lastrowid
                for source in _legacy_sources(payload):
                    conn.execute(
                        """INSERT OR IGNORE INTO tender_sources
                           (tender_id, url, source_kind, is_primary, first_seen_at, last_seen_at)
                           VALUES (?, ?, 'notice', ?, ?, ?)""",
                        (tender_pk, source, int(source == _known(payload.get("contract_url"))), now, now),
                    )
            stage_name = _legacy_stage(payload.get("status"))
            stage = conn.execute("SELECT id, probability_bps, kind FROM pipeline_stages WHERE name = ?", (stage_name,)).fetchone()
            cursor = conn.execute(
                """INSERT INTO opportunities
                   (account_id, primary_contact_id, tender_id, stage_id, type, title, status,
                    value_minor, probability_bps, expected_close_date, source, next_action,
                    loss_reason, won_at, lost_at, notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'Tender', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    account_id,
                    contact_id,
                    tender_pk,
                    stage["id"],
                    title,
                    "Won" if stage["kind"] == "won" else "Lost" if stage["kind"] == "lost" else "Open",
                    int(payload.get("estimated_value") or 0) * 100,
                    stage["probability_bps"],
                    _nullable(payload.get("deadline")),
                    _known(payload.get("source")) or "Legacy tender",
                    _known(payload.get("next_action")),
                    "Rejected during legacy review" if stage_name == "Lost" else "",
                    now if stage_name == "Won" else None,
                    now if stage_name == "Lost" else None,
                    _known(payload.get("manual_notes")),
                    now,
                    now,
                ),
            )
            opportunity_id = cursor.lastrowid
            conn.execute(
                "UPDATE tender_notices SET linked_opportunity_id = ? WHERE id = ?",
                (opportunity_id, tender_pk),
            )
            notes = _known(payload.get("manual_notes"))
            if notes:
                _activity(conn, "opportunity", opportunity_id, "note", "Legacy note", notes, now)

    if _table_exists(conn, "notes"):
        for row in conn.execute("SELECT * FROM notes ORDER BY id"):
            # Legacy numeric links cannot always be mapped losslessly; retain them as provenance.
            _activity(
                conn,
                f"legacy_{row['related_type']}",
                int(row["related_id"]),
                "note",
                "Imported note",
                str(row["body"]),
                str(row["created_at"] or now),
            )
    if _table_exists(conn, "tasks"):
        for row in conn.execute("SELECT * FROM tasks ORDER BY id"):
            payload = _row_payload(row)
            conn.execute(
                """INSERT INTO work_tasks
                   (entity_type, entity_id, title, description, status, priority, due_at,
                    completed_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"legacy_{_known(payload.get('related_type')) or 'record'}",
                    payload.get("related_id"),
                    _known(payload.get("title")) or f"Imported task {row['id']}",
                    _known(payload.get("notes")),
                    _known(payload.get("status")) or "Open",
                    _known(payload.get("priority")) or "Medium",
                    _nullable(payload.get("due_date")),
                    now if _known(payload.get("status")).lower() == "done" else None,
                    _first_known(payload.get("created_at"), now),
                    _first_known(payload.get("updated_at"), now),
                ),
            )
    if _table_exists(conn, "calendar_items"):
        for row in conn.execute("SELECT * FROM calendar_items ORDER BY id"):
            payload = _row_payload(row)
            date = _known(payload.get("date"))
            start = _known(payload.get("start_time")) or "09:00:00"
            end = _known(payload.get("end_time")) or "10:00:00"
            if not date:
                continue
            conn.execute(
                """INSERT INTO calendar_events
                   (entity_type, entity_id, title, body, starts_at, ends_at, created_at, updated_at)
                   VALUES ('legacy_contact', ?, ?, ?, ?, ?, ?, ?)""",
                (
                    payload.get("related_client_id"),
                    _known(payload.get("title")) or "Imported event",
                    _known(payload.get("notes")),
                    f"{date}T{start}",
                    f"{date}T{end}",
                    now,
                    now,
                ),
            )
    rebuild_search_index(conn)
    conn.execute(
        "INSERT INTO schema_migrations (version, name, applied_at) VALUES (2, 'legacy-transform', ?)",
        (now,),
    )


def normalize_migrated_data(conn: sqlite3.Connection) -> None:
    """Reconcile legacy money/provenance, then retire duplicate source tables."""
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version = 3").fetchone():
        return
    now = utc_now().isoformat()
    if _table_exists(conn, "tender_notices"):
        for row in conn.execute(
            "SELECT id, linked_opportunity_id, contract_value_text, estimated_value_minor, outreach_angle FROM tender_notices"
        ).fetchall():
            corrected = _contract_value_minor(
                str(row["contract_value_text"] or ""), int(row["estimated_value_minor"] or 0)
            )
            angle = " | ".join(
                part.strip()
                for part in str(row["outreach_angle"] or "").split("|")
                if part.strip() and "upsert" not in part.lower() and "dry run" not in part.lower()
            )
            conn.execute(
                "UPDATE tender_notices SET estimated_value_minor=?, outreach_angle=?, updated_at=? WHERE id=?",
                (corrected, angle, now, row["id"]),
            )
            if row["linked_opportunity_id"] is not None:
                conn.execute(
                    "UPDATE opportunities SET value_minor=?, updated_at=? WHERE id=?",
                    (corrected, now, row["linked_opportunity_id"]),
                )
    # The online backup retains the exact pre-rebuild store. These tables would
    # otherwise create a second writable source of truth inside the new database.
    for table in ("calendar_items", "notes", "tasks", "clients", "leads", "meta"):
        if _table_exists(conn, table):
            conn.execute(f'DROP TABLE "{table}"')
    rebuild_search_index(conn)
    conn.execute(
        "INSERT INTO schema_migrations (version, name, applied_at) VALUES (3, 'normalized-cutover', ?)",
        (now,),
    )


def _contract_value_minor(text: str, fallback: int) -> int:
    normalized = text.replace(",", "")
    amounts: list[float] = []
    for raw, suffix in re.findall(r"(?<![A-Za-z0-9])([0-9]+(?:\.[0-9]+)?)\s*([kKmMbB]?)", normalized):
        value = float(raw)
        multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix.lower(), 1)
        amounts.append(value * multiplier)
    if not amounts:
        return max(0, fallback)
    estimate = sum(amounts[:2]) / min(2, len(amounts)) if len(amounts) > 1 else amounts[0]
    return max(0, round(estimate * 100))


def upgrade_business_profile(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version = 4").fetchone():
        return
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(business_profile)")}
    additions = {
        "vat_scheme": "vat_scheme TEXT NOT NULL DEFAULT ''",
        "vat_effective_from": "vat_effective_from TEXT",
        "vat_effective_to": "vat_effective_to TEXT",
        "tax_codes_approved": "tax_codes_approved INTEGER NOT NULL DEFAULT 0",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE business_profile ADD COLUMN {definition}")
    conn.execute(
        "INSERT INTO schema_migrations (version, name, applied_at) VALUES (4, 'vat-readiness', ?)",
        (utc_now().isoformat(),),
    )


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    if "payload" in keys and row["payload"]:
        try:
            value = json.loads(row["payload"])
            if isinstance(value, dict):
                return value
        except (TypeError, json.JSONDecodeError):
            pass
    return {key: row[key] for key in row.keys()}


def _find_or_create_account(
    conn: sqlite3.Connection,
    name: str,
    *,
    domain: str,
    website: str,
    source: str,
    status: str,
    notes: str,
    now: str,
) -> int:
    row = None
    if domain:
        row = conn.execute(
            "SELECT id FROM accounts WHERE lower(domain) = lower(?) AND archived_at IS NULL",
            (domain,),
        ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT id FROM accounts WHERE lower(name) = lower(?) AND archived_at IS NULL",
            (name,),
        ).fetchone()
    if row:
        return int(row["id"])
    cursor = conn.execute(
        """INSERT INTO accounts
           (name, domain, website, source, status, notes, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, domain, website, source, status, notes, now, now),
    )
    account_id = int(cursor.lastrowid)
    role = "client" if status.lower() in {"active", "customer", "client"} else "prospect"
    conn.execute("INSERT OR IGNORE INTO account_roles (account_id, role) VALUES (?, ?)", (account_id, role))
    index_record(conn, "account", account_id, name, domain, notes)
    return account_id


def _find_or_create_contact(
    conn: sqlite3.Connection,
    account_id: int,
    display_name: str,
    email: str,
    phone: str,
    source: str,
    now: str,
) -> int:
    row = None
    if email:
        row = conn.execute(
            "SELECT id FROM contacts WHERE lower(email) = lower(?) AND archived_at IS NULL",
            (email,),
        ).fetchone()
    if row:
        return int(row["id"])
    parts = display_name.strip().split(maxsplit=1)
    first_name = parts[0] if parts else ""
    last_name = parts[1] if len(parts) > 1 else ""
    cursor = conn.execute(
        """INSERT INTO contacts
           (account_id, first_name, last_name, display_name, email, phone, source, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (account_id, first_name, last_name, display_name, email, phone, source, now, now),
    )
    contact_id = int(cursor.lastrowid)
    index_record(conn, "contact", contact_id, display_name, email, phone)
    return contact_id


def _activity(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: int,
    kind: str,
    subject: str,
    body: str,
    occurred_at: str,
) -> None:
    conn.execute(
        """INSERT INTO activities
           (entity_type, entity_id, kind, subject, body, occurred_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (entity_type, entity_id, kind, subject, body, occurred_at, utc_now().isoformat()),
    )


def _known(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"unknown", "n/a", "none", "not available", "-"} else text


def _nullable(value: Any) -> str | None:
    return _known(value) or None


def _first_known(*values: Any) -> str:
    for value in values:
        known = _known(value)
        if known:
            return known
    return ""


def _domain(value: Any) -> str:
    from urllib.parse import urlparse

    text = _known(value)
    if not text:
        return ""
    parsed = urlparse(text if "://" in text else f"https://{text}")
    return (parsed.hostname or "").lower().removeprefix("www.")


def _legacy_stage(status: Any) -> str:
    value = _known(status).lower()
    if value == "rejected":
        return "Lost"
    if value == "proposal":
        return "Proposal"
    if value in {"qualified", "confirmed"}:
        return "Qualified"
    if value == "contacted":
        return "Contacted"
    return "Discovery"


def _legacy_tender_status(status: Any) -> str:
    value = _known(status).lower()
    if value == "rejected":
        return "Rejected"
    if value in {"confirmed", "contacted", "qualified", "proposal"}:
        return "Qualified"
    if value == "reviewing":
        return "Reviewing"
    return "New"


def _legacy_sources(payload: dict[str, Any]) -> list[str]:
    values = [payload.get("contract_url"), payload.get("website"), *(payload.get("source_urls") or [])]
    result: list[str] = []
    for value in values:
        text = _known(value)
        if text and text not in result:
            result.append(text)
    return result


def copy_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(source)
    target_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(target_conn)
        if target_conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Backup integrity check failed")
    finally:
        source_conn.close()
        target_conn.close()
