from datetime import date, datetime, time
from enum import Enum

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
    website: str
    phone: str
    owner: str
    value: int
    last_contact: date


class Lead(BaseModel):
    id: int
    name: str
    company: str
    email: EmailStr
    website: str
    status: LeadStatus
    source: str
    confidence_score: int
    outreach_angle: str
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
