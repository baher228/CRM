# CRM Workspace

A local-first CRM workspace with a FastAPI backend and React/Vite frontend. The core product now focuses on contacts, leads, pipeline follow-up, tasks, notes, calendar, search, dashboard summaries, and integration health.

Daybreak is deferred. Its backend code remains available, but the frontend navigation hides it unless `VITE_ENABLE_DAYBREAK=true`.

## Structure

- `backend/` - FastAPI API, SQLite CRM store, Attio/Tavily/Gemini workflows, and optional seed data.
- `frontend/` - React/Vite CRM interface.
- `daybreak_n8n_workflow.json` - optional Daybreak scheduling workflow, not part of the default CRM flow.

Backend modules:

- `backend/app/main.py` - app setup, CORS, and route registration.
- `backend/app/schemas.py` - shared Pydantic API models.
- `backend/app/data.py` - optional seed records for local demos.
- `backend/app/routes/` - thin FastAPI route modules.
- `backend/app/services/` - CRM store, orchestration, and reusable helpers.
- `backend/app/lead_enrichment/` - Attio lead enrichment workflow.
- `backend/app/lead_discovery/` - public contract discovery workflow.

## Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Copy `backend/.env.example` to `backend/.env` and fill in real API keys for Attio, Tavily, Gemini, and IMAP mail when you want those integrations.

Local CRM data is stored in SQLite at `backend/crm.sqlite3` by default. Set `CRM_DB_PATH` to use a different file.

Seed/demo data is opt-in:

- `CRM_INCLUDE_DEMO_DATA=true` imports demo contacts and calendar items.
- `CRM_INCLUDE_DEMO_LEADS=true` also imports demo leads.

Legacy ignored JSON files such as `manual_clients.json`, `manual_calendar.json`, and `discovered_leads.json` are imported once into SQLite if present.

The Emails tab can save a local IMAP mailbox from the web UI. You can also configure the same mailbox with backend env vars:

- `MAIL_IMAP_HOST`
- `MAIL_IMAP_PORT`
- `MAIL_IMAP_USERNAME`
- `MAIL_IMAP_PASSWORD`
- `MAIL_IMAP_FOLDER`
- `MAIL_IMAP_USE_SSL`

## Core API

- `GET /api/health`
- `GET /api/dashboard`
- `GET /api/settings/health`
- `GET /api/settings/mail`
- `POST /api/settings/mail`
- `GET /api/search?q=...`
- `GET /api/clients`
- `POST /api/clients`
- `PATCH /api/clients/{client_id}`
- `DELETE /api/clients/{client_id}`
- `GET /api/leads`
- `POST /api/leads`
- `PATCH /api/leads/{lead_id}`
- `DELETE /api/leads/{lead_id}`
- `POST /api/leads/bulk`
- `POST /api/leads/{lead_id}/confirm`
- `POST /api/leads/{lead_id}/reject`
- `GET /api/tasks`
- `POST /api/tasks`
- `PATCH /api/tasks/{task_id}`
- `DELETE /api/tasks/{task_id}`
- `GET /api/notes`
- `POST /api/notes`
- `DELETE /api/notes/{note_id}`
- `GET /api/activity/{related_type}/{related_id}`
- `GET /api/emails`
- `GET /api/calendar`
- `POST /api/calendar`
- `POST /api/enrichment/run`
- `GET /api/discovery/portals`
- `POST /api/discovery/jobs`
- `GET /api/discovery/jobs/{job_id}`
- `POST /api/discovery/run`

Daybreak endpoints still exist for deferred use:

- `POST /api/briefing/generate`
- `GET /api/briefing/latest`
- `POST /api/briefing/approve`

## Workflows

Run lead enrichment from `backend/`:

```powershell
python -m app.lead_enrichment.runner --limit 3 --dry-run
python -m app.lead_enrichment.runner --limit 1 --write
```

Run public contract discovery from `backend/`:

```powershell
python -m app.lead_discovery.runner --niche landscaping --region "Austin, TX" --limit 10 --dry-run
python -m app.lead_discovery.runner --niche landscaping --region "Austin, TX" --limit 5 --write
```

Trigger discovery through FastAPI:

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/discovery/run" `
  -ContentType "application/json" `
  -Body '{"niche": "landscaping", "region": "Austin, TX", "limit": 10, "dry_run": true}'
```

## Frontend

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

Copy `frontend/.env.example` to `frontend/.env` when you need local overrides:

- `VITE_API_BASE_URL=http://localhost:8000/api`
- `VITE_ENABLE_DAYBREAK=false`

Set `VITE_ENABLE_DAYBREAK=true` only when you want the deferred Daybreak view back in the navigation.

## Checks

```powershell
cd backend
python -m unittest discover -s tests
python -m compileall -q app

cd ..\frontend
npm run build
```
