# CRM Scaffold

A lightweight CRM каркас with a FastAPI backend and React Vite frontend.

## Structure

- `backend/` - FastAPI app with dummy in-memory API data.
- `frontend/` - React Vite app with CRM tabs.

Backend modules:

- `backend/app/main.py` - app setup, CORS, and router registration.
- `backend/app/schemas.py` - Pydantic models and enums.
- `backend/app/data.py` - dummy in-memory records.
- `backend/app/routes/` - thin FastAPI route modules.
- `backend/app/services/` - simple service functions for each CRM area.

## Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Backend API:

- `GET /api/health`
- `GET /api/clients`
- `GET /api/leads`
- `GET /api/events`
- `GET /api/emails`
- `GET /api/calendar`
- `POST /api/enrichment/run`
- `POST /api/discovery/run`

## Lead enrichment agent

The backend includes a standalone lead enrichment agent under
`backend/app/lead_enrichment/`. It reads leads from Attio, researches company
pages with Tavily, classifies signals with Gemini, and can write summaries,
scores, notes, and follow-up tasks back to Attio.

Required environment variables:

```powershell
$env:ATTIO_API_TOKEN="..."
$env:TAVILY_API_KEY="..."
$env:GEMINI_API_KEY="..."
$env:GEMINI_MODEL="gemini-3.5-flash"
$env:ATTIO_LEAD_LIST_ID="..."
```

Optional Attio attribute slug overrides:

```powershell
$env:ATTIO_ENRICHMENT_SUMMARY_ATTRIBUTE="lead_enrichment_summary"
$env:ATTIO_FIT_SCORE_ATTRIBUTE="lead_fit_score"
$env:ATTIO_URGENCY_SCORE_ATTRIBUTE="lead_urgency_score"
$env:ATTIO_CONFIDENCE_SCORE_ATTRIBUTE="lead_enrichment_confidence"
$env:ATTIO_FINGERPRINT_ATTRIBUTE="lead_enrichment_fingerprint"
$env:ATTIO_SOURCE_URLS_ATTRIBUTE="lead_enrichment_source_urls"
$env:ATTIO_ENRICHED_AT_ATTRIBUTE="lead_enriched_at"
```

Run from `backend/`:

```powershell
python -m app.lead_enrichment.runner --limit 3 --dry-run
python -m app.lead_enrichment.runner --limit 1 --write
```

Trigger through FastAPI:

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/enrichment/run" `
  -ContentType "application/json" `
  -Body '{"limit": 3, "dry_run": true}'
```

## Lead discovery agent

The backend also includes a standalone discovery agent under
`backend/app/lead_discovery/`. It takes a niche and optional region, discovers
company websites with Tavily, extracts relevant pages, parses normalized company
profiles with Gemini, and upserts Companies into Attio by domain.

Discovery-specific optional env vars:

```powershell
$env:ATTIO_DISCOVERY_SUMMARY_ATTRIBUTE="discovery_summary"
$env:ATTIO_DISCOVERY_CONFIDENCE_ATTRIBUTE="discovery_confidence_score"
$env:ATTIO_DISCOVERY_SOURCE_URLS_ATTRIBUTE="discovery_source_urls"
$env:ATTIO_DISCOVERY_FINGERPRINT_ATTRIBUTE="discovery_fingerprint"
$env:ATTIO_DISCOVERY_NICHE_ATTRIBUTE="discovery_niche"
$env:ATTIO_DISCOVERY_REGION_ATTRIBUTE="discovery_region"
```

Run from `backend/`:

```powershell
python -m app.lead_discovery.runner --niche landscaping --region "Austin, TX" --limit 10 --dry-run
python -m app.lead_discovery.runner --niche landscaping --region "Austin, TX" --limit 5 --write
```

Trigger through FastAPI:

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

If port `5173` is already busy, Vite will print the fallback URL, for example
`http://127.0.0.1:5174`. The backend allows local Vite ports `5173`-`5179`.
