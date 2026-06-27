from fastapi import APIRouter

from app.schemas import Client
from app.services import clients_service


router = APIRouter()


@router.get("/clients", response_model=list[Client])
def get_clients() -> list[Client]:
    return clients_service.list_clients()

