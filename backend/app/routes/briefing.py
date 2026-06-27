from fastapi import APIRouter, HTTPException

from app.briefing import (
    ApproveActionRequest,
    ApproveActionResponse,
    Briefing,
    GenerateBriefingRequest,
    approve_action,
    generate_briefing,
    get_latest_briefing,
)


router = APIRouter()


@router.post("/briefing/generate", response_model=Briefing)
async def generate_briefing_route(request: GenerateBriefingRequest | None = None) -> Briefing:
    """Generate a Daybreak morning briefing.

    Designed to be triggered by n8n webhook on a cron schedule.
    Can also be called manually from the frontend.
    """
    if request is None:
        request = GenerateBriefingRequest()
    try:
        return await generate_briefing(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/briefing/latest", response_model=Briefing | None)
async def get_latest_briefing_route() -> Briefing | None:
    """Return the most recently generated briefing (in-memory)."""
    return get_latest_briefing()


@router.post("/briefing/approve", response_model=ApproveActionResponse)
async def approve_action_route(request: ApproveActionRequest) -> ApproveActionResponse:
    """Approve a briefing item action. Optionally creates a task in Attio."""
    return await approve_action(request)
