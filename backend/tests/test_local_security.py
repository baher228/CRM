from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.local_security import (
    BOOTSTRAP_HEADER,
    CSRF_HEADER,
    SESSION_COOKIE,
    LocalSecurity,
    LocalSecurityConfig,
    install_local_security,
)


class LocalSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _application(self, *, bypass: bool = False, secure_cookie: bool = False):
        app = FastAPI()

        @app.get("/api/health")
        def health():
            return {"ok": True}

        @app.get("/api/v1/protected")
        def protected():
            return {"ok": True}

        @app.post("/api/v1/protected")
        def protected_write():
            return {"written": True}

        config = LocalSecurityConfig(
            data_directory=self.root,
            app_origin="http://127.0.0.1:8000",
            bypass=bypass,
            cookie_secure=secure_cookie,
        )
        security = install_local_security(app, config)
        return app, security

    def test_per_install_secret_persists_and_sessions_are_signed(self) -> None:
        security = LocalSecurity(LocalSecurityConfig(data_directory=self.root))
        secret = security.bootstrap_secret
        self.assertGreaterEqual(len(secret), 43)
        self.assertEqual(secret, (self.root / "bootstrap.secret").read_text(encoding="ascii"))
        self.assertEqual(secret, LocalSecurity(security.config).bootstrap_secret)

        session = security.issue_session(now=100)
        self.assertEqual(session, security.verify_session(session.cookie, now=101))
        self.assertIsNone(security.verify_session(session.cookie + "tampered", now=101))
        self.assertIsNone(security.verify_session("v1.100.noncé.signature", now=101))
        self.assertIsNone(security.verify_session(session.cookie, now=session.expires_at))

    def test_host_origin_authentication_and_csrf_are_enforced(self) -> None:
        app, security = self._application()
        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            self.assertEqual(200, client.get("/api/health").status_code)
            self.assertEqual(401, client.get("/api/v1/protected").status_code)
            self.assertEqual(
                "host_not_allowed",
                client.get(
                    "/api/v1/protected", headers={"host": "attacker.example"}
                ).json()["code"],
            )
            self.assertEqual(
                "invalid_host",
                client.get("/api/v1/protected", headers={"host": "[broken"}).json()["code"],
            )
            self.assertEqual(
                "origin_not_allowed",
                client.get(
                    "/api/v1/protected", headers={"origin": "http://attacker.example"}
                ).json()["code"],
            )

            rejected = client.post(
                "/api/v1/session/bootstrap", headers={BOOTSTRAP_HEADER: "wrong"}
            )
            self.assertEqual(401, rejected.status_code)
            authenticated = client.post(
                "/api/v1/session/bootstrap",
                headers={BOOTSTRAP_HEADER: security.bootstrap_secret},
            )
            self.assertEqual(200, authenticated.status_code)
            self.assertIn("HttpOnly", authenticated.headers["set-cookie"])
            self.assertIn("SameSite=strict", authenticated.headers["set-cookie"])
            csrf = authenticated.json()["csrf_token"]
            self.assertTrue(client.cookies.get(SESSION_COOKIE))
            self.assertEqual(200, client.get("/api/v1/protected").status_code)
            self.assertEqual(403, client.post("/api/v1/protected").status_code)
            self.assertEqual(
                200,
                client.post(
                    "/api/v1/protected",
                    headers={CSRF_HEADER: csrf, "origin": "http://127.0.0.1:8000"},
                ).status_code,
            )
            current = client.get("/api/v1/session")
            self.assertEqual(csrf, current.json()["csrf_token"])

    def test_browser_bootstrap_does_not_put_secret_in_the_http_request(self) -> None:
        app, security = self._application()
        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            response = client.get("/api/v1/session/bootstrap")
        self.assertEqual(200, response.status_code)
        self.assertNotIn(security.bootstrap_secret, response.text)
        self.assertIn("location.hash.slice(1)", response.text)
        self.assertEqual("no-store", response.headers["cache-control"])
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])

    def test_secure_cookie_flag_and_explicit_development_bypass(self) -> None:
        secure_app, security = self._application(secure_cookie=True)
        with TestClient(secure_app, base_url="https://127.0.0.1:8000") as client:
            response = client.post(
                "/api/v1/session/bootstrap",
                headers={BOOTSTRAP_HEADER: security.bootstrap_secret, "host": "127.0.0.1:8000"},
            )
        self.assertIn("Secure", response.headers["set-cookie"])

        bypass_app, _ = self._application(bypass=True)
        with TestClient(bypass_app) as client:
            self.assertEqual(200, client.get("/api/v1/protected").status_code)
            self.assertEqual(200, client.post("/api/v1/protected").status_code)

    def test_environment_defaults_to_dev_bypass_but_production_does_not(self) -> None:
        original = {name: os.environ.get(name) for name in ("CRM_ENV", "CRM_SECURITY_BYPASS")}
        try:
            os.environ.pop("CRM_ENV", None)
            os.environ.pop("CRM_SECURITY_BYPASS", None)
            self.assertTrue(LocalSecurityConfig.from_environment().bypass)
            os.environ["CRM_ENV"] = "production"
            self.assertFalse(LocalSecurityConfig.from_environment().bypass)
        finally:
            for name, value in original.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
