from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


LeadStatus = Literal["New", "Working", "Qualified", "Nurture", "Disqualified"]
TenderStatus = Literal["New", "Reviewing", "Qualified", "Rejected", "Snoozed", "Expired"]
TaskStatus = Literal["Open", "In progress", "Done", "Cancelled"]
Priority = Literal["Low", "Medium", "High"]


def _strip(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


def _required(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("must not be blank")
    return cleaned


def _url(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("must be a valid http or https URL")
    return cleaned


class Page(BaseModel):
    items: list[dict[str, Any]]
    next_cursor: str | None = None


class VersionedUpdate(BaseModel):
    version: int = Field(ge=1)


class AccountCreate(BaseModel):
    name: str = Field(min_length=1)
    legal_name: str = ""
    domain: str = ""
    website: str = ""
    phone: str = ""
    billing_email: EmailStr | None = None
    company_number: str = ""
    vat_number: str = ""
    source: str = "Manual"
    payment_terms_days: int = Field(default=14, ge=0, le=365)
    status: str = "Prospect"
    health_status: Literal["Healthy", "Watch", "At risk"] = "Healthy"
    health_score: int = Field(default=100, ge=0, le=100)
    renewal_date: date | None = None
    notes: str = ""
    roles: list[Literal["prospect", "client", "supplier", "partner"]] = Field(default_factory=lambda: ["prospect"])
    custom: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _required(value)

    @field_validator("website")
    @classmethod
    def validate_website(cls, value: str) -> str:
        return _url(value)

    @field_validator("legal_name", "domain", "phone", "company_number", "vat_number", "source", "status", "notes")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class AccountUpdate(VersionedUpdate):
    name: str | None = None
    legal_name: str | None = None
    domain: str | None = None
    website: str | None = None
    phone: str | None = None
    billing_email: EmailStr | None = None
    company_number: str | None = None
    vat_number: str | None = None
    source: str | None = None
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    status: str | None = None
    health_status: Literal["Healthy", "Watch", "At risk"] | None = None
    health_score: int | None = Field(default=None, ge=0, le=100)
    renewal_date: date | None = None
    notes: str | None = None
    roles: list[Literal["prospect", "client", "supplier", "partner"]] | None = None
    custom: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        return _required(value) if value is not None else None

    @field_validator("website")
    @classmethod
    def validate_website(cls, value: str | None) -> str | None:
        return _url(value) if value is not None else None


class ContactCreate(BaseModel):
    account_id: int | None = None
    first_name: str = ""
    last_name: str = ""
    display_name: str = Field(min_length=1)
    job_title: str = ""
    email: EmailStr | None = None
    phone: str = ""
    mobile: str = ""
    preferred_channel: str = "Email"
    source: str = "Manual"
    lawful_basis: str = ""
    status: str = "Active"
    notes: str = ""
    custom: dict[str, Any] = Field(default_factory=dict)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        return _required(value)

    @field_validator("first_name", "last_name", "job_title", "phone", "mobile", "preferred_channel", "source", "lawful_basis", "status", "notes")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class ContactUpdate(VersionedUpdate):
    account_id: int | None = None
    first_name: str | None = None
    last_name: str | None = None
    display_name: str | None = None
    job_title: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    mobile: str | None = None
    preferred_channel: str | None = None
    source: str | None = None
    lawful_basis: str | None = None
    status: str | None = None
    notes: str | None = None
    email_opt_out: bool | None = None
    custom: dict[str, Any] | None = None

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str | None) -> str | None:
        return _required(value) if value is not None else None


class LeadCreate(BaseModel):
    account_id: int | None = None
    contact_id: int | None = None
    title: str = Field(min_length=1)
    company: str = ""
    email: EmailStr | None = None
    phone: str = ""
    source: str = "Manual"
    status: LeadStatus = "New"
    score: int = Field(default=0, ge=0, le=100)
    estimated_value_minor: int = Field(default=0, ge=0)
    next_action: str = ""
    notes: str = ""

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _required(value)


class LeadUpdate(VersionedUpdate):
    account_id: int | None = None
    contact_id: int | None = None
    title: str | None = None
    company: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    source: str | None = None
    status: LeadStatus | None = None
    score: int | None = Field(default=None, ge=0, le=100)
    estimated_value_minor: int | None = Field(default=None, ge=0)
    next_action: str | None = None
    notes: str | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        return _required(value) if value is not None else None


class QualificationRequest(BaseModel):
    account_id: int | None = None
    account_name: str = ""
    contact_id: int | None = None
    contact_name: str = ""
    contact_email: EmailStr | None = None
    opportunity_title: str = ""
    stage_id: int | None = None
    value_minor: int = Field(default=0, ge=0)
    expected_close_date: date | None = None
    next_action: str = ""

    @model_validator(mode="after")
    def require_account(self):
        if self.account_id is None and not self.account_name.strip():
            raise ValueError("Choose an account or provide an account name")
        return self


class OpportunityCreate(BaseModel):
    account_id: int
    primary_contact_id: int | None = None
    tender_id: int | None = None
    stage_id: int | None = None
    type: str = "New business"
    title: str = Field(min_length=1)
    value_minor: int = Field(default=0, ge=0)
    probability_bps: int | None = Field(default=None, ge=0, le=10000)
    expected_close_date: date | None = None
    source: str = "Manual"
    next_action: str = ""
    notes: str = ""

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _required(value)


class OpportunityUpdate(VersionedUpdate):
    account_id: int | None = None
    primary_contact_id: int | None = None
    title: str | None = None
    type: str | None = None
    value_minor: int | None = Field(default=None, ge=0)
    probability_bps: int | None = Field(default=None, ge=0, le=10000)
    expected_close_date: date | None = None
    source: str | None = None
    next_action: str | None = None
    notes: str | None = None


class TransitionRequest(BaseModel):
    version: int = Field(ge=1)
    stage_id: int
    probability_bps: int | None = Field(default=None, ge=0, le=10000)
    loss_reason: str = ""


class TenderCreate(BaseModel):
    title: str = Field(min_length=1)
    buyer_name: str = ""
    portal_name: str = ""
    notice_reference: str = ""
    contract_url: str = ""
    contract_value_text: str = ""
    estimated_value_minor: int = Field(default=0, ge=0)
    deadline: datetime | date | None = None
    procurement_stage: str = ""
    contract_status: str = ""
    availability_status: str = "Unverified"
    availability_reason: str = ""
    niche: str = ""
    region: str = ""
    location: str = ""
    confidence_score: int = Field(default=0, ge=0, le=100)
    priority_score: int = Field(default=0, ge=0, le=100)
    priority_reasons: list[str] = Field(default_factory=list)
    outreach_angle: str = ""
    source_urls: list[str] = Field(default_factory=list)
    dedupe_key: str = ""

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _required(value)

    @field_validator("contract_url")
    @classmethod
    def validate_contract_url(cls, value: str) -> str:
        return _url(value)

    @field_validator("source_urls")
    @classmethod
    def validate_sources(cls, values: list[str]) -> list[str]:
        return [_url(value) for value in values if value.strip()]


class TenderDecision(BaseModel):
    version: int = Field(ge=1)
    reason: str = ""
    snoozed_until: date | None = None


class ActivityCreate(BaseModel):
    entity_type: str = Field(min_length=1)
    entity_id: int
    kind: str = "note"
    subject: str = Field(min_length=1)
    body: str = ""
    occurred_at: datetime | None = None

    @field_validator("entity_type", "subject")
    @classmethod
    def validate_required(cls, value: str) -> str:
        return _required(value)


class TaskCreate(BaseModel):
    entity_type: str = ""
    entity_id: int | None = None
    title: str = Field(min_length=1)
    description: str = ""
    status: TaskStatus = "Open"
    priority: Priority = "Medium"
    due_at: datetime | date | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _required(value)


class TaskUpdate(VersionedUpdate):
    entity_type: str | None = None
    entity_id: int | None = None
    title: str | None = None
    description: str | None = None
    status: TaskStatus | None = None
    priority: Priority | None = None
    due_at: datetime | date | None = None


class CalendarEventCreate(BaseModel):
    entity_type: str = ""
    entity_id: int | None = None
    title: str = Field(min_length=1)
    body: str = ""
    location: str = ""
    starts_at: datetime
    ends_at: datetime
    timezone: str = "Europe/London"
    all_day: bool = False
    recurrence: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _required(value)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class CalendarEventUpdate(VersionedUpdate):
    entity_type: str | None = None
    entity_id: int | None = None
    title: str | None = None
    body: str | None = None
    location: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    timezone: str | None = None
    all_day: bool | None = None
    recurrence: dict[str, Any] | None = None


class TagCreate(BaseModel):
    name: str = Field(min_length=1)
    color: str = "blue"

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _required(value)


class CustomFieldCreate(BaseModel):
    entity_type: str
    name: str
    field_type: Literal["text", "number", "date", "boolean", "select", "multiselect", "url"]
    options: list[str] = Field(default_factory=list)
    required: bool = False
    position: int = 0

    @field_validator("entity_type", "name")
    @classmethod
    def validate_required(cls, value: str) -> str:
        return _required(value)


class SavedViewCreate(BaseModel):
    entity_type: str
    name: str
    config: dict[str, Any]

    @field_validator("entity_type", "name")
    @classmethod
    def validate_required(cls, value: str) -> str:
        return _required(value)


class BusinessProfileUpdate(VersionedUpdate):
    legal_name: str | None = None
    trading_name: str | None = None
    company_number: str | None = None
    vat_registered: bool | None = None
    vat_number: str | None = None
    vat_scheme: Literal["Standard", "Flat Rate", "Cash Accounting"] | None = None
    vat_effective_from: date | None = None
    vat_effective_to: date | None = None
    tax_codes_approved: bool | None = None
    registered_address: dict[str, Any] | None = None
    invoice_email: EmailStr | None = None
    invoice_phone: str | None = None
    bank_details: str | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    timezone: str | None = None
    default_vat_bps: int | None = Field(default=None, ge=0, le=10000)
    default_payment_terms_days: int | None = Field(default=None, ge=0, le=365)

    @model_validator(mode="after")
    def validate_vat_dates(self):
        if self.vat_effective_from and self.vat_effective_to and self.vat_effective_to < self.vat_effective_from:
            raise ValueError("vat_effective_to must not be before vat_effective_from")
        return self


class BulkArchiveRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=100)


class MergeRequest(BaseModel):
    source_id: int
    target_id: int
    source_version: int | None = Field(default=None, ge=1)
    target_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def distinct_ids(self):
        if self.source_id == self.target_id:
            raise ValueError("source_id and target_id must differ")
        return self
