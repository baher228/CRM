import html
import re
from urllib.parse import urljoin, urlparse, urlunparse, urldefrag

import httpx

from app.lead_discovery.domain import is_blocked_domain, normalize_domain
from app.lead_discovery.models import CompanyCandidate, CompanySearchResult
from app.lead_enrichment.clients.tavily_client import TavilyClient
from app.lead_enrichment.config import EnrichmentSettings


PROCUREMENT_PORTALS = [
    {
        "name": "Find a Tender Service",
        "domains": ["find-tender.service.gov.uk"],
        "notes": "Higher-value UK public contracts and notices.",
        "priority": 100,
        "regions": ["uk", "england"],
        "themes": ["public", "tender"],
        "default_selected": True,
        "search_weight": 1.2,
    },
    {
        "name": "Contracts Finder",
        "domains": ["contractsfinder.service.gov.uk", "gov.uk"],
        "notes": "England and non-devolved public sector opportunities, including SME-friendly below-threshold work.",
        "priority": 95,
        "regions": ["uk", "england"],
        "themes": ["public", "sme", "local"],
        "default_selected": True,
        "search_weight": 1.2,
    },
    {
        "name": "Public Contracts Scotland",
        "domains": ["publiccontractsscotland.gov.uk"],
        "notes": "Scottish public sector opportunities.",
        "priority": 72,
        "regions": ["scotland", "glasgow", "edinburgh"],
        "themes": ["regional", "public"],
        "default_selected": True,
        "search_weight": 1.0,
    },
    {
        "name": "Sell2Wales",
        "domains": ["sell2wales.gov.wales"],
        "notes": "Welsh public sector opportunities.",
        "priority": 72,
        "regions": ["wales", "cardiff", "swansea"],
        "themes": ["regional", "public"],
        "default_selected": True,
        "search_weight": 1.0,
    },
    {
        "name": "eTendersNI",
        "domains": ["etendersni.gov.uk"],
        "notes": "Northern Ireland devolved government procurement.",
        "priority": 68,
        "regions": ["northern ireland", "belfast", "ni"],
        "themes": ["regional", "public"],
        "default_selected": True,
        "search_weight": 1.0,
    },
    {
        "name": "eSourcingNI / NIHE",
        "domains": ["e-sourcingni.bravosolution.co.uk", "nihe.gov.uk"],
        "notes": "Northern Ireland Housing Executive and related eSourcing opportunities.",
        "priority": 66,
        "regions": ["northern ireland", "belfast", "ni"],
        "themes": ["regional", "housing", "repairs", "facilities"],
        "default_selected": True,
        "search_weight": 1.0,
    },
    {
        "name": "TED / Tenders Electronic Daily",
        "domains": ["ted.europa.eu"],
        "notes": "EU and cross-border public procurement notices.",
        "priority": 38,
        "regions": ["eu", "europe", "cross-border", "international"],
        "themes": ["eu", "cross-border"],
        "default_selected": False,
        "search_weight": 0.8,
    },
    {
        "name": "London Tenders Portal",
        "domains": ["londontenders.org"],
        "notes": "London borough and regional public procurement.",
        "priority": 74,
        "regions": ["london"],
        "themes": ["regional", "local", "council"],
        "default_selected": True,
        "search_weight": 1.0,
    },
    {
        "name": "The Chest",
        "domains": ["the-chest.org.uk"],
        "notes": "North West local authority procurement opportunities.",
        "priority": 70,
        "regions": ["north west", "manchester", "liverpool", "lancashire", "cheshire"],
        "themes": ["regional", "local", "council"],
        "default_selected": True,
        "search_weight": 1.0,
    },
    {
        "name": "NHS Supply Chain / Jaggaer",
        "domains": ["supplychain.nhs.uk", "nhssupplychain.app.jaggaer.com", "jaggaer.com"],
        "notes": "NHS Supply Chain supplier tenders and Jaggaer-hosted opportunities.",
        "priority": 62,
        "regions": ["uk", "england"],
        "themes": ["healthcare", "nhs", "medical", "hospital", "facilities"],
        "default_selected": True,
        "search_weight": 1.0,
    },
    {
        "name": "NHS Shared Business Services",
        "domains": ["sbs.nhs.uk", "nhssbs.co.uk"],
        "notes": "NHS frameworks, procurement documents, and shared services opportunities.",
        "priority": 62,
        "regions": ["uk", "england"],
        "themes": ["healthcare", "nhs", "framework", "medical", "hospital"],
        "default_selected": True,
        "search_weight": 1.0,
    },
    {
        "name": "Crown Commercial Service (CCS)",
        "domains": ["crowncommercial.gov.uk"],
        "notes": "Central government commercial agreements and frameworks.",
        "priority": 60,
        "regions": ["uk"],
        "themes": ["framework", "central government", "supplier", "dps"],
        "default_selected": True,
        "search_weight": 1.0,
    },
    {
        "name": "ESPO",
        "domains": ["espo.org"],
        "notes": "Public sector procurement frameworks and tenders.",
        "priority": 56,
        "regions": ["uk", "england"],
        "themes": ["framework", "supplier", "public"],
        "default_selected": True,
        "search_weight": 0.9,
    },
    {
        "name": "YPO",
        "domains": ["ypo.co.uk"],
        "notes": "Public sector procurement frameworks and supplier opportunities.",
        "priority": 56,
        "regions": ["uk", "england", "yorkshire"],
        "themes": ["framework", "supplier", "public"],
        "default_selected": True,
        "search_weight": 0.9,
    },
    {
        "name": "NEPO",
        "domains": ["nepo.org"],
        "notes": "North East procurement organisation opportunities and frameworks.",
        "priority": 58,
        "regions": ["north east", "newcastle", "durham", "sunderland", "tees valley"],
        "themes": ["regional", "framework", "local"],
        "default_selected": True,
        "search_weight": 0.9,
    },
]

CONTRACT_SEARCH_TERMS = "tender OR contract OR procurement OR opportunity OR framework OR notice"


async def searchCompaniesWithTavily(
    niche: str,
    region: str | None,
    limit: int,
    settings: EnrichmentSettings,
    tavily_client: TavilyClient,
    portals: list[str] | None = None,
    deadline_window: str = "",
    minimum_value: str = "",
    open_notices_only: bool = True,
) -> list[CompanyCandidate]:
    candidates: dict[str, CompanyCandidate] = {}
    region_part = f" in {region}" if region else ""
    selected_portals = prioritize_portals(portals or [], niche, region)
    constraint_terms = _constraint_terms(deadline_window, minimum_value, open_notices_only)
    for portal in selected_portals:
        for portal_domain in portal["domains"]:
            query = f"site:{portal_domain} {niche} ({CONTRACT_SEARCH_TERMS}){region_part} {constraint_terms}".strip()
            results = await tavily_client.search(
                query,
                max_results=settings.discovery_max_search_results,
                search_depth=settings.discovery_search_depth,
                country=settings.tavily_search_country or None,
            )
            for result in results:
                expanded_results = await _expand_search_result(result, portal)
                for expanded in expanded_results:
                    url = _normalize_url(expanded["url"])
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
                            title=expanded.get("title", ""),
                            snippet=expanded.get("snippet", ""),
                            score=float(expanded.get("score") or 0.0),
                            domain=domain,
                            page_type=_infer_page_type(url, CONTRACT_SEARCH_TERMS),
                            portal_name=portal["name"],
                        )
                    )
        if len(candidates) >= max(limit * 3, limit + 5):
            break
    return sorted(candidates.values(), key=_candidate_score, reverse=True)[:limit]


def portal_options() -> list[str]:
    return [portal["name"] for portal in PROCUREMENT_PORTALS]


def portal_metadata(niche: str = "", region: str | None = None) -> list[dict[str, object]]:
    return [
        {
            "name": portal["name"],
            "domains": portal["domains"],
            "notes": portal["notes"],
            "default_selected": portal.get("default_selected", True),
            "priority": _portal_score(portal, niche, region),
            "label": _portal_label(portal, niche, region),
        }
        for portal in prioritize_portals([], niche, region)
    ]


def prioritize_portals(portals: list[str], niche: str = "", region: str | None = None) -> list[dict[str, object]]:
    selected_portals = _selected_portals(portals)
    return sorted(
        selected_portals,
        key=lambda portal: (_portal_score(portal, niche, region), str(portal["name"])),
        reverse=True,
    )


def _candidate_score(candidate: CompanyCandidate) -> float:
    page_type_bonus = len({item.page_type for item in candidate.urls}) * 0.25
    portal = _portal_by_name(candidate.portal_name)
    portal_weight = float(portal.get("search_weight", 1.0)) if portal else 1.0
    return (sum(item.score for item in candidate.urls) + page_type_bonus) * portal_weight


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
    parsed = urlparse(clean_url.rstrip("/"))
    if parsed.netloc.lower().endswith("contractsfinder.service.gov.uk") and parsed.path.lower().startswith("/notice/"):
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", parsed.query, ""))


async def _expand_search_result(result: dict, portal: dict[str, object]) -> list[dict[str, object]]:
    url = _normalize_url(str(result.get("url") or ""))
    title = str(result.get("title") or "")
    snippet = str(result.get("content") or result.get("snippet") or "")
    score = float(result.get("score") or 0.0)
    if _is_contracts_finder_search_url(url):
        expanded = await _contracts_finder_notices_from_search_page(url, score)
        if expanded:
            return expanded
    return [{"url": url, "title": title, "snippet": snippet, "score": score}]


async def _contracts_finder_notices_from_search_page(url: str, score: float) -> list[dict[str, object]]:
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPError:
        return []

    notices: list[dict[str, object]] = []
    pattern = re.compile(
        r'<div class="search-result-header" title="(?P<title>[^"]+)".*?'
        r'<a\s+href="(?P<href>[^"]+)".*?</div>\s*'
        r'<div class="search-result-sub-header wrap-text">(?P<buyer>.*?)</div>',
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(response.text):
        title = html.unescape(_strip_tags(match.group("title"))).strip()
        buyer = html.unescape(_strip_tags(match.group("buyer"))).strip()
        href = html.unescape(match.group("href")).strip()
        notice_url = _normalize_url(urljoin(url, href))
        if not notice_url or _is_contracts_finder_search_url(notice_url):
            continue
        notices.append(
            {
                "url": notice_url,
                "title": title,
                "snippet": buyer,
                "score": score,
            }
        )
    return notices


def _is_contracts_finder_search_url(url: str) -> bool:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return parsed.netloc.lower().endswith("contractsfinder.service.gov.uk") and parsed.path.lower().startswith("/search/")


def _strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value)


def _matches_portal(domain: str, portal_domains: list[str]) -> bool:
    normalized = normalize_domain(domain)
    for portal_domain in portal_domains:
        expected = normalize_domain(portal_domain)
        if normalized == expected or normalized.endswith(f".{expected}"):
            return True
    return False


def _selected_portals(portals: list[str]) -> list[dict[str, object]]:
    if not portals:
        return [portal for portal in PROCUREMENT_PORTALS if portal.get("default_selected", True)]
    selected = {portal.strip().lower() for portal in portals if portal.strip()}
    return [portal for portal in PROCUREMENT_PORTALS if portal["name"].lower() in selected] or PROCUREMENT_PORTALS


def _portal_score(portal: dict[str, object], niche: str, region: str | None) -> int:
    text = f"{niche} {region or ''}".lower()
    score = int(portal.get("priority", 50))
    region_tokens = [str(item).lower() for item in portal.get("regions", [])]
    theme_tokens = [str(item).lower() for item in portal.get("themes", [])]
    if region and any(token and token in text for token in region_tokens):
        score += 35
    if any(token and token in text for token in theme_tokens):
        score += 25
    if _has_any(text, ["nhs", "healthcare", "clinical", "medical", "hospital", "estates", "facilities"]):
        if any(theme in theme_tokens for theme in ["healthcare", "nhs", "medical", "hospital", "facilities"]):
            score += 35
    if _has_any(text, ["framework", "dps", "supplier", "procurement route", "ccs", "espo", "ypo"]):
        if any(theme in theme_tokens for theme in ["framework", "supplier", "dps"]):
            score += 35
    if _has_any(text, ["eu", "europe", "cross-border", "international", "non-uk"]):
        if "eu" in theme_tokens or "cross-border" in theme_tokens:
            score += 45
    return min(score, 200)


def _portal_label(portal: dict[str, object], niche: str, region: str | None) -> str:
    score = _portal_score(portal, niche, region)
    themes = {str(item).lower() for item in portal.get("themes", [])}
    if score >= 110:
        return "High match"
    if "eu" in themes or "cross-border" in themes:
        return "EU"
    if "healthcare" in themes or "nhs" in themes:
        return "Healthcare"
    if "framework" in themes or "supplier" in themes:
        return "Framework"
    if "regional" in themes or "local" in themes:
        return "Regional"
    return "Core"


def _portal_by_name(name: str) -> dict[str, object] | None:
    lowered = name.lower()
    return next((portal for portal in PROCUREMENT_PORTALS if portal["name"].lower() == lowered), None)


def _has_any(text: str, tokens: list[str]) -> bool:
    return any(token in text for token in tokens)


def _constraint_terms(deadline_window: str, minimum_value: str, open_notices_only: bool) -> str:
    terms = []
    if open_notices_only:
        terms.append("open tender active opportunity deadline")
    if deadline_window:
        terms.append(f"deadline {deadline_window}")
    if minimum_value:
        terms.append(f"value {minimum_value}")
    return " ".join(terms)
