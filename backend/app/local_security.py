from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.platform_db import data_root


BOOTSTRAP_HEADER = "x-crm-bootstrap-secret"
CSRF_HEADER = "x-csrf-token"
SESSION_COOKIE = "crm_session"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
PUBLIC_API_PATHS = frozenset({"/api/health", "/api/v1/session/bootstrap"})


def _environment_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false")


def _canonical_origin(value: str) -> tuple[str, str, int]:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("CRM_APP_ORIGIN must be an HTTP origin without a path")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("CRM_APP_ORIGIN contains an invalid port") from exc
    hostname = parsed.hostname.lower()
    if hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("CRM_APP_ORIGIN must use a loopback host")
    return parsed.scheme, hostname, port


def _canonical_host(value: str) -> tuple[str, int | None] | None:
    if not value or any(character.isspace() for character in value):
        return None
    try:
        parsed = urlsplit(f"//{value}")
    except ValueError:
        return None
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return None
    try:
        return parsed.hostname.lower(), parsed.port
    except ValueError:
        return None


@dataclass(frozen=True)
class LocalSecurityConfig:
    data_directory: Path
    app_origin: str = "http://127.0.0.1:8000"
    bypass: bool = False
    session_ttl_seconds: int = 12 * 60 * 60
    cookie_secure: bool = False

    def __post_init__(self) -> None:
        _canonical_origin(self.app_origin)
        if self.session_ttl_seconds < 300:
            raise ValueError("Session lifetime must be at least five minutes")

    @classmethod
    def from_environment(cls) -> "LocalSecurityConfig":
        mode = os.getenv("CRM_ENV", "development").strip().lower()
        bypass = _environment_bool(
            "CRM_SECURITY_BYPASS", mode in {"dev", "development", "test"}
        )
        port = int(os.getenv("CRM_PORT", "8000"))
        if not 1 <= port <= 65535:
            raise RuntimeError("CRM_PORT must be between 1 and 65535")
        origin = os.getenv("CRM_APP_ORIGIN", f"http://127.0.0.1:{port}").rstrip("/")
        scheme, _, _ = _canonical_origin(origin)
        return cls(
            data_directory=data_root(),
            app_origin=origin,
            bypass=bypass,
            session_ttl_seconds=int(os.getenv("CRM_SESSION_TTL_SECONDS", str(12 * 60 * 60))),
            cookie_secure=_environment_bool("CRM_COOKIE_SECURE", scheme == "https"),
        )


@dataclass(frozen=True)
class LocalSession:
    cookie: str
    csrf_token: str
    expires_at: int


class LocalSecurity:
    def __init__(self, config: LocalSecurityConfig | None = None) -> None:
        self.config = config or LocalSecurityConfig.from_environment()
        self._secret: str | None = None

    @property
    def bootstrap_secret_path(self) -> Path:
        return self.config.data_directory / "bootstrap.secret"

    @property
    def bootstrap_secret(self) -> str:
        if self._secret is None:
            self._secret = self._load_or_create_secret()
        return self._secret

    def _load_or_create_secret(self) -> str:
        path = self.bootstrap_secret_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise RuntimeError("Bootstrap secret must not be a symbolic link")
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            value = secrets.token_urlsafe(48)
            try:
                os.write(descriptor, value.encode("ascii"))
            finally:
                os.close(descriptor)
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        try:
            value = path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError) as exc:
            raise RuntimeError("Cannot read the per-install bootstrap secret") from exc
        if len(value) < 43 or any(character not in _TOKEN_CHARACTERS for character in value):
            raise RuntimeError("The per-install bootstrap secret is invalid")
        return value

    def secret_matches(self, presented: str) -> bool:
        return bool(presented) and hmac.compare_digest(presented, self.bootstrap_secret)

    def issue_session(self, *, now: int | None = None) -> LocalSession:
        issued_at = int(time.time() if now is None else now)
        expires_at = issued_at + self.config.session_ttl_seconds
        body = f"v1.{expires_at}.{secrets.token_urlsafe(24)}"
        signature = self._sign(f"session.{body}")
        cookie = f"{body}.{signature}"
        return LocalSession(cookie, self._sign(f"csrf.{body}"), expires_at)

    def verify_session(self, cookie: str, *, now: int | None = None) -> LocalSession | None:
        if len(cookie) > 256 or any(
            character not in _TOKEN_CHARACTERS and character != "." for character in cookie
        ):
            return None
        parts = cookie.split(".")
        if len(parts) != 4 or parts[0] != "v1":
            return None
        body = ".".join(parts[:3])
        if not hmac.compare_digest(parts[3], self._sign(f"session.{body}")):
            return None
        try:
            expires_at = int(parts[1])
        except ValueError:
            return None
        if expires_at <= int(time.time() if now is None else now):
            return None
        return LocalSession(cookie, self._sign(f"csrf.{body}"), expires_at)

    def _sign(self, value: str) -> str:
        digest = hmac.new(
            self.bootstrap_secret.encode("ascii"), value.encode("ascii"), hashlib.sha256
        ).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


_TOKEN_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)


class LocalSecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, security: LocalSecurity) -> None:
        super().__init__(app)
        self.security = security
        scheme, hostname, port = _canonical_origin(security.config.app_origin)
        self.expected_origin = (scheme, hostname, port)
        self.expected_host = (hostname, port)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if self.security.config.bypass:
            return await call_next(request)

        request_host = _canonical_host(request.headers.get("host", ""))
        if request_host is None:
            return _error(400, "invalid_host", "A valid Host header is required", request)
        host, port = request_host
        effective_port = port or (443 if self.expected_origin[0] == "https" else 80)
        if (host, effective_port) != self.expected_host:
            return _error(400, "host_not_allowed", "The Host header is not allowed", request)

        origin = request.headers.get("origin")
        if origin:
            try:
                request_origin = _canonical_origin(origin)
            except ValueError:
                request_origin = None
            if request_origin != self.expected_origin:
                return _error(403, "origin_not_allowed", "The Origin header is not allowed", request)

        if request.url.path.startswith("/api") and not self._is_public(request):
            cookie = request.cookies.get(SESSION_COOKIE, "")
            session = self.security.verify_session(cookie) if cookie else None
            if session is None:
                return _error(401, "authentication_required", "A local session is required", request)
            request.state.local_session = session
            if request.method.upper() not in SAFE_METHODS:
                csrf = request.headers.get(CSRF_HEADER, "")
                if not csrf or not hmac.compare_digest(csrf, session.csrf_token):
                    return _error(403, "csrf_failed", "A valid CSRF token is required", request)

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    @staticmethod
    def _is_public(request: Request) -> bool:
        return request.method.upper() == "OPTIONS" or request.url.path in PUBLIC_API_PATHS


def _error(status: int, code: str, message: str, request: Request) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "code": code,
            "message": message,
            "field_errors": {},
            "request_id": request.headers.get("x-request-id", ""),
        },
        headers={"Cache-Control": "no-store"},
    )


_BOOTSTRAP_SCRIPT = """const token=location.hash.slice(1);history.replaceState(null,'',location.pathname);const status=document.getElementById('status');if(!token){status.textContent='Open CRM Workspace from the Windows launcher.';}else{fetch(location.pathname,{method:'POST',credentials:'same-origin',headers:{'x-crm-bootstrap-secret':token}}).then(response=>{if(!response.ok)throw new Error();location.replace('/');}).catch(()=>{status.textContent='CRM Workspace could not start a secure local session. Run the launcher again.';});}"""
_BOOTSTRAP_SCRIPT_HASH = base64.b64encode(
    hashlib.sha256(_BOOTSTRAP_SCRIPT.encode("utf-8")).digest()
).decode("ascii")
_BOOTSTRAP_HTML = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Opening CRM Workspace</title></head><body><p id="status">Opening CRM Workspace&hellip;</p>
<script>{_BOOTSTRAP_SCRIPT}</script></body></html>"""


def create_local_security_router(security: LocalSecurity) -> APIRouter:
    router = APIRouter(prefix="/api/v1/session", tags=["local-session"])

    @router.get("/bootstrap", include_in_schema=False)
    def bootstrap_page() -> HTMLResponse:
        return HTMLResponse(
            _BOOTSTRAP_HTML,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    "default-src 'none'; connect-src 'self'; "
                    f"script-src 'sha256-{_BOOTSTRAP_SCRIPT_HASH}'; "
                    "base-uri 'none'; frame-ancestors 'none'"
                ),
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.post("/bootstrap", include_in_schema=False)
    def bootstrap(request: Request) -> JSONResponse:
        if not security.config.bypass and not security.secret_matches(
            request.headers.get(BOOTSTRAP_HEADER, "")
        ):
            return _error(401, "invalid_bootstrap_secret", "Bootstrap secret is invalid", request)
        session = security.issue_session()
        response = JSONResponse(
            {"authenticated": True, "csrf_token": session.csrf_token, "expires_at": session.expires_at},
            headers={"Cache-Control": "no-store"},
        )
        response.set_cookie(
            SESSION_COOKIE,
            session.cookie,
            max_age=security.config.session_ttl_seconds,
            path="/",
            secure=security.config.cookie_secure,
            httponly=True,
            samesite="strict",
        )
        return response

    @router.get("")
    def current_session(request: Request) -> dict[str, object]:
        session = getattr(request.state, "local_session", None)
        if session is None:  # Development bypass only.
            return {"authenticated": True, "csrf_token": "development-bypass", "expires_at": None}
        return {
            "authenticated": True,
            "csrf_token": session.csrf_token,
            "expires_at": session.expires_at,
        }

    @router.post("/logout")
    def logout() -> JSONResponse:
        response = JSONResponse({"authenticated": False}, headers={"Cache-Control": "no-store"})
        response.delete_cookie(
            SESSION_COOKIE,
            path="/",
            secure=security.config.cookie_secure,
            httponly=True,
            samesite="strict",
        )
        return response

    return router


def install_local_security(
    app: FastAPI, config: LocalSecurityConfig | None = None
) -> LocalSecurity:
    existing = getattr(app.state, "local_security", None)
    if existing is not None:
        return existing
    security = LocalSecurity(config)
    if not security.config.bypass:
        security.bootstrap_secret  # Fail closed and make the launcher secret available at startup.
    app.state.local_security = security
    app.include_router(create_local_security_router(security))
    app.add_middleware(LocalSecurityMiddleware, security=security)
    return security
