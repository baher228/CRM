from datetime import date
from pathlib import Path
from typing import Any

from app.data import CLIENTS
from app.lead_enrichment.clients.attio_client import AttioClient, record_id_from_response
from app.lead_enrichment.clients.http import ApiClientError
from app.lead_enrichment.config import EnrichmentSettings
from app.schemas import Client, ClientCreateRequest
from app.services.attio_formatting import attio_person_name, attio_phone_number
from app.services.local_store import load_model_list, save_model_list


MANUAL_CLIENTS_PATH = Path(__file__).resolve().parents[2] / "manual_clients.json"


def list_clients() -> list[Client]:
    return sorted([*CLIENTS, *_load_manual_clients()], key=lambda client: client.name.lower())


def get_client(client_id: int) -> Client | None:
    return next((client for client in list_clients() if client.id == client_id), None)


async def create_client(request: ClientCreateRequest) -> Client:
    manual_clients = _load_manual_clients()
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
        id=_next_client_id(manual_clients),
        name=request.name.strip(),
        company=request.company.strip(),
        email=request.email,
        website=request.website.strip(),
        phone=request.phone.strip(),
        owner=request.owner.strip(),
        value=request.value,
        last_contact=request.last_contact or date.today(),
        attio_person_record_id=attio_person_record_id,
        last_sync_message=sync_message,
    )
    manual_clients.append(client)
    _save_manual_clients(manual_clients)
    return client


def _person_values(request: ClientCreateRequest, settings: EnrichmentSettings) -> dict[str, Any]:
    values: dict[str, Any] = {settings.attio_person_email_attribute: [str(request.email)]}
    if request.name.strip():
        values[settings.attio_person_name_attribute] = [attio_person_name(request.name)]
    if request.phone.strip():
        values[settings.attio_person_phone_attribute] = [attio_phone_number(request.phone, settings)]
    return values


def _next_client_id(manual_clients: list[Client]) -> int:
    ids = [client.id for client in [*CLIENTS, *manual_clients]]
    return max(ids, default=0) + 1


def _load_manual_clients() -> list[Client]:
    return load_model_list(MANUAL_CLIENTS_PATH, Client)


def _save_manual_clients(clients: list[Client]) -> None:
    save_model_list(MANUAL_CLIENTS_PATH, clients)
