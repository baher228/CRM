from datetime import datetime, timezone, date
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from app.data import LEADS
from app.lead_discovery.models import DiscoveryCompanyResult
from app.lead_enrichment.clients.attio_client import AttioClient, record_id_from_response
from app.lead_enrichment.clients.http import ApiClientError
from app.lead_enrichment.config import EnrichmentSettings
from app.schemas import Lead, LeadStatus, LeadUpdateRequest
from app.services.contract_availability import assess_contract_availability
from app.services.contact_lookup_service import find_contact_for_lead
from app.services.draft_email_service import draft_email_for_lead


DISCOVERED_LEADS_PATH = Path(__file__).resolve().parents[2] / "discovered_leads.json"


def list_leads() -> list[Lead]:
    settings = EnrichmentSettings()
    leads = _with_priority(_load_discovered_leads())
    if settings.crm_include_demo_leads:
        leads.extend(LEADS)
    return sorted(leads, key=_lead_sort_key)


def get_lead(lead_id: int) -> Lead | None:
    return next((lead for lead in _load_discovered_leads() if lead.id == lead_id), None)


def known_discovery_keys() -> set[str]:
    keys: set[str] = set()
    for lead in _with_priority(_load_discovered_leads()):
        keys.add(_lead_key(lead))
        for url in [lead.contract_url, lead.website, *lead.source_urls]:
            url_key = _canonical_url_key(url)
            if url_key:
                keys.add(url_key)
    return {key for key in keys if key}


def discovery_key_for_url(url: str) -> str:
    return _canonical_url_key(url)


def add_discovered_leads(results: list[DiscoveryCompanyResult]) -> None:
    discovered_leads = _with_priority(_load_discovered_leads())
    existing_keys = {_lead_key(lead): index for index, lead in enumerate(discovered_leads)}
    next_id = _next_lead_id(discovered_leads)
    changed = False

    for result in results:
        if not _is_persistable_result(result):
            continue
        if not _has_useful_result(result):
            continue
        key = _result_key(result)
        if key in existing_keys:
            index = existing_keys[key]
            discovered_leads[index] = _merge_result_into_lead(discovered_leads[index], result)
            changed = True
            continue
        lead = _result_to_lead(result, next_id)
        discovered_leads.append(lead)
        existing_keys[_lead_key(lead)] = len(discovered_leads) - 1
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
        leads[index] = _score_lead(lead.model_copy(update=update))
        _save_discovered_leads(leads)
        return leads[index]
    return None


def reject_lead(lead_id: int) -> Lead | None:
    leads = _load_discovered_leads()
    for index, lead in enumerate(leads):
        if lead.id != lead_id:
            continue
        leads[index] = _score_lead(
            lead.model_copy(
                update={
                    "status": LeadStatus.REJECTED,
                    "rejected_at": _now(),
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
        confirmed = _score_lead(await _confirm_with_attio(lead))
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
    working_lead = _with_availability(lead)

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
        company_values = _company_values(working_lead)
        company_response = await attio_client.upsert_record(
            settings.attio_company_object,
            settings.attio_company_domain_attribute,
            company_values,
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
                "confirmed_at": _now(),
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
    return draft.subject, draft.body, _now()


def _safe_error_message(exc: Exception, settings: EnrichmentSettings) -> str:
    message = str(exc) or exc.__class__.__name__
    for secret in (settings.attio_api_token, settings.tavily_api_key, settings.gemini_api_key):
        if secret and len(secret) > 8:
            message = message.replace(secret, "[redacted]")
    return message[:500]


def _result_to_lead(result: DiscoveryCompanyResult, lead_id: int) -> Lead:
    contact_name, contact_email, contact_phone = _split_contact(result.buyer_contact)
    contact_name = result.contact_name or contact_name
    contact_email = _clean_email(result.contact_email) or contact_email
    contact_phone = result.contact_phone or contact_phone
    contract_url = _best_contract_url(result.contract_url, result.source_urls)
    source_urls = _clean_source_urls([contract_url, *result.source_urls])
    now = _now()
    lead = Lead(
        id=lead_id,
        name=result.contract_title if result.contract_title != "Unknown" else result.company_name,
        company=result.buyer_name if result.buyer_name != "Unknown" else result.company_name,
        email=contact_email,
        website=contract_url or _website_from_domain(result.portal_domain or result.domain),
        status=LeadStatus.NEW,
        source=result.portal_name if result.portal_name != "Unknown" else "Discovery",
        confidence_score=result.confidence_score or 0,
        outreach_angle=_outreach_angle(result),
        estimated_value=_estimated_value(result.contract_value),
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
        availability_checked_at=_datetime_from_iso(result.availability_checked_at),
        source_urls=source_urls,
        contact_name=contact_name,
        contact_email=contact_email,
        contact_phone=contact_phone,
        contact_source_url="",
        buyer_website=result.buyer_website,
        first_seen_at=now,
        last_seen_at=now,
        seen_count=1,
        dedupe_key=_result_key(result),
    )
    return _score_lead(_with_availability(lead))


def _merge_result_into_lead(lead: Lead, result: DiscoveryCompanyResult) -> Lead:
    now = _now()
    incoming = _result_to_lead(result, lead.id)
    merged = lead.model_copy(
        update={
            "name": _best_value(lead.name, incoming.name),
            "company": _best_value(lead.company, incoming.company),
            "email": lead.email or incoming.email,
            "website": _best_source_value(lead.website, incoming.website),
            "source": _best_value(lead.source, incoming.source),
            "confidence_score": max(lead.confidence_score or 0, incoming.confidence_score or 0),
            "outreach_angle": _best_value(lead.outreach_angle, incoming.outreach_angle),
            "estimated_value": max(lead.estimated_value or 0, incoming.estimated_value or 0),
            "contract_title": _best_value(lead.contract_title, incoming.contract_title),
            "buyer_name": _best_value(lead.buyer_name, incoming.buyer_name),
            "company_domain": _best_value(lead.company_domain, incoming.company_domain),
            "portal_name": _best_value(lead.portal_name, incoming.portal_name),
            "contract_url": _best_source_value(lead.contract_url, incoming.contract_url),
            "contract_value": _best_value(lead.contract_value, incoming.contract_value),
            "deadline": _best_value(lead.deadline, incoming.deadline),
            "procurement_stage": _best_value(lead.procurement_stage, incoming.procurement_stage),
            "contract_status": _best_value(lead.contract_status, incoming.contract_status),
            "availability_status": incoming.availability_status or lead.availability_status,
            "availability_reason": incoming.availability_reason or lead.availability_reason,
            "availability_checked_at": incoming.availability_checked_at or lead.availability_checked_at,
            "source_urls": _merge_urls(lead.source_urls, incoming.source_urls),
            "contact_name": _best_value(lead.contact_name, incoming.contact_name),
            "contact_email": lead.contact_email or incoming.contact_email,
            "contact_phone": _best_value(lead.contact_phone, incoming.contact_phone),
            "contact_source_url": _best_value(lead.contact_source_url, incoming.contact_source_url),
            "buyer_website": _best_value(lead.buyer_website, incoming.buyer_website),
            "last_seen_at": now,
            "seen_count": (lead.seen_count or 1) + 1,
            "dedupe_key": lead.dedupe_key or incoming.dedupe_key,
        }
    )
    return _score_lead(merged)


def _with_priority(leads: list[Lead]) -> list[Lead]:
    changed = False
    scored = []
    for lead in leads:
        dedupe_key = lead.dedupe_key or _lead_key(lead)
        now = _now()
        updated = _score_lead(
            _with_availability(
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


def _score_lead(lead: Lead) -> Lead:
    score = 0
    reasons: list[str] = []
    confidence = lead.confidence_score or 0
    score += min(30, round(confidence * 0.3))
    if confidence >= 85:
        reasons.append("High confidence")
    elif confidence >= 65:
        reasons.append("Good confidence")

    if _is_known(lead.deadline):
        score += 15
        reasons.append("Deadline parsed")
    else:
        score -= 6
        reasons.append("Deadline missing")

    if _is_known(lead.contract_value) or lead.estimated_value:
        score += 14
        reasons.append("Value parsed")
    if _is_known(lead.contact_name) or lead.contact_email or _is_known(lead.contact_phone):
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

    if _is_known(lead.portal_name) or _is_known(lead.source):
        score += 7
        reasons.append("Known portal")
    if _is_known(lead.contract_url) or lead.source_urls:
        score += 8
        reasons.append("Source link saved")
    if _is_known(lead.contract_title) and _is_known(lead.buyer_name):
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
            "priority_label": _priority_label(score),
            "priority_reasons": reasons[:6],
        }
    )


def _with_availability(lead: Lead) -> Lead:
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


def _priority_label(score: int) -> str:
    if score >= 80:
        return "Hot"
    if score >= 60:
        return "Warm"
    if score >= 40:
        return "Watch"
    return "Low"


def _company_values(lead: Lead) -> dict[str, Any]:
    values: dict[str, Any] = {
        "name": _first_known(lead.buyer_name, lead.company, lead.name) or "Unknown buyer",
        "description": _lead_summary(lead),
    }
    domain = _company_domain(lead)
    if _is_known(domain):
        values["domains"] = [domain]
    return values


def _person_values(lead: Lead, settings: EnrichmentSettings, company_record_id: str | None = None) -> dict[str, Any]:
    values: dict[str, Any] = {
        settings.attio_person_email_attribute: [str(lead.contact_email)],
    }
    if _is_known(lead.contact_name):
        values[settings.attio_person_name_attribute] = [_attio_person_name(lead.contact_name)]
    if _is_known(lead.contact_phone):
        values[settings.attio_person_phone_attribute] = [_attio_phone_number(lead.contact_phone, settings)]
    if company_record_id:
        values[settings.attio_person_company_attribute] = [
            {
                "target_object": settings.attio_company_object,
                "target_record_id": company_record_id,
            }
        ]
    return values


def _attio_person_name(name: str) -> dict[str, str]:
    parts = [part for part in re.split(r"\s+", name.strip()) if part]
    if not parts:
        return {"first_name": "", "last_name": "", "full_name": ""}
    if len(parts) == 1:
        return {"first_name": parts[0], "last_name": "", "full_name": parts[0]}
    return {
        "first_name": " ".join(parts[:-1]),
        "last_name": parts[-1],
        "full_name": " ".join(parts),
    }


def _attio_phone_number(phone: str, settings: EnrichmentSettings) -> dict[str, str]:
    cleaned = re.sub(r"\s+", " ", phone.strip())
    value = {"original_phone_number": cleaned}
    if not cleaned.startswith("+"):
        value["country_code"] = settings.attio_default_phone_country_code
    return value


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


def _outreach_angle(result: DiscoveryCompanyResult) -> str:
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


def _estimated_value(value: str) -> int:
    digits = "".join(char for char in value if char.isdigit())
    if not digits:
        return 0
    return min(int(digits), 2_000_000_000)


def _next_lead_id(discovered_leads: list[Lead]) -> int:
    ids = [lead.id for lead in [*LEADS, *discovered_leads]]
    return max(ids, default=0) + 1


def _lead_key(lead: Lead) -> str:
    if lead.dedupe_key:
        return lead.dedupe_key
    url_key = _canonical_url_key(lead.contract_url or lead.website)
    if url_key:
        return url_key
    return _fallback_key(lead.portal_name or lead.source, lead.buyer_name or lead.company, lead.contract_title or lead.name)


def _result_key(result: DiscoveryCompanyResult) -> str:
    url_key = _canonical_url_key(_best_contract_url(result.contract_url, result.source_urls))
    if url_key:
        return url_key
    return _fallback_key(result.portal_name, result.buyer_name or result.company_name, result.contract_title)


def _has_useful_result(result: DiscoveryCompanyResult) -> bool:
    return any(
        _is_known(value)
        for value in (
            result.contract_title,
            result.buyer_name,
            result.company_name,
            result.contract_url,
            result.domain,
        )
    )


def _is_persistable_result(result: DiscoveryCompanyResult) -> bool:
    if result.status in {"upserted", "dry_run"}:
        return True
    if result.status != "failed":
        return False
    if not (result.contract_url or result.source_urls):
        return False
    return any(_is_known(value) for value in (result.contract_title, result.buyer_name, result.company_name))


def _company_domain(lead: Lead) -> str:
    for candidate in (lead.company_domain, lead.buyer_website):
        if _is_known(candidate):
            return _domain_from_value(candidate)
    if _is_known(lead.website):
        return _domain_from_value(lead.website)
    return ""


def _canonical_url_key(value: str) -> str:
    if not _is_known(value):
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+", "/", parsed.path.rstrip("/")).lower()
    if _is_bad_source_url(value):
        return ""
    ocds_match = re.search(r"(ocds-[a-z0-9-]+)", path, flags=re.IGNORECASE)
    if ocds_match:
        return f"{host}|{ocds_match.group(1).lower()}"
    if not host:
        return ""
    return urlunparse(("https", host, path, "", "", ""))


def _fallback_key(portal: str, buyer: str, title: str) -> str:
    parts = [_slug(part) for part in (portal, buyer, title) if _is_known(part)]
    if len(parts) >= 2:
        return "|".join(parts)
    return "|".join(parts)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _is_known(value: Any) -> bool:
    return bool(value and str(value).strip() and str(value).strip().lower() not in {"unknown", "n/a", "none", "-"})


def _first_known(*values: Any) -> Any:
    return next((value for value in values if _is_known(value)), None)


def _best_value(current: Any, incoming: Any) -> Any:
    if not _is_known(current) and _is_known(incoming):
        return incoming
    if isinstance(incoming, str) and isinstance(current, str) and _is_known(incoming) and len(incoming) > len(current) and not _is_known(current):
        return incoming
    return current


def _best_source_value(current: str, incoming: str) -> str:
    if _is_known(current) and not _is_bad_source_url(current):
        return current
    if _is_known(incoming) and not _is_bad_source_url(incoming):
        return incoming
    return current or incoming


def _best_contract_url(contract_url: str, source_urls: list[str]) -> str:
    for url in [contract_url, *(source_urls or [])]:
        if _is_known(url) and not _is_bad_source_url(url):
            return _normalize_source_url(url)
    return ""


def _clean_source_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for url in urls:
        if not _is_known(url) or _is_bad_source_url(url):
            continue
        normalized = _normalize_source_url(url)
        key = _canonical_url_key(normalized) or normalized
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(normalized)
    return cleaned


def _is_bad_source_url(value: str) -> bool:
    if not _is_known(value):
        return True
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    return host.endswith("contractsfinder.service.gov.uk") and path.startswith("/search/")


def _normalize_source_url(value: str) -> str:
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/")
    if host.endswith("contractsfinder.service.gov.uk") and path.lower().startswith("/notice/"):
        return urlunparse(("https", host, path, "", "", ""))
    return urlunparse((parsed.scheme or "https", host, path, "", parsed.query, ""))


def _merge_urls(left: list[str], right: list[str]) -> list[str]:
    seen = set()
    merged = []
    for url in _clean_source_urls([*(left or []), *(right or [])]):
        key = _canonical_url_key(url) or url
        if key in seen:
            continue
        seen.add(key)
        merged.append(url)
    return merged


def _lead_sort_key(lead: Lead) -> tuple:
    return (
        -(lead.priority_score or 0),
        parse_dateish(lead.deadline),
        -(lead.confidence_score or 0),
    )


def parse_dateish(value: str) -> int:
    if not _is_known(value):
        return 99999999
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", value)
    if match:
        return int(f"{match.group(1)}{int(match.group(2)):02d}{int(match.group(3)):02d}")
    return 99999999


def _domain_from_value(value: str) -> str:
    cleaned = value.replace("https://", "").replace("http://", "").split("/")[0]
    if cleaned.startswith("www."):
        cleaned = cleaned[4:]
    return cleaned or "example.com"


def _website_from_domain(domain: str) -> str:
    if not domain:
        return "https://example.com"
    if domain.startswith("http://") or domain.startswith("https://"):
        return domain
    return f"https://{domain}"


def _split_contact(value: str) -> tuple[str, str | None, str]:
    if not _is_known(value):
        return "", None, ""
    email_match = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", value)
    phone_match = re.search(r"(?:\+?\d[\d\s().-]{7,}\d)", value)
    email = email_match.group(0) if email_match else None
    phone = phone_match.group(0).strip() if phone_match else ""
    name = value
    for token in (email or "", phone):
        if token:
            name = name.replace(token, "")
    name = re.sub(r"\s+", " ", name.replace("Email:", "").replace("Phone:", "")).strip(" ,-;")
    if name.lower() in {"unknown", "n/a"}:
        name = ""
    return name, email, phone


def _clean_email(value: str | None) -> str | None:
    if not _is_known(value):
        return None
    match = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", str(value))
    return match.group(0) if match else None


def _load_discovered_leads() -> list[Lead]:
    if not DISCOVERED_LEADS_PATH.exists():
        return []
    try:
        payload = json.loads(DISCOVERED_LEADS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    leads = []
    for item in payload:
        try:
            leads.append(Lead.model_validate(item))
        except ValueError:
            continue
    return leads


def _save_discovered_leads(leads: list[Lead]) -> None:
    payload = [lead.model_dump(mode="json") for lead in leads]
    DISCOVERED_LEADS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _datetime_from_iso(value: str | None) -> datetime | None:
    if not _is_known(value):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)
