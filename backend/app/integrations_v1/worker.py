from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import threading
import time
import uuid
from dataclasses import asdict
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path
from typing import Any, Mapping

from app import platform_db
from app.platform_db import write_audit

from .backup import create_backup, stage_restore
from .google import GoogleWorkspaceAdapter, bounded_initial_sync_time
from .jobs import (
    Job,
    JobStore,
    JobWorker,
    OutboxMessage,
    OutboxStore,
    OutboxWorker,
    PermanentJobError,
    RetryableJobError,
)
from .state import IntegrationStateStore, NotificationStore
from .stripe import StripeAdapter, StripePaymentState


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _utc_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            parsed = parsedate_to_datetime(text)
    else:
        parsed = platform_db.utc_now()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _gmail_addresses(*values: Any) -> list[str]:
    headers: list[str] = []
    for value in values:
        if isinstance(value, (list, tuple)):
            headers.extend(str(item) for item in value if item)
        elif value:
            headers.append(str(value))
    return list(dict.fromkeys(address.strip().lower() for _, address in getaddresses(headers) if "@" in address))


def _gmail_body(payload: Mapping[str, Any]) -> str:
    body = payload.get("body") or {}
    encoded = body.get("data") if isinstance(body, Mapping) else None
    if encoded and str(payload.get("mimeType") or "").lower().startswith("text/plain"):
        try:
            data = str(encoded) + "=" * (-len(str(encoded)) % 4)
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            pass
    for part in payload.get("parts") or []:
        if isinstance(part, Mapping):
            text = _gmail_body(part)
            if text:
                return text
    return ""


def _gmail_attachments(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    body = payload.get("body") or {}
    filename = str(payload.get("filename") or "")
    attachment_id = str(body.get("attachmentId") or "") if isinstance(body, Mapping) else ""
    if filename or attachment_id:
        attachments.append(
            {
                "filename": filename,
                "attachment_id": attachment_id,
                "mime_type": str(payload.get("mimeType") or "application/octet-stream"),
                "size": int(body.get("size") or 0) if isinstance(body, Mapping) else 0,
            }
        )
    for part in payload.get("parts") or []:
        if isinstance(part, Mapping):
            attachments.extend(_gmail_attachments(part))
    return attachments


class Worker:
    """One restart-safe local worker for jobs, provider outbox, and polling."""

    def __init__(
        self,
        *,
        poll_interval: float = 1.0,
        reconciliation_interval: int = 300,
        google: GoogleWorkspaceAdapter | None = None,
        stripe: StripeAdapter | None = None,
    ) -> None:
        fake = _truthy(os.getenv("CRM_INTEGRATIONS_FAKE"))
        self.google = google or GoogleWorkspaceAdapter(
            client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
            redirect_uri=os.getenv("GOOGLE_OAUTH_REDIRECT_URI", ""),
            fake=fake,
        )
        self.stripe = stripe or StripeAdapter(fake=fake)
        self.jobs, self.outbox = JobStore(), OutboxStore()
        self.state, self.notifications = IntegrationStateStore(), NotificationStore()
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.poll_interval = max(0.05, poll_interval)
        self.reconciliation_interval = max(60, reconciliation_interval)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_reconciliation = 0.0
        self._job_worker = JobWorker(
            self.worker_id,
            self.jobs,
            {
                "google.gmail.send": self._send_gmail,
                "google.calendar.push": self._push_calendar_event,
                "google.drive.document.create": self._create_drive_document,
                "google.drive.document.sync": self._sync_drive_document,
                "google.drive.version.upload": self._upload_drive_version,
                "google.reconcile": self._reconcile_google,
                "stripe.checkout.create": self._create_stripe_checkout,
                "stripe.reconcile": self._reconcile_stripe,
                "automation.event": self._run_automation,
                "invoices.refresh_overdue": self._refresh_overdue_invoices,
                "renewals.process": self._process_renewals,
                "backup.create": self._create_backup,
                "backup.restore": self._stage_restore,
            },
        )
        self._outbox_worker = OutboxWorker(
            self.worker_id,
            self.outbox,
            {
                "google.gmail": self._deliver_google_outbox,
                "stripe.checkout": self._deliver_stripe_outbox,
            },
        )

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="crm-integration-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def run_once(self, *, limit: int = 10) -> int:
        self._schedule_reconciliation()
        return self._job_worker.run_once(limit=limit) + self._outbox_worker.run_once(limit=limit)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                processed = self.run_once()
            except Exception as exc:
                bucket = int(time.time() // 300)
                self.notifications.create(
                    "system",
                    "Integration worker needs attention",
                    body="Worker loop failed; retrying automatically.",
                    severity="error",
                    dedupe_key=f"integration-worker:{exc.__class__.__name__}:{bucket}",
                )
                processed = 0
            self._stop.wait(0.05 if processed else self.poll_interval)

    def _schedule_reconciliation(self) -> None:
        now = time.monotonic()
        if now < self._next_reconciliation:
            return
        self._next_reconciliation = now + self.reconciliation_interval
        bucket = int(time.time() // self.reconciliation_interval)
        try:
            if self.google.fake or self.google.connected():
                self.jobs.enqueue(
                    "google.reconcile", {}, idempotency_key=f"google-poll:{bucket}"
                )
        except Exception:
            pass
        try:
            if self.stripe.fake or self.stripe.configured():
                self.jobs.enqueue(
                    "stripe.reconcile", {}, idempotency_key=f"stripe-poll:{bucket}"
                )
        except Exception:
            pass
        day = platform_db.utc_now().date().isoformat()
        self.jobs.enqueue(
            "renewals.process",
            {"days": 90},
            idempotency_key=f"renewals:{day}",
        )
        self.jobs.enqueue(
            "invoices.refresh_overdue",
            {},
            idempotency_key=f"invoices-overdue:{day}",
        )

    def _send_gmail(self, payload: dict[str, Any], _job: Job) -> dict[str, Any]:
        return self.google.send_gmail(**payload)

    def _push_calendar_event(self, payload: dict[str, Any], _job: Job) -> dict[str, Any]:
        if not self.google.fake and not self.google.connected():
            raise RetryableJobError("Google Workspace is disconnected; local calendar change remains queued")
        event_id = int(payload["event_id"])
        with platform_db.connect() as conn:
            row = conn.execute("SELECT * FROM calendar_events WHERE id=?", (event_id,)).fetchone()
        if row is None:
            raise PermanentJobError("Calendar event no longer exists")
        event = dict(row)
        if event["archived_at"]:
            raise PermanentJobError("Archived calendar events are not pushed")
        recurrence = json.loads(event["recurrence_json"] or "{}")
        body: dict[str, Any] = {
            "summary": event["title"],
            "description": event["body"],
            "location": event["location"],
            "start": {"dateTime": event["starts_at"], "timeZone": event["timezone"]},
            "end": {"dateTime": event["ends_at"], "timeZone": event["timezone"]},
        }
        if event["all_day"]:
            body["start"], body["end"] = (
                {"date": str(event["starts_at"])[:10]},
                {"date": str(event["ends_at"])[:10]},
            )
        rules = recurrence.get("rules") if isinstance(recurrence, dict) else None
        if rules:
            body["recurrence"] = list(rules)
        stable_external_id = event["google_event_id"] or f"crm{event_id:016x}"
        remote = self.google.upsert_calendar_event(
            body,
            external_id=stable_external_id,
            send_updates=False,
        )
        outcome = self._store_calendar_event(
            remote, preferred_local_id=event_id, pushed_version=int(event["version"])
        )
        return {"event_id": event_id, "google_event_id": stable_external_id, **outcome}

    def _create_drive_document(self, payload: dict[str, Any], _job: Job) -> dict[str, Any]:
        title = str(payload.get("title") or "CRM document")
        parent_id = str(payload.get("parent_google_file_id") or "") or None
        template_id = str(payload.get("template_google_file_id") or "")
        remote = (
            self.google.copy_drive_document(template_id, title, parent_id=parent_id)
            if template_id
            else self.google.create_drive_document(title, parent_id=parent_id)
        )
        document_id = int(payload["document_id"])
        external_id = str(remote["id"])
        merge_data = dict(payload.get("merge_data") or {})
        merge_result = self.google.merge_google_document(external_id, merge_data) if merge_data else None
        remote_metadata = {**remote, "template_google_file_id": template_id, "merge": merge_result}
        drive_url = str(remote.get("webViewLink") or f"https://docs.google.com/document/d/{external_id}/edit")
        now = platform_db.utc_now().isoformat()
        with platform_db.connect() as conn:
            conn.execute(
                """UPDATE documents
                   SET google_file_id=?, drive_url=?, sync_state='Ready', last_sync_error='',
                       version=version+1, updated_at=? WHERE id=?""",
                (external_id, drive_url, now, document_id),
            )
            conn.execute(
                """INSERT INTO integration_external_refs
                   (provider, resource_type, local_type, local_id, external_id,
                    metadata_json, created_at, updated_at)
                   VALUES ('google', 'drive_file', 'document', ?, ?, ?, ?, ?)
                   ON CONFLICT(provider, resource_type, local_type, local_id) DO UPDATE SET
                     external_id=excluded.external_id, metadata_json=excluded.metadata_json,
                     updated_at=excluded.updated_at""",
                (str(document_id), external_id, json.dumps(remote_metadata, default=str), now, now),
            )
        return {
            "document_id": document_id,
            "google_file_id": external_id,
            "drive_url": drive_url,
            "template_google_file_id": template_id,
            "merge": merge_result,
        }

    def _sync_drive_document(self, payload: dict[str, Any], _job: Job) -> dict[str, Any]:
        document_id = int(payload["document_id"])
        external_id = str(payload.get("google_file_id") or "")
        if not external_id:
            raise PermanentJobError("Document has no Google Drive file ID")
        content = self.google.export_drive_file(external_id, mime_type="application/pdf")
        checksum = hashlib.sha256(content).hexdigest()
        folder = platform_db.data_root() / "documents" / str(document_id)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = platform_db.utc_now().strftime("%Y%m%dT%H%M%SZ")
        target = folder / f"drive-{stamp}-{checksum[:10]}.pdf"
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(content)
        temporary.replace(target)
        now = platform_db.utc_now().isoformat()
        with platform_db.connect() as conn:
            row = conn.execute("SELECT 1 FROM documents WHERE id=?", (document_id,)).fetchone()
            if row is None:
                raise PermanentJobError("Document no longer exists")
            version_number = conn.execute(
                "SELECT COALESCE(MAX(version_number), 0) + 1 FROM document_versions WHERE document_id=?",
                (document_id,),
            ).fetchone()[0]
            version_id = conn.execute(
                """INSERT INTO document_versions
                   (document_id, version_number, google_file_id, local_path, mime_type,
                    checksum_sha256, size_bytes, issued, source, created_at)
                   VALUES (?, ?, ?, ?, 'application/pdf', ?, ?, 0, 'drive-sync', ?)""",
                (document_id, version_number, external_id, str(target), checksum, len(content), now),
            ).lastrowid
            conn.execute(
                """UPDATE documents SET local_path=?, checksum_sha256=?, sync_state='Ready',
                   last_sync_error='', version=version+1, updated_at=? WHERE id=?""",
                (str(target), checksum, now, document_id),
            )
        return {
            "document_id": document_id,
            "version_id": version_id,
            "checksum_sha256": checksum,
            "local_path": str(target),
        }

    def _upload_drive_version(self, payload: dict[str, Any], _job: Job) -> dict[str, Any]:
        source = Path(str(payload.get("local_path") or "")).expanduser().resolve()
        allowed_root = platform_db.data_root().resolve()
        if allowed_root != source and allowed_root not in source.parents:
            raise PermanentJobError("Only CRM-managed files can be uploaded")
        if not source.is_file():
            raise PermanentJobError("Document version file is missing")
        content = source.read_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        expected = str(payload.get("checksum_sha256") or "").lower()
        if expected and checksum != expected:
            raise PermanentJobError("Document version checksum does not match")
        remote = self.google.upload_drive_file(
            name=source.name,
            content=content,
            mime_type=str(payload.get("mime_type") or "application/octet-stream"),
        )
        version_id = int(payload["version_id"])
        now = platform_db.utc_now().isoformat()
        with platform_db.connect() as conn:
            conn.execute(
                """INSERT INTO integration_external_refs
                   (provider, resource_type, local_type, local_id, external_id,
                    metadata_json, created_at, updated_at)
                   VALUES ('google', 'drive_file', 'document_version', ?, ?, ?, ?, ?)
                   ON CONFLICT(provider, resource_type, local_type, local_id) DO UPDATE SET
                     external_id=excluded.external_id, metadata_json=excluded.metadata_json,
                     updated_at=excluded.updated_at""",
                (str(version_id), str(remote["id"]), json.dumps(remote, default=str), now, now),
            )
        return {"version_id": version_id, "google_file_id": str(remote["id"]), "checksum_sha256": checksum}

    def _create_stripe_checkout(self, payload: dict[str, Any], job: Job) -> dict[str, Any]:
        remote = self._create_checkout(payload, job.idempotency_key or job.id)
        return asdict(remote)

    def _create_checkout(
        self, payload: dict[str, Any], idempotency_key: str
    ) -> StripePaymentState:
        prepared, invoice = self._prepare_checkout(payload)
        remote = self.stripe.create_payment_link(
            **prepared, idempotency_key=idempotency_key
        )
        if (
            remote.amount_minor != prepared["amount_minor"]
            or remote.currency.upper() != prepared["currency"].upper()
        ):
            raise PermanentJobError(
                "Stripe returned a checkout for a different amount or currency"
            )
        self._store_checkout_reference(remote, invoice, "awaiting_payment")
        return remote

    @staticmethod
    def _invoice_row(conn: Any, identifier: str | int) -> Any:
        value = str(identifier).strip()
        if value.isdigit():
            return conn.execute("SELECT * FROM invoices WHERE id = ?", (int(value),)).fetchone()
        return conn.execute("SELECT * FROM invoices WHERE number = ?", (value,)).fetchone()

    def _prepare_checkout(
        self, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        with platform_db.connect() as conn:
            invoice_row = self._invoice_row(conn, payload.get("invoice_id", ""))
            if invoice_row is None:
                raise PermanentJobError("Invoice not found")
            invoice = dict(invoice_row)
        outstanding = max(
            invoice["total_pence"] - invoice["paid_pence"] - invoice["credited_pence"],
            0,
        )
        if invoice["status"] not in {"Sent", "Part-paid", "Overdue"} or outstanding <= 0:
            raise PermanentJobError("Only an issued invoice with an outstanding balance can be collected")
        prepared = dict(payload)
        prepared["invoice_id"] = str(invoice["id"])
        prepared["amount_minor"] = outstanding
        prepared["currency"] = invoice["currency"].lower()
        if not prepared.get("description"):
            prepared["description"] = f"Invoice {invoice['number'] or invoice['id']}"
        return prepared, invoice

    def _store_checkout_reference(
        self,
        remote: StripePaymentState,
        invoice: dict[str, Any] | None = None,
        reconciliation_state: str = "awaiting_payment",
        **extra: Any,
    ) -> None:
        now = platform_db.utc_now().isoformat()
        with platform_db.connect() as conn:
            row = invoice or self._invoice_row(conn, remote.invoice_id)
            if row is None:
                return
            local = dict(row)
            metadata = {
                **asdict(remote),
                "reconciliation_state": reconciliation_state,
                **extra,
            }
            metadata_json = json.dumps(metadata, sort_keys=True, default=str)
            previous = conn.execute(
                """SELECT external_id, metadata_json FROM integration_external_refs
                   WHERE provider='stripe' AND resource_type='checkout_session'
                     AND local_type='invoice' AND local_id=?""",
                (str(local["id"]),),
            ).fetchone()
            conn.execute(
                """INSERT INTO integration_external_refs
                   (provider, resource_type, local_type, local_id, external_id,
                    metadata_json, created_at, updated_at)
                   VALUES ('stripe', 'checkout_session', 'invoice', ?, ?, ?, ?, ?)
                   ON CONFLICT(provider, resource_type, local_type, local_id) DO UPDATE SET
                     external_id=excluded.external_id, metadata_json=excluded.metadata_json,
                     updated_at=excluded.updated_at""",
                (str(local["id"]), remote.remote_id, metadata_json, now, now),
            )
            if remote.url and local.get("stripe_payment_url") != remote.url:
                conn.execute(
                    """UPDATE invoices SET stripe_payment_url=?, version=version+1,
                       updated_at=? WHERE id=?""",
                    (remote.url, now, local["id"]),
                )
            changed = previous is None or (
                previous["external_id"] != remote.remote_id
                or previous["metadata_json"] != metadata_json
            )
            if changed:
                write_audit(
                    conn,
                    "stripe.checkout.updated",
                    "invoice",
                    local["id"],
                    before=dict(previous) if previous else {},
                    after=metadata,
                )

    def _record_paid_checkout(self, remote: StripePaymentState) -> dict[str, Any]:
        with platform_db.connect() as conn:
            invoice_row = self._invoice_row(conn, remote.invoice_id)
            existing = conn.execute(
                """SELECT local_id, metadata_json FROM integration_external_refs
                   WHERE provider='stripe' AND resource_type='payment' AND external_id=?""",
                (remote.remote_id,),
            ).fetchone()
        if existing:
            metadata = json.loads(existing["metadata_json"] or "{}")
            if invoice_row is not None:
                self._store_checkout_reference(
                    remote,
                    dict(invoice_row),
                    "recorded",
                    local_payment_id=int(existing["local_id"]),
                    allocated_minor=int(metadata.get("allocated_minor") or 0),
                    unallocated_minor=int(metadata.get("unallocated_minor") or 0),
                )
            return {
                "remote_id": remote.remote_id,
                "state": "already_recorded",
                "payment_id": int(existing["local_id"]),
                "allocated_minor": int(metadata.get("allocated_minor") or 0),
            }
        if invoice_row is None:
            self.notifications.create(
                "billing",
                "Stripe payment could not be matched",
                body=f"Checkout {remote.remote_id} references an unknown CRM invoice.",
                severity="error",
                dedupe_key=f"stripe-orphan:{remote.remote_id}",
            )
            return {"remote_id": remote.remote_id, "state": "invoice_not_found"}

        invoice = dict(invoice_row)
        if remote.currency.upper() != invoice["currency"].upper():
            self._store_checkout_reference(remote, invoice, "currency_mismatch")
            self.notifications.create(
                "billing",
                "Stripe payment currency mismatch",
                body=f"Checkout {remote.remote_id} was not posted because its currency does not match invoice {invoice['number'] or invoice['id']}.",
                severity="error",
                dedupe_key=f"stripe-currency:{remote.remote_id}",
            )
            return {"remote_id": remote.remote_id, "state": "currency_mismatch"}
        if remote.amount_minor <= 0:
            self._store_checkout_reference(remote, invoice, "invalid_amount")
            return {"remote_id": remote.remote_id, "state": "invalid_amount"}

        from app.operations.models import PaymentAllocationCreate, PaymentCreate
        from app.operations.service import allocate_payment, create_payment, get_invoice

        payment = create_payment(
            PaymentCreate(
                amount_pence=remote.amount_minor,
                currency=remote.currency.upper(),
                method="stripe",
                reference=remote.remote_id,
            ),
            f"stripe:{remote.remote_id}:receipt",
        )
        invoice = get_invoice(invoice["id"])
        allocation_minor = min(
            payment["unallocated_pence"], invoice["outstanding_pence"]
        )
        if allocation_minor:
            payment = allocate_payment(
                payment["id"],
                PaymentAllocationCreate(
                    invoice_id=invoice["id"], amount_pence=allocation_minor
                ),
                f"stripe:{remote.remote_id}:allocation",
            )
        allocated_minor = sum(
            item["amount_pence"]
            for item in payment["allocations"]
            if item["invoice_id"] == invoice["id"]
        )
        unallocated_minor = payment["unallocated_pence"]

        metadata = {
            **asdict(remote),
            "invoice_id": invoice["id"],
            "payment_id": payment["id"],
            "allocated_minor": allocated_minor,
            "unallocated_minor": unallocated_minor,
            "reconciliation_state": "recorded",
        }
        now = platform_db.utc_now().isoformat()
        with platform_db.connect() as conn:
            inserted = conn.execute(
                """INSERT INTO integration_external_refs
                   (provider, resource_type, local_type, local_id, external_id,
                    metadata_json, created_at, updated_at)
                   VALUES ('stripe', 'payment', 'payment', ?, ?, ?, ?, ?)
                   ON CONFLICT DO NOTHING""",
                (
                    str(payment["id"]),
                    remote.remote_id,
                    json.dumps(metadata, sort_keys=True, default=str),
                    now,
                    now,
                ),
            ).rowcount
            if inserted:
                write_audit(
                    conn,
                    "stripe.payment.reconciled",
                    "payment",
                    payment["id"],
                    after=metadata,
                )
        self._store_checkout_reference(
            remote,
            invoice,
            "recorded",
            local_payment_id=payment["id"],
            allocated_minor=allocated_minor,
            unallocated_minor=unallocated_minor,
        )
        return {
            "remote_id": remote.remote_id,
            "state": "recorded" if inserted else "already_recorded",
            "payment_id": payment["id"],
            "allocated_minor": allocated_minor,
        }

    @staticmethod
    def _gmail_thread_ids(result: Mapping[str, Any]) -> list[str]:
        ids: list[str] = []
        for thread in result.get("threads") or []:
            thread_id = str(thread.get("threadId") or thread.get("id") or "")
            if thread_id:
                ids.append(thread_id)
        for change in result.get("history") or []:
            for group in ("messages", "messagesAdded", "messagesDeleted", "labelsAdded", "labelsRemoved"):
                for item in change.get(group) or []:
                    message = item.get("message") or item
                    thread_id = str(message.get("threadId") or "")
                    if thread_id:
                        ids.append(thread_id)
        return list(dict.fromkeys(ids))

    @staticmethod
    def _gmail_cache_payload(thread: Mapping[str, Any]) -> Any:
        from app.communications.router import GmailMessageCache, GmailThreadCache

        cached_messages = []
        participants: list[str] = []
        for message in thread.get("messages") or []:
            payload = message.get("payload") if isinstance(message.get("payload"), Mapping) else {}
            headers = {
                str(item.get("name") or "").lower(): str(item.get("value") or "")
                for item in payload.get("headers") or []
                if isinstance(item, Mapping)
            }
            sender = _gmail_addresses(message.get("from_email"), message.get("from"), headers.get("from"))
            to = _gmail_addresses(message.get("to"), headers.get("to"))
            cc = _gmail_addresses(message.get("cc"), headers.get("cc"))
            bcc = _gmail_addresses(message.get("bcc"), headers.get("bcc"))
            labels = [str(label) for label in message.get("labelIds") or message.get("labels") or []]
            internal_date = message.get("internalDate")
            sent_at = (
                datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc)
                if internal_date and str(internal_date).isdigit()
                else _utc_datetime(message.get("sent_at") or headers.get("date"))
            )
            direction = "draft" if "DRAFT" in labels else "outbound" if "SENT" in labels else "inbound"
            subject = str(message.get("subject") or headers.get("subject") or "")
            snippet = str(message.get("snippet") or "")
            body_text = str(message.get("body_text") or _gmail_body(payload) or snippet)
            cached_messages.append(
                GmailMessageCache(
                    gmail_message_id=str(message.get("id") or ""),
                    rfc_message_id=str(message.get("rfc_message_id") or headers.get("message-id") or ""),
                    direction=direction,
                    from_email=sender[0] if sender else "",
                    to=to,
                    cc=cc,
                    bcc=bcc,
                    subject=subject,
                    snippet=snippet,
                    body_text=body_text,
                    sent_at=sent_at,
                    labels=labels,
                    attachments=_gmail_attachments(payload),
                )
            )
            participants.extend([*sender, *to, *cc, *bcc])
        newest = max((message.sent_at for message in cached_messages), default=None)
        subject = next((message.subject for message in cached_messages if message.subject), "")
        return GmailThreadCache(
            gmail_thread_id=str(thread.get("id") or ""),
            history_id=str(thread.get("historyId") or ""),
            subject=subject,
            snippet=str(thread.get("snippet") or (cached_messages[-1].snippet if cached_messages else "")),
            participants=list(dict.fromkeys(participants)),
            last_message_at=newest,
            unread=any("UNREAD" in message.labels for message in cached_messages),
            messages=cached_messages,
        )

    def _persist_gmail_changes(self, result: Mapping[str, Any]) -> list[str]:
        from app.communications.router import cache_thread

        details = []
        for thread_id in self._gmail_thread_ids(result):
            try:
                details.append(self.google.get_gmail_thread(thread_id))
            except KeyError:
                continue
            except Exception as exc:
                if self._http_status(exc) == 404:
                    continue
                raise
        cached: list[str] = []
        with platform_db.connect() as conn:
            for detail in details:
                payload = self._gmail_cache_payload(detail)
                if not payload.gmail_thread_id or not payload.messages:
                    continue
                cache_thread(payload, conn)
                cached.append(payload.gmail_thread_id)
        return cached

    def _store_calendar_event(
        self,
        remote: Mapping[str, Any],
        *,
        preferred_local_id: int | None = None,
        pushed_version: int | None = None,
    ) -> dict[str, Any]:
        external_id = str(remote.get("id") or "")
        if not external_id:
            raise ValueError("Google Calendar event has no ID")
        now = platform_db.utc_now().isoformat()
        remote_updated = _utc_datetime(remote.get("updated")).isoformat()
        etag = str(remote.get("etag") or "")
        cancelled = str(remote.get("status") or "").lower() == "cancelled"
        with platform_db.connect() as conn:
            existing = None
            if preferred_local_id is not None:
                existing = conn.execute(
                    "SELECT * FROM calendar_events WHERE id=?", (preferred_local_id,)
                ).fetchone()
            if existing is None:
                existing = conn.execute(
                    "SELECT * FROM calendar_events WHERE google_event_id=? ORDER BY id LIMIT 1",
                    (external_id,),
                ).fetchone()
            if cancelled and existing is None:
                return {"state": "ignored_deleted", "google_event_id": external_id}

            local_id = int(existing["id"]) if existing else None
            if existing is not None:
                dirty = existing["sync_state"] in {"Local", "Pending", "Conflict"}
                remote_changed = bool(
                    (etag and existing["google_etag"] and etag != existing["google_etag"])
                    or (
                        existing["remote_updated_at"]
                        and _utc_datetime(existing["remote_updated_at"]) < _utc_datetime(remote_updated)
                    )
                )
                if pushed_version is not None and int(existing["version"]) != pushed_version:
                    conn.execute(
                        """UPDATE calendar_events SET google_event_id=?, google_etag=?,
                           google_html_link=?, remote_updated_at=?, sync_state='Pending',
                           updated_at=?, version=version+1 WHERE id=?""",
                        (
                            external_id,
                            etag,
                            str(remote.get("htmlLink") or ""),
                            remote_updated,
                            now,
                            local_id,
                        ),
                    )
                    state = "pending_local_change"
                elif pushed_version is None and dirty and existing["google_event_id"]:
                    if remote_changed:
                        conn.execute(
                            """UPDATE calendar_events SET google_etag=?, remote_updated_at=?,
                               sync_state='Conflict', updated_at=?, version=version+1 WHERE id=?""",
                            (etag, remote_updated, now, local_id),
                        )
                        state = "conflict"
                    else:
                        state = "pending_local_change"
                    self._store_calendar_reference(conn, local_id, external_id, remote, now)
                    return {"state": state, "event_id": local_id, "google_event_id": external_id}
                else:
                    state = "deleted" if cancelled else "updated"
                    if cancelled:
                        conn.execute(
                            """UPDATE calendar_events SET google_event_id=?, google_etag=?,
                               google_html_link=?, remote_updated_at=?, sync_state='Synced',
                               archived_at=COALESCE(archived_at, ?), updated_at=?, version=version+1
                               WHERE id=?""",
                            (external_id, etag, str(remote.get("htmlLink") or ""), remote_updated, now, now, local_id),
                        )
                    else:
                        self._update_calendar_from_remote(conn, local_id, remote, remote_updated, now)
            else:
                if cancelled:
                    return {"state": "ignored_deleted", "google_event_id": external_id}
                start, end, event_timezone, all_day = self._calendar_times(remote)
                cursor = conn.execute(
                    """INSERT INTO calendar_events
                       (title, body, location, starts_at, ends_at, timezone, all_day,
                        recurrence_json, google_event_id, google_etag, google_html_link,
                        sync_state, remote_updated_at, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Synced', ?, ?, ?)""",
                    (
                        str(remote.get("summary") or "(Untitled event)"),
                        str(remote.get("description") or ""),
                        str(remote.get("location") or ""),
                        start,
                        end,
                        event_timezone,
                        int(all_day),
                        json.dumps({"rules": remote.get("recurrence") or []}, sort_keys=True),
                        external_id,
                        etag,
                        str(remote.get("htmlLink") or ""),
                        remote_updated,
                        _utc_datetime(remote.get("created")).isoformat() if remote.get("created") else now,
                        now,
                    ),
                )
                local_id, state = int(cursor.lastrowid), "created"
            assert local_id is not None
            self._store_calendar_reference(conn, local_id, external_id, remote, now)
        return {"state": state, "event_id": local_id, "google_event_id": external_id}

    @staticmethod
    def _calendar_times(remote: Mapping[str, Any]) -> tuple[str, str, str, bool]:
        start, end = remote.get("start") or {}, remote.get("end") or {}
        if start.get("date"):
            starts = datetime.combine(date.fromisoformat(str(start["date"])), datetime_time(), tzinfo=timezone.utc)
            ends = datetime.combine(date.fromisoformat(str(end.get("date") or start["date"])), datetime_time(), tzinfo=timezone.utc)
            return starts.isoformat(), ends.isoformat(), str(start.get("timeZone") or "Europe/London"), True
        return (
            _utc_datetime(start.get("dateTime")).isoformat(),
            _utc_datetime(end.get("dateTime")).isoformat(),
            str(start.get("timeZone") or end.get("timeZone") or "Europe/London"),
            False,
        )

    def _update_calendar_from_remote(
        self, conn: Any, local_id: int, remote: Mapping[str, Any], remote_updated: str, now: str
    ) -> None:
        start, end, event_timezone, all_day = self._calendar_times(remote)
        conn.execute(
            """UPDATE calendar_events SET title=?, body=?, location=?, starts_at=?, ends_at=?,
               timezone=?, all_day=?, recurrence_json=?, google_event_id=?, google_etag=?,
               google_html_link=?, sync_state='Synced', remote_updated_at=?, archived_at=NULL,
               updated_at=?, version=version+1 WHERE id=?""",
            (
                str(remote.get("summary") or "(Untitled event)"),
                str(remote.get("description") or ""),
                str(remote.get("location") or ""),
                start,
                end,
                event_timezone,
                int(all_day),
                json.dumps({"rules": remote.get("recurrence") or []}, sort_keys=True),
                str(remote["id"]),
                str(remote.get("etag") or ""),
                str(remote.get("htmlLink") or ""),
                remote_updated,
                now,
                local_id,
            ),
        )

    @staticmethod
    def _store_calendar_reference(
        conn: Any, local_id: int, external_id: str, remote: Mapping[str, Any], now: str
    ) -> None:
        metadata = {
            "etag": str(remote.get("etag") or ""),
            "updated": str(remote.get("updated") or ""),
            "status": str(remote.get("status") or "confirmed"),
        }
        conn.execute(
            """INSERT INTO integration_external_refs
               (provider, resource_type, local_type, local_id, external_id,
                metadata_json, created_at, updated_at)
               VALUES ('google', 'calendar_event', 'calendar_event', ?, ?, ?, ?, ?)
               ON CONFLICT(provider, resource_type, local_type, local_id) DO UPDATE SET
                 external_id=excluded.external_id, metadata_json=excluded.metadata_json,
                 updated_at=excluded.updated_at""",
            (str(local_id), external_id, json.dumps(metadata, sort_keys=True), now, now),
        )

    def _persist_calendar_changes(self, result: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [self._store_calendar_event(event) for event in result.get("items") or []]

    def _reconcile_google(self, _payload: dict[str, Any], _job: Job) -> dict[str, Any]:
        if not self.google.fake and not self.google.connected():
            raise PermanentJobError("Google Workspace is not connected")
        try:
            gmail_cursor = self.state.get_cursor("google", "gmail")
            try:
                gmail = self.google.list_gmail_threads(history_id=gmail_cursor)
            except Exception as exc:
                if not gmail_cursor or self._http_status(exc) != 404:
                    raise
                self.state.clear_cursor("google", "gmail")
                gmail = self.google.list_gmail_threads(days=90)
            calendar_cursor = self.state.get_cursor("google", "calendar")
            try:
                calendar = self.google.list_calendar_events(
                    sync_token=calendar_cursor,
                    time_min=None if calendar_cursor else bounded_initial_sync_time(),
                )
            except Exception as exc:
                if not calendar_cursor or self._http_status(exc) != 410:
                    raise
                self.state.clear_cursor("google", "calendar")
                calendar = self.google.list_calendar_events(
                    time_min=bounded_initial_sync_time()
                )
            cached_threads = self._persist_gmail_changes(gmail)
            calendar_results = self._persist_calendar_changes(calendar)
            if gmail.get("historyId"):
                self.state.set_cursor("google", "gmail", str(gmail["historyId"]))
            if calendar.get("nextSyncToken"):
                self.state.set_cursor("google", "calendar", str(calendar["nextSyncToken"]))
            resolved_messages = []
            resolved_calendar = []
            for pending in self.jobs.list(state="unknown", limit=100):
                if pending.kind == "google.gmail.send":
                    found = self.google.find_gmail_message(
                        str(pending.payload.get("rfc_message_id") or "")
                    )
                    if found:
                        self.jobs.resolve_unknown(pending.id, succeeded=True, result=found)
                        resolved_messages.append(pending.id)
                elif pending.kind == "google.calendar.push":
                    local_id = int(pending.payload.get("event_id") or 0)
                    if not local_id:
                        continue
                    with platform_db.connect() as conn:
                        local = conn.execute(
                            "SELECT google_event_id FROM calendar_events WHERE id=?", (local_id,)
                        ).fetchone()
                    external_id = str(local["google_event_id"] or f"crm{local_id:016x}") if local else ""
                    found = self.google.get_calendar_event(external_id) if external_id else None
                    if found:
                        result = self._store_calendar_event(
                            found,
                            preferred_local_id=local_id,
                            pushed_version=(
                                int(pending.payload["version"])
                                if pending.payload.get("version") is not None
                                else None
                            ),
                        )
                        self.jobs.resolve_unknown(pending.id, succeeded=True, result=result)
                        resolved_calendar.append(pending.id)
            self.state.set_connection("google", status="connected", synced=True)
            return {
                "gmail_changes": len(gmail.get("threads") or gmail.get("history") or []),
                "calendar_changes": len(calendar.get("items") or []),
                "cached_threads": cached_threads,
                "calendar_results": calendar_results,
                "resolved_unknown_sends": resolved_messages,
                "resolved_unknown_calendar": resolved_calendar,
            }
        except PermanentJobError:
            raise
        except Exception as exc:
            self.state.set_connection(
                "google", status="degraded", last_error="Google sync failed; retry scheduled"
            )
            raise RetryableJobError(str(exc)) from exc

    def _reconcile_stripe(self, _payload: dict[str, Any], _job: Job) -> dict[str, Any]:
        if not self.stripe.fake and not self.stripe.configured():
            raise PermanentJobError("Stripe is not configured")
        try:
            paid_states = self.stripe.list_paid()
            paid = [asdict(payment) for payment in paid_states]
            local = [self._record_paid_checkout(payment) for payment in paid_states]
            resolved_checkouts = []
            for pending in self.jobs.list(state="unknown", limit=100):
                if pending.kind != "stripe.checkout.create":
                    continue
                remote = self.stripe.reconcile_payment(
                    invoice_id=str(pending.payload.get("invoice_id") or ""),
                    idempotency_key=pending.idempotency_key,
                )
                if remote:
                    if remote.paid:
                        self._record_paid_checkout(remote)
                    else:
                        self._store_checkout_reference(
                            remote, reconciliation_state="awaiting_payment"
                        )
                    self.jobs.resolve_unknown(
                        pending.id, succeeded=True, result=asdict(remote)
                    )
                    resolved_checkouts.append(pending.id)
            self.state.set_connection("stripe", status="connected", synced=True)
            return {
                "payments": paid,
                "local_reconciliation": local,
                "resolved_unknown_checkouts": resolved_checkouts,
            }
        except PermanentJobError:
            raise
        except Exception as exc:
            self.state.set_connection(
                "stripe", status="degraded", last_error="Stripe reconciliation failed; retry scheduled"
            )
            raise RetryableJobError(str(exc)) from exc

    def _process_renewals(self, payload: dict[str, Any], job: Job) -> dict[str, Any]:
        from app.operations.service import process_renewals

        result = process_renewals(
            int(payload.get("days", 90)),
            job.idempotency_key or job.id,
        )
        today = platform_db.utc_now().date()
        with platform_db.connect() as conn:
            rows = conn.execute(
                """SELECT cs.id, cs.account_id, cs.renewal_on, a.name account_name
                   FROM client_success cs JOIN accounts a ON a.id=cs.account_id
                   WHERE cs.archived_at IS NULL AND cs.renewal_on BETWEEN ? AND ?""",
                (today.isoformat(), (today + timedelta(days=90)).isoformat()),
            ).fetchall()
        reminders = []
        for row in rows:
            remaining = (date.fromisoformat(row["renewal_on"]) - today).days
            milestone = 30 if remaining <= 30 else 60 if remaining <= 60 else 90
            notification = self.notifications.create(
                "client_success",
                f"{row['account_name']} renewal due in {remaining} days",
                body=f"Renewal date: {row['renewal_on']}. The linked renewal deal is ready for review.",
                severity="warning" if remaining <= 30 else "info",
                action_url=f"/client-success/{row['account_id']}",
                dedupe_key=f"renewal:{row['id']}:{row['renewal_on']}:{milestone}",
            )
            reminders.append(notification.id)
            with platform_db.connect() as conn:
                platform_db.enqueue_automation_event(
                    conn,
                    "renewal.due",
                    {
                        "type": "client_success",
                        "id": row["id"],
                        "account_id": row["account_id"],
                        "account_name": row["account_name"],
                        "renewal_on": row["renewal_on"],
                        "remaining_days": remaining,
                        "milestone_days": milestone,
                    },
                    correlation_id=f"automation:renewal:{row['id']}:{row['renewal_on']}:{milestone}",
                )
        return {**result, "renewal_notification_ids": reminders}

    @staticmethod
    def _run_automation(payload: dict[str, Any], job: Job) -> dict[str, Any]:
        from .automation_runtime import AutomationRuntime

        executions = AutomationRuntime().run_event(payload, attempt=job.attempts)
        failed = [execution for execution in executions if execution.outcome == "failed"]
        if failed:
            raise RetryableJobError("; ".join(execution.error for execution in failed) or "Automation action failed")
        return {
            "execution_ids": [execution.id for execution in executions],
            "matched": sum(execution.outcome in {"matched", "succeeded"} for execution in executions),
            "outcomes": [execution.outcome for execution in executions],
        }

    @staticmethod
    def _refresh_overdue_invoices(_payload: dict[str, Any], _job: Job) -> dict[str, Any]:
        from app.operations.service import list_invoices

        result = list_invoices(limit=100, status="Overdue")
        return {"overdue_invoice_ids": [item["id"] for item in result["items"]]}

    @staticmethod
    def _create_backup(payload: dict[str, Any], _job: Job) -> dict[str, Any]:
        return asdict(create_backup(payload["destination_directory"]))

    def _stage_restore(self, payload: dict[str, Any], _job: Job) -> dict[str, Any]:
        marker = stage_restore(payload["backup_path"], payload["target_path"])
        self.notifications.create(
            "system",
            "Database restore ready",
            body="Restart CRM Workspace to apply the validated backup.",
            severity="warning",
            dedupe_key=f"restore-staged:{marker}",
        )
        return {"staged": True, "restart_required": True, "marker": str(marker)}

    def _deliver_google_outbox(
        self, payload: dict[str, Any], message: OutboxMessage
    ) -> dict[str, Any]:
        if message.event_type != "email.send":
            raise PermanentJobError(f"Unsupported Google outbox event {message.event_type}")
        return self.google.send_gmail(**payload)

    def _deliver_stripe_outbox(
        self, payload: dict[str, Any], message: OutboxMessage
    ) -> Mapping[str, Any]:
        if message.event_type != "checkout.create":
            raise PermanentJobError(f"Unsupported Stripe outbox event {message.event_type}")
        remote = self._create_checkout(payload, message.idempotency_key)
        return {"external_id": remote.remote_id, **asdict(remote)}

    @staticmethod
    def _http_status(error: Exception) -> int | None:
        return getattr(getattr(error, "resp", None), "status", None)
