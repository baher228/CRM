from datetime import date
import json
from pathlib import Path
from typing import Any

from app.data import CLIENTS
from app.lead_enrichment.clients.attio_client import AttioClient, record_id_from_response
from app.lead_enrichment.clients.http import ApiClientError
from app.lead_enrichment.config import EnrichmentSettings
from app.schemas import Client, ClientCreateRequest


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
                    _person_values(request),
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


def _person_values(request: ClientCreateRequest) -> dict[str, Any]:
    values: dict[str, Any] = {"email_addresses": [str(request.email)]}
    if request.name.strip():
        values["name"] = request.name.strip()
    return values


def _next_client_id(manual_clients: list[Client]) -> int:
    ids = [client.id for client in [*CLIENTS, *manual_clients]]
    return max(ids, default=0) + 1


def _load_manual_clients() -> list[Client]:
    if not MANUAL_CLIENTS_PATH.exists():
        return []
    try:
        payload = json.loads(MANUAL_CLIENTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    clients = []
    for item in payload:
        try:
            clients.append(Client.model_validate(item))
        except ValueError:
            continue
    return clients


def _save_manual_clients(clients: list[Client]) -> None:
    payload = [client.model_dump(mode="json") for client in clients]
    MANUAL_CLIENTS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
