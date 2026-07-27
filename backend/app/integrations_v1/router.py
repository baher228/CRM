from __future__ import annotations

import os
import threading
from dataclasses import asdict
from datetime import timedelta
from typing import Any, Callable

from fastapi import APIRouter, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field, SecretStr

from app.platform_db import db_path, utc_now
from app.confirmation import Confirmation

from .automation import AutomationEngine, AutomationStore, OptimisticRuleConflict
from .google import (
    GOOGLE_OAUTH_TIMEOUT_SECONDS,
    GoogleOAuthLoopbackListener,
    GoogleOAuthStart,
    GoogleWorkspaceAdapter,
)
from .jobs import IdempotencyConflict, JobStore
from .secrets import (
    GEMINI_API_KEY,
    GOOGLE_CLIENT_SECRET_KEY,
    STRIPE_API_KEY,
    TAVILY_API_KEY,
    CredentialStore,
    CredentialStoreUnavailable,
    application_credential_store,
)
from .state import IntegrationStateStore, NotificationConflict, NotificationStore
from .stripe import StripeAdapter


class EmailSendRequest(BaseModel):
    to: str = Field(min_length=3, max_length=320)
    subject: str = Field(min_length=1, max_length=998)
    body_text: str = Field(max_length=1_000_000)
    rfc_message_id: str = Field(min_length=3, max_length=998)
    thread_id: str | None = None
    reply_to_message_id: str | None = None


class PaymentLinkRequest(BaseModel):
    amount_minor: int = Field(gt=0)
    currency: str = Field(default="gbp", min_length=3, max_length=3)
    description: str = Field(default="", max_length=500)
    customer_email: str | None = Field(default=None, max_length=320)


class ReconcileRequest(BaseModel):
    succeeded: bool
    result: Any = None


class NotificationReadRequest(BaseModel):
    version: int = Field(ge=1)
    read: bool = True


class RuleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    trigger_name: str
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[dict[str, Any]]
    enabled: bool = False
    dry_run: bool = True


class RuleUpdateRequest(RuleRequest):
    version: int = Field(ge=1)


class RulePreviewRequest(BaseModel):
    records: list[dict[str, Any]] = Field(max_length=100)


class BackupRequest(BaseModel):
    destination_directory: str = Field(min_length=1, max_length=1000)


class RestoreRequest(BaseModel):
    backup_path: str = Field(min_length=1, max_length=1000)
    confirmed: bool = False


class IntegrationCredentialRequest(BaseModel):
    secret: SecretStr = Field(min_length=1, max_length=10_000)


_CREDENTIAL_KEYS = {
    "google": (GOOGLE_CLIENT_SECRET_KEY, "Google OAuth client secret"),
    "stripe": (STRIPE_API_KEY, "Stripe API key"),
    "tavily": (TAVILY_API_KEY, "Tavily API key"),
    "gemini": (GEMINI_API_KEY, "Gemini API key"),
}
_FAKE_GOOGLE_STATE: dict[str, Any] = {}
_FAKE_STRIPE_STATE: dict[str, Any] = {}


def _fake_mode() -> bool:
    return os.getenv("CRM_INTEGRATIONS_FAKE", "").strip().lower() in {"1", "true", "yes", "on"}


def _default_google() -> GoogleWorkspaceAdapter:
    fake = _fake_mode()
    return GoogleWorkspaceAdapter(
        client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
        redirect_uri=os.getenv("GOOGLE_OAUTH_REDIRECT_URI", ""),
        credentials=application_credential_store(fake=fake),
        fake=fake,
        fake_state=_FAKE_GOOGLE_STATE if fake else None,
    )


def _default_stripe() -> StripeAdapter:
    fake = _fake_mode()
    return StripeAdapter(
        credentials=application_credential_store(fake=fake),
        fake=fake,
        fake_state=_FAKE_STRIPE_STATE if fake else None,
    )


def _default_credentials() -> CredentialStore:
    return application_credential_store(fake=_fake_mode())


def create_router(
    *,
    job_store_factory: Callable[[], JobStore] = JobStore,
    state_store_factory: Callable[[], IntegrationStateStore] = IntegrationStateStore,
    notification_store_factory: Callable[[], NotificationStore] = NotificationStore,
    automation_store_factory: Callable[[], AutomationStore] = AutomationStore,
    google_factory: Callable[[], GoogleWorkspaceAdapter] = _default_google,
    stripe_factory: Callable[[], StripeAdapter] = _default_stripe,
    credential_store_factory: Callable[[], CredentialStore] = _default_credentials,
) -> APIRouter:
    api = APIRouter()
    pending_oauth: dict[
        str,
        tuple[GoogleOAuthStart, Any, GoogleOAuthLoopbackListener | None],
    ] = {}
    oauth_lock = threading.Lock()

    def enqueue_job(*args: Any, **kwargs: Any) -> Any:
        try:
            return job_store_factory().enqueue(*args, **kwargs)
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    def credential_definition(provider: str) -> tuple[str, str, str]:
        normalized = provider.strip().lower()
        definition = _CREDENTIAL_KEYS.get(normalized)
        if definition is None:
            raise HTTPException(status_code=404, detail="Integration credential not found")
        return normalized, *definition

    def complete_google_oauth(code: str, state: str, provider_error: str = "") -> dict[str, Any]:
        with oauth_lock:
            pending = pending_oauth.pop(state, None)
        if pending is None or pending[1] < utc_now() - timedelta(seconds=GOOGLE_OAUTH_TIMEOUT_SECONDS):
            if pending and pending[2]:
                pending[2].stop()
            raise ValueError("OAuth state is missing or expired")
        if pending[2]:
            pending[2].stop()
        if provider_error or not code.strip():
            state_store_factory().set_connection(
                "google",
                status="error",
                last_error="Google authorization was not completed; reconnect and retry",
            )
            raise ValueError("Google authorization was not completed")
        try:
            result = google_factory().exchange_code(pending[0], code)
        except Exception as exc:
            state_store_factory().set_connection(
                "google", status="error", last_error="Google connection failed; reconnect and retry"
            )
            raise ValueError("Google connection failed") from exc
        state_store_factory().set_connection(
            "google", status="connected", scopes=list(result.get("scopes") or [])
        )
        return result

    def expire_google_oauth(state: str, listener: GoogleOAuthLoopbackListener) -> None:
        with oauth_lock:
            pending = pending_oauth.get(state)
            if pending is None or pending[2] is not listener:
                return
            pending_oauth.pop(state, None)
        state_store_factory().set_connection(
            "google",
            status="error",
            last_error="Google authorization timed out; reconnect and retry",
        )

    @api.get("/jobs")
    def list_jobs(state: str | None = None, limit: int = Query(default=100, ge=1, le=100)) -> dict[str, Any]:
        return {"items": [asdict(item) for item in job_store_factory().list(state=state, limit=limit)], "next_cursor": None}

    @api.get("/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = job_store_factory().get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return asdict(job)

    @api.post("/jobs/{job_id}/reconcile")
    def reconcile_job(
        job_id: str,
        request: ReconcileRequest,
        _idempotency_key: str = Header(
            alias="Idempotency-Key", min_length=1, max_length=255
        ),
        _confirmation: None = Confirmation,
    ) -> dict[str, Any]:
        try:
            return asdict(
                job_store_factory().resolve_unknown(
                    job_id, succeeded=request.succeeded, result=request.result
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.post("/jobs/{job_id}/retry")
    def retry_job(
        job_id: str,
        _idempotency_key: str = Header(
            alias="Idempotency-Key", min_length=1, max_length=255
        ),
    ) -> dict[str, Any]:
        try:
            return asdict(job_store_factory().retry_failed(job_id))
        except ValueError as exc:
            status_code = 404 if str(exc) == "Job not found" else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    @api.post("/integrations/google/email/send", status_code=202)
    def queue_email(
        request: EmailSendRequest,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
        _confirmation: None = Confirmation,
    ) -> dict[str, str]:
        job = enqueue_job(
            "google.gmail.send",
            request.model_dump(),
            idempotency_key=idempotency_key,
            requires_reconciliation=True,
        )
        return {"job_id": job.id, "state": job.state}

    @api.post("/invoices/{invoice_id}/payment-link", status_code=202)
    def queue_payment_link(
        invoice_id: str,
        request: PaymentLinkRequest,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
        _confirmation: None = Confirmation,
    ) -> dict[str, str]:
        job = enqueue_job(
            "stripe.checkout.create",
            {"invoice_id": invoice_id, **request.model_dump()},
            idempotency_key=idempotency_key,
            requires_reconciliation=True,
        )
        return {"job_id": job.id, "state": job.state}

    @api.get("/integrations/credentials")
    def list_integration_credentials(response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        store = credential_store_factory()
        try:
            items = [
                {
                    "provider": provider,
                    "label": label,
                    "configured": store.has(key),
                }
                for provider, (key, label) in _CREDENTIAL_KEYS.items()
            ]
            error = ""
        except CredentialStoreUnavailable as exc:
            items = [
                {"provider": provider, "label": label, "configured": False}
                for provider, (_, label) in _CREDENTIAL_KEYS.items()
            ]
            error = str(exc)
        return {
            "items": items,
            "google_oauth": {
                "client_id_configured": bool(os.getenv("GOOGLE_CLIENT_ID", "").strip()),
                "random_loopback": True,
                "redirect_uri_configured": bool(
                    os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
                ),
            },
            "credential_store": "Windows Credential Manager",
            "error": error,
        }

    @api.post("/integrations/credentials/{provider}")
    def save_integration_credential(
        provider: str,
        request: IntegrationCredentialRequest,
        response: Response,
        _idempotency_key: str = Header(
            alias="Idempotency-Key", min_length=1, max_length=255
        ),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        normalized, key, _ = credential_definition(provider)
        secret = request.secret.get_secret_value().strip()
        if not secret:
            raise HTTPException(status_code=422, detail="Credential cannot be blank")
        try:
            credential_store_factory().set(key, secret)
        except CredentialStoreUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"provider": normalized, "configured": True}

    @api.delete("/integrations/credentials/{provider}")
    def delete_integration_credential(
        provider: str,
        response: Response,
        _idempotency_key: str = Header(
            alias="Idempotency-Key", min_length=1, max_length=255
        ),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        normalized, key, _ = credential_definition(provider)
        try:
            credential_store_factory().delete(key)
        except CredentialStoreUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"provider": normalized, "configured": False}

    @api.get("/integrations/google")
    def google_status() -> dict[str, Any]:
        state = state_store_factory().get_connection("google")
        try:
            has_credentials, error = google_factory().connected(), ""
        except CredentialStoreUnavailable as exc:
            has_credentials, error = False, str(exc)
        return {
            "provider": "google",
            "status": state.status if state else "disconnected",
            "account_label": state.account_label if state else "",
            "scopes": state.scopes if state else [],
            "last_sync_at": state.last_sync_at if state else None,
            "configured": has_credentials,
            "error": error or (state.last_error if state else ""),
        }

    @api.post("/integrations/google/oauth/start")
    def start_google_oauth() -> dict[str, str]:
        google = google_factory()
        listener: GoogleOAuthLoopbackListener | None = None
        if not google.client_id:
            oauth = google.begin_oauth()
        elif not google.fake:
            try:
                listener = GoogleOAuthLoopbackListener(complete_google_oauth)
                oauth = google.begin_oauth(redirect_uri=listener.redirect_uri)
            except OSError:
                listener = None
                oauth = google.begin_oauth()
        else:
            oauth = google.begin_oauth()
        now = utc_now()
        with oauth_lock:
            previous = list(pending_oauth.values())
            pending_oauth.clear()
            pending_oauth[oauth.state] = (oauth, now, listener)
        for _, _, previous_listener in previous:
            if previous_listener:
                previous_listener.stop()
        if listener:
            listener.start(
                on_expire=lambda: expire_google_oauth(oauth.state, listener)
            )
        state_store_factory().set_connection("google", status="connecting")
        return {
            "authorization_url": oauth.authorization_url,
            "state": oauth.state,
            "redirect_uri": oauth.redirect_uri,
        }

    @api.get("/integrations/google/oauth/callback")
    def finish_google_oauth(state: str, code: str = "", error: str = "") -> dict[str, Any]:
        try:
            return complete_google_oauth(code, state, error)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.post("/integrations/google/reconcile", status_code=202)
    def queue_google_reconcile(
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ) -> dict[str, str]:
        job = enqueue_job(
            "google.reconcile", {}, idempotency_key=idempotency_key
        )
        return {"job_id": job.id, "state": job.state}

    @api.get("/integrations/stripe")
    def stripe_status() -> dict[str, Any]:
        state = state_store_factory().get_connection("stripe")
        try:
            configured, error = stripe_factory().configured(), ""
        except CredentialStoreUnavailable as exc:
            configured, error = False, str(exc)
        return {
            "provider": "stripe",
            "status": state.status if state else ("connected" if configured else "disconnected"),
            "configured": configured,
            "last_sync_at": state.last_sync_at if state else None,
            "error": error or (state.last_error if state else ""),
        }

    @api.post("/integrations/stripe/reconcile", status_code=202)
    def queue_stripe_reconcile(
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ) -> dict[str, str]:
        job = enqueue_job(
            "stripe.reconcile", {}, idempotency_key=idempotency_key
        )
        return {"job_id": job.id, "state": job.state}

    @api.get("/notifications")
    def list_notifications(
        unread_only: bool = False, limit: int = Query(default=100, ge=1, le=100)
    ) -> dict[str, Any]:
        return {
            "items": [asdict(item) for item in notification_store_factory().list(unread_only=unread_only, limit=limit)],
            "next_cursor": None,
        }

    @api.patch("/notifications/{notification_id}")
    def read_notification(notification_id: str, request: NotificationReadRequest) -> dict[str, Any]:
        try:
            item = notification_store_factory().mark_read(
                notification_id, version=request.version, read=request.read
            )
        except NotificationConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if item is None:
            raise HTTPException(status_code=404, detail="Notification not found")
        return asdict(item)

    @api.get("/automations")
    def list_automations() -> dict[str, Any]:
        return {"items": [asdict(rule) for rule in automation_store_factory().list_rules()], "next_cursor": None}

    @api.get("/automations/executions")
    def list_automation_executions(
        rule_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=100),
    ) -> dict[str, Any]:
        return {
            "items": [
                asdict(item)
                for item in automation_store_factory().list_executions(
                    rule_id=rule_id, limit=limit
                )
            ],
            "next_cursor": None,
        }

    @api.post("/automations", status_code=201)
    def create_automation(request: RuleRequest) -> dict[str, Any]:
        try:
            return asdict(automation_store_factory().create_rule(**request.model_dump()))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @api.get("/automations/{rule_id}")
    def get_automation(rule_id: str) -> dict[str, Any]:
        rule = automation_store_factory().get_rule(rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="Automation rule not found")
        return asdict(rule)

    @api.patch("/automations/{rule_id}")
    def update_automation(rule_id: str, request: RuleUpdateRequest) -> dict[str, Any]:
        try:
            return asdict(
                automation_store_factory().update_rule(rule_id, **request.model_dump())
            )
        except OptimisticRuleConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @api.post("/automations/{rule_id}/preview")
    def preview_automation(rule_id: str, request: RulePreviewRequest) -> dict[str, Any]:
        try:
            engine = AutomationEngine(automation_store_factory(), {})
            return {"items": engine.preview(rule_id, request.records), "next_cursor": None}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Automation rule not found") from exc

    @api.post("/backups", status_code=202)
    def queue_backup(
        request: BackupRequest,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ) -> dict[str, str]:
        job = enqueue_job(
            "backup.create", request.model_dump(), idempotency_key=idempotency_key
        )
        return {"job_id": job.id, "state": job.state}

    @api.post("/backups/restore", status_code=202)
    def queue_restore(
        request: RestoreRequest,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
        _confirmation: None = Confirmation,
    ) -> dict[str, str]:
        if not request.confirmed:
            raise HTTPException(status_code=409, detail="Restore requires explicit confirmation")
        job = enqueue_job(
            "backup.restore",
            {"backup_path": request.backup_path, "target_path": str(db_path())},
            idempotency_key=idempotency_key,
        )
        return {"job_id": job.id, "state": job.state}

    return api


router = create_router()
