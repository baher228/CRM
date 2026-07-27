import argparse
import asyncio
import json
import sys

from app import platform_db
from app.lead_enrichment.build_summary import buildSummary
from app.lead_enrichment.classify_lead import classifyLead
from app.lead_enrichment.clients.gemini_client import GeminiClient
from app.lead_enrichment.clients.tavily_client import TavilyClient
from app.lead_enrichment.config import EnrichmentSettings
from app.lead_enrichment.extract_pages import extractPages
from app.lead_enrichment.find_pages_with_tavily import findPagesWithTavily
from app.lead_enrichment.idempotency import is_unchanged
from app.lead_enrichment.logging import configure_logging, log_event
from app.lead_enrichment.models import EnrichmentRunResponse, LeadRunResult, LeadSource


async def run_enrichment(limit: int | None = None, dry_run: bool = True) -> EnrichmentRunResponse:
    configure_logging()
    settings = EnrichmentSettings()
    settings.require_read_keys()
    requested_limit = limit or settings.enrichment_default_limit
    leads = _local_leads(requested_limit)
    tavily_client = TavilyClient(settings)
    gemini_client = GeminiClient(settings) if settings.gemini_configured else None
    try:
        semaphore = asyncio.Semaphore(
            min(settings.enrichment_local_concurrency, settings.enrichment_tavily_concurrency, settings.enrichment_llm_concurrency)
        )

        async def run_one(lead: LeadSource) -> LeadRunResult:
            async with semaphore:
                return await _run_lead(settings, lead, tavily_client, gemini_client, dry_run)

        results = await asyncio.gather(*(run_one(lead) for lead in leads))
        return EnrichmentRunResponse(
            dry_run=dry_run,
            requested_limit=requested_limit,
            fetched=len(leads),
            enriched=sum(1 for item in results if item.status in {"enriched", "dry_run"}),
            skipped=sum(1 for item in results if item.status == "skipped"),
            failed=sum(1 for item in results if item.status == "failed"),
            results=results,
        )
    finally:
        await tavily_client.close()


async def _run_lead(settings, lead, tavily_client, gemini_client, dry_run) -> LeadRunResult:
    try:
        found_pages = await findPagesWithTavily(lead, settings, tavily_client)
        extracted_pages = await extractPages(found_pages, tavily_client)
        classification = await classifyLead(lead, extracted_pages, gemini_client)
        enrichment = buildSummary(lead, found_pages, extracted_pages, classification, settings.enrichment_classifier_version)
        if is_unchanged(lead.existing_fingerprint, enrichment.fingerprint):
            return LeadRunResult(record_id=lead.record_id, name=lead.name, status="skipped", message="Enrichment is already current", source_urls=enrichment.source_urls)
        if not dry_run:
            _save_local_enrichment(lead, enrichment)
        return LeadRunResult(
            record_id=lead.record_id,
            name=lead.name,
            status="dry_run" if dry_run else "enriched",
            message="Preview ready" if dry_run else "Enrichment saved in CRM Workspace",
            fit_score=classification.fit_score,
            urgency_score=classification.urgency_score,
            confidence_score=classification.confidence_score,
            source_urls=enrichment.source_urls,
        )
    except Exception as exc:
        log_event("enrichment_lead_failed", record_id=lead.record_id, error=str(exc))
        return LeadRunResult(record_id=lead.record_id, name=lead.name, status="failed", message=str(exc))


def _local_leads(limit: int) -> list[LeadSource]:
    with platform_db.connect() as conn:
        rows = conn.execute(
            """SELECT o.id, o.title, o.custom_json, a.domain, c.email
               FROM opportunities o JOIN accounts a ON a.id=o.account_id
               LEFT JOIN contacts c ON c.id=o.primary_contact_id
               WHERE o.archived_at IS NULL ORDER BY o.updated_at ASC LIMIT ?""",
            (limit,),
        ).fetchall()
    result = []
    for row in rows:
        try:
            custom = json.loads(row["custom_json"] or "{}")
        except json.JSONDecodeError:
            custom = {}
        result.append(
            LeadSource(
                object_slug="opportunity",
                record_id=str(row["id"]),
                name=row["title"],
                domain=row["domain"] or None,
                email=row["email"] or None,
                existing_fingerprint=custom.get("enrichment_fingerprint"),
            )
        )
    return result


def _save_local_enrichment(lead: LeadSource, enrichment) -> None:
    classification = enrichment.classification
    with platform_db.connect() as conn:
        row = conn.execute("SELECT custom_json, version FROM opportunities WHERE id=?", (int(lead.record_id),)).fetchone()
        if not row:
            return
        try:
            custom = json.loads(row["custom_json"] or "{}")
        except json.JSONDecodeError:
            custom = {}
        custom.update(
            {
                "enrichment_fingerprint": enrichment.fingerprint,
                "enrichment_summary": enrichment.summary_text,
                "fit_score": classification.fit_score,
                "urgency_score": classification.urgency_score,
                "confidence_score": classification.confidence_score,
                "source_urls": enrichment.source_urls,
            }
        )
        now = platform_db.utc_now().isoformat()
        conn.execute(
            "UPDATE opportunities SET custom_json=?, updated_at=?, version=version+1 WHERE id=?",
            (json.dumps(custom), now, int(lead.record_id)),
        )
        conn.execute(
            """INSERT INTO activities(entity_type,entity_id,kind,subject,body,occurred_at,created_at)
               VALUES('opportunity',?,'system','Research refreshed',?,?,?)""",
            (int(lead.record_id), enrichment.summary_text, now, now),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich local CRM opportunities with public evidence.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving.")
    parser.add_argument("--write", action="store_true", help="Save enrichment to CRM Workspace.")
    args = parser.parse_args()
    try:
        response = asyncio.run(run_enrichment(limit=args.limit, dry_run=not args.write))
        print(response.model_dump_json(indent=2))
    except ValueError as exc:
        print(json.dumps({"status": "failed", "message": str(exc)}, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
