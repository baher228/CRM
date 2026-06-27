from typing import Any

from app.lead_enrichment.clients.http import AsyncApiClient
from app.lead_enrichment.config import EnrichmentSettings


class AttioClient:
    def __init__(self, settings: EnrichmentSettings) -> None:
        self.settings = settings
        self.http = AsyncApiClient(
            settings.attio_base_url,
            headers={
                "Authorization": f"Bearer {settings.attio_api_token}",
                "Content-Type": "application/json",
            },
            timeout_seconds=settings.enrichment_timeout_seconds,
            max_retries=settings.enrichment_max_retries,
        )

    async def close(self) -> None:
        await self.http.close()

    async def query_list_entries(self, list_id: str, limit: int) -> list[dict[str, Any]]:
        payload = {"limit": limit}
        data = await self.http.request("POST", f"/v2/lists/{list_id}/entries/query", json=payload)
        return _records_from_response(data)

    async def query_records(self, object_slug: str, limit: int) -> list[dict[str, Any]]:
        payload = {"limit": limit}
        data = await self.http.request("POST", f"/v2/objects/{object_slug}/records/query", json=payload)
        return _records_from_response(data)

    async def update_record(self, object_slug: str, record_id: str, values: dict[str, Any]) -> dict[str, Any]:
        payload = {"data": {"values": values}}
        return await self.http.request(
            "PATCH",
            f"/v2/objects/{object_slug}/records/{record_id}",
            json=payload,
        )

    async def create_note(
        self,
        object_slug: str,
        record_id: str,
        title: str,
        content: str,
    ) -> dict[str, Any]:
        payload = {
            "data": {
                "parent_object": object_slug,
                "parent_record_id": record_id,
                "title": title,
                "format": "plaintext",
                "content": content,
            }
        }
        return await self.http.request("POST", "/v2/notes", json=payload)

    async def create_task(
        self,
        object_slug: str,
        record_id: str,
        title: str,
        content: str,
    ) -> dict[str, Any]:
        payload = {
            "data": {
                "linked_records": [{"object": object_slug, "record_id": record_id}],
                "title": title,
                "content": content,
            }
        }
        return await self.http.request("POST", "/v2/tasks", json=payload)


def _records_from_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    records = data.get("data", [])
    if isinstance(records, dict):
        return records.get("records", []) or records.get("entries", []) or []
    return records if isinstance(records, list) else []

