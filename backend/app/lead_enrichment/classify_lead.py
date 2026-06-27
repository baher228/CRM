from app.lead_enrichment.clients.gemini_client import GeminiClient
from app.lead_enrichment.models import ExtractedPage, LeadClassification, LeadSource


async def classifyLead(
    lead: LeadSource,
    pages: list[ExtractedPage],
    gemini_client: GeminiClient,
) -> LeadClassification:
    prompt = _build_prompt(lead, pages)
    return await gemini_client.classify(prompt)


def _build_prompt(lead: LeadSource, pages: list[ExtractedPage]) -> str:
    page_blocks = []
    for page in pages:
        if page.failed:
            continue
        content = page.content[:8000]
        page_blocks.append(
            f"URL: {page.url}\nTYPE: {page.page_type}\nTITLE: {page.title}\nCONTENT:\n{content}"
        )

    return f"""
You are a B2B CRM lead enrichment classifier.

Classify this lead using only the supplied public web evidence.
Return JSON matching the provided schema. Scores must be integers from 0 to 100.
Use "Unknown" when evidence is insufficient. Keep evidence concise and source-linked.

Lead:
Name: {lead.name}
Domain: {lead.domain or "Unknown"}
Email: {lead.email or "Unknown"}

Extracted pages:
{chr(10).join(page_blocks)}
""".strip()

