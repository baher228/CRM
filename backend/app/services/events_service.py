from app.data import EVENTS
from app.schemas import Event


def list_events() -> list[Event]:
    return EVENTS

