from app.lead_enrichment.clients.attio_client import AttioClient
from app.lead_enrichment.config import EnrichmentSettings
from app.lead_enrichment.models import EnrichmentSummary


async def writeBackToAttio(
    settings: EnrichmentSettings,
    enrichment: EnrichmentSummary,
    attio_client: AttioClient,
    dry_run: bool,
) -> str:
    if dry_run:
        return "Dry run: writeback skipped"

    lead = enrichment.lead
    classification = enrichment.classification
    values = {
        settings.attio_enrichment_summary_attribute: enrichment.summary_text,
        settings.attio_fit_score_attribute: classification.fit_score,
        settings.attio_urgency_score_attribute: classification.urgency_score,
        settings.attio_confidence_score_attribute: classification.confidence_score,
        settings.attio_fingerprint_attribute: enrichment.fingerprint,
        settings.attio_source_urls_attribute: enrichment.source_urls,
        settings.attio_enriched_at_attribute: enrichment.generated_at.isoformat(),
    }

    await attio_client.update_record(lead.object_slug, lead.record_id, values)
    await attio_client.create_note(
        lead.object_slug,
        lead.record_id,
        "Lead enrichment summary",
        enrichment.summary_text,
    )

    should_create_task = (
        settings.enrichment_create_tasks
        and (
            classification.urgency_score >= settings.enrichment_task_urgency_threshold
            or bool(classification.procurement_signals)
        )
    )
    if should_create_task:
        await attio_client.create_task(
            lead.object_slug,
            lead.record_id,
            f"Follow up with enriched lead: {lead.name}",
            enrichment.summary_text[:4000],
        )
    return "Attio writeback complete"

