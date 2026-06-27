from app.lead_discovery.models import CompanyCandidate, SelectedUrl
from app.lead_enrichment.config import EnrichmentSettings


DENSE_PAGE_TYPES = {"contract_notice", "award_notice", "framework"}
PAGE_PRIORITY = {
    "contract_notice": 100,
    "framework": 90,
    "award_notice": 60,
    "buyer_profile": 40,
}


def selectRelevantUrls(
    candidate: CompanyCandidate,
    settings: EnrichmentSettings,
) -> list[SelectedUrl]:
    selected_by_type: dict[str, SelectedUrl] = {}
    for result in sorted(candidate.urls, key=lambda item: item.score, reverse=True):
        page_type = result.page_type
        current = selected_by_type.get(page_type)
        if current and current.score >= result.score:
            continue
        selected_by_type[page_type] = SelectedUrl(
            url=result.url,
            domain=candidate.domain,
            page_type=page_type,
            title=result.title,
            score=result.score,
            extract_depth=_extract_depth(page_type, settings),
        )

    selected = sorted(
        selected_by_type.values(),
        key=lambda item: (PAGE_PRIORITY.get(item.page_type, 0), item.score),
        reverse=True,
    )
    return selected[: settings.discovery_max_extract_urls]


def _extract_depth(page_type: str, settings: EnrichmentSettings) -> str:
    if settings.discovery_extract_mode == "advanced":
        return settings.discovery_advanced_extract_depth
    if settings.discovery_extract_mode == "basic":
        return settings.discovery_basic_extract_depth
    return (
        settings.discovery_advanced_extract_depth
        if page_type in DENSE_PAGE_TYPES
        else settings.discovery_basic_extract_depth
    )
