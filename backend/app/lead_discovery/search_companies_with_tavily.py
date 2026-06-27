from urllib.parse import urldefrag

from app.lead_discovery.domain import is_blocked_domain, normalize_domain
from app.lead_discovery.models import CompanyCandidate, CompanySearchResult
from app.lead_enrichment.clients.tavily_client import TavilyClient
from app.lead_enrichment.config import EnrichmentSettings


PROCUREMENT_PORTALS = [
    {
        "name": "Find a Tender Service",
        "domains": ["find-tender.service.gov.uk"],
        "notes": "Higher-value UK public contracts and notices.",
    },
    {
        "name": "Contracts Finder",
        "domains": ["contractsfinder.service.gov.uk", "gov.uk"],
        "notes": "England and non-devolved public sector opportunities, including SME-friendly below-threshold work.",
    },
    {
        "name": "Public Contracts Scotland",
        "domains": ["publiccontractsscotland.gov.uk"],
        "notes": "Scottish public sector opportunities.",
    },
    {
        "name": "Sell2Wales",
        "domains": ["sell2wales.gov.wales"],
        "notes": "Welsh public sector opportunities.",
    },
    {
        "name": "eTendersNI",
        "domains": ["etendersni.gov.uk"],
        "notes": "Northern Ireland devolved government procurement.",
    },
    {
        "name": "eSourcingNI / NIHE",
        "domains": ["e-sourcingni.bravosolution.co.uk", "nihe.gov.uk"],
        "notes": "Northern Ireland Housing Executive and related eSourcing opportunities.",
    },
]

CONTRACT_SEARCH_TERMS = "tender OR contract OR procurement OR opportunity OR framework OR notice"


async def searchCompaniesWithTavily(
    niche: str,
    region: str | None,
    limit: int,
    settings: EnrichmentSettings,
    tavily_client: TavilyClient,
) -> list[CompanyCandidate]:
    candidates: dict[str, CompanyCandidate] = {}
    region_part = f" in {region}" if region else ""
    for portal in PROCUREMENT_PORTALS:
        for portal_domain in portal["domains"]:
            query = f"site:{portal_domain} {niche} ({CONTRACT_SEARCH_TERMS}){region_part}"
            results = await tavily_client.search(
                query,
                max_results=settings.discovery_max_search_results,
                search_depth=settings.discovery_search_depth,
                country=settings.tavily_search_country or None,
            )
            for result in results:
                url = _normalize_url(result.get("url", ""))
                if not url:
                    continue
                domain = normalize_domain(url)
                if not domain or is_blocked_domain(domain) or not _matches_portal(domain, portal["domains"]):
                    continue
                candidate = candidates.setdefault(
                    url,
                    CompanyCandidate(domain=domain, portal_name=portal["name"]),
                )
                candidate.urls.append(
                    CompanySearchResult(
                        url=url,
                        title=result.get("title", ""),
                        snippet=result.get("content", "") or result.get("snippet", ""),
                        score=float(result.get("score") or 0.0),
                        domain=domain,
                        page_type=_infer_page_type(url, CONTRACT_SEARCH_TERMS),
                        portal_name=portal["name"],
                    )
                )
    return sorted(candidates.values(), key=_candidate_score, reverse=True)[:limit]


def _candidate_score(candidate: CompanyCandidate) -> float:
    page_type_bonus = len({item.page_type for item in candidate.urls}) * 0.25
    return sum(item.score for item in candidate.urls) + page_type_bonus


def _infer_page_type(url: str, fallback: str) -> str:
    lowered = url.lower()
    checks = {
        "contract_notice": ("tender", "contract", "notice", "opportunity"),
        "award_notice": ("award", "awarded"),
        "framework": ("framework", "dynamic-purchasing", "dps"),
        "buyer_profile": ("buyer", "authority", "organisation"),
    }
    for page_type, tokens in checks.items():
        if any(token in lowered for token in tokens):
            return page_type
    if "framework" in fallback:
        return "framework"
    return "contract_notice"


def _normalize_url(url: str) -> str:
    if not url:
        return ""
    clean_url, _fragment = urldefrag(url)
    return clean_url.rstrip("/")


def _matches_portal(domain: str, portal_domains: list[str]) -> bool:
    normalized = normalize_domain(domain)
    for portal_domain in portal_domains:
        expected = normalize_domain(portal_domain)
        if normalized == expected or normalized.endswith(f".{expected}"):
            return True
    return False
