from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import (
    briefing,
    calendar,
    clients,
    dashboard,
    discovery,
    emails,
    enrichment,
    events,
    health,
    leads,
    notes,
    search,
    settings,
    tasks,
)


app = FastAPI(title="CRM Workspace API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):517[3-9]",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(dashboard.router, prefix="/api", tags=["dashboard"])
app.include_router(clients.router, prefix="/api", tags=["clients"])
app.include_router(leads.router, prefix="/api", tags=["leads"])
app.include_router(tasks.router, prefix="/api", tags=["tasks"])
app.include_router(notes.router, prefix="/api", tags=["notes"])
app.include_router(search.router, prefix="/api", tags=["search"])
app.include_router(settings.router, prefix="/api", tags=["settings"])
app.include_router(events.router, prefix="/api", tags=["events"])
app.include_router(emails.router, prefix="/api", tags=["emails"])
app.include_router(calendar.router, prefix="/api", tags=["calendar"])
app.include_router(enrichment.router, prefix="/api", tags=["enrichment"])
app.include_router(discovery.router, prefix="/api", tags=["discovery"])
app.include_router(briefing.router, prefix="/api", tags=["briefing"])
