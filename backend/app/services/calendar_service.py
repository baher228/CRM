from app.schemas import CalendarCreateRequest, CalendarItem
from app.services import clients_service, crm_store


def list_calendar_items() -> list[CalendarItem]:
    return crm_store.list_calendar_items()


async def create_calendar_item(request: CalendarCreateRequest) -> CalendarItem:
    related_client = clients_service.get_client(request.related_client_id) if request.related_client_id else None
    item = CalendarItem(
        id=0,
        title=request.title.strip(),
        date=request.date,
        start_time=request.start_time,
        end_time=request.end_time,
        related_to=request.related_to.strip() or (related_client.company or related_client.name if related_client else ""),
        notes=request.notes.strip(),
        related_client_id=request.related_client_id,
        sync_status="Local",
        last_sync_message="Saved in CRM Workspace",
    )
    return crm_store.create_calendar_item(item)

