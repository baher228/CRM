from typing import Literal

from pydantic import BaseModel, Field, field_validator


class CompanySearchResult(BaseModel):
    url: str
    title: str = ""
    snippet: str = ""
    score: float = 0.0
    domain: str
    page_type: str = "contract_notice"
    portal_name: str = ""


class CompanyCandidate(BaseModel):
    domain: str
    portal_name: str = ""
    urls: list[CompanySearchResult] = Field(default_factory=list)


class SelectedUrl(BaseModel):
    url: str
    domain: str
    page_type: str
    title: str = ""
    score: float = 0.0
    extract_depth: Literal["basic", "advanced"] = "basic"


class ExtractedCompanyPage(BaseModel):
    url: str
    domain: str
    page_type: str
    title: str = ""
    content: str = ""
    failed: bool = False
    error: str | None = None


class CompanyProfile(BaseModel):
    company_name: str
    domain: str
    contract_title: str = "Unknown"
    buyer_name: str = "Unknown"
    portal_name: str = "Unknown"
    portal_domain: str = "Unknown"
    contract_url: str = ""
    contract_value: str = "Unknown"
    deadline: str = "Unknown"
    procurement_stage: str = "Unknown"
    contract_status: str = "Unknown"
    availability_status: str = "Unverified"
    availability_reason: str = ""
    availability_checked_at: str = ""
    buyer_website: str = ""
    buyer_contact: str = "Unknown"
    contact_name: str = ""
    contact_email: str = ""
    contact_phone: str = ""
    priority_score: int = Field(default=0, ge=0, le=100)
    priority_label: str = "Low"
    priority_reasons: list[str] = Field(default_factory=list)
    dedupe_key: str = ""
    cpv_codes: list[str] = Field(default_factory=list)
    location: str = "Unknown"
    services: list[str] = Field(default_factory=list)
    industry: str = "Unknown"
    segment: str = "Unknown"
    pricing_model: str = "Unknown"
    compliance_signals: list[str] = Field(default_factory=list)
    contract_or_procurement_signals: list[str] = Field(default_factory=list)
    outreach_angle: str = "Unknown"
    confidence_score: int = Field(default=0, ge=0, le=100)
    source_urls: list[str] = Field(default_factory=list)

    @field_validator("services", "compliance_signals", "contract_or_procurement_signals", "cpv_codes", mode="before")
    @classmethod
    def listify(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value


class CompanyDiscoverySummary(BaseModel):
    profile: CompanyProfile
    niche: str
    region: str | None
    fingerprint: str
    summary_text: str
    source_urls: list[str]


class DiscoveryRunRequest(BaseModel):
    niche: str = Field(min_length=2, max_length=120)
    region: str | None = Field(default=None, max_length=120)
    limit: int = Field(default=10, ge=1, le=100)
    dry_run: bool = True
    portals: list[str] = Field(default_factory=list)
    deadline_window: str = ""
    minimum_value: str = ""
    open_notices_only: bool = True


DiscoveryPhase = Literal["queued", "searching", "extracting", "parsing", "saving", "syncing", "completed", "cancelled", "failed"]
DiscoveryRowStatus = Literal[
    "searching",
    "extracting",
    "parsing",
    "saving",
    "syncing",
    "upserted",
    "skipped",
    "failed",
    "dry_run",
]


class DiscoveryCompanyResult(BaseModel):
    domain: str
    company_name: str = "Unknown"
    status: DiscoveryRowStatus
    message: str
    confidence_score: int | None = None
    source_urls: list[str] = Field(default_factory=list)
    contract_title: str = "Unknown"
    buyer_name: str = "Unknown"
    portal_name: str = "Unknown"
    portal_domain: str = "Unknown"
    contract_url: str = ""
    contract_value: str = "Unknown"
    deadline: str = "Unknown"
    procurement_stage: str = "Unknown"
    contract_status: str = "Unknown"
    availability_status: str = "Unverified"
    availability_reason: str = ""
    availability_checked_at: str = ""
    buyer_website: str = ""
    buyer_contact: str = "Unknown"
    contact_name: str = ""
    contact_email: str = ""
    contact_phone: str = ""
    priority_score: int = Field(default=0, ge=0, le=100)
    priority_label: str = "Low"
    priority_reasons: list[str] = Field(default_factory=list)
    dedupe_key: str = ""


class DiscoveryRunResponse(BaseModel):
    dry_run: bool
    niche: str
    region: str | None
    requested_limit: int
    discovered: int
    upserted: int
    skipped: int
    failed: int
    results: list[DiscoveryCompanyResult]


class DiscoveryJobStartResponse(BaseModel):
    job_id: str


class DiscoveryJobState(BaseModel):
    job_id: str
    state: Literal["running", "completed", "failed"]
    phase: DiscoveryPhase
    message: str
    elapsed_seconds: float
    completed: int = 0
    total: int = 0
    dry_run: bool
    niche: str
    region: str | None
    requested_limit: int
    discovered: int = 0
    upserted: int = 0
    skipped: int = 0
    failed: int = 0
    results: list[DiscoveryCompanyResult] = Field(default_factory=list)


class DiscoveryJobStatusResponse(DiscoveryJobState):
    pass
