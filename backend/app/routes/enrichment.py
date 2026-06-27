from fastapi import APIRouter, HTTPException

from app.lead_enrichment.models import EnrichmentRunRequest, EnrichmentRunResponse
from app.lead_enrichment.runner import run_enrichment


router = APIRouter()


@router.post("/enrichment/run", response_model=EnrichmentRunResponse)
async def run_enrichment_route(request: EnrichmentRunRequest) -> EnrichmentRunResponse:
    try:
        return await run_enrichment(limit=request.limit, dry_run=request.dry_run)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

