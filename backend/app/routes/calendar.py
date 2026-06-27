from fastapi import APIRouter

from app.schemas import CalendarCreateRequest, CalendarItem
from app.services import calendar_service


router = APIRouter()


@router.get("/calendar", response_model=list[CalendarItem])
def get_calendar() -> list[CalendarItem]:
    return calendar_service.list_calendar_items()


@router.post("/calendar", response_model=CalendarItem)
async def create_calendar_item(request: CalendarCreateRequest) -> CalendarItem:
    return await calendar_service.create_calendar_item(request)
