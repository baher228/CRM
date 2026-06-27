import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable

from app.lead_discovery.config import DiscoverySettings
from app.lead_discovery.extract_pages import extractPages
from app.lead_discovery.log_run_result import logRunResult
from app.lead_discovery.models import DiscoveryCompanyResult, DiscoveryRunResponse
from app.lead_discovery.parse_company_profile import parseCompanyProfile
from app.lead_discovery.search_companies_with_tavily import searchCompaniesWithTavily
from app.lead_discovery.select_relevant_urls import selectRelevantUrls
from app.lead_discovery.summary import build_company_summary, compute_discovery_fingerprint
from app.lead_discovery.upsert_company_to_attio import upsertCompanyToAttio
from app.lead_enrichment.clients.attio_client import AttioClient
from app.lead_enrichment.clients.gemini_client import GeminiClient
from app.lead_enrichment.clients.tavily_client import TavilyClient
from app.lead_enrichment.logging import configure_logging, log_event


async def run_discovery(
    niche: str,
    region: str | None = None,
    limit: int | None = None,
    dry_run: bool = True,
    progress_callback: Callable[[dict], Awaitable[None]] | None = None,
) -> DiscoveryRunResponse:
    configure_logging()
    settings = DiscoverySettings()
    settings.require_discovery_keys()
    requested_limit = limit or settings.discovery_default_limit

    attio_client = AttioClient(settings)
    tavily_client = TavilyClient(settings)
    gemini_client = GeminiClient(settings)

    try:
        await _publish(
            progress_callback,
            phase="searching",
            message="Searching public procurement portals",
            total=0,
        )
        raw_candidates = await searchCompaniesWithTavily(
            niche,
            region,
            requested_limit,
            settings,
            tavily_client,
        )
        candidates = raw_candidates[:requested_limit]
        await _publish(
            progress_callback,
            phase="searching",
            message=f"Found {len(candidates)} candidate contract notices",
            total=len(candidates),
        )
        for candidate in candidates:
            await _publish_result(
                progress_callback,
                "searching",
                candidate.domain,
                f"Candidate notice found on {candidate.portal_name or candidate.domain}",
                portal_name=candidate.portal_name,
                portal_domain=candidate.domain,
                contract_url=candidate.urls[0].url if candidate.urls else "",
            )
        log_event(
            "lead_discovery_started",
            niche=niche,
            region=region,
            dry_run=dry_run,
            discovered=len(candidates),
        )
        semaphore = asyncio.Semaphore(
            min(
                settings.enrichment_attio_concurrency,
                settings.enrichment_tavily_concurrency,
                settings.enrichment_llm_concurrency,
            )
        )

        async def run_one(candidate):
            async with semaphore:
                result = await _run_candidate(
                    niche,
                    region,
                    candidate,
                    settings,
                    attio_client,
                    tavily_client,
                    gemini_client,
                    dry_run,
                    progress_callback,
                )
                logRunResult(result)
                return result

        results = await asyncio.gather(*(run_one(candidate) for candidate in candidates))
        return _response(dry_run, niche, region, requested_limit, len(candidates), results)
    finally:
        await attio_client.close()
        await tavily_client.close()


async def _run_candidate(
    niche,
    region,
    candidate,
    settings,
    attio_client,
    tavily_client,
    gemini_client,
    dry_run,
    progress_callback,
) -> DiscoveryCompanyResult:
    try:
        selected_urls = selectRelevantUrls(candidate, settings)
        await _publish_result(
            progress_callback,
            "extracting",
            candidate.domain,
            f"Extracting {len(selected_urls)} relevant pages",
            portal_name=candidate.portal_name,
            portal_domain=candidate.domain,
            contract_url=selected_urls[0].url if selected_urls else "",
        )
        extracted_pages = await extractPages(selected_urls, tavily_client)
        extracted_source_urls = [page.url for page in extracted_pages if not page.failed]
        await _publish_result(
            progress_callback,
            "parsing",
            candidate.domain,
            "Parsing contract opportunity",
            source_urls=extracted_source_urls,
            portal_name=candidate.portal_name,
            portal_domain=candidate.domain,
            contract_url=selected_urls[0].url if selected_urls else "",
        )
        profile = await parseCompanyProfile(
            niche,
            region,
            candidate.domain,
            extracted_pages,
            gemini_client,
        )
        profile.source_urls = profile.source_urls or extracted_source_urls
        fingerprint = compute_discovery_fingerprint(
            niche,
            region,
            profile.domain,
            [item.url for item in selected_urls],
            [page.content for page in extracted_pages],
            settings.discovery_parser_version,
        )
        summary = build_company_summary(profile, niche, region, fingerprint)
        await _publish_result(
            progress_callback,
            "syncing",
            profile.domain,
            "Syncing parsed contract opportunity" if not dry_run else "Preparing dry-run result",
            company_name=profile.company_name,
            confidence_score=profile.confidence_score,
            source_urls=summary.source_urls,
            contract_title=profile.contract_title,
            buyer_name=profile.buyer_name,
            portal_name=profile.portal_name,
            portal_domain=profile.portal_domain,
            contract_url=profile.contract_url,
            contract_value=profile.contract_value,
            deadline=profile.deadline,
            procurement_stage=profile.procurement_stage,
        )
        try:
            message, _record_id = await upsertCompanyToAttio(summary, settings, attio_client, dry_run)
            result = _result_from_profile(profile, "dry_run" if dry_run else "upserted", message, summary.source_urls)
            await _publish(progress_callback, phase="syncing", message=message, result=result)
            return result
        except Exception as exc:  # noqa: BLE001 - preserve parsed profile when sync fails.
            result = _result_from_profile(profile, "failed", f"Attio sync failed: {exc}", summary.source_urls)
            await _publish(progress_callback, phase="syncing", message=result.message, result=result)
            return result
    except Exception as exc:  # noqa: BLE001 - keep batch running per company.
        result = DiscoveryCompanyResult(
            domain=candidate.domain,
            status="failed",
            message=str(exc),
        )
        await _publish(progress_callback, phase="failed", message=str(exc), result=result)
        return result


async def _publish_result(
    progress_callback: Callable[[dict], Awaitable[None]] | None,
    phase: str,
    domain: str,
    message: str,
    company_name: str = "Unknown",
    confidence_score: int | None = None,
    source_urls: list[str] | None = None,
    contract_title: str = "Unknown",
    buyer_name: str = "Unknown",
    portal_name: str = "Unknown",
    portal_domain: str = "Unknown",
    contract_url: str = "",
    contract_value: str = "Unknown",
    deadline: str = "Unknown",
    procurement_stage: str = "Unknown",
) -> None:
    await _publish(
        progress_callback,
        phase=phase,
        message=message,
        result=DiscoveryCompanyResult(
            domain=domain,
            company_name=company_name,
            status=phase,
            message=message,
            confidence_score=confidence_score,
            source_urls=source_urls or [],
            contract_title=contract_title,
            buyer_name=buyer_name,
            portal_name=portal_name,
            portal_domain=portal_domain,
            contract_url=contract_url,
            contract_value=contract_value,
            deadline=deadline,
            procurement_stage=procurement_stage,
        ),
    )


async def _publish(
    progress_callback: Callable[[dict], Awaitable[None]] | None,
    **event,
) -> None:
    if progress_callback:
        await progress_callback(event)


def _result_from_profile(
    profile,
    status: str,
    message: str,
    source_urls: list[str],
) -> DiscoveryCompanyResult:
    return DiscoveryCompanyResult(
        domain=profile.domain,
        company_name=profile.company_name,
        status=status,
        message=message,
        confidence_score=profile.confidence_score,
        source_urls=source_urls,
        contract_title=profile.contract_title,
        buyer_name=profile.buyer_name,
        portal_name=profile.portal_name,
        portal_domain=profile.portal_domain,
        contract_url=profile.contract_url,
        contract_value=profile.contract_value,
        deadline=profile.deadline,
        procurement_stage=profile.procurement_stage,
    )


def _response(
    dry_run: bool,
    niche: str,
    region: str | None,
    requested_limit: int,
    discovered: int,
    results: list[DiscoveryCompanyResult],
) -> DiscoveryRunResponse:
    return DiscoveryRunResponse(
        dry_run=dry_run,
        niche=niche,
        region=region,
        requested_limit=requested_limit,
        discovered=discovered,
        upserted=sum(1 for item in results if item.status in {"upserted", "dry_run"}),
        skipped=sum(1 for item in results if item.status == "skipped"),
        failed=sum(1 for item in results if item.status == "failed"),
        results=results,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover public contract opportunities with Tavily and sync them to Attio.")
    parser.add_argument("--niche", required=True)
    parser.add_argument("--region", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Run without writing back to Attio.")
    parser.add_argument("--write", action="store_true", help="Write discovered companies to Attio.")
    args = parser.parse_args()
    try:
        response = asyncio.run(
            run_discovery(
                niche=args.niche,
                region=args.region,
                limit=args.limit,
                dry_run=not args.write,
            )
        )
        print(response.model_dump_json(indent=2))
    except ValueError as exc:
        print(json.dumps({"status": "failed", "message": str(exc)}, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
