from datetime import date
from typing import Any

from app.lead_enrichment.clients.attio_client import AttioClient, record_id_from_response
from app.lead_enrichment.clients.http import ApiClientError
from app.lead_enrichment.config import EnrichmentSettings
from app.schemas import Client, ClientCreateRequest, ClientUpdateRequest
from app.services.attio_formatting import attio_person_name, attio_phone_number
from app.services import crm_store


def list_clients() -> list[Client]:
    return crm_store.list_clients()


def get_client(client_id: int) -> Client | None:
    return crm_store.get_client(client_id)


async def create_client(request: ClientCreateRequest) -> Client:
    sync_message = "Saved locally"
    attio_person_record_id = None

    if request.email:
        try:
            settings = EnrichmentSettings()
            settings.require_attio_key()
            attio_client = AttioClient(settings)
            try:
                response = await attio_client.upsert_record(
                    settings.attio_person_object,
                    settings.attio_person_email_attribute,
                    _person_values(request, settings),
                )
                attio_person_record_id = record_id_from_response(response)
                sync_message = "Saved locally; Attio person upserted"
            finally:
                await attio_client.close()
        except (ApiClientError, ValueError) as exc:
            sync_message = f"Saved locally; Attio sync failed: {exc}"
    elif not request.email:
        sync_message = "Saved locally; add an email to sync to Attio"

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
        sync_status="Synced" if attio_person_record_id else "Local",
        attio_person_record_id=attio_person_record_id,
        last_sync_message=sync_message,
    )
    return crm_store.create_client(client)


def update_client(client_id: int, request: ClientUpdateRequest) -> Client | None:
    client = get_client(client_id)
    if not client:
        return None
    update = {}
    for key, value in request.model_dump(exclude_unset=True).items():
        if isinstance(value, str):
            value = value.strip()
        update[key] = value
    return crm_store.save_client(client.model_copy(update=update))


def delete_client(client_id: int) -> bool:
    return crm_store.delete_client(client_id)


def _person_values(request: ClientCreateRequest, settings: EnrichmentSettings) -> dict[str, Any]:
    values: dict[str, Any] = {settings.attio_person_email_attribute: [str(request.email)]}
    if request.name.strip():
        values[settings.attio_person_name_attribute] = [attio_person_name(request.name)]
    if request.phone.strip():
        values[settings.attio_person_phone_attribute] = [attio_phone_number(request.phone, settings)]
    return values

