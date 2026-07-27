from __future__ import annotations

import sqlite3

from app.platform_db import connect


SCHEMA = """
CREATE TABLE IF NOT EXISTS integration_connections (
    provider TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'disconnected',
    account_label TEXT NOT NULL DEFAULT '',
    scopes_json TEXT NOT NULL DEFAULT '[]',
    last_sync_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS integration_sync_cursors (
    provider TEXT NOT NULL,
    resource TEXT NOT NULL,
    cursor TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (provider, resource)
);

CREATE TABLE IF NOT EXISTS integration_external_refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    local_type TEXT NOT NULL,
    local_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (provider, resource_type, external_id),
    UNIQUE (provider, resource_type, local_type, local_id)
);

CREATE TABLE IF NOT EXISTS integration_jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued'
        CHECK (state IN ('queued', 'running', 'retry_wait', 'unknown', 'succeeded', 'failed', 'cancelled')),
    priority INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
    lease_owner TEXT,
    lease_expires_at TEXT,
    idempotency_key TEXT,
    requires_reconciliation INTEGER NOT NULL DEFAULT 0 CHECK (requires_reconciliation IN (0, 1)),
    reconciliation_state TEXT NOT NULL DEFAULT 'not_required'
        CHECK (reconciliation_state IN ('not_required', 'pending', 'required', 'resolved')),
    result_json TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS integration_jobs_idempotency
    ON integration_jobs(kind, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS integration_jobs_ready
    ON integration_jobs(state, available_at, priority DESC, created_at);

CREATE TABLE IF NOT EXISTS integration_outbox (
    id TEXT PRIMARY KEY,
    destination TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'processing', 'retry_wait', 'unknown', 'delivered', 'dead_letter', 'cancelled')),
    available_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
    lease_owner TEXT,
    lease_expires_at TEXT,
    idempotency_key TEXT NOT NULL,
    external_id TEXT,
    reconciliation_state TEXT NOT NULL DEFAULT 'pending'
        CHECK (reconciliation_state IN ('pending', 'required', 'resolved')),
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    delivered_at TEXT,
    UNIQUE (destination, idempotency_key)
);
CREATE INDEX IF NOT EXISTS integration_outbox_ready
    ON integration_outbox(state, available_at, created_at);

CREATE TABLE IF NOT EXISTS integration_delivery_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_type TEXT NOT NULL CHECK (queue_type IN ('job', 'outbox')),
    item_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    worker_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    outcome TEXT NOT NULL DEFAULT 'running',
    error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS integration_delivery_attempts_item
    ON integration_delivery_attempts(queue_type, item_id, attempt_number);

CREATE TABLE IF NOT EXISTS integration_notifications (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'info'
        CHECK (severity IN ('info', 'success', 'warning', 'error')),
    action_url TEXT NOT NULL DEFAULT '',
    dedupe_key TEXT,
    read_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS integration_notifications_dedupe
    ON integration_notifications(dedupe_key)
    WHERE dedupe_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS integration_automation_rules (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    trigger_name TEXT NOT NULL,
    conditions_json TEXT NOT NULL DEFAULT '[]',
    actions_json TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    dry_run INTEGER NOT NULL DEFAULT 1 CHECK (dry_run IN (0, 1)),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS integration_automation_executions (
    id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL REFERENCES integration_automation_rules(id),
    trigger_name TEXT NOT NULL,
    record_key TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('dry_run', 'live')),
    outcome TEXT NOT NULL CHECK (outcome IN ('matched', 'skipped', 'succeeded', 'failed', 'cycle_blocked')),
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE (rule_id, record_key, correlation_id, mode)
);
"""


def install_schema(conn: sqlite3.Connection | None = None) -> None:
    """Install the idempotent integration schema on the application database."""
    if conn is not None:
        conn.executescript(SCHEMA)
        _upgrade_schema(conn)
        return
    with connect() as database:
        database.executescript(SCHEMA)
        _upgrade_schema(database)


def _upgrade_schema(conn: sqlite3.Connection) -> None:
    """Small forward migration for databases created by an earlier preview build."""
    columns = {
        row["name"] if isinstance(row, sqlite3.Row) else row[1]
        for row in conn.execute("PRAGMA table_info(integration_notifications)")
    }
    if "updated_at" not in columns:
        conn.execute(
            "ALTER TABLE integration_notifications ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''"
        )
        conn.execute(
            "UPDATE integration_notifications SET updated_at = created_at WHERE updated_at = ''"
        )
    if "version" not in columns:
        conn.execute(
            "ALTER TABLE integration_notifications ADD COLUMN version INTEGER NOT NULL DEFAULT 1"
        )
