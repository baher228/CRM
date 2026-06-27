from app.lead_discovery.models import DiscoveryCompanyResult
from app.lead_enrichment.logging import log_event


def logRunResult(result: DiscoveryCompanyResult) -> None:
    log_event(
        "lead_discovery_company_result",
        domain=result.domain,
        status=result.status,
        confidence_score=result.confidence_score,
        message=result.message,
    )

