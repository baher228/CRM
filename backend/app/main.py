from datetime import date, datetime, time
from enum import Enum

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr


class LeadStatus(str, Enum):
    NEW = "New"
    CONTACTED = "Contacted"
    QUALIFIED = "Qualified"
    PROPOSAL = "Proposal"


class Priority(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class HealthResponse(BaseModel):
    status: str
    service: str


class Client(BaseModel):
    id: int
    name: str
    company: str
    email: EmailStr
    phone: str
    owner: str
    value: int
    last_contact: date


class Lead(BaseModel):
    id: int
    name: str
    company: str
    email: EmailStr
    status: LeadStatus
    source: str
    estimated_value: int
    created_at: date


class Event(BaseModel):
    id: int
    title: str
    type: str
    client: str
    starts_at: datetime
    location: str
    owner: str


class EmailMessage(BaseModel):
    id: int
    subject: str
    from_name: str
    from_email: EmailStr
    preview: str
    received_at: datetime
    unread: bool
    priority: Priority


class CalendarItem(BaseModel):
    id: int
    title: str
    date: date
    start_time: time
    end_time: time
    related_to: str
    notes: str


app = FastAPI(title="CRM Scaffold API", version="0.1.0")

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


CLIENTS = [
    Client(
        id=1,
        name="Anna Petrova",
        company="Northstar Logistics",
        email="anna.petrova@northstar.example",
        phone="+44 20 7946 0211",
        owner="Maya Chen",
        value=42000,
        last_contact=date(2026, 6, 19),
    ),
    Client(
        id=2,
        name="Daniel Brooks",
        company="Evergreen Retail",
        email="daniel.brooks@evergreen.example",
        phone="+44 20 7946 0324",
        owner="Owen Hart",
        value=28500,
        last_contact=date(2026, 6, 21),
    ),
    Client(
        id=3,
        name="Sofia Novak",
        company="BrightPath Studio",
        email="sofia.novak@brightpath.example",
        phone="+44 20 7946 0457",
        owner="Maya Chen",
        value=16750,
        last_contact=date(2026, 6, 24),
    ),
]

LEADS = [
    Lead(
        id=1,
        name="Marcus Lee",
        company="Orbit Analytics",
        email="marcus.lee@orbit.example",
        status=LeadStatus.NEW,
        source="Website",
        estimated_value=18000,
        created_at=date(2026, 6, 22),
    ),
    Lead(
        id=2,
        name="Elena Smirnova",
        company="Blue Harbour Foods",
        email="elena@blueharbour.example",
        status=LeadStatus.CONTACTED,
        source="Referral",
        estimated_value=31000,
        created_at=date(2026, 6, 20),
    ),
    Lead(
        id=3,
        name="Tom Wallace",
        company="CivicGrid",
        email="tom.wallace@civicgrid.example",
        status=LeadStatus.PROPOSAL,
        source="Conference",
        estimated_value=54000,
        created_at=date(2026, 6, 15),
    ),
]

EVENTS = [
    Event(
        id=1,
        title="Quarterly account review",
        type="Meeting",
        client="Northstar Logistics",
        starts_at=datetime(2026, 6, 29, 10, 30),
        location="Zoom",
        owner="Maya Chen",
    ),
    Event(
        id=2,
        title="Product demo",
        type="Demo",
        client="Orbit Analytics",
        starts_at=datetime(2026, 6, 30, 14, 0),
        location="London office",
        owner="Owen Hart",
    ),
    Event(
        id=3,
        title="Contract renewal call",
        type="Call",
        client="Evergreen Retail",
        starts_at=datetime(2026, 7, 2, 9, 15),
        location="Phone",
        owner="Maya Chen",
    ),
]

EMAILS = [
    EmailMessage(
        id=1,
        subject="Updated proposal attached",
        from_name="Tom Wallace",
        from_email="tom.wallace@civicgrid.example",
        preview="Thanks for the walkthrough. I added a few notes to the pricing section.",
        received_at=datetime(2026, 6, 26, 16, 42),
        unread=True,
        priority=Priority.HIGH,
    ),
    EmailMessage(
        id=2,
        subject="Re: Renewal timeline",
        from_name="Daniel Brooks",
        from_email="daniel.brooks@evergreen.example",
        preview="The first week of July works for our finance review.",
        received_at=datetime(2026, 6, 26, 11, 18),
        unread=False,
        priority=Priority.MEDIUM,
    ),
    EmailMessage(
        id=3,
        subject="Intro from the conference",
        from_name="Marcus Lee",
        from_email="marcus.lee@orbit.example",
        preview="Great meeting you this week. Could we schedule a quick demo?",
        received_at=datetime(2026, 6, 25, 18, 5),
        unread=True,
        priority=Priority.MEDIUM,
    ),
]

CALENDAR = [
    CalendarItem(
        id=1,
        title="Prepare renewal packet",
        date=date(2026, 6, 29),
        start_time=time(9, 0),
        end_time=time(10, 0),
        related_to="Evergreen Retail",
        notes="Confirm billing contacts and revised pricing.",
    ),
    CalendarItem(
        id=2,
        title="Northstar account review",
        date=date(2026, 6, 29),
        start_time=time(10, 30),
        end_time=time(11, 30),
        related_to="Northstar Logistics",
        notes="Review adoption dashboard and next quarter goals.",
    ),
    CalendarItem(
        id=3,
        title="Orbit Analytics demo",
        date=date(2026, 6, 30),
        start_time=time(14, 0),
        end_time=time(15, 0),
        related_to="Orbit Analytics",
        notes="Focus on reporting workflows and integrations.",
    ),
]


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="crm-scaffold-api")


@app.get("/api/clients", response_model=list[Client])
def get_clients() -> list[Client]:
    return CLIENTS


@app.get("/api/leads", response_model=list[Lead])
def get_leads() -> list[Lead]:
    return LEADS


@app.get("/api/events", response_model=list[Event])
def get_events() -> list[Event]:
    return EVENTS


@app.get("/api/emails", response_model=list[EmailMessage])
def get_emails() -> list[EmailMessage]:
    return EMAILS


@app.get("/api/calendar", response_model=list[CalendarItem])
def get_calendar() -> list[CalendarItem]:
    return CALENDAR
