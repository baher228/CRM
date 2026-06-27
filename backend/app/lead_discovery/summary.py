from app.lead_discovery.models import CompanyDiscoverySummary, CompanyProfile
from app.lead_enrichment.idempotency import content_hash


def build_company_summary(
    profile: CompanyProfile,
    niche: str,
    region: str | None,
    fingerprint: str,
) -> CompanyDiscoverySummary:
    summary_text = _summary_text(profile, niche, region)
    return CompanyDiscoverySummary(
        profile=profile,
        niche=niche,
        region=region,
        fingerprint=fingerprint,
        summary_text=summary_text,
        source_urls=profile.source_urls,
    )


def compute_discovery_fingerprint(
    niche: str,
    region: str | None,
    domain: str,
    urls: list[str],
    contents: list[str],
    parser_version: str,
) -> str:
    parts = [
        niche.lower().strip(),
        (region or "").lower().strip(),
        domain.lower().strip(),
        parser_version,
        *sorted(urls),
        *sorted(content_hash(content[:50000]) for content in contents if content),
    ]
    return content_hash("\n".join(parts))


def _summary_text(profile: CompanyProfile, niche: str, region: str | None) -> str:
    services = ", ".join(profile.services) or "Unknown"
    cpv_codes = ", ".join(profile.cpv_codes) or "Unknown"
    procurement = (
        "; ".join(profile.contract_or_procurement_signals)
        or "No clear contract/procurement signals found"
    )
    sources = "\n".join(f"- {url}" for url in profile.source_urls[:8])
    return f"""Contract discovery profile: {profile.contract_title}

Search niche: {niche}
Search region: {region or "None"}
Buyer: {profile.buyer_name}
Buyer domain: {profile.domain}
Buyer website: {profile.buyer_website or "Unknown"}
Portal: {profile.portal_name} ({profile.portal_domain})
Contract URL: {profile.contract_url or "Unknown"}
Value: {profile.contract_value}
Deadline: {profile.deadline}
Stage: {profile.procurement_stage}
Status: {profile.contract_status}
Location: {profile.location}
Industry: {profile.industry}
Relevant services: {services}
CPV codes: {cpv_codes}
Parsed signals: {procurement}
Fit angle: {profile.outreach_angle}
Confidence: {profile.confidence_score}/100

Sources:
{sources or "- No extracted sources"}
""".strip()
