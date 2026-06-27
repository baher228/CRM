from urllib.parse import urlparse

from app.lead_enrichment.clients.tavily_client import TavilyClient
from app.lead_enrichment.config import EnrichmentSettings
from app.lead_enrichment.models import FoundPage, LeadSource


PAGE_INTENTS = [
    ("website", ""),
    ("pricing", "pricing"),
    ("about", "about"),
    ("security", "security compliance trust"),
    ("terms", "terms privacy legal"),
    ("careers", "careers jobs hiring"),
    ("procurement", "procurement contracts tender public sector"),
]


async def findPagesWithTavily(
    lead: LeadSource,
    settings: EnrichmentSettings,
    tavily_client: TavilyClient,
) -> list[FoundPage]:
    found: dict[str, FoundPage] = {}
    base = lead.domain or lead.name
    for page_type, intent in PAGE_INTENTS:
        query = f"{base} {intent}".strip()
        results = await tavily_client.search(query, settings.tavily_max_search_results)
        for result in results:
            url = result.get("url")
            if not url:
                continue
            found[url] = FoundPage(
                url=url,
                title=result.get("title", ""),
                snippet=result.get("content", "") or result.get("snippet", ""),
                score=float(result.get("score") or 0.0),
                page_type=_infer_page_type(url, page_type),
                official_domain=_is_official_domain(url, lead.domain),
            )
    return _rank_pages(list(found.values()), settings.tavily_max_extract_urls)


def _rank_pages(pages: list[FoundPage], limit: int) -> list[FoundPage]:
    priority = {
        "website": 20,
        "pricing": 18,
        "security": 16,
        "about": 14,
        "terms": 12,
        "careers": 10,
        "procurement": 8,
        "general": 0,
    }
    return sorted(
        pages,
        key=lambda page: (
            page.official_domain,
            priority.get(page.page_type, 0),
            page.score,
        ),
        reverse=True,
    )[:limit]


def _infer_page_type(url: str, fallback: str) -> str:
    lowered = url.lower()
    for page_type in ("pricing", "security", "about", "terms", "careers", "procurement"):
        if page_type in lowered:
            return page_type
    if "privacy" in lowered or "legal" in lowered:
        return "terms"
    if "trust" in lowered or "compliance" in lowered:
        return "security"
    if "contract" in lowered or "tender" in lowered:
        return "procurement"
    return fallback or "general"


def _is_official_domain(url: str, domain: str | None) -> bool:
    if not domain:
        return False
    host = urlparse(url).netloc.lower().removeprefix("www.")
    expected = domain.lower().removeprefix("www.")
    return host == expected or host.endswith(f".{expected}")

