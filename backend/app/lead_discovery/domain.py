from urllib.parse import urlparse


BLOCKED_DOMAINS = {
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "x.com",
    "twitter.com",
    "youtube.com",
    "yelp.com",
    "google.com",
    "bing.com",
    "mapquest.com",
    "yellowpages.com",
    "angi.com",
    "thumbtack.com",
    "homeadvisor.com",
}


def normalize_domain(value: str) -> str:
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = (parsed.netloc or parsed.path).lower()
    host = host.split("@")[-1].split(":")[0].strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def is_blocked_domain(domain: str) -> bool:
    normalized = normalize_domain(domain)
    return normalized in BLOCKED_DOMAINS or any(normalized.endswith(f".{blocked}") for blocked in BLOCKED_DOMAINS)

