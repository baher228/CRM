from app.data import CALENDAR
from app.schemas import CalendarItem


def list_calendar_items() -> list[CalendarItem]:
    return CALENDAR

