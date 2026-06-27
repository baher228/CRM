from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator


class LeadSource(BaseModel):
    object_slug: str
    record_id: str
    name: str
    domain: str | None = None
    email: str | None = None
    existing_fingerprint: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class FoundPage(BaseModel):
    url: str
    title: str = ""
    snippet: str = ""
    score: float = 0.0
    page_type: str = "general"
    official_domain: bool = False


class ExtractedPage(BaseModel):
    url: str
    title: str = ""
    content: str = ""
    page_type: str = "general"
    failed: bool = False
    error: str | None = None


class ClassificationEvidence(BaseModel):
    label: str
    detail: str
    source_url: str


class LeadClassification(BaseModel):
    industry: str = "Unknown"
    segment: str = "Unknown"
    pricing_model: str = "Unknown"
    compliance_posture: str = "Unknown"
    procurement_signals: list[str] = Field(default_factory=list)
    outreach_triggers: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    fit_score: int = Field(default=0, ge=0, le=100)
    urgency_score: int = Field(default=0, ge=0, le=100)
    confidence_score: int = Field(default=0, ge=0, le=100)
    evidence: list[ClassificationEvidence] = Field(default_factory=list)

    @field_validator("procurement_signals", "outreach_triggers", "risks", mode="before")
    @classmethod
    def listify(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value


class EnrichmentSummary(BaseModel):
    lead: LeadSource
    selected_urls: list[str]
    classification: LeadClassification
    summary_text: str
    source_urls: list[str]
    fingerprint: str
    generated_at: datetime


class LeadRunResult(BaseModel):
    record_id: str
    name: str
    status: Literal["enriched", "skipped", "failed", "dry_run"]
    message: str
    fit_score: int | None = None
    urgency_score: int | None = None
    confidence_score: int | None = None
    source_urls: list[str] = Field(default_factory=list)


class EnrichmentRunRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=100)
    dry_run: bool = True


class EnrichmentRunResponse(BaseModel):
    dry_run: bool
    requested_limit: int
    fetched: int
    enriched: int
    skipped: int
    failed: int
    results: list[LeadRunResult]

