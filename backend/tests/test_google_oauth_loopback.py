from __future__ import annotations

import http.client
import os
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.integrations_v1.google import (
    GoogleOAuthLoopbackListener,
    GoogleOAuthStart,
    GoogleWorkspaceAdapter,
)
from app.integrations_v1.router import create_router
from app.integrations_v1.secrets import CredentialStore
from app.platform_db import reset_bootstrap_for_tests


def _get(port: int, path: str) -> tuple[int, dict[str, str], str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read().decode("utf-8")
    finally:
        connection.close()


class _LocalExchangeGoogle(GoogleWorkspaceAdapter):
    def __init__(self, credentials: CredentialStore) -> None:
        super().__init__(
            client_id="desktop-client",
            redirect_uri="http://127.0.0.1:48123/api/v1/integrations/google/oauth/callback",
            credentials=credentials,
            fake=False,
        )
        self.last_exchange: tuple[GoogleOAuthStart, str] | None = None

    def exchange_code(self, oauth: GoogleOAuthStart, code: str) -> dict[str, object]:
        self.last_exchange = (oauth, code)
        self.credentials.set_json(self.TOKEN_KEY, {"token": "local-test", "scopes": list(self.scopes)})
        return {"connected": True, "scopes": list(self.scopes)}


class GoogleOAuthLoopbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        os.environ["CRM_DB_PATH"] = str(root / "crm.sqlite3")
        os.environ["CRM_DATA_DIR"] = str(root / "data")
        reset_bootstrap_for_tests()

    def tearDown(self) -> None:
        reset_bootstrap_for_tests()
        os.environ.pop("CRM_DB_PATH", None)
        os.environ.pop("CRM_DATA_DIR", None)
        self.temp.cleanup()

    def test_production_route_uses_one_shot_random_loopback_callback(self) -> None:
        credentials = CredentialStore.for_tests()
        google = _LocalExchangeGoogle(credentials)
        app = FastAPI()
        app.include_router(create_router(google_factory=lambda: google), prefix="/api/v1")

        with TestClient(app) as client:
            started = client.post("/api/v1/integrations/google/oauth/start")
            self.assertEqual(200, started.status_code)
            redirect_uri = started.json()["redirect_uri"]
            parsed = urlparse(redirect_uri)
            self.assertEqual("127.0.0.1", parsed.hostname)
            self.assertEqual("", parsed.path)
            self.assertIsNotNone(parsed.port)
            authorization = parse_qs(urlparse(started.json()["authorization_url"]).query)
            self.assertEqual([redirect_uri], authorization["redirect_uri"])
            self.assertEqual(["S256"], authorization["code_challenge_method"])

            query = urlencode({"state": started.json()["state"], "code": "one-time-code"})
            status, headers, body = _get(parsed.port or 0, f"/?{query}")
            self.assertEqual(200, status)
            self.assertEqual("no-store", headers["Cache-Control"])
            self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
            self.assertEqual("CRMWorkspace", headers["Server"])
            self.assertIn("Google Workspace connected", body)
            self.assertNotIn("one-time-code", body)
            self.assertEqual("one-time-code", google.last_exchange[1] if google.last_exchange else "")
            self.assertEqual(
                "connected", client.get("/api/v1/integrations/google").json()["status"]
            )

            replay = client.get(
                "/api/v1/integrations/google/oauth/callback",
                params={"state": started.json()["state"], "code": "replayed"},
            )
            self.assertEqual(400, replay.status_code)

    def test_listener_ignores_other_paths_and_expires_without_a_callback(self) -> None:
        received: list[tuple[str, str, str]] = []
        expired = threading.Event()
        listener = GoogleOAuthLoopbackListener(
            lambda code, state, error: received.append((code, state, error)),
            timeout_seconds=0.2,
        )
        listener.start(on_expire=expired.set)

        status, _, body = _get(listener.port, "/not-the-callback?code=must-not-leak")
        self.assertEqual(404, status)
        self.assertNotIn("must-not-leak", body)
        self.assertTrue(listener.wait_closed(2))
        self.assertTrue(expired.wait(1))
        self.assertEqual([], received)

    def test_listener_returns_safe_failure_page_and_consumes_rejection(self) -> None:
        received: list[tuple[str, str, str]] = []

        def reject(code: str, state: str, error: str) -> None:
            received.append((code, state, error))
            raise ValueError("provider details must not be shown")

        listener = GoogleOAuthLoopbackListener(reject, timeout_seconds=2)
        listener.start()
        query = urlencode({"state": "secret-state", "error": "access_denied"})
        status, headers, body = _get(listener.port, f"/?{query}")
        self.assertEqual(400, status)
        self.assertEqual("no-referrer", headers["Referrer-Policy"])
        self.assertIn("Google Workspace connection failed", body)
        self.assertNotIn("access_denied", body)
        self.assertNotIn("secret-state", body)
        self.assertTrue(listener.wait_closed(2))
        self.assertEqual([("", "secret-state", "access_denied")], received)

    def test_adapter_accepts_ephemeral_redirect_but_keeps_fixed_fake_fallback(self) -> None:
        google = GoogleWorkspaceAdapter(
            client_id="desktop-client",
            redirect_uri="http://127.0.0.1:48123/api/v1/integrations/google/oauth/callback",
            credentials=CredentialStore.for_tests(),
            fake=True,
        )
        ephemeral = "http://127.0.0.1:49321"
        oauth = google.begin_oauth(redirect_uri=ephemeral)
        self.assertEqual(ephemeral, oauth.redirect_uri)
        self.assertTrue(google.exchange_code(oauth, "fake-code")["connected"])

        fallback = google.begin_oauth()
        self.assertEqual(google.redirect_uri, fallback.redirect_uri)


if __name__ == "__main__":
    unittest.main()
