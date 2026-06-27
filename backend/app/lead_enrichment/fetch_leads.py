from typing import Any

from app.lead_enrichment.clients.attio_client import AttioClient
from app.lead_enrichment.config import EnrichmentSettings
from app.lead_enrichment.models import LeadSource


async def fetchLeads(
    settings: EnrichmentSettings,
    attio_client: AttioClient,
    limit: int,
) -> list[LeadSource]:
    if settings.attio_lead_list_id:
        raw_records = await attio_client.query_list_entries(settings.attio_lead_list_id, limit)
    else:
        raw_records = await attio_client.query_records(settings.attio_lead_object, limit)
    return [_to_lead_source(settings, item) for item in raw_records]


def _to_lead_source(settings: EnrichmentSettings, item: dict[str, Any]) -> LeadSource:
    record = item.get("parent_record") or item.get("record") or item
    values = record.get("values", {}) if isinstance(record, dict) else {}
    object_slug = (
        record.get("object_slug")
        or record.get("object")
        or item.get("parent_object")
        or settings.attio_lead_object
    )
    record_id = (
        record.get("id", {}).get("record_id")
        if isinstance(record.get("id"), dict)
        else record.get("record_id") or record.get("id") or item.get("parent_record_id")
    )

    name = _first_value(values, settings.attio_company_name_attribute) or record.get("name") or "Unknown lead"
    domain = _first_value(values, settings.attio_company_domain_attribute)
    email = _first_value(values, settings.attio_person_email_attribute)
    fingerprint = _first_value(values, settings.attio_fingerprint_attribute)

    return LeadSource(
        object_slug=str(object_slug),
        record_id=str(record_id),
        name=str(name),
        domain=str(domain) if domain else None,
        email=str(email) if email else None,
        existing_fingerprint=str(fingerprint) if fingerprint else None,
        raw=item,
    )


def _first_value(values: dict[str, Any], slug: str) -> Any:
    value = values.get(slug)
    if value is None:
        return None
    if isinstance(value, list):
        if not value:
            return None
        return _unwrap_value(value[0])
    return _unwrap_value(value)


def _unwrap_value(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("value", "domain", "email_address", "name", "target_object", "title"):
            if key in value:
                return value[key]
        if "option" in value and isinstance(value["option"], dict):
            return value["option"].get("title")
    return value

