from __future__ import annotations

import sqlite3

from app.platform_db import connect


SCHEMA = """
CREATE TABLE IF NOT EXISTS gmail_threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gmail_thread_id TEXT NOT NULL UNIQUE,
    history_id TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    snippet TEXT NOT NULL DEFAULT '',
    participants_json TEXT NOT NULL DEFAULT '[]',
    last_message_at TEXT,
    unread INTEGER NOT NULL DEFAULT 0 CHECK(unread IN (0, 1)),
    message_count INTEGER NOT NULL DEFAULT 0,
    sync_state TEXT NOT NULL DEFAULT 'Cached',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_gmail_threads_last_message
    ON gmail_threads(last_message_at DESC, archived_at);
CREATE INDEX IF NOT EXISTS idx_gmail_threads_unread
    ON gmail_threads(unread, archived_at, last_message_at DESC);

CREATE TABLE IF NOT EXISTS gmail_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL REFERENCES gmail_threads(id) ON DELETE CASCADE,
    gmail_message_id TEXT NOT NULL UNIQUE,
    rfc_message_id TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL DEFAULT 'inbound'
        CHECK(direction IN ('inbound', 'outbound', 'draft')),
    from_email TEXT NOT NULL DEFAULT '',
    to_json TEXT NOT NULL DEFAULT '[]',
    cc_json TEXT NOT NULL DEFAULT '[]',
    bcc_json TEXT NOT NULL DEFAULT '[]',
    subject TEXT NOT NULL DEFAULT '',
    snippet TEXT NOT NULL DEFAULT '',
    body_text TEXT NOT NULL DEFAULT '',
    sent_at TEXT NOT NULL,
    labels_json TEXT NOT NULL DEFAULT '[]',
    attachments_json TEXT NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_gmail_messages_rfc_id
    ON gmail_messages(rfc_message_id) WHERE rfc_message_id <> '';
CREATE INDEX IF NOT EXISTS idx_gmail_messages_thread
    ON gmail_messages(thread_id, sent_at);

CREATE TABLE IF NOT EXISTS gmail_thread_links (
    thread_id INTEGER NOT NULL REFERENCES gmail_threads(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    link_source TEXT NOT NULL DEFAULT 'manual'
        CHECK(link_source IN ('manual', 'email_match', 'sync')),
    created_at TEXT NOT NULL,
    PRIMARY KEY(thread_id, entity_type, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_gmail_thread_links_entity
    ON gmail_thread_links(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS email_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    body_text TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_email_templates_name
    ON email_templates(name COLLATE NOCASE) WHERE archived_at IS NULL;

CREATE TABLE IF NOT EXISTS sales_sequences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'Draft'
        CHECK(state IN ('Draft', 'Active', 'Paused', 'Archived')),
    timezone TEXT NOT NULL DEFAULT 'Europe/London',
    business_days_json TEXT NOT NULL DEFAULT '[0,1,2,3,4]',
    send_window_start TEXT NOT NULL DEFAULT '09:00',
    send_window_end TEXT NOT NULL DEFAULT '17:00',
    daily_cap INTEGER NOT NULL DEFAULT 40 CHECK(daily_cap BETWEEN 1 AND 500),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sales_sequences_name
    ON sales_sequences(name COLLATE NOCASE) WHERE archived_at IS NULL;

CREATE TABLE IF NOT EXISTS sequence_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sequence_id INTEGER NOT NULL REFERENCES sales_sequences(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK(position >= 0),
    step_type TEXT NOT NULL CHECK(step_type IN ('email', 'delay', 'manual_task')),
    template_id INTEGER REFERENCES email_templates(id) ON DELETE SET NULL,
    subject TEXT NOT NULL DEFAULT '',
    body_text TEXT NOT NULL DEFAULT '',
    delay_minutes INTEGER NOT NULL DEFAULT 0 CHECK(delay_minutes >= 0),
    task_title TEXT NOT NULL DEFAULT '',
    task_description TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(sequence_id, position)
);

CREATE TABLE IF NOT EXISTS sequence_enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sequence_id INTEGER NOT NULL REFERENCES sales_sequences(id),
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    email TEXT NOT NULL COLLATE NOCASE,
    state TEXT NOT NULL DEFAULT 'Active'
        CHECK(state IN ('Active', 'Paused', 'Completed', 'Cancelled', 'Replied', 'Bounced', 'Opted out')),
    current_step_position INTEGER NOT NULL DEFAULT 0,
    next_action_at TEXT,
    stopped_reason TEXT NOT NULL DEFAULT '',
    replied_at TEXT,
    bounced_at TEXT,
    opted_out_at TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sequence_active_enrollment
    ON sequence_enrollments(sequence_id, email)
    WHERE state IN ('Active', 'Paused');
CREATE INDEX IF NOT EXISTS idx_sequence_enrollments_state
    ON sequence_enrollments(state, next_action_at);

CREATE TABLE IF NOT EXISTS scheduled_email_sends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    enrollment_id INTEGER REFERENCES sequence_enrollments(id) ON DELETE CASCADE,
    sequence_step_id INTEGER REFERENCES sequence_steps(id) ON DELETE SET NULL,
    to_email TEXT NOT NULL COLLATE NOCASE,
    subject TEXT NOT NULL,
    body_text TEXT NOT NULL,
    rfc_message_id TEXT NOT NULL UNIQUE,
    gmail_thread_id TEXT,
    reply_to_message_id TEXT,
    scheduled_for TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'Queued'
        CHECK(state IN ('Queued', 'Paused', 'Sending', 'Sent', 'Cancelled', 'Failed', 'Unknown')),
    job_id TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_scheduled_email_ready
    ON scheduled_email_sends(state, scheduled_for);

CREATE TABLE IF NOT EXISTS email_suppressions (
    email TEXT PRIMARY KEY COLLATE NOCASE,
    reason TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    google_file_id TEXT NOT NULL DEFAULT '',
    mime_type TEXT NOT NULL DEFAULT 'application/vnd.google-apps.document',
    merge_schema_json TEXT NOT NULL DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_document_templates_name
    ON document_templates(name COLLATE NOCASE) WHERE archived_at IS NULL;

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER REFERENCES document_templates(id) ON DELETE SET NULL,
    entity_type TEXT NOT NULL DEFAULT '',
    entity_id INTEGER,
    title TEXT NOT NULL,
    mime_type TEXT NOT NULL DEFAULT 'application/vnd.google-apps.document',
    google_file_id TEXT NOT NULL DEFAULT '',
    drive_url TEXT NOT NULL DEFAULT '',
    local_path TEXT NOT NULL DEFAULT '',
    checksum_sha256 TEXT NOT NULL DEFAULT '',
    sync_state TEXT NOT NULL DEFAULT 'Local'
        CHECK(sync_state IN ('Local', 'Queued', 'Ready', 'Error')),
    drive_job_id TEXT,
    last_sync_error TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_entity
    ON documents(entity_type, entity_id, archived_at);
CREATE INDEX IF NOT EXISTS idx_documents_google_file
    ON documents(google_file_id) WHERE google_file_id <> '';

CREATE TABLE IF NOT EXISTS document_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
    version_number INTEGER NOT NULL CHECK(version_number > 0),
    google_file_id TEXT NOT NULL DEFAULT '',
    local_path TEXT NOT NULL DEFAULT '',
    mime_type TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0 CHECK(size_bytes >= 0),
    issued INTEGER NOT NULL DEFAULT 0 CHECK(issued IN (0, 1)),
    source TEXT NOT NULL DEFAULT 'local',
    drive_job_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(document_id, version_number)
);

CREATE TABLE IF NOT EXISTS communication_idempotency (
    action TEXT NOT NULL,
    key TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(action, key)
);

CREATE TRIGGER IF NOT EXISTS document_versions_no_update
BEFORE UPDATE OF document_id, version_number, google_file_id, local_path, mime_type,
                 checksum_sha256, size_bytes, issued, source, created_at
ON document_versions
BEGIN SELECT RAISE(ABORT, 'document versions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS document_versions_no_delete
BEFORE DELETE ON document_versions
BEGIN SELECT RAISE(ABORT, 'document versions are immutable'); END;
"""


def install_schema(conn: sqlite3.Connection | None = None) -> None:
    """Install the communications and document metadata schema."""
    if conn is not None:
        conn.executescript(SCHEMA)
        return
    with connect() as database:
        database.executescript(SCHEMA)
