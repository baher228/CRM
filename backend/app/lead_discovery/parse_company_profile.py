import json
import re
from typing import Any
from urllib.parse import urlparse

from app.lead_discovery.domain import normalize_domain
from app.lead_discovery.models import CompanyProfile, ExtractedCompanyPage
from app.lead_enrichment.clients.gemini_client import GeminiClient


COMPANY_PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "company_name": {"type": "string"},
        "domain": {"type": "string"},
        "contract_title": {"type": "string"},
        "buyer_name": {"type": "string"},
        "portal_name": {"type": "string"},
        "portal_domain": {"type": "string"},
        "contract_url": {"type": "string"},
        "contract_value": {"type": "string"},
        "deadline": {"type": "string"},
        "procurement_stage": {"type": "string"},
        "contract_status": {"type": "string"},
        "buyer_website": {"type": "string"},
        "buyer_contact": {"type": "string"},
        "contact_name": {"type": "string"},
        "contact_email": {"type": "string"},
        "contact_phone": {"type": "string"},
        "cpv_codes": {"type": "array", "items": {"type": "string"}},
        "location": {"type": "string"},
        "services": {"type": "array", "items": {"type": "string"}},
        "industry": {"type": "string"},
        "segment": {"type": "string"},
        "pricing_model": {"type": "string"},
        "compliance_signals": {"type": "array", "items": {"type": "string"}},
        "contract_or_procurement_signals": {"type": "array", "items": {"type": "string"}},
        "outreach_angle": {"type": "string"},
        "confidence_score": {"type": "integer"},
        "source_urls": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "company_name",
        "domain",
        "contract_title",
        "buyer_name",
        "portal_name",
        "portal_domain",
        "contract_url",
        "contract_value",
        "deadline",
        "procurement_stage",
        "contract_status",
        "buyer_website",
        "buyer_contact",
        "contact_name",
        "contact_email",
        "contact_phone",
        "cpv_codes",
        "location",
        "services",
        "industry",
        "segment",
        "pricing_model",
        "compliance_signals",
        "contract_or_procurement_signals",
        "outreach_angle",
        "confidence_score",
        "source_urls",
    ],
}


async def parseCompanyProfile(
    niche: str,
    region: str | None,
    domain: str,
    pages: list[ExtractedCompanyPage],
    gemini_client: GeminiClient | None,
) -> CompanyProfile:
    if gemini_client is None:
        return _fallback_profile(niche, region, domain, pages)
    prompt = _build_prompt(niche, region, domain, pages)
    try:
        payload = await gemini_client.generate_json(prompt, COMPANY_PROFILE_SCHEMA)
    except Exception:
        return _fallback_profile(niche, region, domain, pages)
    source_url = next((page.url for page in pages if not page.failed), "")
    payload["portal_domain"] = _clean_unknown(payload.get("portal_domain")) or domain
    payload["contract_url"] = _best_contract_url(payload.get("contract_url"), source_url)
    payload["buyer_name"] = _clean_unknown(payload.get("buyer_name")) or payload.get("company_name") or "Unknown"
    payload["contract_title"] = _clean_unknown(payload.get("contract_title")) or payload["buyer_name"]
    payload["company_name"] = (
        _clean_unknown(payload.get("buyer_name"))
        or _clean_unknown(payload.get("company_name"))
        or _clean_unknown(payload.get("contract_title"))
        or "Unknown"
    )
    payload["domain"] = _best_domain(payload, domain)
    return CompanyProfile.model_validate(payload)


def _fallback_profile(
    niche: str,
    region: str | None,
    domain: str,
    pages: list[ExtractedCompanyPage],
) -> CompanyProfile:
    available = [page for page in pages if not page.failed and page.content.strip()]
    page = available[0] if available else next((item for item in pages if not item.failed), None)
    content = page.content if page else ""
    title = (page.title.strip() if page and page.title.strip() else niche.strip()) or "Public contract opportunity"
    buyer = _match(content, r"(?:buyer|contracting authority|organisation)\s*[:\-]\s*([^\n]{3,120})", re.I) or "Unknown"
    value = _match(content, r"(?:value|contract value)\s*[:\-]?\s*(£\s?[\d,.]+(?:\s*(?:million|thousand|m|k))?)", re.I) or "Unknown"
    deadline = _match(content, r"(?:deadline|closing date|submission date)\s*[:\-]\s*([^\n]{4,80})", re.I) or "Unknown"
    email = _match(content, r"\b([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})\b", re.I)
    lower = content.lower()
    status = "Open" if any(word in lower for word in ("open", "apply", "submission deadline")) else "Unknown"
    signals = [term for term in ("tender", "procurement", "framework", "contract") if term in lower]
    source_urls = [item.url for item in available]
    return CompanyProfile(
        company_name=buyer if buyer != "Unknown" else title,
        domain=normalize_domain(domain),
        contract_title=title,
        buyer_name=buyer,
        portal_name=domain,
        portal_domain=domain,
        contract_url=_best_contract_url("", page.url if page else ""),
        contract_value=value,
        deadline=deadline,
        contract_status=status,
        contact_email=email,
        location=region or "Unknown",
        services=[niche],
        industry=niche,
        contract_or_procurement_signals=signals,
        outreach_angle=f"Public opportunity matching {niche}" + (f" in {region}" if region else ""),
        confidence_score=45 if available else 15,
        source_urls=source_urls,
    )


def _match(text: str, pattern: str, flags: int = 0) -> str:
    match = re.search(pattern, text, flags)
    return match.group(1).strip(" .,:;\t") if match else ""


def parse_company_profile_json(payload: str) -> CompanyProfile:
    return CompanyProfile.model_validate(json.loads(payload))


def _build_prompt(
    niche: str,
    region: str | None,
    domain: str,
    pages: list[ExtractedCompanyPage],
) -> str:
    page_blocks = []
    for page in pages:
        if page.failed:
            continue
        page_blocks.append(
            f"URL: {page.url}\nTYPE: {page.page_type}\nTITLE: {page.title}\nCONTENT:\n{page.content[:8000]}"
        )
    return f"""
You normalize public procurement/tender website research into CRM-ready contract opportunity profiles.
Use only the supplied extracted web evidence. Return strict JSON matching the schema.
Use "Unknown" or [] when evidence is insufficient. confidence_score must be 0-100.

Important extraction rules:
- contract_title is the tender/opportunity title, not the portal name.
- buyer_name is the contracting authority/customer.
- company_name should equal buyer_name when known; otherwise use contract_title.
- domain should be the buyer website domain when explicit evidence is available.
- If no buyer website is present, use the portal domain as domain.
- contract_url must be the exact source notice URL where the opportunity was found.
- deadline must be the tender submission/response deadline when visible, not an award date.
- contract_status must say whether the notice is open, active, closed, awarded, cancelled, withdrawn, expired, or unknown.
- portal_name and portal_domain identify the tender portal.
- outreach_angle should explain why this opportunity matches the searched niche.
- contact_name, contact_email, and contact_phone should be extracted from the notice when visible.
- buyer_contact can be a compact combined contact string for display, but never invent contact details.

Contract search niche: {niche}
Search region: {region or "None"}
Source portal domain: {domain}

Extracted pages:
{chr(10).join(page_blocks)}
""".strip()


def _best_domain(payload: dict[str, Any], fallback_domain: str) -> str:
    for key in ("buyer_website", "domain"):
        value = _clean_unknown(payload.get(key))
        if not value:
            continue
        domain = normalize_domain(value)
        if "." in domain:
            return domain
    return normalize_domain(fallback_domain)


def _clean_unknown(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip()
    if not normalized or normalized.lower() in {"unknown", "n/a", "none", "not available"}:
        return ""
    return normalized


def _best_contract_url(parsed_url: Any, source_url: str) -> str:
    parsed = _clean_unknown(parsed_url)
    if parsed and not _is_search_results_url(parsed):
        return parsed
    if source_url and not _is_search_results_url(source_url):
        return source_url
    return parsed or source_url


def _is_search_results_url(value: str) -> bool:
    parsed = urlparse(value if "://" in value else f"https://{value}")
    path = parsed.path.lower()
    return path.startswith("/search/") or path in {"/search", "/search/results"}
