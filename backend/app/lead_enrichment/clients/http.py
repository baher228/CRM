import asyncio
from typing import Any

import httpx


class ApiClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        method: str | None = None,
        path: str | None = None,
        status_code: int | None = None,
        response_body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.method = method
        self.path = path
        self.status_code = status_code
        self.response_body = response_body


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
                    last_error = self._response_error(method, path, response)
                    if attempt == self.max_retries - 1:
                        break
                    await self._sleep_before_retry(response, attempt)
                    continue
                if 400 <= response.status_code < 500:
                    raise self._response_error(method, path, response)
                response.raise_for_status()
                if not response.content:
                    return {}
                return response.json()
            except ApiClientError:
                raise
            except httpx.HTTPStatusError as exc:
                last_error = self._response_error(method, path, exc.response)
                break
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt == self.max_retries - 1:
                    break
                await asyncio.sleep(2**attempt)
        if isinstance(last_error, ApiClientError):
            raise last_error
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

    def _response_error(self, method: str, path: str, response: httpx.Response) -> ApiClientError:
        body = _safe_response_body(response)
        message = f"{method} {path} failed with status {response.status_code}"
        if body:
            message = f"{message}: {body}"
        return ApiClientError(
            message,
            method=method,
            path=path,
            status_code=response.status_code,
            response_body=body,
        )


def _safe_response_body(response: httpx.Response) -> str:
    if not response.content:
        return ""
    content_type = response.headers.get("content-type", "")
    try:
        if "json" in content_type:
            body = response.text
        else:
            body = response.text
    except UnicodeDecodeError:
        return "<binary response>"
    body = " ".join(body.split())
    return body[:800]
