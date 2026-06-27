import json
from pathlib import Path

from app.data import CALENDAR
from app.lead_enrichment.clients.attio_client import AttioClient
from app.lead_enrichment.clients.http import ApiClientError
from app.lead_enrichment.config import EnrichmentSettings
from app.schemas import CalendarCreateRequest, CalendarItem
from app.services import clients_service


MANUAL_CALENDAR_PATH = Path(__file__).resolve().parents[2] / "manual_calendar.json"


def list_calendar_items() -> list[CalendarItem]:
    return sorted(
        [*CALENDAR, *_load_manual_calendar_items()],
        key=lambda item: (item.date, item.start_time, item.title.lower()),
    )


async def create_calendar_item(request: CalendarCreateRequest) -> CalendarItem:
    manual_items = _load_manual_calendar_items()
    sync_message = "Saved locally"
    attio_task_created = False

    related_client = clients_service.get_client(request.related_client_id) if request.related_client_id else None
    if related_client and related_client.attio_person_record_id:
        try:
            settings = EnrichmentSettings()
            settings.require_attio_key()
            attio_client = AttioClient(settings)
            try:
                await attio_client.create_task(
                    settings.attio_person_object,
                    related_client.attio_person_record_id,
                    request.title.strip(),
                    _task_content(request),
                )
                attio_task_created = True
                sync_message = "Saved locally; Attio task created"
            finally:
                await attio_client.close()
        except (ApiClientError, ValueError) as exc:
            sync_message = f"Saved locally; Attio task failed: {exc}"
    elif request.related_client_id:
        sync_message = "Saved locally; selected contact has no Attio person id"
    elif request.related_to.strip():
        sync_message = "Saved locally; select an Attio-synced contact to create an Attio task"

    item = CalendarItem(
        id=_next_calendar_id(manual_items),
        title=request.title.strip(),
        date=request.date,
        start_time=request.start_time,
        end_time=request.end_time,
        related_to=_related_to(request, related_client),
        notes=request.notes.strip(),
        related_client_id=request.related_client_id,
        attio_task_created=attio_task_created,
        last_sync_message=sync_message,
    )
    manual_items.append(item)
    _save_manual_calendar_items(manual_items)
    return item


def _related_to(request: CalendarCreateRequest, related_client) -> str:
    if request.related_to.strip():
        return request.related_to.strip()
    if related_client:
        return related_client.company or related_client.name
    return ""


def _task_content(request: CalendarCreateRequest) -> str:
    return f"""Calendar event

Date: {request.date.isoformat()}
Time: {request.start_time.strftime("%H:%M")} - {request.end_time.strftime("%H:%M")}
Related to: {request.related_to or "Contact"}

Notes:
{request.notes or ""}
""".strip()


def _next_calendar_id(manual_items: list[CalendarItem]) -> int:
    ids = [item.id for item in [*CALENDAR, *manual_items]]
    return max(ids, default=0) + 1


def _load_manual_calendar_items() -> list[CalendarItem]:
    if not MANUAL_CALENDAR_PATH.exists():
        return []
    try:
        payload = json.loads(MANUAL_CALENDAR_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    items = []
    for item in payload:
        try:
            items.append(CalendarItem.model_validate(item))
        except ValueError:
            continue
    return items


def _save_manual_calendar_items(items: list[CalendarItem]) -> None:
    payload = [item.model_dump(mode="json") for item in items]
    MANUAL_CALENDAR_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
