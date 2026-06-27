from app.data import CLIENTS
from app.schemas import Client


def list_clients() -> list[Client]:
    return CLIENTS

