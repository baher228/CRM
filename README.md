# CRM Workspace

CRM Workspace is a local-first, single-operator business system for Windows. It owns the complete operating record from a new lead or tender through sales, proposals, contracts, delivery, invoicing, payment, client health, and renewal.

The application runs as one FastAPI process on `127.0.0.1`, serves the production React build, and stores its SQLite database and generated documents under `%LOCALAPPDATA%\CRMWorkspace` by default. Google Workspace and Stripe are optional integrations; local work remains available when either is disconnected.

## What is included

- Today operating centre for overdue work, replies, meetings, tender deadlines, deal risk, blocked delivery, unpaid invoices, and renewals
- Accounts and contacts with record-360 timelines, tags, custom fields, duplicate merging, archive/restore, saved views, and global search
- Lead and tender qualification with provenance, deduplication, durable discovery runs, CSV import previews, and exports
- Configurable opportunity pipeline with board and table views, forecast values, stages, win/loss state, and optimistic conflict protection
- Gmail thread cache, linked messages, scheduled sends, templates, sequences, reply/bounce/opt-out stopping, and daily send caps
- Calendar records and Google synchronization jobs
- Drive-backed document templates, immutable PDF versions, proposals, contracts, catalog items, and commercial snapshots
- Projects, milestones, tasks, time, expenses, delivery profitability, client health, risks, and renewals
- UK-oriented invoices, VAT configuration, credit notes, partial payments, refunds, immutable PDFs, balanced append-only journals, aging, and Stripe Checkout collection
- Allowlisted automations with dry-run previews, notifications, retry history, recursion guards, and durable jobs
- Backup validation, staged restore, audit records, numbered migrations, SQLite WAL/foreign keys/FTS5, and optimistic `version` fields
- Responsive keyboard-accessible React interface with deep links, `Ctrl/Cmd+K` search, and `Ctrl/Cmd+N` quick create

## Run in development

Requirements: Python 3.12+, Node.js 20+, and PowerShell.

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. Development mode bypasses the per-install browser session only when `CRM_ENV=development` and `CRM_SECURITY_BYPASS=true`.

## Build and run the Windows application

```powershell
cd frontend
npm ci
npm run build
cd ..
.\scripts\Start-CRM.ps1
```

The launcher binds exclusively to `127.0.0.1`, starts the hidden FastAPI process, restricts the bootstrap secret to the current Windows account, and opens a signed HttpOnly/SameSite session. Install logon startup with:

```powershell
.\scripts\Install-CRMStartup.ps1
```

Create an integrity-checked backup with:

```powershell
.\scripts\Backup-CRM.ps1 -Destination D:\CRM-Backups
```

Settings also provides guarded recovery and data operations: CSV imports are mapped and previewed before a separate commit, restores require the operator to type `RESTORE` and are staged for the next launch, and the durable-job view separates ordinary retries from unknown Google/Stripe outcomes that must be reconciled first.

Secrets are entered from Settings and stored through `keyring` in Windows Credential Manager. They are not stored in SQLite, source files, browser state, logs, or backups.

## Integration setup

Google Workspace requires a Google Cloud Desktop OAuth client with Gmail, Calendar, Drive, and identity APIs enabled. Put only the public client ID in `backend/.env`; enter the client secret in Settings, then use Connect Google. Each connection opens a one-time callback listener bound to `127.0.0.1` on an OS-assigned port and closes it after the callback or ten minutes. `GOOGLE_OAUTH_REDIRECT_URI` is an optional fixed callback for deterministic tests or fallback only. The application requests Gmail modify, Calendar events, Drive file, and identity scopes.

Stripe uses a restricted test or live secret entered in Settings. CRM Workspace creates one-use Checkout sessions for an invoice's outstanding balance. The local invoice, allocations, and ledger remain authoritative; provider-generated invoices and automatic tax are disabled.

Tavily and Gemini are optional. Their keys are entered in Settings and are used only for tender discovery, enrichment, scoring, summaries, and drafts. Fake adapters are available through `CRM_INTEGRATIONS_FAKE=true` and `CRM_DISCOVERY_FAKE=true` for deterministic testing.

## Test and release checks

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q app
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pip install pip-audit
.\.venv\Scripts\pip-audit.exe -r requirements.txt

cd ..\frontend
npm test
npm run build
npm audit --audit-level=high
npx playwright test
```

Browser acceptance tests live in `frontend/e2e` and run against deterministic local adapters. Live smoke tests use a dedicated Google account and Stripe sandbox; credentials never enter CI.

Live provider checks are read-only and opt-in. After connecting an isolated Google test account and saving a Stripe test-mode key in Windows Credential Manager, run:

```powershell
cd backend
$env:CRM_LIVE_SMOKE = "1"
.\.venv\Scripts\python.exe -m unittest tests.test_live_providers -v
Remove-Item Env:CRM_LIVE_SMOKE
```

Normal test runs skip these checks and never require provider credentials.

## Data and API conventions

The default database is `%LOCALAPPDATA%\CRMWorkspace\crm.sqlite3`. Override it only for development/tests with `CRM_DB_PATH`. Business data uses UTC timestamps, Europe/London scheduling, integer minor currency units, decimal-string quantities, and basis-point tax rates.

The primary API is `/api/v1`. Collection responses use `{items, next_cursor}`; mutable updates carry `version`; stale writes return `409`; external work returns `202` with a durable `job_id`; and side effects require an `Idempotency-Key`. API docs can be enabled locally with `CRM_ENABLE_DOCS=true` and opened at `/api/docs`.

Financial and audit records are append-only. Corrections use credit notes, allocations, refunds, and reversing journals. Business records archive instead of being hard-deleted.
