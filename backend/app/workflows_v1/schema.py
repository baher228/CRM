from __future__ import annotations

import sqlite3

from app.platform_db import connect


SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_idempotency (
    scope TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    resource_id TEXT NOT NULL DEFAULT '',
    response_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    PRIMARY KEY (scope, idempotency_key)
);

CREATE TABLE IF NOT EXISTS workflow_discovery_imports (
    run_id TEXT NOT NULL REFERENCES discovery_runs(id) ON DELETE CASCADE,
    result_key TEXT NOT NULL,
    tender_id INTEGER NOT NULL REFERENCES tender_notices(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, result_key)
);
CREATE INDEX IF NOT EXISTS workflow_discovery_imports_tender
    ON workflow_discovery_imports(tender_id);
"""


def install_schema(conn: sqlite3.Connection | None = None) -> None:
    """Install the idempotent workflow schema on an existing core database."""
    if conn is not None:
        conn.executescript(SCHEMA)
        return
    with connect() as database:
        database.executescript(SCHEMA)

