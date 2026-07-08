from datetime import datetime, timezone
import re
from typing import Any
from urllib.parse import urlparse, urlunparse


def is_known(value: Any) -> bool:
    return bool(value and str(value).strip() and str(value).strip().lower() not in {"unknown", "n/a", "none", "-"})


def first_known(*values: Any) -> Any:
    return next((value for value in values if is_known(value)), None)


def best_value(current: Any, incoming: Any) -> Any:
    if not is_known(current) and is_known(incoming):
        return incoming
    return current


def best_source_value(current: str, incoming: str) -> str:
    if is_known(current) and not is_bad_source_url(current):
        return current
    if is_known(incoming) and not is_bad_source_url(incoming):
        return incoming
    return current or incoming


def best_contract_url(contract_url: str, source_urls: list[str]) -> str:
    for url in [contract_url, *(source_urls or [])]:
        if is_known(url) and not is_bad_source_url(url):
            return normalize_source_url(url)
    return ""


def clean_source_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for url in urls:
        if not is_known(url) or is_bad_source_url(url):
            continue
        normalized = normalize_source_url(url)
        key = canonical_url_key(normalized) or normalized
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(normalized)
    return cleaned


def merge_urls(left: list[str], right: list[str]) -> list[str]:
    seen = set()
    merged = []
    for url in clean_source_urls([*(left or []), *(right or [])]):
        key = canonical_url_key(url) or url
        if key in seen:
            continue
        seen.add(key)
        merged.append(url)
    return merged


def canonical_url_key(value: str) -> str:
    if not is_known(value):
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+", "/", parsed.path.rstrip("/")).lower()
    if is_bad_source_url(value):
        return ""
    ocds_match = re.search(r"(ocds-[a-z0-9-]+)", path, flags=re.IGNORECASE)
    if ocds_match:
        return f"{host}|{ocds_match.group(1).lower()}"
    if not host:
        return ""
    return urlunparse(("https", host, path, "", "", ""))


def is_bad_source_url(value: str) -> bool:
    if not is_known(value):
        return True
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    return host.endswith("contractsfinder.service.gov.uk") and path.startswith("/search/")


def normalize_source_url(value: str) -> str:
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/")
    if host.endswith("contractsfinder.service.gov.uk") and path.lower().startswith("/notice/"):
        return urlunparse(("https", host, path, "", "", ""))
    return urlunparse((parsed.scheme or "https", host, path, "", parsed.query, ""))


def fallback_key(portal: str, buyer: str, title: str) -> str:
    parts = [slug(part) for part in (portal, buyer, title) if is_known(part)]
    return "|".join(parts)


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def company_domain(lead) -> str:
    for candidate in (lead.company_domain, lead.buyer_website):
        if is_known(candidate):
            return domain_from_value(candidate)
    if is_known(lead.website):
        return domain_from_value(lead.website)
    return ""


def domain_from_value(value: str) -> str:
    cleaned = value.replace("https://", "").replace("http://", "").split("/")[0]
    if cleaned.startswith("www."):
        cleaned = cleaned[4:]
    return cleaned or "example.com"


def website_from_domain(domain: str) -> str:
    if not domain:
        return "https://example.com"
    if domain.startswith("http://") or domain.startswith("https://"):
        return domain
    return f"https://{domain}"


def split_contact(value: str) -> tuple[str, str | None, str]:
    if not is_known(value):
        return "", None, ""
    email_match = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", value)
    phone_match = re.search(r"(?:\+?\d[\d\s().-]{7,}\d)", value)
    email = email_match.group(0) if email_match else None
    phone = phone_match.group(0).strip() if phone_match else ""
    name = value
    for token in (email or "", phone):
        if token:
            name = name.replace(token, "")
    name = re.sub(r"\s+", " ", name.replace("Email:", "").replace("Phone:", "")).strip(" ,-;")
    if name.lower() in {"unknown", "n/a"}:
        name = ""
    return name, email, phone


def clean_email(value: str | None) -> str | None:
    if not is_known(value):
        return None
    match = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", str(value))
    return match.group(0) if match else None


def datetime_from_iso(value: str | None) -> datetime | None:
    if not is_known(value):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
