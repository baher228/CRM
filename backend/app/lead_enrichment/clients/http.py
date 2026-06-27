import asyncio
from typing import Any

import httpx


class ApiClientError(RuntimeError):
    pass


class AsyncApiClient:
    def __init__(
        self,
        base_url: str,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            timeout=timeout_seconds,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = await self._client.request(method, path, **kwargs)
                if response.status_code in {429, 500, 502, 503, 504}:
                    await self._sleep_before_retry(response, attempt)
                    continue
                response.raise_for_status()
                if not response.content:
                    return {}
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt == self.max_retries - 1:
                    break
                await asyncio.sleep(2**attempt)
        raise ApiClientError(f"{method} {path} failed after {self.max_retries} attempts") from last_error

    async def _sleep_before_retry(self, response: httpx.Response, attempt: int) -> None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                await asyncio.sleep(float(retry_after))
                return
            except ValueError:
                pass
        await asyncio.sleep(2**attempt)

