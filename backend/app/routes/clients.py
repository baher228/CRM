from fastapi import APIRouter, HTTPException

from app.schemas import Client, ClientCreateRequest, ClientUpdateRequest
from app.services import clients_service


router = APIRouter()


@router.get("/clients", response_model=list[Client])
def get_clients() -> list[Client]:
    return clients_service.list_clients()


@router.post("/clients", response_model=Client)
async def create_client(request: ClientCreateRequest) -> Client:
    return await clients_service.create_client(request)


@router.patch("/clients/{client_id}", response_model=Client)
def update_client(client_id: int, request: ClientUpdateRequest) -> Client:
    client = clients_service.update_client(client_id, request)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


@router.delete("/clients/{client_id}")
def delete_client(client_id: int) -> dict[str, bool]:
    deleted = clients_service.delete_client(client_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Client not found")
    return {"deleted": True}
