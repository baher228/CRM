from __future__ import annotations

import sqlite3


def install_schema(conn: sqlite3.Connection) -> None:
    """Install the commercial, delivery and finance schema on an open connection."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS catalog_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            unit TEXT NOT NULL DEFAULT 'item',
            unit_price_pence INTEGER NOT NULL CHECK(unit_price_pence >= 0),
            tax_rate_bps INTEGER NOT NULL CHECK(tax_rate_bps BETWEEN 0 AND 10000),
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT
        );

        CREATE TABLE IF NOT EXISTS proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number TEXT UNIQUE,
            account_id INTEGER NOT NULL,
            opportunity_id INTEGER,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Draft',
            currency TEXT NOT NULL DEFAULT 'GBP',
            valid_until TEXT,
            notes TEXT NOT NULL DEFAULT '',
            net_pence INTEGER NOT NULL DEFAULT 0,
            vat_pence INTEGER NOT NULL DEFAULT 0,
            total_pence INTEGER NOT NULL DEFAULT 0,
            sent_at TEXT,
            accepted_at TEXT,
            rejected_at TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT
        );
        CREATE TABLE IF NOT EXISTS proposal_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_id INTEGER NOT NULL REFERENCES proposals(id) ON DELETE CASCADE,
            catalog_item_id INTEGER,
            description TEXT NOT NULL,
            quantity TEXT NOT NULL,
            unit_price_pence INTEGER NOT NULL,
            discount_bps INTEGER NOT NULL DEFAULT 0,
            tax_rate_bps INTEGER NOT NULL DEFAULT 0,
            net_pence INTEGER NOT NULL,
            vat_pence INTEGER NOT NULL,
            total_pence INTEGER NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number TEXT UNIQUE,
            account_id INTEGER NOT NULL,
            proposal_id INTEGER REFERENCES proposals(id),
            opportunity_id INTEGER,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Draft',
            starts_on TEXT,
            ends_on TEXT,
            value_pence INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'GBP',
            notes TEXT NOT NULL DEFAULT '',
            sent_at TEXT,
            signed_at TEXT,
            signed_file_id INTEGER,
            activated_at TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT
        );

        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            opportunity_id INTEGER,
            contract_id INTEGER REFERENCES contracts(id),
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Planned',
            billing_type TEXT NOT NULL DEFAULT 'fixed',
            budget_pence INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'GBP',
            starts_on TEXT,
            due_on TEXT,
            notes TEXT NOT NULL DEFAULT '',
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT
        );
        CREATE TABLE IF NOT EXISTS milestones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id),
            title TEXT NOT NULL,
            due_on TEXT,
            amount_pence INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'Planned',
            sort_order INTEGER NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT
        );
        CREATE TABLE IF NOT EXISTS time_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id),
            entry_date TEXT NOT NULL,
            minutes INTEGER NOT NULL CHECK(minutes > 0),
            description TEXT NOT NULL DEFAULT '',
            billable INTEGER NOT NULL DEFAULT 1 CHECK(billable IN (0, 1)),
            hourly_rate_pence INTEGER NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT
        );
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER REFERENCES projects(id),
            account_id INTEGER,
            expense_date TEXT NOT NULL,
            vendor TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL,
            net_pence INTEGER NOT NULL CHECK(net_pence >= 0),
            tax_rate_bps INTEGER NOT NULL DEFAULT 0,
            vat_pence INTEGER NOT NULL DEFAULT 0,
            total_pence INTEGER NOT NULL DEFAULT 0,
            billable INTEGER NOT NULL DEFAULT 0 CHECK(billable IN (0, 1)),
            receipt_file_id INTEGER,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT
        );

        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number TEXT UNIQUE,
            account_id INTEGER NOT NULL,
            project_id INTEGER REFERENCES projects(id),
            contract_id INTEGER REFERENCES contracts(id),
            status TEXT NOT NULL DEFAULT 'Draft',
            currency TEXT NOT NULL DEFAULT 'GBP',
            issued_on TEXT,
            due_on TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            customer_address TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            net_pence INTEGER NOT NULL DEFAULT 0,
            vat_pence INTEGER NOT NULL DEFAULT 0,
            total_pence INTEGER NOT NULL DEFAULT 0,
            paid_pence INTEGER NOT NULL DEFAULT 0,
            credited_pence INTEGER NOT NULL DEFAULT 0,
            state TEXT GENERATED ALWAYS AS (status) VIRTUAL,
            balance_minor INTEGER GENERATED ALWAYS AS (
                MAX(total_pence - paid_pence - credited_pence, 0)
            ) VIRTUAL,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT
        );
        CREATE TABLE IF NOT EXISTS invoice_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
            catalog_item_id INTEGER,
            description TEXT NOT NULL,
            quantity TEXT NOT NULL,
            unit_price_pence INTEGER NOT NULL,
            discount_bps INTEGER NOT NULL DEFAULT 0,
            tax_rate_bps INTEGER NOT NULL DEFAULT 0,
            net_pence INTEGER NOT NULL,
            vat_pence INTEGER NOT NULL,
            total_pence INTEGER NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS credit_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number TEXT UNIQUE,
            invoice_id INTEGER NOT NULL REFERENCES invoices(id),
            status TEXT NOT NULL DEFAULT 'Draft',
            reason TEXT NOT NULL,
            currency TEXT NOT NULL DEFAULT 'GBP',
            issued_on TEXT,
            net_pence INTEGER NOT NULL DEFAULT 0,
            vat_pence INTEGER NOT NULL DEFAULT 0,
            total_pence INTEGER NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS credit_note_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            credit_note_id INTEGER NOT NULL REFERENCES credit_notes(id) ON DELETE CASCADE,
            description TEXT NOT NULL,
            quantity TEXT NOT NULL,
            unit_price_pence INTEGER NOT NULL,
            discount_bps INTEGER NOT NULL DEFAULT 0,
            tax_rate_bps INTEGER NOT NULL DEFAULT 0,
            net_pence INTEGER NOT NULL,
            vat_pence INTEGER NOT NULL,
            total_pence INTEGER NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount_pence INTEGER NOT NULL CHECK(amount_pence > 0),
            currency TEXT NOT NULL DEFAULT 'GBP',
            received_at TEXT NOT NULL,
            method TEXT NOT NULL,
            reference TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payment_allocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_id INTEGER NOT NULL REFERENCES payments(id),
            invoice_id INTEGER NOT NULL REFERENCES invoices(id),
            amount_pence INTEGER NOT NULL CHECK(amount_pence > 0),
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payment_refunds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_id INTEGER NOT NULL REFERENCES payments(id),
            invoice_id INTEGER REFERENCES invoices(id),
            amount_pence INTEGER NOT NULL CHECK(amount_pence > 0),
            reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ledger_accounts (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            kind TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS journals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            description TEXT NOT NULL,
            posted_at TEXT NOT NULL,
            UNIQUE(source_type, source_id)
        );
        CREATE TABLE IF NOT EXISTS journal_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            journal_id INTEGER NOT NULL REFERENCES journals(id),
            account_code TEXT NOT NULL REFERENCES ledger_accounts(code),
            debit_pence INTEGER NOT NULL DEFAULT 0 CHECK(debit_pence >= 0),
            credit_pence INTEGER NOT NULL DEFAULT 0 CHECK(credit_pence >= 0),
            CHECK((debit_pence = 0) != (credit_pence = 0))
        );

        CREATE TABLE IF NOT EXISTS client_success (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL UNIQUE,
            manual_health TEXT,
            open_risks INTEGER NOT NULL DEFAULT 0,
            onboarding_status TEXT NOT NULL DEFAULT 'Not started',
            next_review_on TEXT,
            renewal_on TEXT,
            notes TEXT NOT NULL DEFAULT '',
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT
        );
        CREATE TABLE IF NOT EXISTS operation_idempotency (
            action TEXT NOT NULL,
            key TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(action, key)
        );
        CREATE TABLE IF NOT EXISTS lifecycle_links (
            kind TEXT NOT NULL,
            source_key TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(kind, source_key)
        );

        CREATE INDEX IF NOT EXISTS idx_projects_account ON projects(account_id, status);
        CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status, archived_at, id);
        CREATE INDEX IF NOT EXISTS idx_milestones_project ON milestones(project_id, sort_order);
        CREATE INDEX IF NOT EXISTS idx_time_project_date ON time_entries(project_id, entry_date);
        CREATE INDEX IF NOT EXISTS idx_expenses_project_date ON expenses(project_id, expense_date);
        CREATE INDEX IF NOT EXISTS idx_proposals_account ON proposals(account_id, status);
        CREATE INDEX IF NOT EXISTS idx_contracts_account ON contracts(account_id, status);
        CREATE INDEX IF NOT EXISTS idx_invoices_account_status ON invoices(account_id, status, due_on);
        CREATE INDEX IF NOT EXISTS idx_invoices_status_due ON invoices(status, due_on, id);
        CREATE INDEX IF NOT EXISTS idx_allocations_invoice ON payment_allocations(invoice_id);
        CREATE INDEX IF NOT EXISTS idx_success_renewal ON client_success(renewal_on);
        CREATE INDEX IF NOT EXISTS idx_lifecycle_target ON lifecycle_links(target_type, target_id);

        INSERT OR IGNORE INTO ledger_accounts(code, name, kind) VALUES
            ('1100', 'Accounts receivable', 'asset'),
            ('1200', 'Cash and payment clearing', 'asset'),
            ('1300', 'VAT reclaimable', 'asset'),
            ('2100', 'VAT payable', 'liability'),
            ('2200', 'Unapplied payments', 'liability'),
            ('4000', 'Sales revenue', 'income'),
            ('5000', 'Expenses', 'expense');

        CREATE TRIGGER IF NOT EXISTS journals_no_update
        BEFORE UPDATE ON journals BEGIN SELECT RAISE(ABORT, 'posted journals are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS journals_no_delete
        BEFORE DELETE ON journals BEGIN SELECT RAISE(ABORT, 'posted journals are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS journal_lines_no_update
        BEFORE UPDATE ON journal_lines BEGIN SELECT RAISE(ABORT, 'posted journal lines are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS journal_lines_no_delete
        BEFORE DELETE ON journal_lines BEGIN SELECT RAISE(ABORT, 'posted journal lines are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS invoice_lines_no_update_after_issue
        BEFORE UPDATE ON invoice_lines
        WHEN (SELECT status FROM invoices WHERE id = OLD.invoice_id) != 'Draft'
        BEGIN SELECT RAISE(ABORT, 'issued invoice lines are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS invoice_lines_no_insert_after_issue
        BEFORE INSERT ON invoice_lines
        WHEN (SELECT status FROM invoices WHERE id = NEW.invoice_id) != 'Draft'
        BEGIN SELECT RAISE(ABORT, 'issued invoice lines are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS invoice_lines_no_delete_after_issue
        BEFORE DELETE ON invoice_lines
        WHEN (SELECT status FROM invoices WHERE id = OLD.invoice_id) != 'Draft'
        BEGIN SELECT RAISE(ABORT, 'issued invoice lines are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS invoices_no_snapshot_update_after_issue
        BEFORE UPDATE OF account_id, project_id, contract_id, currency, issued_on, due_on,
                         customer_name, customer_address, notes, net_pence, vat_pence, total_pence
        ON invoices WHEN OLD.status != 'Draft'
        BEGIN SELECT RAISE(ABORT, 'issued invoice snapshots are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS invoices_no_delete_after_issue
        BEFORE DELETE ON invoices WHEN OLD.status != 'Draft'
        BEGIN SELECT RAISE(ABORT, 'issued invoices are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS credit_lines_no_update_after_issue
        BEFORE UPDATE ON credit_note_lines
        WHEN (SELECT status FROM credit_notes WHERE id = OLD.credit_note_id) != 'Draft'
        BEGIN SELECT RAISE(ABORT, 'issued credit note lines are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS credit_lines_no_insert_after_issue
        BEFORE INSERT ON credit_note_lines
        WHEN (SELECT status FROM credit_notes WHERE id = NEW.credit_note_id) != 'Draft'
        BEGIN SELECT RAISE(ABORT, 'issued credit note lines are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS credit_lines_no_delete_after_issue
        BEFORE DELETE ON credit_note_lines
        WHEN (SELECT status FROM credit_notes WHERE id = OLD.credit_note_id) != 'Draft'
        BEGIN SELECT RAISE(ABORT, 'issued credit note lines are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS credit_notes_no_snapshot_update_after_issue
        BEFORE UPDATE OF invoice_id, reason, currency, issued_on, net_pence, vat_pence, total_pence
        ON credit_notes WHEN OLD.status != 'Draft'
        BEGIN SELECT RAISE(ABORT, 'issued credit notes are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS credit_notes_no_delete_after_issue
        BEFORE DELETE ON credit_notes WHEN OLD.status != 'Draft'
        BEGIN SELECT RAISE(ABORT, 'issued credit notes are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS payments_no_update
        BEFORE UPDATE ON payments BEGIN SELECT RAISE(ABORT, 'payments are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS payments_no_delete
        BEFORE DELETE ON payments BEGIN SELECT RAISE(ABORT, 'payments are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS allocations_no_update
        BEFORE UPDATE ON payment_allocations BEGIN SELECT RAISE(ABORT, 'payment allocations are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS allocations_no_delete
        BEFORE DELETE ON payment_allocations BEGIN SELECT RAISE(ABORT, 'payment allocations are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS refunds_no_update
        BEFORE UPDATE ON payment_refunds BEGIN SELECT RAISE(ABORT, 'payment refunds are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS refunds_no_delete
        BEFORE DELETE ON payment_refunds BEGIN SELECT RAISE(ABORT, 'payment refunds are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS proposal_lines_no_insert_after_send
        BEFORE INSERT ON proposal_lines
        WHEN (SELECT status FROM proposals WHERE id = NEW.proposal_id) != 'Draft'
        BEGIN SELECT RAISE(ABORT, 'sent proposal lines are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS proposal_lines_no_update_after_send
        BEFORE UPDATE ON proposal_lines
        WHEN (SELECT status FROM proposals WHERE id = OLD.proposal_id) != 'Draft'
        BEGIN SELECT RAISE(ABORT, 'sent proposal lines are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS proposal_lines_no_delete_after_send
        BEFORE DELETE ON proposal_lines
        WHEN (SELECT status FROM proposals WHERE id = OLD.proposal_id) != 'Draft'
        BEGIN SELECT RAISE(ABORT, 'sent proposal lines are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS contracts_no_snapshot_update_after_send
        BEFORE UPDATE OF account_id, proposal_id, opportunity_id, title, starts_on, ends_on,
                         value_pence, currency, notes
        ON contracts WHEN OLD.status != 'Draft'
        BEGIN SELECT RAISE(ABORT, 'sent contract snapshots are immutable'); END;
        """
    )
    _ensure_column(conn, "invoices", "archived_at", "archived_at TEXT")
    _ensure_column(
        conn,
        "invoices",
        "state",
        "state TEXT GENERATED ALWAYS AS (status) VIRTUAL",
    )
    _ensure_column(
        conn,
        "invoices",
        "balance_minor",
        "balance_minor INTEGER GENERATED ALWAYS AS (MAX(total_pence - paid_pence - credited_pence, 0)) VIRTUAL",
    )
    _ensure_column(conn, "credit_notes", "version", "version INTEGER NOT NULL DEFAULT 1")
    _ensure_column(conn, "invoices", "pdf_path", "pdf_path TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "invoices", "pdf_sha256", "pdf_sha256 TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "invoices", "stripe_payment_url", "stripe_payment_url TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "proposals", "document_path", "document_path TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "contracts", "signed_document_path", "signed_document_path TEXT NOT NULL DEFAULT ''")


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_xinfo({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
