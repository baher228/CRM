from datetime import date
import json
from pathlib import Path

from app.data import LEADS
from app.lead_discovery.models import DiscoveryCompanyResult
from app.schemas import Lead, LeadStatus


DISCOVERED_LEADS_PATH = Path(__file__).resolve().parents[2] / "discovered_leads.json"


def list_leads() -> list[Lead]:
    return [*_load_discovered_leads(), *LEADS]


def add_discovered_leads(results: list[DiscoveryCompanyResult]) -> None:
    discovered_leads = _load_discovered_leads()
    existing_keys = {_lead_key(lead) for lead in discovered_leads}
    next_id = _next_lead_id(discovered_leads)
    changed = False

    for result in results:
        if result.status not in {"upserted", "dry_run"}:
            continue
        key = _result_key(result)
        if key in existing_keys:
            continue
        discovered_leads.append(_result_to_lead(result, next_id))
        existing_keys.add(key)
        next_id += 1
        changed = True

    if changed:
        _save_discovered_leads(discovered_leads)


def _result_to_lead(result: DiscoveryCompanyResult, lead_id: int) -> Lead:
    return Lead(
        id=lead_id,
        name=result.contract_title if result.contract_title != "Unknown" else result.company_name,
        company=result.buyer_name if result.buyer_name != "Unknown" else result.company_name,
        email="unknown@example.com",
        website=result.contract_url or _website_from_domain(result.portal_domain or result.domain),
        status=LeadStatus.NEW,
        source=result.portal_name if result.portal_name != "Unknown" else "Discovery",
        confidence_score=result.confidence_score or 0,
        outreach_angle=_outreach_angle(result),
        estimated_value=_estimated_value(result.contract_value),
        created_at=date.today(),
    )


def _outreach_angle(result: DiscoveryCompanyResult) -> str:
    parts = []
    if result.contract_value and result.contract_value != "Unknown":
        parts.append(f"Value: {result.contract_value}")
    if result.deadline and result.deadline != "Unknown":
        parts.append(f"Deadline: {result.deadline}")
    if result.procurement_stage and result.procurement_stage != "Unknown":
        parts.append(f"Stage: {result.procurement_stage}")
    if result.message:
        parts.append(result.message)
    return " | ".join(parts) or "Discovered from public procurement portal."


def _estimated_value(value: str) -> int:
    digits = "".join(char for char in value if char.isdigit())
    if not digits:
        return 0
    return min(int(digits), 2_000_000_000)


def _next_lead_id(discovered_leads: list[Lead]) -> int:
    ids = [lead.id for lead in [*LEADS, *discovered_leads]]
    return max(ids, default=0) + 1


def _lead_key(lead: Lead) -> str:
    return f"{lead.company}|{lead.website}".lower()


def _result_key(result: DiscoveryCompanyResult) -> str:
    return f"{result.buyer_name}|{result.contract_url or result.domain}".lower()


def _website_from_domain(domain: str) -> str:
    if not domain:
        return "https://example.com"
    if domain.startswith("http://") or domain.startswith("https://"):
        return domain
    return f"https://{domain}"


def _load_discovered_leads() -> list[Lead]:
    if not DISCOVERED_LEADS_PATH.exists():
        return []
    try:
        payload = json.loads(DISCOVERED_LEADS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    leads = []
    for item in payload:
        try:
            leads.append(Lead.model_validate(item))
        except ValueError:
            continue
    return leads


def _save_discovered_leads(leads: list[Lead]) -> None:
    payload = [lead.model_dump(mode="json") for lead in leads]
    DISCOVERED_LEADS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
