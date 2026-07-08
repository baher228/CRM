from datetime import datetime
from typing import Any

from app.data import LEADS
from app.lead_discovery.models import DiscoveryCompanyResult
from app.lead_enrichment.clients.attio_client import AttioClient, record_id_from_response
from app.lead_enrichment.clients.http import ApiClientError
from app.lead_enrichment.config import EnrichmentSettings
from app.schemas import Lead, LeadBulkRequest, LeadBulkResponse, LeadCreateRequest, LeadStatus, LeadUpdateRequest
from app.services import crm_store
from app.services.attio_formatting import attio_person_name, attio_phone_number
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
from app.services.lead_sources import canonical_url_key, company_domain, first_known, is_known, utc_now


def list_leads() -> list[Lead]:
    settings = EnrichmentSettings()
    leads = _with_priority(_load_discovered_leads())
    if settings.crm_include_demo_leads:
        leads.extend(LEADS)
    return sorted(leads, key=lead_sort_key)


def get_lead(lead_id: int) -> Lead | None:
    return next((lead for lead in _load_discovered_leads() if lead.id == lead_id), None)


def known_discovery_keys() -> set[str]:
    keys: set[str] = set()
    for lead in _with_priority(_load_discovered_leads()):
        keys.add(lead_key(lead))
        for url in [lead.contract_url, lead.website, *lead.source_urls]:
            url_key = canonical_url_key(url)
            if url_key:
                keys.add(url_key)
    return {key for key in keys if key}


def discovery_key_for_url(url: str) -> str:
    return canonical_url_key(url)


def add_discovered_leads(results: list[DiscoveryCompanyResult]) -> None:
    discovered_leads = _with_priority(_load_discovered_leads())
    existing_keys = {lead_key(lead): index for index, lead in enumerate(discovered_leads)}
    next_id = _next_lead_id(discovered_leads)
    changed = False

    for result in results:
        if not is_persistable_result(result):
            continue
        if not has_useful_result(result):
            continue
        key = result_key(result)
        if key in existing_keys:
            index = existing_keys[key]
            discovered_leads[index] = merge_result_into_lead(discovered_leads[index], result)
            changed = True
            continue
        lead = result_to_lead(result, next_id)
        discovered_leads.append(lead)
        existing_keys[lead_key(lead)] = len(discovered_leads) - 1
        next_id += 1
        changed = True

    if changed:
        _save_discovered_leads(discovered_leads)


def update_lead(lead_id: int, request: LeadUpdateRequest) -> Lead | None:
    leads = _load_discovered_leads()
    for index, lead in enumerate(leads):
        if lead.id != lead_id:
            continue
        update = request.model_dump(exclude_unset=True)
        leads[index] = score_lead(lead.model_copy(update=update))
        _save_discovered_leads(leads)
        return leads[index]
    return None


def create_lead(request: LeadCreateRequest) -> Lead:
    today = utc_now().date()
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
        created_at=today,
        manual_notes=request.manual_notes.strip(),
        next_action=request.next_action.strip(),
        first_seen_at=utc_now(),
        last_seen_at=utc_now(),
    )
    return crm_store.create_lead(score_lead(with_availability(lead)))


def delete_lead(lead_id: int) -> bool:
    return crm_store.delete_lead(lead_id)


def bulk_update_leads(request: LeadBulkRequest) -> LeadBulkResponse:
    updated: list[Lead] = []
    failed = 0
    for lead_id in request.lead_ids:
        lead = get_lead(lead_id)
        if not lead:
            failed += 1
            continue
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
    leads = _load_discovered_leads()
    for index, lead in enumerate(leads):
        if lead.id != lead_id:
            continue
        leads[index] = score_lead(
            lead.model_copy(
                update={
                    "status": LeadStatus.REJECTED,
                    "rejected_at": utc_now(),
                    "last_sync_message": "Lead rejected",
                }
            )
        )
        _save_discovered_leads(leads)
        return leads[index]
    return None


async def confirm_lead(lead_id: int) -> Lead | None:
    leads = _load_discovered_leads()
    for index, lead in enumerate(leads):
        if lead.id != lead_id:
            continue
        confirmed = score_lead(await _confirm_with_attio(lead))
        leads[index] = confirmed
        _save_discovered_leads(leads)
        return confirmed
    return None


async def _confirm_with_attio(lead: Lead) -> Lead:
    settings = EnrichmentSettings()
    attio_client: AttioClient | None = None
    messages = []
    company_record_id = lead.attio_company_record_id
    person_record_id = lead.attio_person_record_id
    draft_email_subject = lead.draft_email_subject
    draft_email_body = lead.draft_email_body
    draft_email_generated_at = lead.draft_email_generated_at
    working_lead = with_availability(lead)

    if working_lead.availability_status == "Unavailable":
        return working_lead.model_copy(
            update={
                "status": LeadStatus.REVIEWING,
                "last_sync_message": f"Cannot confirm unavailable contract: {working_lead.availability_reason}",
            }
        )

    try:
        settings.require_attio_key()
        attio_client = AttioClient(settings)
        company_response = await attio_client.upsert_record(
            settings.attio_company_object,
            settings.attio_company_domain_attribute,
            _company_values(working_lead),
        )
        company_record_id = record_id_from_response(company_response)
        messages.append("Attio company upserted")

        if not working_lead.contact_email:
            working_lead = await _enrich_contact_before_person_sync(working_lead, settings, messages)

        if working_lead.contact_email:
            try:
                person_response = await attio_client.upsert_record(
                    settings.attio_person_object,
                    settings.attio_person_email_attribute,
                    _person_values(working_lead, settings, company_record_id),
                )
                person_record_id = record_id_from_response(person_response)
                messages.append("Attio person upserted")
                status = LeadStatus.CONFIRMED
                draft_email_subject, draft_email_body, draft_email_generated_at = await _generate_draft_email(
                    working_lead,
                    settings,
                    messages,
                )
                if company_record_id and draft_email_subject and draft_email_body:
                    await _safe_create_note(
                        attio_client,
                        settings.attio_company_object,
                        company_record_id,
                        "Draft outreach email",
                        _draft_email_note(draft_email_subject, draft_email_body),
                        messages,
                    )
                if person_record_id:
                    await _safe_create_note(
                        attio_client,
                        settings.attio_person_object,
                        person_record_id,
                        "Tender contact context",
                        _lead_summary(working_lead),
                        messages,
                    )
            except ApiClientError as exc:
                messages.append(f"Attio person sync failed: {exc}")
                status = LeadStatus.REVIEWING
        else:
            status = LeadStatus.NEEDS_CONTACT
            messages.append("No contact email found; person was not created")
            if company_record_id:
                await _safe_create_note(
                    attio_client,
                    settings.attio_company_object,
                    company_record_id,
                    "Lead needs contact",
                    f"No contact email was found for {working_lead.name}. Source: {working_lead.contract_url or working_lead.website}",
                    messages,
                )

        if company_record_id:
            await _safe_create_note(
                attio_client,
                settings.attio_company_object,
                company_record_id,
                "Lead confirmation",
                _lead_summary(working_lead),
                messages,
            )

        return working_lead.model_copy(
            update={
                "status": status,
                "confirmed_at": utc_now(),
                "attio_company_record_id": company_record_id,
                "attio_person_record_id": person_record_id,
                "draft_email_subject": draft_email_subject,
                "draft_email_body": draft_email_body,
                "draft_email_generated_at": draft_email_generated_at,
                "last_sync_message": "; ".join(messages),
            }
        )
    except (ApiClientError, ValueError) as exc:
        return lead.model_copy(
            update={
                "status": LeadStatus.REVIEWING,
                "last_sync_message": f"Attio sync failed: {exc}",
            }
        )
    finally:
        if attio_client:
            await attio_client.close()


async def _enrich_contact_before_person_sync(
    lead: Lead,
    settings: EnrichmentSettings,
    messages: list[str],
) -> Lead:
    try:
        result = await find_contact_for_lead(lead, settings)
    except Exception as exc:
        messages.append(f"Contact lookup failed; company upserted only: {_safe_error_message(exc, settings)}")
        return lead

    if not result.contact_email:
        messages.append("Contact lookup completed; no valid email found")
        return lead

    messages.append("Contact found via Tavily")
    return lead.model_copy(
        update={
            "contact_name": result.contact_name or lead.contact_name,
            "contact_email": result.contact_email,
            "email": result.contact_email,
            "contact_phone": result.contact_phone or lead.contact_phone,
            "contact_source_url": result.contact_source_url,
        }
    )


async def _generate_draft_email(
    lead: Lead,
    settings: EnrichmentSettings,
    messages: list[str],
) -> tuple[str, str, datetime | None]:
    try:
        draft = await draft_email_for_lead(lead, settings)
    except Exception as exc:
        messages.append(f"Draft email failed: {_safe_error_message(exc, settings)}")
        return lead.draft_email_subject, lead.draft_email_body, lead.draft_email_generated_at

    messages.append("Draft email generated")
    return draft.subject, draft.body, utc_now()


def _safe_error_message(exc: Exception, settings: EnrichmentSettings) -> str:
    message = str(exc) or exc.__class__.__name__
    for secret in (settings.attio_api_token, settings.tavily_api_key, settings.gemini_api_key):
        if secret and len(secret) > 8:
            message = message.replace(secret, "[redacted]")
    return message[:500]


def _with_priority(leads: list[Lead]) -> list[Lead]:
    changed = False
    scored = []
    for lead in leads:
        dedupe_key = lead.dedupe_key or lead_key(lead)
        now = utc_now()
        updated = score_lead(
            with_availability(
                lead.model_copy(
                    update={
                        "dedupe_key": dedupe_key,
                        "first_seen_at": lead.first_seen_at or now,
                        "last_seen_at": lead.last_seen_at or now,
                        "seen_count": lead.seen_count or 1,
                    }
                )
            )
        )
        changed = changed or updated != lead
        scored.append(updated)
    if changed:
        _save_discovered_leads(scored)
    return scored


def _company_values(lead: Lead) -> dict[str, Any]:
    values: dict[str, Any] = {
        "name": first_known(lead.buyer_name, lead.company, lead.name) or "Unknown buyer",
        "description": _lead_summary(lead),
    }
    domain = company_domain(lead)
    if is_known(domain):
        values["domains"] = [domain]
    return values


def _person_values(lead: Lead, settings: EnrichmentSettings, company_record_id: str | None = None) -> dict[str, Any]:
    values: dict[str, Any] = {
        settings.attio_person_email_attribute: [str(lead.contact_email)],
    }
    if is_known(lead.contact_name):
        values[settings.attio_person_name_attribute] = [attio_person_name(lead.contact_name)]
    if is_known(lead.contact_phone):
        values[settings.attio_person_phone_attribute] = [attio_phone_number(lead.contact_phone, settings)]
    if company_record_id:
        values[settings.attio_person_company_attribute] = [
            {
                "target_object": settings.attio_company_object,
                "target_record_id": company_record_id,
            }
        ]
    return values


def _draft_email_note(subject: str, body: str) -> str:
    return f"""Subject: {subject}

{body}
""".strip()


async def _safe_create_note(
    attio_client: AttioClient,
    object_slug: str,
    record_id: str,
    title: str,
    content: str,
    messages: list[str],
) -> None:
    try:
        await attio_client.create_note(object_slug, record_id, title, content)
    except ApiClientError as exc:
        messages.append(f"note failed: {exc}")


def _lead_summary(lead: Lead) -> str:
    return f"""Confirmed tender lead: {lead.name}

Buyer: {lead.company}
Portal: {lead.portal_name}
Contract URL: {lead.contract_url or lead.website}
Value: {lead.contract_value}
Deadline: {lead.deadline}
Stage: {lead.procurement_stage}
Availability: {lead.availability_status} - {lead.availability_reason}
Contact: {lead.contact_name or "Unknown"} {lead.contact_email or ""}

Notes:
{lead.outreach_angle}
""".strip()


def _next_lead_id(discovered_leads: list[Lead]) -> int:
    ids = [lead.id for lead in [*LEADS, *discovered_leads]]
    return max(ids, default=0) + 1


def _load_discovered_leads() -> list[Lead]:
    return crm_store.list_leads()


def _save_discovered_leads(leads: list[Lead]) -> None:
    crm_store.replace_leads(leads)
