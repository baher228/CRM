from fastapi import APIRouter

from app.schemas import Client, ClientCreateRequest
from app.services import clients_service


router = APIRouter()


@router.get("/clients", response_model=list[Client])
def get_clients() -> list[Client]:
    return clients_service.list_clients()


@router.post("/clients", response_model=Client)
async def create_client(request: ClientCreateRequest) -> Client:
    return await clients_service.create_client(request)
