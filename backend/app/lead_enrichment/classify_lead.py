from app.lead_enrichment.clients.gemini_client import GeminiClient
from app.lead_enrichment.models import ClassificationEvidence, ExtractedPage, LeadClassification, LeadSource


async def classifyLead(
    lead: LeadSource,
    pages: list[ExtractedPage],
    gemini_client: GeminiClient | None,
) -> LeadClassification:
    prompt = _build_prompt(lead, pages)
    if gemini_client is not None:
        try:
            return await gemini_client.classify(prompt)
        except Exception:
            pass
    return _fallback_classification(pages)


def _fallback_classification(pages: list[ExtractedPage]) -> LeadClassification:
    available = [page for page in pages if not page.failed and page.content.strip()]
    text = " ".join(page.content.lower() for page in available)
    signals = [term for term in ("tender", "procurement", "framework", "contract") if term in text]
    triggers = [term for term in ("deadline", "supplier", "implementation", "transformation") if term in text]
    evidence = []
    if available:
        evidence.append(ClassificationEvidence(
            label="Public source",
            detail=(available[0].title or "Relevant public evidence")[:160],
            source_url=available[0].url,
        ))
    return LeadClassification(
        procurement_signals=signals,
        outreach_triggers=triggers,
        fit_score=50 if available else 0,
        urgency_score=60 if "deadline" in text else 30 if available else 0,
        confidence_score=45 if available else 0,
        evidence=evidence,
    )


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
