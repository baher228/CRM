import argparse
import asyncio
import json
import sys

from app.lead_enrichment.build_summary import buildSummary
from app.lead_enrichment.classify_lead import classifyLead
from app.lead_enrichment.clients.attio_client import AttioClient
from app.lead_enrichment.clients.gemini_client import GeminiClient
from app.lead_enrichment.clients.tavily_client import TavilyClient
from app.lead_enrichment.config import EnrichmentSettings
from app.lead_enrichment.extract_pages import extractPages
from app.lead_enrichment.fetch_leads import fetchLeads
from app.lead_enrichment.find_pages_with_tavily import findPagesWithTavily
from app.lead_enrichment.idempotency import is_unchanged
from app.lead_enrichment.logging import configure_logging, log_event
from app.lead_enrichment.models import EnrichmentRunResponse, LeadRunResult, LeadSource
from app.lead_enrichment.write_back_to_attio import writeBackToAttio


async def run_enrichment(limit: int | None = None, dry_run: bool = True) -> EnrichmentRunResponse:
    configure_logging()
    settings = EnrichmentSettings()
    settings.require_read_keys()
    requested_limit = limit or settings.enrichment_default_limit

    attio_client = AttioClient(settings)
    tavily_client = TavilyClient(settings)
    gemini_client = GeminiClient(settings)

    try:
        leads = await fetchLeads(settings, attio_client, requested_limit)
        log_event("enrichment_run_started", limit=requested_limit, dry_run=dry_run, fetched=len(leads))
        semaphore = asyncio.Semaphore(
            min(
                settings.enrichment_attio_concurrency,
                settings.enrichment_tavily_concurrency,
                settings.enrichment_llm_concurrency,
            )
        )

        async def run_one(lead: LeadSource) -> LeadRunResult:
            async with semaphore:
                return await _run_lead(
                    settings,
                    lead,
                    attio_client,
                    tavily_client,
                    gemini_client,
                    dry_run,
                )

        results = await asyncio.gather(*(run_one(lead) for lead in leads))
        return _response(dry_run, requested_limit, len(leads), results)
    finally:
        await attio_client.close()
        await tavily_client.close()


async def _run_lead(
    settings: EnrichmentSettings,
    lead: LeadSource,
    attio_client: AttioClient,
    tavily_client: TavilyClient,
    gemini_client: GeminiClient,
    dry_run: bool,
) -> LeadRunResult:
    try:
        found_pages = await findPagesWithTavily(lead, settings, tavily_client)
        extracted_pages = await extractPages(found_pages, tavily_client)
        classification = await classifyLead(lead, extracted_pages, gemini_client)
        enrichment = buildSummary(
            lead,
            found_pages,
            extracted_pages,
            classification,
            settings.enrichment_classifier_version,
        )
        if is_unchanged(lead.existing_fingerprint, enrichment.fingerprint):
            return LeadRunResult(
                record_id=lead.record_id,
                name=lead.name,
                status="skipped",
                message="Existing fingerprint matches current enrichment",
                source_urls=enrichment.source_urls,
            )

        message = await writeBackToAttio(settings, enrichment, attio_client, dry_run)
        return LeadRunResult(
            record_id=lead.record_id,
            name=lead.name,
            status="dry_run" if dry_run else "enriched",
            message=message,
            fit_score=classification.fit_score,
            urgency_score=classification.urgency_score,
            confidence_score=classification.confidence_score,
            source_urls=enrichment.source_urls,
        )
    except Exception as exc:  # noqa: BLE001 - keep batch running per lead.
        log_event("enrichment_lead_failed", record_id=lead.record_id, error=str(exc))
        return LeadRunResult(
            record_id=lead.record_id,
            name=lead.name,
            status="failed",
            message=str(exc),
        )


def _response(
    dry_run: bool,
    requested_limit: int,
    fetched: int,
    results: list[LeadRunResult],
) -> EnrichmentRunResponse:
    return EnrichmentRunResponse(
        dry_run=dry_run,
        requested_limit=requested_limit,
        fetched=fetched,
        enriched=sum(1 for item in results if item.status in {"enriched", "dry_run"}),
        skipped=sum(1 for item in results if item.status == "skipped"),
        failed=sum(1 for item in results if item.status == "failed"),
        results=results,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run lead enrichment against Attio leads.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Run without writing back to Attio.")
    parser.add_argument("--write", action="store_true", help="Write enrichment results back to Attio.")
    args = parser.parse_args()
    try:
        response = asyncio.run(run_enrichment(limit=args.limit, dry_run=not args.write))
        print(response.model_dump_json(indent=2))
    except ValueError as exc:
        print(json.dumps({"status": "failed", "message": str(exc)}, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
