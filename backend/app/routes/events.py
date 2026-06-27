from fastapi import APIRouter

from app.schemas import Event
from app.services import events_service


router = APIRouter()


@router.get("/events", response_model=list[Event])
def get_events() -> list[Event]:
    return events_service.list_events()

