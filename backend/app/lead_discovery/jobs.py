import asyncio
import time
from uuid import uuid4

from app.lead_discovery.models import (
    DiscoveryCompanyResult,
    DiscoveryJobState,
    DiscoveryJobStatusResponse,
    DiscoveryRunRequest,
)
from app.services.leads_service import add_discovered_leads


_jobs: dict[str, DiscoveryJobState] = {}
_started_at: dict[str, float] = {}
_lock = asyncio.Lock()


async def create_discovery_job(request: DiscoveryRunRequest) -> str:
    job_id = str(uuid4())
    now = time.monotonic()
    async with _lock:
        _started_at[job_id] = now
        _jobs[job_id] = DiscoveryJobState(
            job_id=job_id,
            state="running",
            phase="queued",
            message="Discovery queued",
            elapsed_seconds=0,
            dry_run=request.dry_run,
            niche=request.niche,
            region=request.region,
            requested_limit=request.limit,
        )
    asyncio.create_task(_run_job(job_id, request))
    return job_id


async def get_discovery_job(job_id: str) -> DiscoveryJobStatusResponse | None:
    async with _lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        snapshot = job.model_copy(deep=True)
        snapshot.elapsed_seconds = _elapsed(job_id)
        return DiscoveryJobStatusResponse.model_validate(snapshot.model_dump())


async def _run_job(job_id: str, request: DiscoveryRunRequest) -> None:
    from app.lead_discovery.runner import run_discovery

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
            progress_callback=lambda event: update_discovery_job(job_id, event),
        )
        if not request.dry_run:
            add_discovered_leads(response.results)
        await update_discovery_job(
            job_id,
            {
                "phase": "completed",
                "message": "Discovery complete",
                "completed": len(response.results),
                "total": response.discovered,
                "response": response,
            },
        )
    except Exception as exc:  # noqa: BLE001 - background job should expose failures through polling.
        await update_discovery_job(
            job_id,
            {
                "phase": "failed",
                "message": str(exc),
                "state": "failed",
            },
        )


async def update_discovery_job(job_id: str, event: dict) -> None:
    async with _lock:
        job = _jobs.get(job_id)
        if not job:
            return

        result = event.get("result")
        phase = event.get("phase")
        if phase and not (phase == "failed" and isinstance(result, DiscoveryCompanyResult)):
            job.phase = phase
        if event.get("message"):
            job.message = event["message"]
        if "total" in event:
            job.total = int(event["total"] or 0)
            job.discovered = job.total
        if "completed" in event:
            job.completed = int(event["completed"] or 0)
        if event.get("state"):
            job.state = event["state"]

        if isinstance(result, DiscoveryCompanyResult):
            _upsert_result(job, result)

        response = event.get("response")
        if response:
            job.state = "completed"
            job.phase = "completed"
            job.message = event.get("message") or "Discovery complete"
            job.discovered = response.discovered
            job.total = response.discovered
            job.completed = len(response.results)
            job.upserted = response.upserted
            job.skipped = response.skipped
            job.failed = response.failed
            job.results = response.results
        else:
            _refresh_counts(job)

        if job.phase == "failed":
            job.state = "failed"
        elif job.phase == "completed":
            job.state = "completed"
        job.elapsed_seconds = _elapsed(job_id)


def _upsert_result(job: DiscoveryJobState, result: DiscoveryCompanyResult) -> None:
    result_key = _result_key(result)
    for index, item in enumerate(job.results):
        if _result_key(item) == result_key:
            job.results[index] = result
            return
    job.results.append(result)


def _refresh_counts(job: DiscoveryJobState) -> None:
    terminal_statuses = {"upserted", "dry_run", "skipped", "failed"}
    job.completed = sum(1 for item in job.results if item.status in terminal_statuses)
    job.upserted = sum(1 for item in job.results if item.status in {"upserted", "dry_run"})
    job.skipped = sum(1 for item in job.results if item.status == "skipped")
    job.failed = sum(1 for item in job.results if item.status == "failed")


def _elapsed(job_id: str) -> float:
    started = _started_at.get(job_id, time.monotonic())
    return round(time.monotonic() - started, 1)


def _result_key(result: DiscoveryCompanyResult) -> str:
    dedupe_key = getattr(result, "dedupe_key", "")
    if dedupe_key:
        return dedupe_key
    if result.contract_url:
        return result.contract_url
    if result.source_urls:
        return result.source_urls[0]
    return result.domain
