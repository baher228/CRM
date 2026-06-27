import re
from typing import Any
from urllib.parse import urlparse, urlunparse

from pydantic import BaseModel, Field

from app.lead_enrichment.clients.gemini_client import GeminiClient
from app.lead_enrichment.clients.tavily_client import TavilyClient
from app.lead_enrichment.config import EnrichmentSettings
from app.schemas import Lead


CONTACT_LOOKUP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "contact_name": {"type": "string"},
        "contact_email": {"type": "string"},
        "contact_phone": {"type": "string"},
        "contact_source_url": {"type": "string"},
        "confidence": {"type": "integer"},
    },
    "required": [
        "contact_name",
        "contact_email",
        "contact_phone",
        "contact_source_url",
        "confidence",
    ],
}


class ContactLookupResult(BaseModel):
    contact_name: str = ""
    contact_email: str | None = None
    contact_phone: str = ""
    contact_source_url: str = ""
    confidence: int = Field(default=0, ge=0, le=100)


async def find_contact_for_lead(lead: Lead, settings: EnrichmentSettings) -> ContactLookupResult:
    settings.require_discovery_keys()
    tavily_client = TavilyClient(settings)
    gemini_client = GeminiClient(settings)
    try:
        seed_urls = _lead_urls(lead)
        search_urls = await _search_contact_urls(lead, settings, tavily_client)
        urls = _dedupe_urls([*seed_urls, *search_urls])[: settings.discovery_max_extract_urls]
        if not urls:
            return ContactLookupResult()

        extracted_pages, _failed = await tavily_client.extract(
            urls,
            extract_depth=settings.discovery_advanced_extract_depth,
            query="Find procurement, tender, contracts, buying, supplier, or commercial contact details.",
            output_format="markdown",
        )
        pages = _normalize_pages(extracted_pages)
        if not pages:
            return ContactLookupResult()

        payload = await gemini_client.generate_json(_contact_prompt(lead, pages), CONTACT_LOOKUP_SCHEMA)
        parsed = ContactLookupResult.model_validate(payload)
        email = clean_email(parsed.contact_email)
        return parsed.model_copy(
            update={
                "contact_email": email,
                "contact_name": _clean_known_text(parsed.contact_name),
                "contact_phone": _clean_known_text(parsed.contact_phone),
                "contact_source_url": _clean_known_text(parsed.contact_source_url),
                "confidence": max(0, min(100, parsed.confidence or 0)),
            }
        )
    finally:
        await tavily_client.close()


async def _search_contact_urls(
    lead: Lead,
    settings: EnrichmentSettings,
    tavily_client: TavilyClient,
) -> list[str]:
    urls: list[str] = []
    for query in _contact_queries(lead):
        results = await tavily_client.search(
            query,
            max_results=min(4, settings.discovery_max_search_results),
            search_depth=settings.discovery_search_depth,
            country=settings.tavily_search_country or None,
        )
        urls.extend(str(result.get("url") or "") for result in results)
    return urls


def _lead_urls(lead: Lead) -> list[str]:
    return [
        url
        for url in [
            lead.contract_url,
            *lead.source_urls,
            lead.buyer_website,
            lead.website,
        ]
        if _is_useful_url(url)
    ]


def _contact_queries(lead: Lead) -> list[str]:
    buyer = _first_known(lead.buyer_name, lead.company, lead.name)
    title = _first_known(lead.contract_title, lead.name)
    queries = [
        f"{buyer} procurement contact email",
        f"{buyer} tenders contact",
        f"{buyer} contracts email",
    ]
    if title and title != buyer:
        queries.append(f"{buyer} {title} tender contact email")
    domain = _domain_from_url(lead.buyer_website or lead.website)
    if domain:
        queries.insert(0, f"site:{domain} procurement contact email")
        queries.insert(1, f"site:{domain} tenders contracts contact")
    return [query for query in queries if query.strip()]


def _normalize_pages(pages: list[dict[str, Any]]) -> list[dict[str, str]]:
    normalized = []
    for page in pages:
        url = str(page.get("url") or "")
        content = str(page.get("raw_content") or page.get("content") or "")
        title = str(page.get("title") or "")
        if not _is_useful_url(url) or not content.strip():
            continue
        normalized.append(
            {
                "url": url,
                "title": title[:160],
                "content": re.sub(r"\s+", " ", content).strip()[:5000],
            }
        )
    return normalized[:8]


def _contact_prompt(lead: Lead, pages: list[dict[str, str]]) -> str:
    page_blocks = "\n\n".join(
        f"URL: {page['url']}\nTITLE: {page['title']}\nCONTENT:\n{page['content']}"
        for page in pages
    )
    return f"""
You are extracting real procurement contact details for a public-sector tender lead.

Tender title: {lead.contract_title or lead.name}
Buyer: {lead.buyer_name or lead.company}
Portal: {lead.portal_name or lead.source}
Contract URL: {lead.contract_url or lead.website}

Rules:
- Use only the supplied page evidence.
- Prefer a procurement, tenders, contracts, buying, supplier, commercial, or estates/facilities contact.
- Return a real email address only when it is visible in the evidence.
- Do not invent placeholder emails.
- If no field is visible, return an empty string for that field.
- contact_source_url must be the exact supplied URL where the chosen contact detail was found.
- confidence is 0-100 based on how directly the evidence matches the tender or buyer procurement team.

Evidence:
{page_blocks}
""".strip()


def clean_email(value: str | None) -> str | None:
    if not _is_known(value):
        return None
    match = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", str(value))
    return match.group(0) if match else None


def _clean_known_text(value: str | None) -> str:
    return str(value).strip() if _is_known(value) else ""


def _dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for url in urls:
        key = _canonical_url(url)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(url)
    return deduped


def _canonical_url(value: str) -> str:
    if not _is_useful_url(value):
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+", "/", parsed.path.rstrip("/")).lower()
    return urlunparse(("https", host, path, "", "", ""))


def _is_useful_url(value: str | None) -> bool:
    if not _is_known(value):
        return False
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = parsed.netloc.lower()
    return bool(host and not host.endswith("example.com"))


def _domain_from_url(value: str | None) -> str:
    if not _is_useful_url(value):
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _first_known(*values: str) -> str:
    for value in values:
        if _is_known(value):
            return str(value).strip()
    return ""


def _is_known(value: Any) -> bool:
    return bool(value and str(value).strip() and str(value).strip().lower() not in {"unknown", "n/a", "none", "-"})
