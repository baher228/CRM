from datetime import datetime, timezone
from hashlib import sha256

from app.lead_enrichment.models import ExtractedPage, LeadSource


def content_hash(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


def compute_fingerprint(
    lead: LeadSource,
    urls: list[str],
    pages: list[ExtractedPage],
    classifier_version: str,
) -> str:
    page_hashes = [content_hash(page.content[:50000]) for page in pages if not page.failed]
    parts = [
        lead.object_slug,
        lead.record_id,
        lead.name,
        lead.domain or "",
        classifier_version,
        *sorted(urls),
        *sorted(page_hashes),
    ]
    return sha256("\n".join(parts).encode("utf-8")).hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def is_unchanged(existing_fingerprint: str | None, new_fingerprint: str) -> bool:
    return bool(existing_fingerprint and existing_fingerprint == new_fingerprint)

