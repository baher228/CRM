import re

from app.schemas import Lead, LeadStatus
from app.services.contract_availability import assess_contract_availability
from app.services.lead_sources import is_known


def score_lead(lead: Lead) -> Lead:
    score = 0
    reasons: list[str] = []
    confidence = lead.confidence_score or 0
    score += min(30, round(confidence * 0.3))
    if confidence >= 85:
        reasons.append("High confidence")
    elif confidence >= 65:
        reasons.append("Good confidence")

    if is_known(lead.deadline):
        score += 15
        reasons.append("Deadline parsed")
    else:
        score -= 6
        reasons.append("Deadline missing")

    if is_known(lead.contract_value) or lead.estimated_value:
        score += 14
        reasons.append("Value parsed")
    if is_known(lead.contact_name) or lead.contact_email or is_known(lead.contact_phone):
        score += 12
        reasons.append("Contact details found")
    else:
        score -= 4
        reasons.append("Contact missing")

    stage_text = f"{lead.procurement_stage} {lead.outreach_angle} {lead.contract_title}".lower()
    if any(token in stage_text for token in ["open", "active", "tender", "opportunity"]):
        score += 10
        reasons.append("Active tender signal")
    if any(token in stage_text for token in ["framework", "dps", "dynamic purchasing"]):
        score += 8
        reasons.append("Framework route")

    if lead.availability_status == "Available":
        score += 12
        reasons.append("Still available")
    elif lead.availability_status == "Unavailable":
        score = min(score, 20)
        reasons.append("No longer available")
    else:
        score -= 8
        reasons.append("Availability unverified")

    if is_known(lead.portal_name) or is_known(lead.source):
        score += 7
        reasons.append("Known portal")
    if is_known(lead.contract_url) or lead.source_urls:
        score += 8
        reasons.append("Source link saved")
    if is_known(lead.contract_title) and is_known(lead.buyer_name):
        score += 10
        reasons.append("Buyer and title parsed")

    if lead.status == LeadStatus.REJECTED:
        score = min(score, 15)
        reasons.append("Rejected")
    elif lead.status == LeadStatus.CONFIRMED:
        score = max(score, 85)
        reasons.append("Confirmed")

    if lead.availability_status == "Unavailable":
        score = min(score, 20)

    score = max(0, min(100, score))
    return lead.model_copy(
        update={
            "priority_score": score,
            "priority_label": priority_label(score),
            "priority_reasons": reasons[:6],
        }
    )


def with_availability(lead: Lead) -> Lead:
    should_keep_existing = (
        lead.availability_checked_at
        and lead.availability_status
        and lead.availability_reason != "Notice has open/active tender wording, but no parseable deadline"
        and "deadline text: Unknown" not in lead.availability_reason
    )
    if should_keep_existing:
        return lead
    availability = assess_contract_availability(
        lead.deadline,
        lead.contract_status,
        lead.procurement_stage,
        lead.outreach_angle,
    )
    return lead.model_copy(
        update={
            "availability_status": availability.status,
            "availability_reason": availability.reason,
            "availability_checked_at": availability.checked_at,
        }
    )


def priority_label(score: int) -> str:
    if score >= 80:
        return "Hot"
    if score >= 60:
        return "Warm"
    if score >= 40:
        return "Watch"
    return "Low"


def lead_sort_key(lead: Lead) -> tuple:
    return (
        -(lead.priority_score or 0),
        parse_dateish(lead.deadline),
        -(lead.confidence_score or 0),
    )


def parse_dateish(value: str) -> int:
    if not is_known(value):
        return 99999999
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", value)
    if match:
        return int(f"{match.group(1)}{int(match.group(2)):02d}{int(match.group(3)):02d}")
    return 99999999
