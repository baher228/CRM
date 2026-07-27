from fastapi import APIRouter, HTTPException

from app.lead_discovery.jobs import create_discovery_job, get_discovery_job
from app.lead_discovery.models import (
    DiscoveryJobStartResponse,
    DiscoveryJobStatusResponse,
    DiscoveryRunRequest,
    DiscoveryRunResponse,
)
from app.lead_discovery.runner import run_discovery
from app.lead_discovery.search_companies_with_tavily import portal_metadata
from app.services.leads_service import add_discovered_leads


router = APIRouter()


@router.get("/discovery/portals")
def get_discovery_portals(niche: str = "", region: str | None = None) -> list[dict[str, object]]:
    return portal_metadata(niche=niche, region=region)


@router.post("/discovery/jobs", response_model=DiscoveryJobStartResponse)
async def start_discovery_job(request: DiscoveryRunRequest) -> DiscoveryJobStartResponse:
    job_id = await create_discovery_job(request)
    return DiscoveryJobStartResponse(job_id=job_id)


@router.get("/discovery/jobs/{job_id}", response_model=DiscoveryJobStatusResponse)
async def get_discovery_job_route(job_id: str) -> DiscoveryJobStatusResponse:
    job = await get_discovery_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Discovery job not found")
    return job


@router.post("/discovery/run", response_model=DiscoveryRunResponse)
async def run_discovery_route(request: DiscoveryRunRequest) -> DiscoveryRunResponse:
    try:
        response = await run_discovery(
            niche=request.niche,
            region=request.region,
            limit=request.limit,
            dry_run=request.dry_run,
            portals=request.portals,
            deadline_window=request.deadline_window,
            minimum_value=request.minimum_value,
            open_notices_only=request.open_notices_only,
        )
        if not request.dry_run:
            add_discovered_leads(response.results)
        return response
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
