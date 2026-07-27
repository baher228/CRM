from __future__ import annotations

import json
import os
import threading
from typing import Any, MutableMapping


GOOGLE_CLIENT_SECRET_KEY = "google.oauth.client_secret"
STRIPE_API_KEY = "stripe.api_key"
TAVILY_API_KEY = "tavily.api_key"
GEMINI_API_KEY = "gemini.api_key"


class CredentialStoreUnavailable(RuntimeError):
    pass


class MemoryCredentialBackend:
    """Explicit test backend; production never silently falls back to memory."""

    def __init__(self, values: MutableMapping[tuple[str, str], str] | None = None) -> None:
        self.values = values if values is not None else {}
        self._lock = threading.Lock()

    def get_password(self, service: str, username: str) -> str | None:
        with self._lock:
            return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        with self._lock:
            self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        with self._lock:
            self.values.pop((service, username), None)


class CredentialStore:
    def __init__(self, service_name: str = "CRMWorkspace", *, backend: Any | None = None) -> None:
        self.service_name = service_name
        self._backend = backend

    @classmethod
    def for_tests(cls, service_name: str = "CRMWorkspace.Tests") -> "CredentialStore":
        return cls(service_name, backend=MemoryCredentialBackend())

    def _keyring(self) -> Any:
        if self._backend is not None:
            return self._backend
        try:
            import keyring  # type: ignore[import-not-found]
        except ImportError as exc:
            raise CredentialStoreUnavailable(
                "Install keyring to use Windows Credential Manager"
            ) from exc
        return keyring

    def get(self, name: str) -> str | None:
        self._validate_name(name)
        try:
            return self._keyring().get_password(self.service_name, name)
        except CredentialStoreUnavailable:
            raise
        except Exception as exc:
            raise CredentialStoreUnavailable("Could not read Windows Credential Manager") from exc

    def set(self, name: str, value: str) -> None:
        self._validate_name(name)
        if not isinstance(value, str) or not value:
            raise ValueError("Credential value must be a non-empty string")
        try:
            self._keyring().set_password(self.service_name, name, value)
        except CredentialStoreUnavailable:
            raise
        except Exception as exc:
            raise CredentialStoreUnavailable("Could not write Windows Credential Manager") from exc

    def delete(self, name: str) -> None:
        self._validate_name(name)
        try:
            self._keyring().delete_password(self.service_name, name)
        except CredentialStoreUnavailable:
            raise
        except Exception as exc:
            not_found = exc.__class__.__name__.lower() == "passworddeleterror"
            if not not_found:
                raise CredentialStoreUnavailable("Could not update Windows Credential Manager") from exc

    def has(self, name: str) -> bool:
        return self.get(name) is not None

    def get_json(self, name: str) -> dict[str, Any] | None:
        value = self.get(name)
        if value is None:
            return None
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError(f"Credential {name!r} does not contain a JSON object")
        return parsed

    def set_json(self, name: str, value: dict[str, Any]) -> None:
        self.set(name, json.dumps(value, separators=(",", ":"), sort_keys=True))

    @staticmethod
    def _validate_name(name: str) -> None:
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("Credential name must be between 1 and 200 characters")


_FAKE_APPLICATION_BACKEND = MemoryCredentialBackend()


def application_credential_store(*, fake: bool | None = None) -> CredentialStore:
    """Return the production keyring store or the explicit shared fake used by local tests."""
    if fake is None:
        fake = os.getenv("CRM_INTEGRATIONS_FAKE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    if fake:
        return CredentialStore(
            "CRMWorkspace.FakeIntegrations", backend=_FAKE_APPLICATION_BACKEND
        )
    return CredentialStore()
