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
from app.lead_enrichment.clients.gemini_client import GeminiClient
from app.lead_enrichment.clients.tavily_client import TavilyClient
from app.lead_enrichment.logging import configure_logging, log_event
from app.services.contract_availability import assess_contract_availability
from app import platform_db
from app.services.lead_sources import canonical_url_key


async def run_discovery(
    niche: str,
    region: str | None = None,
    limit: int | None = None,
    dry_run: bool = True,
    portals: list[str] | None = None,
    deadline_window: str = "",
    minimum_value: str = "",
    open_notices_only: bool = True,
    progress_callback: Callable[[dict], Awaitable[None]] | None = None,
) -> DiscoveryRunResponse:
    configure_logging()
    settings = DiscoverySettings()
    settings.require_discovery_keys()
    requested_limit = limit or settings.discovery_default_limit

    tavily_client = TavilyClient(settings)
    gemini_client = GeminiClient(settings) if settings.gemini_configured else None

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
            max(requested_limit * 3, requested_limit + 10),
            settings,
            tavily_client,
            portals=portals or [],
            deadline_window=deadline_window,
            minimum_value=minimum_value,
            open_notices_only=open_notices_only,
        )
        known_keys = known_discovery_keys()
        candidates = []
        skipped_results = []
        for candidate in raw_candidates:
            candidate_key = _candidate_known_key(candidate)
            if candidate_key and candidate_key in known_keys:
                result = DiscoveryCompanyResult(
                    domain=candidate.domain,
                    company_name="Already saved",
                    status="skipped",
                    message="Already in Leads",
                    source_urls=[item.url for item in candidate.urls],
                    portal_name=candidate.portal_name or "Unknown",
                    portal_domain=candidate.domain,
                    contract_url=candidate.urls[0].url if candidate.urls else "",
                    dedupe_key=candidate_key,
                )
                skipped_results.append(result)
                await _publish(progress_callback, phase="searching", message="Already in Leads", result=result)
                continue
            candidates.append(candidate)
            if len(candidates) >= requested_limit:
                break
        await _publish(
            progress_callback,
            phase="searching",
            message=f"Found {len(candidates)} candidate contract notices",
            total=len(candidates) + len(skipped_results),
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
                settings.enrichment_local_concurrency,
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
                    tavily_client,
                    gemini_client,
                    dry_run,
                    progress_callback,
                )
                logRunResult(result)
                return result

        parsed_results = await asyncio.gather(*(run_one(candidate) for candidate in candidates))
        results = [*skipped_results, *parsed_results]
        return _response(dry_run, niche, region, requested_limit, len(results), results)
    finally:
        await tavily_client.close()


async def _run_candidate(
    niche,
    region,
    candidate,
    settings,
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
        availability = assess_contract_availability(
            profile.deadline,
            profile.contract_status,
            profile.procurement_stage,
            profile.outreach_angle,
        )
        profile.availability_status = availability.status
        profile.availability_reason = availability.reason
        profile.availability_checked_at = availability.checked_at.isoformat()
        if availability.status == "Unavailable":
            result = _result_from_profile(
                profile,
                "skipped",
                f"Contract no longer appears available: {availability.reason}",
                profile.source_urls,
            )
            await _publish(progress_callback, phase="parsing", message=result.message, result=result)
            return result
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
            "saving",
            profile.domain,
            "Saving parsed contract opportunity" if not dry_run else "Preparing preview result",
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
            contract_status=profile.contract_status,
            availability_status=profile.availability_status,
            availability_reason=profile.availability_reason,
            availability_checked_at=profile.availability_checked_at,
        )
        message = "Preview ready" if dry_run else "Saved to CRM Workspace tender inbox"
        result = _result_from_profile(profile, "dry_run" if dry_run else "upserted", message, summary.source_urls)
        await _publish(progress_callback, phase="saving", message=message, result=result)
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
    contract_status: str = "Unknown",
    availability_status: str = "Unverified",
    availability_reason: str = "",
    availability_checked_at: str = "",
    buyer_website: str = "",
    buyer_contact: str = "Unknown",
    contact_name: str = "",
    contact_email: str = "",
    contact_phone: str = "",
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
            contract_status=contract_status,
            availability_status=availability_status,
            availability_reason=availability_reason,
            availability_checked_at=availability_checked_at,
            buyer_website=buyer_website,
            buyer_contact=buyer_contact,
            contact_name=contact_name,
            contact_email=contact_email,
            contact_phone=contact_phone,
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
        contract_status=profile.contract_status,
        availability_status=profile.availability_status,
        availability_reason=profile.availability_reason,
        availability_checked_at=profile.availability_checked_at,
        buyer_website=profile.buyer_website,
        buyer_contact=profile.buyer_contact,
        contact_name=profile.contact_name,
        contact_email=profile.contact_email,
        contact_phone=profile.contact_phone,
    )


def _candidate_known_key(candidate) -> str:
    for item in candidate.urls:
        key = discovery_key_for_url(item.url)
        if key:
            return key
    return ""


def discovery_key_for_url(url: str) -> str:
    return canonical_url_key(url)


def known_discovery_keys() -> set[str]:
    keys: set[str] = set()
    with platform_db.connect() as conn:
        for row in conn.execute("SELECT dedupe_key, contract_url FROM tender_notices"):
            for value in (row["dedupe_key"], row["contract_url"]):
                if value:
                    keys.add(str(value))
                    canonical = canonical_url_key(str(value))
                    if canonical:
                        keys.add(canonical)
        for row in conn.execute("SELECT url FROM tender_sources"):
            canonical = canonical_url_key(str(row["url"] or ""))
            if canonical:
                keys.add(canonical)
    return keys


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
    parser = argparse.ArgumentParser(description="Discover public contract opportunities and save them to CRM Workspace.")
    parser.add_argument("--niche", required=True)
    parser.add_argument("--region", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Preview results without saving them.")
    parser.add_argument("--write", action="store_true", help="Save discovered tenders to CRM Workspace.")
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
