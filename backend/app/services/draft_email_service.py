from typing import Any

from pydantic import BaseModel

from app.lead_enrichment.clients.gemini_client import GeminiClient
from app.lead_enrichment.config import EnrichmentSettings
from app.schemas import Lead


DRAFT_EMAIL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["subject", "body"],
}


class DraftEmail(BaseModel):
    subject: str
    body: str


async def draft_email_for_lead(lead: Lead, settings: EnrichmentSettings) -> DraftEmail:
    _require_gemini_key(settings)
    gemini_client = GeminiClient(settings)
    payload = await gemini_client.generate_json(_draft_prompt(lead), DRAFT_EMAIL_SCHEMA)
    draft = DraftEmail.model_validate(payload)
    return DraftEmail(
        subject=_clean_subject(draft.subject),
        body=_clean_body(draft.body),
    )


def _draft_prompt(lead: Lead) -> str:
    contact_name = lead.contact_name or "there"
    contact_email = lead.contact_email or "Unknown"
    return f"""
You are drafting a concise first outreach email for a confirmed CRM tender lead.

Write a professional, warm email from our team to the buyer contact. Do not claim we have submitted anything.
Do not invent company capabilities, pricing, certifications, or meetings.
Use only the lead facts below. Keep the body under 170 words.

Return JSON with:
- subject: short email subject line, no "Subject:" prefix
- body: plain text email body with greeting, short reason for reaching out, one clear next step, and sign-off placeholder "Best,"

Lead facts:
Contact name: {contact_name}
Contact email: {contact_email}
Buyer: {lead.buyer_name or lead.company}
Tender title: {lead.contract_title or lead.name}
Portal: {lead.portal_name or lead.source}
Contract URL: {lead.contract_url or lead.website}
Value: {lead.contract_value or lead.estimated_value}
Deadline: {lead.deadline}
Stage: {lead.procurement_stage}
Availability: {lead.availability_status} - {lead.availability_reason}
Outreach angle: {lead.outreach_angle}
""".strip()


def _clean_subject(value: str) -> str:
    subject = " ".join(str(value or "").split())
    if subject.lower().startswith("subject:"):
        subject = subject.split(":", 1)[1].strip()
    return subject[:160] or "Following up on your tender"


def _clean_body(value: str) -> str:
    body = str(value or "").strip()
    return body or "Hi,\n\nI wanted to follow up on this opportunity and see who would be best to speak with.\n\nBest,"


def _require_gemini_key(settings: EnrichmentSettings) -> None:
    normalized = settings.gemini_api_key.strip().lower()
    if not normalized or normalized.startswith("your_") or normalized in {"changeme", "todo"}:
        raise ValueError("Missing required environment variables: GEMINI_API_KEY")
