from typing import Any

from app.lead_discovery.models import CompanyDiscoverySummary
from app.lead_enrichment.clients.attio_client import AttioClient, record_id_from_response
from app.lead_enrichment.clients.http import ApiClientError
from app.lead_enrichment.config import EnrichmentSettings


async def upsertCompanyToAttio(
    summary: CompanyDiscoverySummary,
    settings: EnrichmentSettings,
    attio_client: AttioClient,
    dry_run: bool,
) -> tuple[str, str | None]:
    profile = summary.profile
    if dry_run:
        return "Dry run: Attio upsert skipped", None

    values = build_company_upsert_values(summary, settings)
    message = f"Attio opportunity upsert complete for {profile.company_name}"
    try:
        response = await _upsert_values(settings, attio_client, values)
    except ApiClientError as exc:
        if exc.status_code not in {400, 422}:
            raise
        minimal_values = build_minimal_company_upsert_values(summary)
        response = await _upsert_values(settings, attio_client, minimal_values)
        message = (
            f"Attio opportunity upsert complete for {profile.company_name} with standard fields; "
            "optional custom discovery attributes were rejected. "
            f"Original error: {exc}"
        )
    record_id = record_id_from_response(response)
    if record_id:
        try:
            await attio_client.create_note(
                settings.discovery_company_object,
                record_id,
                "Contract discovery summary",
                summary.summary_text,
            )
        except ApiClientError as exc:
            message = f"{message}; note creation failed: {exc}"
    return message, record_id


def build_company_upsert_values(
    summary: CompanyDiscoverySummary,
    settings: EnrichmentSettings,
) -> dict[str, Any]:
    profile = summary.profile
    values = {
        "name": _record_name(profile),
        "domains": [profile.domain],
        "description": summary.summary_text,
    }
    if settings.attio_discovery_write_custom_attributes:
        _add_optional_attribute(values, settings.attio_discovery_summary_attribute, summary.summary_text)
        _add_optional_attribute(values, settings.attio_discovery_confidence_attribute, profile.confidence_score)
        _add_optional_attribute(values, settings.attio_discovery_source_urls_attribute, profile.source_urls)
        _add_optional_attribute(values, settings.attio_discovery_fingerprint_attribute, summary.fingerprint)
        _add_optional_attribute(values, settings.attio_discovery_niche_attribute, summary.niche)
        _add_optional_attribute(values, settings.attio_discovery_region_attribute, summary.region or "")
    return values


def build_minimal_company_upsert_values(summary: CompanyDiscoverySummary) -> dict[str, Any]:
    profile = summary.profile
    return {
        "name": _record_name(profile),
        "domains": [profile.domain],
        "description": summary.summary_text,
    }


def _record_name(profile) -> str:
    if profile.buyer_name and profile.buyer_name != "Unknown":
        return profile.buyer_name
    if profile.company_name and profile.company_name != "Unknown":
        return profile.company_name
    return profile.contract_title


def _add_optional_attribute(values: dict[str, Any], attribute_slug: str, value: Any) -> None:
    if attribute_slug.strip():
        values[attribute_slug.strip()] = value


async def _upsert_values(
    settings: EnrichmentSettings,
    attio_client: AttioClient,
    values: dict[str, Any],
) -> dict[str, Any]:
    return await attio_client.upsert_record(
        settings.discovery_company_object,
        settings.discovery_domain_matching_attribute,
        values,
    )
