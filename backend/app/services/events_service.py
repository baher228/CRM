from datetime import datetime

from app.schemas import Event
from app.services import crm_store


def list_events() -> list[Event]:
    events = []
    for item in crm_store.list_calendar_items():
        events.append(
            Event(
                id=item.id,
                title=item.title,
                type="Calendar",
                client=item.related_to,
                starts_at=datetime.combine(item.date, item.start_time),
                location="Calendar",
                owner="",
            )
        )
    return events
