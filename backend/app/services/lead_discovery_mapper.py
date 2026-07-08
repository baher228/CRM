from datetime import date

from app.lead_discovery.models import DiscoveryCompanyResult
from app.schemas import Lead, LeadStatus
from app.services.lead_scoring import score_lead, with_availability
from app.services.lead_sources import (
    best_contract_url,
    best_source_value,
    best_value,
    canonical_url_key,
    clean_email,
    clean_source_urls,
    datetime_from_iso,
    fallback_key,
    is_known,
    merge_urls,
    split_contact,
    utc_now,
    website_from_domain,
)


def result_to_lead(result: DiscoveryCompanyResult, lead_id: int) -> Lead:
    contact_name, contact_email, contact_phone = split_contact(result.buyer_contact)
    contact_name = result.contact_name or contact_name
    contact_email = clean_email(result.contact_email) or contact_email
    contact_phone = result.contact_phone or contact_phone
    contract_url = best_contract_url(result.contract_url, result.source_urls)
    source_urls = clean_source_urls([contract_url, *result.source_urls])
    now = utc_now()
    lead = Lead(
        id=lead_id,
        name=result.contract_title if result.contract_title != "Unknown" else result.company_name,
        company=result.buyer_name if result.buyer_name != "Unknown" else result.company_name,
        email=contact_email,
        website=contract_url or website_from_domain(result.portal_domain or result.domain),
        status=LeadStatus.NEW,
        source=result.portal_name if result.portal_name != "Unknown" else "Discovery",
        confidence_score=result.confidence_score or 0,
        outreach_angle=outreach_angle(result),
        estimated_value=estimated_value(result.contract_value),
        created_at=date.today(),
        contract_title=result.contract_title,
        buyer_name=result.buyer_name,
        company_domain=result.domain,
        portal_name=result.portal_name,
        contract_url=contract_url,
        contract_value=result.contract_value,
        deadline=result.deadline,
        procurement_stage=result.procurement_stage,
        contract_status=result.contract_status,
        availability_status=result.availability_status,
        availability_reason=result.availability_reason,
        availability_checked_at=datetime_from_iso(result.availability_checked_at),
        source_urls=source_urls,
        contact_name=contact_name,
        contact_email=contact_email,
        contact_phone=contact_phone,
        contact_source_url="",
        buyer_website=result.buyer_website,
        first_seen_at=now,
        last_seen_at=now,
        seen_count=1,
        dedupe_key=result_key(result),
    )
    return score_lead(with_availability(lead))


def merge_result_into_lead(lead: Lead, result: DiscoveryCompanyResult) -> Lead:
    now = utc_now()
    incoming = result_to_lead(result, lead.id)
    merged = lead.model_copy(
        update={
            "name": best_value(lead.name, incoming.name),
            "company": best_value(lead.company, incoming.company),
            "email": lead.email or incoming.email,
            "website": best_source_value(lead.website, incoming.website),
            "source": best_value(lead.source, incoming.source),
            "confidence_score": max(lead.confidence_score or 0, incoming.confidence_score or 0),
            "outreach_angle": best_value(lead.outreach_angle, incoming.outreach_angle),
            "estimated_value": max(lead.estimated_value or 0, incoming.estimated_value or 0),
            "contract_title": best_value(lead.contract_title, incoming.contract_title),
            "buyer_name": best_value(lead.buyer_name, incoming.buyer_name),
            "company_domain": best_value(lead.company_domain, incoming.company_domain),
            "portal_name": best_value(lead.portal_name, incoming.portal_name),
            "contract_url": best_source_value(lead.contract_url, incoming.contract_url),
            "contract_value": best_value(lead.contract_value, incoming.contract_value),
            "deadline": best_value(lead.deadline, incoming.deadline),
            "procurement_stage": best_value(lead.procurement_stage, incoming.procurement_stage),
            "contract_status": best_value(lead.contract_status, incoming.contract_status),
            "availability_status": incoming.availability_status or lead.availability_status,
            "availability_reason": incoming.availability_reason or lead.availability_reason,
            "availability_checked_at": incoming.availability_checked_at or lead.availability_checked_at,
            "source_urls": merge_urls(lead.source_urls, incoming.source_urls),
            "contact_name": best_value(lead.contact_name, incoming.contact_name),
            "contact_email": lead.contact_email or incoming.contact_email,
            "contact_phone": best_value(lead.contact_phone, incoming.contact_phone),
            "contact_source_url": best_value(lead.contact_source_url, incoming.contact_source_url),
            "buyer_website": best_value(lead.buyer_website, incoming.buyer_website),
            "last_seen_at": now,
            "seen_count": (lead.seen_count or 1) + 1,
            "dedupe_key": lead.dedupe_key or incoming.dedupe_key,
        }
    )
    return score_lead(merged)


def lead_key(lead: Lead) -> str:
    if lead.dedupe_key:
        return lead.dedupe_key
    url_key = canonical_url_key(lead.contract_url or lead.website)
    if url_key:
        return url_key
    return fallback_key(lead.portal_name or lead.source, lead.buyer_name or lead.company, lead.contract_title or lead.name)


def result_key(result: DiscoveryCompanyResult) -> str:
    url_key = canonical_url_key(best_contract_url(result.contract_url, result.source_urls))
    if url_key:
        return url_key
    return fallback_key(result.portal_name, result.buyer_name or result.company_name, result.contract_title)


def has_useful_result(result: DiscoveryCompanyResult) -> bool:
    return any(
        is_known(value)
        for value in (
            result.contract_title,
            result.buyer_name,
            result.company_name,
            result.contract_url,
            result.domain,
        )
    )


def is_persistable_result(result: DiscoveryCompanyResult) -> bool:
    if result.status in {"upserted", "dry_run"}:
        return True
    if result.status != "failed":
        return False
    if not (result.contract_url or result.source_urls):
        return False
    return any(is_known(value) for value in (result.contract_title, result.buyer_name, result.company_name))


def outreach_angle(result: DiscoveryCompanyResult) -> str:
    parts = []
    if result.contract_value and result.contract_value != "Unknown":
        parts.append(f"Value: {result.contract_value}")
    if result.deadline and result.deadline != "Unknown":
        parts.append(f"Deadline: {result.deadline}")
    if result.procurement_stage and result.procurement_stage != "Unknown":
        parts.append(f"Stage: {result.procurement_stage}")
    if result.message:
        parts.append(result.message)
    return " | ".join(parts) or "Discovered from public procurement portal."


def estimated_value(value: str) -> int:
    digits = "".join(char for char in value if char.isdigit())
    if not digits:
        return 0
    return min(int(digits), 2_000_000_000)
