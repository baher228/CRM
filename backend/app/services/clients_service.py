from datetime import date

from app.schemas import Client, ClientCreateRequest, ClientUpdateRequest
from app.services import crm_store


def list_clients() -> list[Client]:
    return crm_store.list_clients()


def get_client(client_id: int) -> Client | None:
    return crm_store.get_client(client_id)


async def create_client(request: ClientCreateRequest) -> Client:
    client = Client(
        id=0,
        name=request.name.strip(),
        company=request.company.strip(),
        email=request.email,
        website=request.website.strip(),
        phone=request.phone.strip(),
        owner=request.owner.strip(),
        value=request.value,
        last_contact=request.last_contact or date.today(),
        status=request.status.strip() or "Active",
        source=request.source.strip(),
        next_action=request.next_action.strip(),
        notes=request.notes.strip(),
        sync_status="Local",
        last_sync_message="Saved in CRM Workspace",
    )
    return crm_store.create_client(client)


def update_client(client_id: int, request: ClientUpdateRequest) -> Client | None:
    client = get_client(client_id)
    if not client:
        return None
    update = {
        key: value.strip() if isinstance(value, str) else value
        for key, value in request.model_dump(exclude_unset=True).items()
    }
    return crm_store.save_client(client.model_copy(update=update))


def delete_client(client_id: int) -> bool:
    return crm_store.delete_client(client_id)

