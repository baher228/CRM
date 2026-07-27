from __future__ import annotations

import base64
import hashlib
import io
import json
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, MutableMapping, Sequence
from urllib.parse import parse_qs, urlencode, urlparse, urlsplit

from app.platform_db import utc_now

from .secrets import CredentialStore


DEFAULT_GOOGLE_SCOPES = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive.file",
)
GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_OAUTH_TIMEOUT_SECONDS = 10 * 60


class GoogleApiUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class GoogleOAuthStart:
    authorization_url: str
    state: str
    verifier: str
    redirect_uri: str


def _validate_loopback_redirect_uri(redirect_uri: str) -> str:
    if not redirect_uri:
        raise ValueError("Google client_id and loopback redirect_uri are required")
    parsed = urlparse(redirect_uri)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Google redirect URI has an invalid port") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or port is None
        or port == 0
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Google redirect URI must use an explicit loopback IP and port")
    return redirect_uri


class GoogleOAuthLoopbackListener:
    """One-shot Desktop OAuth receiver using only the Python standard library."""

    callback_path = "/"

    def __init__(
        self,
        complete: Callable[[str, str, str], None],
        *,
        timeout_seconds: float = GOOGLE_OAUTH_TIMEOUT_SECONDS,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("OAuth listener timeout must be positive")
        self._complete = complete
        self._timeout_seconds = timeout_seconds
        self._done = threading.Event()
        self._closed = threading.Event()
        self._on_expire: Callable[[], None] | None = None
        listener = self

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                listener._handle(self)

            def version_string(self) -> str:
                return "CRMWorkspace"

            def log_message(self, format: str, *args: Any) -> None:
                # OAuth codes and state values must never reach application logs.
                return

        self._server = HTTPServer(("127.0.0.1", 0), CallbackHandler)
        self._server.timeout = min(0.25, timeout_seconds)
        self.port = int(self._server.server_address[1])
        self.redirect_uri = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(
            target=self._serve,
            name=f"google-oauth-loopback-{self.port}",
            daemon=True,
        )

    def start(self, *, on_expire: Callable[[], None] | None = None) -> None:
        self._on_expire = on_expire
        self._thread.start()

    def stop(self) -> None:
        self._done.set()

    def wait_closed(self, timeout: float | None = None) -> bool:
        return self._closed.wait(timeout)

    def _serve(self) -> None:
        deadline = time.monotonic() + self._timeout_seconds
        expired = False
        try:
            while not self._done.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    expired = True
                    break
                self._server.timeout = min(0.25, remaining)
                self._server.handle_request()
        finally:
            self._server.server_close()
            self._closed.set()
        if expired and self._on_expire:
            self._on_expire()

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlsplit(handler.path)
        if parsed.path != self.callback_path:
            self._respond(handler, 404, success=False)
            return
        if handler.headers.get("Host", "") != f"127.0.0.1:{self.port}":
            self._respond(handler, 400, success=False)
            return

        # A request to the actual callback path consumes the listener. State is
        # still validated by the router before any token exchange occurs.
        self._done.set()
        values = parse_qs(parsed.query, keep_blank_values=True)
        code_values = values.get("code", [])
        state_values = values.get("state", [])
        error_values = values.get("error", [])
        code = code_values[0] if len(code_values) == 1 else ""
        state = state_values[0] if len(state_values) == 1 else ""
        error = error_values[0] if len(error_values) == 1 else ""
        try:
            self._complete(code, state, error)
        except Exception:
            self._respond(handler, 400, success=False)
            return
        self._respond(handler, 200, success=True)

    @staticmethod
    def _respond(handler: BaseHTTPRequestHandler, status: int, *, success: bool) -> None:
        if success:
            title = "Google Workspace connected"
            message = "Authorization is complete. Return to CRM Workspace and close this page."
        else:
            title = "Google Workspace connection failed"
            message = "Return to CRM Workspace and start the connection again."
        body = (
            "<!doctype html><html lang=en><meta charset=utf-8>"
            f"<title>{title}</title><main><h1>{title}</h1><p>{message}</p></main></html>"
        ).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        handler.send_header("Referrer-Policy", "no-referrer")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("X-Frame-Options", "DENY")
        handler.end_headers()
        handler.wfile.write(body)


class GoogleWorkspaceAdapter:
    """Small Google Workspace boundary with a deterministic, network-free fake."""

    TOKEN_KEY = "google.oauth.credentials"
    CLIENT_SECRET_KEY = "google.oauth.client_secret"

    def __init__(
        self,
        *,
        client_id: str = "",
        redirect_uri: str = "",
        scopes: Sequence[str] = DEFAULT_GOOGLE_SCOPES,
        credentials: CredentialStore | None = None,
        fake: bool = False,
        fake_state: MutableMapping[str, Any] | None = None,
    ) -> None:
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.scopes = tuple(scopes)
        self.credentials = credentials or CredentialStore()
        self.fake = fake
        self._fake = fake_state if fake_state is not None else {}
        self._fake.setdefault("gmail_messages", {})
        self._fake.setdefault("calendar_events", {})
        self._fake.setdefault("drive_files", {})
        self._fake.setdefault("history_id", 1)

    def save_client_secret(self, client_secret: str) -> None:
        self.credentials.set(self.CLIENT_SECRET_KEY, client_secret)

    def connected(self) -> bool:
        return self.credentials.has(self.TOKEN_KEY)

    def begin_oauth(self, *, redirect_uri: str | None = None) -> GoogleOAuthStart:
        if not self.client_id:
            raise ValueError("Google client_id and loopback redirect_uri are required")
        effective_redirect_uri = _validate_loopback_redirect_uri(redirect_uri or self.redirect_uri)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(32)
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": effective_redirect_uri,
                "response_type": "code",
                "scope": " ".join(self.scopes),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",
            }
        )
        return GoogleOAuthStart(f"{GOOGLE_AUTH_URI}?{query}", state, verifier, effective_redirect_uri)

    def exchange_code(self, oauth: GoogleOAuthStart, code: str) -> dict[str, Any]:
        if not code.strip():
            raise ValueError("OAuth code is required")
        redirect_uri = _validate_loopback_redirect_uri(oauth.redirect_uri)
        if self.fake:
            token = {
                "token": f"fake-access-{uuid.uuid4()}",
                "refresh_token": f"fake-refresh-{uuid.uuid4()}",
                "token_uri": GOOGLE_TOKEN_URI,
                "client_id": self.client_id,
                "scopes": list(self.scopes),
            }
        else:
            try:
                from google_auth_oauthlib.flow import Flow  # type: ignore[import-not-found]
            except ImportError as exc:
                raise GoogleApiUnavailable(
                    "Install google-auth-oauthlib to connect Google Workspace"
                ) from exc
            client_secret = self.credentials.get(self.CLIENT_SECRET_KEY)
            if not client_secret:
                raise ValueError("Google OAuth client secret is not configured")
            flow = Flow.from_client_config(
                {
                    "installed": {
                        "client_id": self.client_id,
                        "client_secret": client_secret,
                        "auth_uri": GOOGLE_AUTH_URI,
                        "token_uri": GOOGLE_TOKEN_URI,
                        "redirect_uris": [redirect_uri],
                    }
                },
                scopes=list(self.scopes),
                redirect_uri=redirect_uri,
                code_verifier=oauth.verifier,
                autogenerate_code_verifier=False,
            )
            flow.fetch_token(code=code)
            token = json.loads(flow.credentials.to_json())
        self.credentials.set_json(self.TOKEN_KEY, token)
        return {"connected": True, "scopes": list(token.get("scopes") or self.scopes)}

    def disconnect(self) -> None:
        self.credentials.delete(self.TOKEN_KEY)

    def list_gmail_threads(
        self,
        *,
        history_id: str | None = None,
        max_results: int = 500,
        days: int = 90,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        max_results = max(1, min(max_results, 500))
        if self.fake:
            messages = list(self._fake["gmail_messages"].values())
            if history_id:
                try:
                    after = int(history_id)
                except (TypeError, ValueError):
                    after = 0
                changes = [
                    {
                        "id": str(message.get("historyId") or 1),
                        "messagesAdded": [{"message": {
                            "id": str(message.get("id") or ""),
                            "threadId": str(message.get("threadId") or message.get("id") or ""),
                        }}],
                    }
                    for message in messages
                    if int(message.get("historyId") or 1) > after
                ][:max_results]
                return {
                    "history": changes,
                    "historyId": str(self._fake["history_id"]),
                    "nextPageToken": None,
                }
            threads: list[dict[str, str]] = []
            seen: set[str] = set()
            for message in messages:
                thread_id = str(message.get("threadId") or message.get("id") or "")
                if thread_id and thread_id not in seen:
                    threads.append({"id": thread_id})
                    seen.add(thread_id)
                if len(threads) >= max_results:
                    break
            return {
                "threads": threads,
                "historyId": str(self._fake["history_id"]),
                "nextPageToken": None,
            }
        service = self._service("gmail", "v1")
        if history_id:
            request = service.users().history().list(
                userId="me",
                startHistoryId=history_id,
                historyTypes=["messageAdded"],
                maxResults=max_results,
                pageToken=page_token,
            )
        else:
            request = service.users().threads().list(
                userId="me",
                q=f"newer_than:{max(1, days)}d",
                maxResults=max_results,
                pageToken=page_token,
            )
        return request.execute()

    def get_gmail_thread(self, thread_id: str) -> dict[str, Any]:
        """Fetch a full thread after list/history APIs return lightweight IDs."""
        if not thread_id.strip():
            raise ValueError("Gmail thread ID is required")
        if self.fake:
            messages = [
                dict(message)
                for message in self._fake["gmail_messages"].values()
                if str(message.get("threadId") or message.get("id") or "") == thread_id
            ]
            if not messages:
                raise KeyError(thread_id)
            return {
                "id": thread_id,
                "historyId": str(max(int(item.get("historyId") or 1) for item in messages)),
                "messages": messages,
                "snippet": str(messages[-1].get("snippet") or messages[-1].get("body_text") or ""),
            }
        return (
            self._service("gmail", "v1")
            .users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )

    def send_gmail(
        self,
        *,
        to: str,
        subject: str,
        body_text: str,
        rfc_message_id: str,
        thread_id: str | None = None,
        reply_to_message_id: str | None = None,
    ) -> dict[str, Any]:
        if not to.strip() or not subject.strip() or not rfc_message_id.strip():
            raise ValueError("to, subject and stable rfc_message_id are required")
        if any("\r" in value or "\n" in value for value in (to, subject, rfc_message_id)):
            raise ValueError("Email headers cannot contain line breaks")
        stable_id = rfc_message_id.strip()
        if not stable_id.startswith("<"):
            stable_id = f"<{stable_id}>"
        if not stable_id.endswith(">"):
            stable_id += ">"
        existing = self.find_gmail_message(stable_id)
        if existing:
            return {**existing, "reconciled": True}
        if self.fake:
            remote_id = f"msg_{uuid.uuid4().hex}"
            self._fake["history_id"] += 1
            sent_at = utc_now()
            message = {
                "id": remote_id,
                "threadId": thread_id or f"thread_{uuid.uuid4().hex}",
                "rfc_message_id": stable_id,
                "to": to,
                "subject": subject,
                "body_text": body_text,
                "sent_at": sent_at.isoformat(),
                "internalDate": str(int(sent_at.timestamp() * 1000)),
                "labelIds": ["SENT"],
                "historyId": str(self._fake["history_id"]),
            }
            self._fake["gmail_messages"][stable_id] = message
            return message
        service = self._service("gmail", "v1")
        message = EmailMessage()
        message["To"], message["Subject"], message["Message-ID"] = to, subject, stable_id
        if reply_to_message_id:
            message["In-Reply-To"] = reply_to_message_id
            message["References"] = reply_to_message_id
        message.set_content(body_text)
        body: dict[str, Any] = {
            "raw": base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")
        }
        if thread_id:
            body["threadId"] = thread_id
        sent = service.users().messages().send(userId="me", body=body).execute()
        return {**sent, "rfc_message_id": stable_id}

    def find_gmail_message(self, rfc_message_id: str) -> dict[str, Any] | None:
        stable_id = rfc_message_id.strip()
        if not stable_id.startswith("<"):
            stable_id = f"<{stable_id}>"
        if not stable_id.endswith(">"):
            stable_id += ">"
        if self.fake:
            return self._fake["gmail_messages"].get(stable_id)
        found = (
            self._service("gmail", "v1")
            .users()
            .messages()
            .list(userId="me", q=f"rfc822msgid:{stable_id}", maxResults=1)
            .execute()
        )
        if not found.get("messages"):
            return None
        return {**found["messages"][0], "rfc_message_id": stable_id}

    def list_calendar_events(
        self,
        *,
        sync_token: str | None = None,
        time_min: str | None = None,
        max_results: int = 2500,
    ) -> dict[str, Any]:
        if self.fake:
            events = list(self._fake["calendar_events"].values())
            if sync_token:
                try:
                    after = int(sync_token)
                except (TypeError, ValueError):
                    after = 0
                events = [event for event in events if int(event.get("_change_id") or 1) > after]
            return {
                "items": events,
                "nextSyncToken": str(self._fake["history_id"]),
            }
        kwargs: dict[str, Any] = {
            "calendarId": "primary",
            "maxResults": max(1, min(max_results, 2500)),
            "singleEvents": True,
        }
        if sync_token:
            kwargs["syncToken"] = sync_token
        elif time_min:
            kwargs["timeMin"] = time_min
        return self._service("calendar", "v3").events().list(**kwargs).execute()

    def upsert_calendar_event(
        self,
        event: dict[str, Any],
        *,
        external_id: str | None = None,
        send_updates: bool = False,
    ) -> dict[str, Any]:
        if not event.get("summary") or not event.get("start") or not event.get("end"):
            raise ValueError("Calendar event requires summary, start and end")
        if self.fake:
            event_id = external_id or f"event_{uuid.uuid4().hex}"
            self._fake["history_id"] += 1
            saved = {
                **event,
                "id": event_id,
                "etag": f'"fake-{self._fake["history_id"]}"',
                "updated": utc_now().isoformat(),
                "_change_id": self._fake["history_id"],
            }
            self._fake["calendar_events"][event_id] = saved
            return saved
        events = self._service("calendar", "v3").events()
        kwargs = {"calendarId": "primary", "body": event, "sendUpdates": "all" if send_updates else "none"}
        if external_id:
            try:
                return events.update(eventId=external_id, **kwargs).execute()
            except Exception as exc:
                if getattr(getattr(exc, "resp", None), "status", None) != 404:
                    raise
                kwargs["body"] = {**event, "id": external_id}
                return events.insert(**kwargs).execute()
        return events.insert(**kwargs).execute()

    def get_calendar_event(self, event_id: str) -> dict[str, Any] | None:
        if not event_id.strip():
            raise ValueError("Calendar event ID is required")
        if self.fake:
            event = self._fake["calendar_events"].get(event_id)
            return dict(event) if event else None
        try:
            return (
                self._service("calendar", "v3")
                .events()
                .get(calendarId="primary", eventId=event_id)
                .execute()
            )
        except Exception as exc:
            if getattr(getattr(exc, "resp", None), "status", None) == 404:
                return None
            raise

    def upload_drive_file(
        self,
        *,
        name: str,
        content: bytes,
        mime_type: str,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        if not name.strip() or not mime_type.strip():
            raise ValueError("Drive file name and MIME type are required")
        if self.fake:
            file_id = f"file_{uuid.uuid4().hex}"
            saved = {
                "id": file_id,
                "name": name,
                "mimeType": mime_type,
                "parents": [parent_id] if parent_id else [],
                "sha256Checksum": hashlib.sha256(content).hexdigest(),
                "content": content,
            }
            self._fake["drive_files"][file_id] = saved
            return {key: value for key, value in saved.items() if key != "content"}
        try:
            from googleapiclient.http import MediaIoBaseUpload  # type: ignore[import-not-found]
        except ImportError as exc:
            raise GoogleApiUnavailable("Install google-api-python-client to use Drive") from exc
        metadata: dict[str, Any] = {"name": name}
        if parent_id:
            metadata["parents"] = [parent_id]
        media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=False)
        return (
            self._service("drive", "v3")
            .files()
            .create(body=metadata, media_body=media, fields="id,name,mimeType,parents,webViewLink")
            .execute()
        )

    def create_drive_document(self, title: str, *, parent_id: str | None = None) -> dict[str, Any]:
        if not title.strip():
            raise ValueError("Document title is required")
        if self.fake:
            return self.upload_drive_file(
                name=title,
                content=b"",
                mime_type="application/vnd.google-apps.document",
                parent_id=parent_id,
            )
        body: dict[str, Any] = {
            "name": title,
            "mimeType": "application/vnd.google-apps.document",
        }
        if parent_id:
            body["parents"] = [parent_id]
        return (
            self._service("drive", "v3")
            .files()
            .create(body=body, fields="id,name,mimeType,parents,webViewLink")
            .execute()
        )

    def copy_drive_document(
        self,
        template_file_id: str,
        title: str,
        *,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        if not template_file_id.strip() or not title.strip():
            raise ValueError("Template file ID and document title are required")
        if self.fake:
            source = self._fake["drive_files"].get(template_file_id)
            if source is None:
                raise KeyError(template_file_id)
            copied = self.upload_drive_file(
                name=title,
                content=bytes(source.get("content", b"")),
                mime_type="application/vnd.google-apps.document",
                parent_id=parent_id,
            )
            self._fake["drive_files"][copied["id"]]["copiedFrom"] = template_file_id
            return copied
        body: dict[str, Any] = {"name": title}
        if parent_id:
            body["parents"] = [parent_id]
        return (
            self._service("drive", "v3")
            .files()
            .copy(
                fileId=template_file_id,
                body=body,
                fields="id,name,mimeType,parents,webViewLink",
            )
            .execute()
        )

    def merge_google_document(self, file_id: str, values: dict[str, Any]) -> dict[str, Any]:
        if not file_id.strip():
            raise ValueError("Google document file ID is required")
        replacements = {
            str(key): "" if value is None else str(value)
            for key, value in values.items()
        }
        if self.fake:
            file = self._fake["drive_files"].get(file_id)
            if file is None:
                raise KeyError(file_id)
            file["mergeData"] = replacements
            return {"documentId": file_id, "replacements": len(replacements)}
        requests = [
            {
                "replaceAllText": {
                    "containsText": {"text": "{{" + key + "}}", "matchCase": True},
                    "replaceText": value,
                }
            }
            for key, value in replacements.items()
        ]
        if not requests:
            return {"documentId": file_id, "replacements": 0}
        response = (
            self._service("docs", "v1")
            .documents()
            .batchUpdate(documentId=file_id, body={"requests": requests})
            .execute()
        )
        return {"documentId": file_id, "replacements": len(response.get("replies") or requests)}

    def export_drive_file(self, file_id: str, *, mime_type: str = "application/pdf") -> bytes:
        if self.fake:
            file = self._fake["drive_files"].get(file_id)
            if file is None:
                raise KeyError(file_id)
            return bytes(file.get("content", b""))
        try:
            from googleapiclient.http import MediaIoBaseDownload  # type: ignore[import-not-found]
        except ImportError as exc:
            raise GoogleApiUnavailable("Install google-api-python-client to use Drive") from exc
        target = io.BytesIO()
        request = (
            self._service("drive", "v3")
            .files()
            .export_media(fileId=file_id, mimeType=mime_type)
        )
        downloader = MediaIoBaseDownload(target, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return target.getvalue()

    def _service(self, api: str, version: str) -> Any:
        if self.fake:
            raise RuntimeError("Fake adapter does not create Google API clients")
        info = self.credentials.get_json(self.TOKEN_KEY)
        if not info:
            raise ValueError("Google Workspace is not connected")
        try:
            from google.auth.transport.requests import Request  # type: ignore[import-not-found]
            from google.oauth2.credentials import Credentials  # type: ignore[import-not-found]
            from googleapiclient.discovery import build  # type: ignore[import-not-found]
        except ImportError as exc:
            raise GoogleApiUnavailable(
                "Install google-auth, google-auth-oauthlib and google-api-python-client"
            ) from exc
        credentials = Credentials.from_authorized_user_info(info, scopes=list(self.scopes))
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            self.credentials.set_json(self.TOKEN_KEY, json.loads(credentials.to_json()))
        return build(api, version, credentials=credentials, cache_discovery=False)


def bounded_initial_sync_time() -> str:
    """UTC lower bound for the default 90-day initial Calendar sync."""
    return (utc_now() - timedelta(days=90)).isoformat()
