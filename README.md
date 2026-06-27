# CRM Scaffold

A lightweight CRM каркас with a FastAPI backend and React Vite frontend.

## Structure

- `backend/` - FastAPI app with dummy in-memory API data.
- `frontend/` - React Vite app with CRM tabs.

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

## Frontend

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

If port `5173` is already busy, Vite will print the fallback URL, for example
`http://127.0.0.1:5174`. The backend allows local Vite ports `5173`-`5179`.
