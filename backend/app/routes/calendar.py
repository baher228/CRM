from fastapi import APIRouter

from app.schemas import CalendarItem
from app.services import calendar_service


router = APIRouter()


@router.get("/calendar", response_model=list[CalendarItem])
def get_calendar() -> list[CalendarItem]:
    return calendar_service.list_calendar_items()

