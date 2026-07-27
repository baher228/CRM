from datetime import datetime

from app import platform_db
from app.data import LEADS
from app.lead_discovery.models import DiscoveryCompanyResult
from app.lead_enrichment.config import EnrichmentSettings
from app.schemas import Lead, LeadBulkRequest, LeadBulkResponse, LeadCreateRequest, LeadStatus, LeadUpdateRequest
from app.services import crm_store
from app.services.contact_lookup_service import find_contact_for_lead
from app.services.draft_email_service import draft_email_for_lead
from app.services.lead_discovery_mapper import (
    has_useful_result,
    is_persistable_result,
    lead_key,
    merge_result_into_lead,
    result_key,
    result_to_lead,
)
from app.services.lead_scoring import lead_sort_key, score_lead, with_availability
from app.services.lead_sources import canonical_url_key, utc_now
from app.v1 import core_service as v1_service
from app.v1 import models as v1_models


def list_leads() -> list[Lead]:
    settings = EnrichmentSettings()
    leads = _with_priority(crm_store.list_leads())
    if settings.crm_include_demo_leads:
        existing_ids = {lead.id for lead in leads}
        leads.extend(lead for lead in LEADS if lead.id not in existing_ids)
    return sorted(leads, key=lead_sort_key)


def get_lead(lead_id: int) -> Lead | None:
    return crm_store.get_lead(lead_id)


def known_discovery_keys() -> set[str]:
    keys: set[str] = set()
    for lead in _with_priority(crm_store.list_leads()):
        keys.add(lead_key(lead))
        for url in [lead.contract_url, lead.website, *lead.source_urls]:
            key = canonical_url_key(url)
            if key:
                keys.add(key)
    try:
        with platform_db.connect() as conn:
            keys.update(row["dedupe_key"] for row in conn.execute("SELECT dedupe_key FROM tender_notices"))
    except Exception:
        pass
    return {key for key in keys if key}


def discovery_key_for_url(url: str) -> str:
    return canonical_url_key(url)


def add_discovered_leads(results: list[DiscoveryCompanyResult]) -> None:
    existing = crm_store.list_leads()
    by_key = {lead_key(lead): lead for lead in existing}
    next_id = max([lead.id for lead in [*LEADS, *existing]], default=0) + 1
    for result in results:
        if not is_persistable_result(result) or not has_useful_result(result):
            continue
        key = result_key(result)
        if key in by_key:
            merged = merge_result_into_lead(by_key[key], result)
            crm_store.save_lead(merged)
            by_key[key] = merged
        else:
            lead = result_to_lead(result, next_id)
            crm_store.create_lead(lead)
            by_key[lead_key(lead)] = lead
            next_id += 1
        _persist_tender(result)


def update_lead(lead_id: int, request: LeadUpdateRequest) -> Lead | None:
    lead = get_lead(lead_id)
    if not lead:
        return None
    update = {
        key: value.strip() if isinstance(value, str) else value
        for key, value in request.model_dump(exclude_unset=True).items()
    }
    return crm_store.save_lead(score_lead(lead.model_copy(update=update)))


def create_lead(request: LeadCreateRequest) -> Lead:
    now = utc_now()
    lead = Lead(
        id=0,
        name=request.name.strip(),
        company=request.company.strip() or request.name.strip(),
        email=request.email,
        website=request.website.strip(),
        status=request.status,
        source=request.source.strip() or "Manual",
        confidence_score=request.confidence_score,
        outreach_angle=request.outreach_angle.strip(),
        estimated_value=request.estimated_value,
        created_at=now.date(),
        manual_notes=request.manual_notes.strip(),
        next_action=request.next_action.strip(),
        first_seen_at=now,
        last_seen_at=now,
        sync_status="Local",
        last_sync_message="Saved in CRM Workspace",
    )
    return crm_store.create_lead(score_lead(with_availability(lead)))


def delete_lead(lead_id: int) -> bool:
    return crm_store.delete_lead(lead_id)


def bulk_update_leads(request: LeadBulkRequest) -> LeadBulkResponse:
    updated: list[Lead] = []
    failed = 0
    for lead_id in request.lead_ids:
        if request.action == "reject":
            next_lead = reject_lead(lead_id)
        elif request.action == "status" and request.status:
            next_lead = update_lead(lead_id, LeadUpdateRequest(status=request.status))
        elif request.action == "review":
            next_lead = update_lead(lead_id, LeadUpdateRequest(status=LeadStatus.REVIEWING))
        else:
            next_lead = None
        if next_lead:
            updated.append(next_lead)
        else:
            failed += 1
    return LeadBulkResponse(updated=len(updated), failed=failed, leads=updated)


def reject_lead(lead_id: int) -> Lead | None:
    lead = get_lead(lead_id)
    if not lead:
        return None
    return crm_store.save_lead(
        score_lead(
            lead.model_copy(
                update={
                    "status": LeadStatus.REJECTED,
                    "rejected_at": utc_now(),
                    "sync_status": "Local",
                    "last_sync_message": "Lead rejected in CRM Workspace",
                }
            )
        )
    )


async def confirm_lead(lead_id: int) -> Lead | None:
    lead = get_lead(lead_id)
    if not lead:
        return None
    working = with_availability(lead)
    if working.availability_status == "Unavailable":
        return crm_store.save_lead(
            working.model_copy(
                update={
                    "status": LeadStatus.REVIEWING,
                    "last_sync_message": f"Cannot qualify unavailable contract: {working.availability_reason}",
                }
            )
        )
    settings = EnrichmentSettings()
    messages = ["Qualified in CRM Workspace"]
    if not working.contact_email:
        try:
            settings.require_read_keys()
            contact = await find_contact_for_lead(working, settings)
            if contact.contact_email:
                working = working.model_copy(
                    update={
                        "contact_name": contact.contact_name or working.contact_name,
                        "contact_email": contact.contact_email,
                        "email": contact.contact_email,
                        "contact_phone": contact.contact_phone or working.contact_phone,
                        "contact_source_url": contact.contact_source_url,
                    }
                )
                messages.append("Contact found")
        except Exception as exc:
            messages.append(f"Contact lookup unavailable: {_safe_error_message(exc, settings)}")
    draft_subject = working.draft_email_subject
    draft_body = working.draft_email_body
    draft_at = working.draft_email_generated_at
    if working.contact_email:
        try:
            settings.require_read_keys()
            draft = await draft_email_for_lead(working, settings)
            draft_subject, draft_body, draft_at = draft.subject, draft.body, utc_now()
            messages.append("Draft email generated")
        except Exception as exc:
            messages.append(f"Draft unavailable: {_safe_error_message(exc, settings)}")
    status = LeadStatus.CONFIRMED if working.contact_email else LeadStatus.NEEDS_CONTACT
    confirmed = score_lead(
        working.model_copy(
            update={
                "status": status,
                "confirmed_at": utc_now(),
                "draft_email_subject": draft_subject,
                "draft_email_body": draft_body,
                "draft_email_generated_at": draft_at,
                "sync_status": "Local",
                "last_sync_message": "; ".join(messages),
            }
        )
    )
    confirmed = crm_store.save_lead(confirmed)
    _qualify_into_primary_crm(confirmed)
    return confirmed


def _persist_tender(result: DiscoveryCompanyResult) -> None:
    try:
        with platform_db.connect() as conn:
            v1_service.create_tender(
                conn,
                v1_models.TenderCreate(
                    title=result.contract_title if result.contract_title != "Unknown" else result.company_name,
                    buyer_name=result.buyer_name if result.buyer_name != "Unknown" else result.company_name,
                    portal_name=result.portal_name if result.portal_name != "Unknown" else "",
                    contract_url=result.contract_url or "",
                    contract_value_text=result.contract_value if result.contract_value != "Unknown" else "",
                    estimated_value_minor=_minor_value(result.contract_value),
                    deadline=None,
                    procurement_stage=result.procurement_stage if result.procurement_stage != "Unknown" else "",
                    contract_status=result.contract_status if result.contract_status != "Unknown" else "",
                    availability_status=result.availability_status,
                    availability_reason=result.availability_reason,
                    confidence_score=result.confidence_score or 0,
                    source_urls=result.source_urls,
                    dedupe_key=result.dedupe_key or result_key(result),
                ),
            )
    except Exception:
        # The compatibility list must remain usable if the primary store is temporarily unavailable.
        return


def _qualify_into_primary_crm(lead: Lead) -> None:
    result = DiscoveryCompanyResult(
        domain=lead.company_domain,
        company_name=lead.company,
        status="upserted",
        message="Qualified locally",
        confidence_score=lead.confidence_score,
        source_urls=lead.source_urls,
        contract_title=lead.contract_title,
        buyer_name=lead.buyer_name,
        portal_name=lead.portal_name,
        contract_url=lead.contract_url,
        contract_value=lead.contract_value,
        deadline=lead.deadline,
        procurement_stage=lead.procurement_stage,
        contract_status=lead.contract_status,
        availability_status=lead.availability_status,
        availability_reason=lead.availability_reason,
        buyer_website=lead.buyer_website,
        contact_name=lead.contact_name,
        contact_email=str(lead.contact_email or ""),
        contact_phone=lead.contact_phone,
        dedupe_key=lead.dedupe_key or lead_key(lead),
    )
    try:
        with platform_db.connect() as conn:
            tender = v1_service.create_tender(
                conn,
                v1_models.TenderCreate(
                    title=lead.contract_title if lead.contract_title != "Unknown" else lead.name,
                    buyer_name=lead.buyer_name if lead.buyer_name != "Unknown" else lead.company,
                    portal_name=lead.portal_name if lead.portal_name != "Unknown" else "",
                    contract_url=lead.contract_url or (lead.website if lead.website.startswith("http") else ""),
                    contract_value_text=lead.contract_value if lead.contract_value != "Unknown" else "",
                    estimated_value_minor=lead.estimated_value * 100,
                    procurement_stage=lead.procurement_stage if lead.procurement_stage != "Unknown" else "",
                    contract_status=lead.contract_status if lead.contract_status != "Unknown" else "",
                    availability_status=lead.availability_status,
                    availability_reason=lead.availability_reason,
                    confidence_score=lead.confidence_score,
                    priority_score=lead.priority_score,
                    priority_reasons=lead.priority_reasons,
                    outreach_angle=lead.outreach_angle,
                    source_urls=lead.source_urls,
                    dedupe_key=result.dedupe_key,
                ),
            )
            if not tender.get("linked_opportunity_id"):
                v1_service.qualify_tender(
                    conn,
                    tender["id"],
                    v1_models.QualificationRequest(
                        account_name=lead.buyer_name if lead.buyer_name != "Unknown" else lead.company or lead.name,
                        contact_name=lead.contact_name,
                        contact_email=lead.contact_email,
                        opportunity_title=lead.contract_title if lead.contract_title != "Unknown" else lead.name,
                        value_minor=lead.estimated_value * 100,
                        next_action=lead.next_action,
                    ),
                )
    except Exception:
        return


def _safe_error_message(exc: Exception, settings: EnrichmentSettings) -> str:
    message = str(exc) or exc.__class__.__name__
    for secret in (settings.tavily_api_key, settings.gemini_api_key):
        if secret and len(secret) > 8:
            message = message.replace(secret, "[redacted]")
    return message[:300]


def _with_priority(leads: list[Lead]) -> list[Lead]:
    scored: list[Lead] = []
    for lead in leads:
        now = utc_now()
        updated = score_lead(
            with_availability(
                lead.model_copy(
                    update={
                        "dedupe_key": lead.dedupe_key or lead_key(lead),
                        "first_seen_at": lead.first_seen_at or now,
                        "last_seen_at": lead.last_seen_at or now,
                        "seen_count": lead.seen_count or 1,
                    }
                )
            )
        )
        if updated != lead:
            crm_store.save_lead(updated)
        scored.append(updated)
    return scored


def _minor_value(value: str) -> int:
    import re

    numbers = re.findall(r"[\d,]+(?:\.\d+)?", value or "")
    if not numbers:
        return 0
    try:
        return int(float(numbers[0].replace(",", "")) * 100)
    except ValueError:
        return 0
