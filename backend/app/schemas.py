from datetime import date, datetime, time
from enum import Enum

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


class LeadStatus(str, Enum):
    NEW = "New"
    REVIEWING = "Reviewing"
    CONFIRMED = "Confirmed"
    NEEDS_CONTACT = "Needs Contact"
    REJECTED = "Rejected"
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
    email: EmailStr | None = None
    website: str = ""
    phone: str = ""
    owner: str = ""
    value: int = 0
    last_contact: date
    attio_person_record_id: str | None = None
    last_sync_message: str = ""

    @field_validator("email", mode="before")
    @classmethod
    def clean_unknown_client_email(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned or cleaned.lower() in {"unknown", "n/a", "none", "not available", "-"}:
                return None
        return value


class ClientCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    company: str = ""
    email: EmailStr | None = None
    website: str = ""
    phone: str = ""
    owner: str = ""
    value: int = Field(default=0, ge=0)
    last_contact: date | None = None

    @field_validator("email", mode="before")
    @classmethod
    def clean_unknown_email(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned or cleaned.lower() in {"unknown", "n/a", "none", "not available", "-"}:
                return None
        return value


class Lead(BaseModel):
    id: int
    name: str
    company: str
    email: EmailStr | None = None
    website: str
    status: LeadStatus
    source: str
    confidence_score: int
    outreach_angle: str
    estimated_value: int
    created_at: date
    contract_title: str = "Unknown"
    buyer_name: str = "Unknown"
    company_domain: str = ""
    portal_name: str = "Unknown"
    contract_url: str = ""
    contract_value: str = "Unknown"
    deadline: str = "Unknown"
    procurement_stage: str = "Unknown"
    contract_status: str = "Unknown"
    availability_status: str = "Unverified"
    availability_reason: str = ""
    availability_checked_at: datetime | None = None
    source_urls: list[str] = Field(default_factory=list)
    contact_name: str = ""
    contact_email: EmailStr | None = None
    contact_phone: str = ""
    contact_source_url: str = ""
    buyer_website: str = ""
    attio_company_record_id: str | None = None
    attio_person_record_id: str | None = None
    confirmed_at: datetime | None = None
    rejected_at: datetime | None = None
    draft_email_subject: str = ""
    draft_email_body: str = ""
    draft_email_generated_at: datetime | None = None
    last_sync_message: str = ""
    manual_notes: str = ""
    priority_score: int = Field(default=0, ge=0, le=100)
    priority_label: str = "Low"
    priority_reasons: list[str] = Field(default_factory=list)
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    seen_count: int = 1
    dedupe_key: str = ""

    @field_validator("email", "contact_email", mode="before")
    @classmethod
    def clean_unknown_email(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned or cleaned.lower() in {"unknown", "n/a", "none", "not available", "-"}:
                return None
        return value


class LeadUpdateRequest(BaseModel):
    status: LeadStatus | None = None
    manual_notes: str | None = None
    contact_name: str | None = None
    contact_email: EmailStr | None = None
    contact_phone: str | None = None

    @field_validator("contact_email", mode="before")
    @classmethod
    def clean_unknown_contact_email(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned or cleaned.lower() in {"unknown", "n/a", "none", "not available", "-"}:
                return None
        return value


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
    related_client_id: int | None = None
    attio_task_created: bool = False
    last_sync_message: str = ""


class CalendarCreateRequest(BaseModel):
    title: str = Field(min_length=1)
    date: date
    start_time: time
    end_time: time
    related_to: str = ""
    notes: str = ""
    related_client_id: int | None = None

    @model_validator(mode="after")
    def validate_time_order(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self
