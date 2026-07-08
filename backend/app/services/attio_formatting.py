import re

from app.lead_enrichment.config import EnrichmentSettings


def attio_person_name(name: str) -> dict[str, str]:
    parts = [part for part in re.split(r"\s+", name.strip()) if part]
    if not parts:
        return {"first_name": "", "last_name": "", "full_name": ""}
    if len(parts) == 1:
        return {"first_name": parts[0], "last_name": "", "full_name": parts[0]}
    return {
        "first_name": " ".join(parts[:-1]),
        "last_name": parts[-1],
        "full_name": " ".join(parts),
    }


def attio_phone_number(phone: str, settings: EnrichmentSettings) -> dict[str, str]:
    cleaned = re.sub(r"\s+", " ", phone.strip())
    value = {"original_phone_number": cleaned}
    if not cleaned.startswith("+"):
        value["country_code"] = settings.attio_default_phone_country_code
    return value
