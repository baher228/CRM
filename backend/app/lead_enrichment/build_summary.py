from app.lead_enrichment.idempotency import compute_fingerprint, utc_now
from app.lead_enrichment.models import EnrichmentSummary, ExtractedPage, FoundPage, LeadClassification, LeadSource


def buildSummary(
    lead: LeadSource,
    found_pages: list[FoundPage],
    extracted_pages: list[ExtractedPage],
    classification: LeadClassification,
    classifier_version: str,
) -> EnrichmentSummary:
    selected_urls = [page.url for page in found_pages]
    source_urls = [page.url for page in extracted_pages if not page.failed]
    fingerprint = compute_fingerprint(lead, selected_urls, extracted_pages, classifier_version)
    summary_text = _format_summary(lead, classification, source_urls)
    return EnrichmentSummary(
        lead=lead,
        selected_urls=selected_urls,
        classification=classification,
        summary_text=summary_text,
        source_urls=source_urls,
        fingerprint=fingerprint,
        generated_at=utc_now(),
    )


def _format_summary(
    lead: LeadSource,
    classification: LeadClassification,
    source_urls: list[str],
) -> str:
    triggers = "; ".join(classification.outreach_triggers) or "No clear trigger found"
    procurement = "; ".join(classification.procurement_signals) or "No clear procurement signal found"
    risks = "; ".join(classification.risks) or "No major risks identified"
    evidence = "\n".join(
        f"- {item.label}: {item.detail} ({item.source_url})"
        for item in classification.evidence[:5]
    )
    sources = "\n".join(f"- {url}" for url in source_urls[:8])
    return f"""Lead enrichment: {lead.name}

Industry: {classification.industry}
Segment: {classification.segment}
Pricing model: {classification.pricing_model}
Compliance posture: {classification.compliance_posture}

Scores:
- Fit: {classification.fit_score}/100
- Urgency: {classification.urgency_score}/100
- Confidence: {classification.confidence_score}/100

Outreach triggers: {triggers}
Procurement signals: {procurement}
Risks: {risks}

Evidence:
{evidence or "- No source-linked evidence returned"}

Sources:
{sources or "- No extracted sources"}
""".strip()

