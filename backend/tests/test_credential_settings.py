from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.integrations_v1.router import create_router
from app.integrations_v1.secrets import (
    GEMINI_API_KEY,
    GOOGLE_CLIENT_SECRET_KEY,
    STRIPE_API_KEY,
    TAVILY_API_KEY,
    CredentialStore,
)
from app.lead_enrichment.config import EnrichmentSettings
from app.local_security import (
    BOOTSTRAP_HEADER,
    CSRF_HEADER,
    LocalSecurityConfig,
    install_local_security,
)


class CredentialSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.credentials = CredentialStore.for_tests("CRMWorkspace.CredentialSettingsTests")
        self.app = FastAPI()
        self.app.include_router(
            create_router(credential_store_factory=lambda: self.credentials),
            prefix="/api/v1",
        )

    def test_secret_crud_returns_only_configuration_state(self) -> None:
        secrets = {
            "google": (GOOGLE_CLIENT_SECRET_KEY, "google-client-secret-value"),
            "stripe": (STRIPE_API_KEY, "sk_test_secret-value"),
            "tavily": (TAVILY_API_KEY, "tvly-secret-value"),
            "gemini": (GEMINI_API_KEY, "gemini-secret-value"),
        }
        with TestClient(self.app) as client:
            initial = client.get("/api/v1/integrations/credentials")
            self.assertEqual(200, initial.status_code)
            self.assertTrue(all(not item["configured"] for item in initial.json()["items"]))
            self.assertEqual("no-store", initial.headers["cache-control"])

            for provider, (key, secret) in secrets.items():
                saved = client.post(
                    f"/api/v1/integrations/credentials/{provider}",
                    json={"secret": secret},
                    headers={"Idempotency-Key": f"save-{provider}"},
                )
                self.assertEqual(200, saved.status_code)
                self.assertEqual({"provider": provider, "configured": True}, saved.json())
                self.assertNotIn(secret, saved.text)
                self.assertEqual(secret, self.credentials.get(key))

            status = client.get("/api/v1/integrations/credentials")
            self.assertTrue(all(item["configured"] for item in status.json()["items"]))
            for _, secret in secrets.values():
                self.assertNotIn(secret, status.text)

            removed = client.delete(
                "/api/v1/integrations/credentials/gemini",
                headers={"Idempotency-Key": "remove-gemini"},
            )
            self.assertEqual({"provider": "gemini", "configured": False}, removed.json())
            self.assertIsNone(self.credentials.get(GEMINI_API_KEY))

            self.assertEqual(
                404,
                client.post(
                    "/api/v1/integrations/credentials/arbitrary-provider",
                    json={"secret": "must-not-be-stored"},
                    headers={"Idempotency-Key": "invalid-provider"},
                ).status_code,
            )

    def test_credential_writes_require_a_valid_local_csrf_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = FastAPI()
            security = install_local_security(
                app,
                LocalSecurityConfig(
                    data_directory=Path(directory),
                    app_origin="http://127.0.0.1:8000",
                    bypass=False,
                ),
            )
            app.include_router(
                create_router(credential_store_factory=lambda: self.credentials),
                prefix="/api/v1",
            )
            with TestClient(app, base_url="http://127.0.0.1:8000") as client:
                authenticated = client.post(
                    "/api/v1/session/bootstrap",
                    headers={BOOTSTRAP_HEADER: security.bootstrap_secret},
                )
                csrf = authenticated.json()["csrf_token"]
                path = "/api/v1/integrations/credentials/stripe"
                payload = {"secret": "sk_test_csrf-protected"}
                self.assertEqual(
                    403,
                    client.post(
                        path,
                        json=payload,
                        headers={"Idempotency-Key": "csrf-rejected"},
                    ).status_code,
                )
                self.assertEqual(
                    200,
                    client.post(
                        path,
                        json=payload,
                        headers={
                            CSRF_HEADER: csrf,
                            "origin": "http://127.0.0.1:8000",
                            "Idempotency-Key": "csrf-accepted",
                        },
                    ).status_code,
                )

    def test_enrichment_prefers_credential_manager_values(self) -> None:
        previous = {
            "TAVILY_API_KEY": os.environ.get("TAVILY_API_KEY"),
            "GEMINI_API_KEY": os.environ.get("GEMINI_API_KEY"),
        }
        try:
            os.environ["TAVILY_API_KEY"] = "environment-tavily"
            os.environ["GEMINI_API_KEY"] = "environment-gemini"
            self.credentials.set(TAVILY_API_KEY, "credential-manager-tavily")
            self.credentials.set(GEMINI_API_KEY, "credential-manager-gemini")

            settings = EnrichmentSettings(_credential_store=self.credentials)

            self.assertEqual("credential-manager-tavily", settings.tavily_api_key)
            self.assertEqual("credential-manager-gemini", settings.gemini_api_key)
            settings.require_read_keys()

            self.credentials.delete(TAVILY_API_KEY)
            self.credentials.delete(GEMINI_API_KEY)
            without_store = EnrichmentSettings(_credential_store=self.credentials)
            self.assertEqual("", without_store.tavily_api_key)
            self.assertEqual("", without_store.gemini_api_key)
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
