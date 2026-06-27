import re
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


AvailabilityStatus = Literal["Available", "Unavailable", "Unverified"]


class ContractAvailability(BaseModel):
    status: AvailabilityStatus
    reason: str
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deadline_date: date | None = None


UNAVAILABLE_TERMS = {
    "awarded",
    "award notice",
    "cancelled",
    "canceled",
    "closed",
    "deadline passed",
    "expired",
    "no longer available",
    "not accepting",
    "terminated",
    "withdrawn",
}

AVAILABLE_TERMS = {
    "accepting",
    "active",
    "available",
    "invitation to tender",
    "open",
}


def assess_contract_availability(
    deadline: str = "",
    contract_status: str = "",
    procurement_stage: str = "",
    message: str = "",
    today: date | None = None,
) -> ContractAvailability:
    today = today or date.today()
    text = " ".join([contract_status or "", procurement_stage or "", message or ""]).lower()
    parsed_deadline = parse_deadline_date(deadline)

    if any(term in text for term in UNAVAILABLE_TERMS):
        return ContractAvailability(
            status="Unavailable",
            reason=_reason("Notice says closed/awarded/cancelled", deadline, parsed_deadline),
            deadline_date=parsed_deadline,
        )

    if parsed_deadline and parsed_deadline < today:
        return ContractAvailability(
            status="Unavailable",
            reason=f"Deadline passed on {parsed_deadline.isoformat()}",
            deadline_date=parsed_deadline,
        )

    if parsed_deadline:
        return ContractAvailability(
            status="Available",
            reason=f"Deadline is {parsed_deadline.isoformat()}",
            deadline_date=parsed_deadline,
        )

    if any(term in text for term in AVAILABLE_TERMS):
        return ContractAvailability(
            status="Available",
            reason="Notice has open/active tender wording, but no parseable deadline",
            deadline_date=None,
        )

    return ContractAvailability(
        status="Unverified",
        reason="No parseable deadline or clear open/closed status",
        deadline_date=None,
    )


def parse_deadline_date(value: str | None) -> date | None:
    if not value:
        return None
    text = _normalize_date_text(value)
    candidates = _date_candidates(text)
    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d %B %Y",
        "%d %b %Y",
        "%B %d %Y",
        "%b %d %Y",
        "%d %B, %Y",
        "%d %b, %Y",
        "%B %d, %Y",
        "%b %d, %Y",
    ]
    for candidate in candidates:
        for fmt in formats:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


def _date_candidates(text: str) -> list[str]:
    candidates = [text]
    candidates.extend(re.findall(r"20\d{2}[-/]\d{1,2}[-/]\d{1,2}", text))
    candidates.extend(re.findall(r"\d{1,2}[-/]\d{1,2}[-/]20\d{2}", text))
    candidates.extend(re.findall(r"\d{1,2}\s+[a-zA-Z]+,?\s+20\d{2}", text))
    candidates.extend(re.findall(r"[a-zA-Z]+\s+\d{1,2},?\s+20\d{2}", text))
    seen: set[str] = set()
    deduped: list[str] = []
    for candidate in candidates:
        normalized = candidate.strip(" .,;:")
        if normalized and normalized not in seen:
            seen.add(normalized)
            deduped.append(normalized)
    return deduped


def _normalize_date_text(value: str) -> str:
    text = re.sub(r"\b(\d{1,2})(st|nd|rd|th)\b", r"\1", value, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _reason(base: str, deadline: str, parsed_deadline: date | None) -> str:
    if parsed_deadline:
        return f"{base}; deadline {parsed_deadline.isoformat()}"
    if deadline and deadline.strip().lower() not in {"unknown", "n/a", "none", "-"}:
        return f"{base}; deadline text: {deadline}"
    return base
