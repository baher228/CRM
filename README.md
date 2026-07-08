# CRM Workspace

A lightweight CRM workspace with a FastAPI backend and React/Vite frontend. It combines local CRM records with Attio sync, Tavily research, Gemini drafting, tender discovery, and the Daybreak briefing workflow.

## Structure

- `backend/` - FastAPI API, local JSON persistence, Attio/Tavily/Gemini workflows.
- `frontend/` - React/Vite CRM interface.
- `daybreak_n8n_workflow.json` - starter n8n workflow for Daybreak scheduling.

Backend modules:

- `backend/app/main.py` - app setup, CORS, and route registration.
- `backend/app/schemas.py` - shared Pydantic API models.
- `backend/app/data.py` - optional seed records for local demos.
- `backend/app/routes/` - thin FastAPI route modules.
- `backend/app/services/` - CRM orchestration and reusable helpers.
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

Copy `backend/.env.example` to `backend/.env` and fill in real API keys for Attio, Tavily, and Gemini.

Core API:

- `GET /api/health`
- `GET /api/clients`
- `POST /api/clients`
- `GET /api/leads`
- `PATCH /api/leads/{lead_id}`
- `POST /api/leads/{lead_id}/confirm`
- `POST /api/leads/{lead_id}/reject`
- `GET /api/events`
- `GET /api/emails`
- `GET /api/calendar`
- `POST /api/calendar`
- `POST /api/enrichment/run`
- `GET /api/discovery/portals`
- `POST /api/discovery/jobs`
- `GET /api/discovery/jobs/{job_id}`
- `POST /api/discovery/run`
- `POST /api/briefing/generate`
- `GET /api/briefing/latest`
- `POST /api/briefing/approve`

Seed data is kept as a local fallback. Set `CRM_INCLUDE_DEMO_LEADS=true` to include demo leads alongside discovered leads; manually added clients, calendar items, and discovered leads are stored in ignored JSON files under `backend/`.

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

Open `http://localhost:5173`. If the backend is not on `http://localhost:8000/api`, copy `frontend/.env.example` to `frontend/.env` and set `VITE_API_BASE_URL`.

## Checks

```powershell
cd backend
python -m unittest discover -s tests
python -m compileall -q app

cd ..\frontend
npm run build
```
