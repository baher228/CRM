from typing import Any

from app.lead_enrichment.clients.http import AsyncApiClient
from app.lead_enrichment.config import EnrichmentSettings


class TavilyClient:
    def __init__(self, settings: EnrichmentSettings) -> None:
        self.settings = settings
        self.http = AsyncApiClient(
            settings.tavily_base_url,
            headers={
                "Authorization": f"Bearer {settings.tavily_api_key}",
                "Content-Type": "application/json",
            },
            timeout_seconds=settings.enrichment_timeout_seconds,
            max_retries=settings.enrichment_max_retries,
        )

    async def close(self) -> None:
        await self.http.close()

    async def search(
        self,
        query: str,
        max_results: int,
        search_depth: str = "advanced",
        country: str | None = None,
        include_usage: bool = False,
    ) -> list[dict[str, Any]]:
        payload = {
            "query": query,
            "search_depth": search_depth,
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
            "include_usage": include_usage,
        }
        if country:
            payload["country"] = country
        data = await self.http.request("POST", "/search", json=payload)
        return data.get("results", [])

    async def extract(
        self,
        urls: list[str],
        extract_depth: str = "advanced",
        query: str | None = None,
        output_format: str = "markdown",
        timeout: float | None = None,
        include_usage: bool = False,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        payload = {
            "urls": urls,
            "extract_depth": extract_depth,
            "include_images": False,
            "include_favicon": False,
            "format": output_format,
            "include_usage": include_usage,
        }
        if query:
            payload["query"] = query
        if timeout:
            payload["timeout"] = timeout
        data = await self.http.request("POST", "/extract", json=payload)
        return data.get("results", []), data.get("failed_results", [])
