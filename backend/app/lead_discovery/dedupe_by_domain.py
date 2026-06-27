from app.lead_discovery.domain import normalize_domain
from app.lead_discovery.models import CompanyCandidate


def dedupeByDomain(candidates: list[CompanyCandidate]) -> list[CompanyCandidate]:
    deduped: dict[str, CompanyCandidate] = {}
    for candidate in candidates:
        domain = normalize_domain(candidate.domain)
        existing = deduped.get(domain)
        if not existing:
            candidate.domain = domain
            deduped[domain] = candidate
            continue
        existing.urls.extend(candidate.urls)
    return list(deduped.values())

