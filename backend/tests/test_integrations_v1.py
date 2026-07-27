from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.integrations_v1.automation import (
    AutomationEngine,
    AutomationStore,
    OptimisticRuleConflict,
)
from app.integrations_v1.backup import (
    apply_staged_restore,
    create_backup,
    restore_backup,
    stage_restore,
    validate_backup,
)
from app.integrations_v1.google import GoogleWorkspaceAdapter
from app.integrations_v1.jobs import (
    IdempotencyConflict,
    JobStore,
    JobWorker,
    OutboxStore,
    RetryableJobError,
    UnknownExternalOutcome,
)
from app.integrations_v1.router import create_router
from app.integrations_v1.secrets import CredentialStore, MemoryCredentialBackend
from app.integrations_v1.state import IntegrationStateStore, NotificationStore
from app.integrations_v1.stripe import StripeAdapter
from app.integrations_v1.worker import Worker
from app.operations.models import InvoiceCreate, PaymentCreate
from app.operations.service import create_invoice, create_payment, issue_invoice
from app.platform_db import connect, reset_bootstrap_for_tests, utc_now


class IntegrationV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        os.environ["CRM_DB_PATH"] = str(self.root / "crm.sqlite3")
        os.environ["CRM_DATA_DIR"] = str(self.root / "data")
        reset_bootstrap_for_tests()

    def tearDown(self) -> None:
        reset_bootstrap_for_tests()
        os.environ.pop("CRM_DB_PATH", None)
        os.environ.pop("CRM_DATA_DIR", None)
        self.temp.cleanup()

    def _issued_invoice(self, amount_pence: int = 1_000) -> dict:
        invoice = create_invoice(
            InvoiceCreate(
                account_id=1,
                due_on=date.today() + timedelta(days=14),
                customer_name="Stripe Test Ltd",
                lines=[
                    {
                        "description": "Service",
                        "quantity": "1",
                        "unit_price_pence": amount_pence,
                    }
                ],
            )
        )
        return issue_invoice(invoice["id"], f"issue:{invoice['id']}")

    def test_credentials_are_explicitly_memory_backed_for_tests(self) -> None:
        backend = MemoryCredentialBackend()
        credentials = CredentialStore("CRM.Test", backend=backend)
        secret = "never-write-this-secret-to-sqlite"
        credentials.set("stripe.api_key", secret)
        credentials.set_json("google.token", {"refresh_token": "also-secret"})

        self.assertEqual(credentials.get("stripe.api_key"), secret)
        self.assertEqual(credentials.get_json("google.token")["refresh_token"], "also-secret")
        jobs = JobStore()
        with self.assertRaises(ValueError):
            jobs.enqueue("unsafe", {"api_key": secret})
        safe_job = jobs.enqueue("safe", {})
        jobs.claim("worker")
        failed = jobs.fail(
            safe_job.id, f"provider api_key={secret}", worker_id="worker", retryable=False
        )
        self.assertNotIn(secret, failed.last_error)
        self.assertNotIn(secret.encode(), Path(os.environ["CRM_DB_PATH"]).read_bytes())
        credentials.delete("stripe.api_key")
        self.assertIsNone(credentials.get("stripe.api_key"))

    def test_job_idempotency_retries_attempt_history_and_worker(self) -> None:
        store = JobStore()
        first = store.enqueue("report.build", {"report": "pipeline"}, idempotency_key="same")
        duplicate = store.enqueue("report.build", {"report": "pipeline"}, idempotency_key="same")
        self.assertEqual(first.id, duplicate.id)
        with self.assertRaises(IdempotencyConflict):
            store.enqueue("report.build", {"report": "different"}, idempotency_key="same")

        claimed = store.claim("worker-a")
        self.assertEqual([first.id], [job.id for job in claimed])
        failed = store.fail(first.id, "temporary", worker_id="worker-a", retry_delay_seconds=0)
        self.assertEqual("retry_wait", failed.state)

        calls: list[str] = []
        worker = JobWorker(
            "worker-b",
            store,
            {"report.build": lambda payload, _job: calls.append(payload["report"]) or {"ok": True}},
        )
        self.assertEqual(1, worker.run_once())
        completed = store.get(first.id)
        self.assertEqual("succeeded", completed.state)
        self.assertEqual({"ok": True}, completed.result)
        self.assertEqual(["pipeline"], calls)
        with connect() as conn:
            attempts = conn.execute(
                "SELECT outcome FROM integration_delivery_attempts WHERE item_id = ? ORDER BY id",
                (first.id,),
            ).fetchall()
        self.assertEqual(["retry_wait", "succeeded"], [row["outcome"] for row in attempts])

    def test_expired_external_job_requires_reconciliation_before_retry(self) -> None:
        store = JobStore()
        base = utc_now()
        job = store.enqueue(
            "stripe.checkout.create",
            {"invoice_id": "INV-1"},
            available_at=base,
            requires_reconciliation=True,
        )
        store.claim("crashed", lease_seconds=1, now=base)

        self.assertEqual(1, store.recover_expired(now=base + timedelta(seconds=2)))
        unknown = store.get(job.id)
        self.assertEqual("unknown", unknown.state)
        self.assertEqual("required", unknown.reconciliation_state)
        self.assertEqual([], store.claim("unsafe-retry", now=base + timedelta(minutes=1)))

        ready = store.resolve_unknown(job.id, succeeded=False)
        self.assertEqual("retry_wait", ready.state)
        self.assertEqual(1, len(store.claim("safe-retry", now=base + timedelta(minutes=1))))

    def test_worker_distinguishes_retryable_and_unknown_failures(self) -> None:
        store = JobStore()
        retry = store.enqueue("retry", {})
        unknown = store.enqueue("unknown", {}, requires_reconciliation=True)

        worker = JobWorker(
            "worker",
            store,
            {
                "retry": lambda *_: (_ for _ in ()).throw(RetryableJobError("later")),
                "unknown": lambda *_: (_ for _ in ()).throw(UnknownExternalOutcome("check remote")),
            },
        )
        self.assertEqual(2, worker.run_once(limit=2))
        self.assertEqual("retry_wait", store.get(retry.id).state)
        self.assertEqual("unknown", store.get(unknown.id).state)

    def test_failed_jobs_support_deliberate_retry_but_unknown_outcomes_do_not(self) -> None:
        store = JobStore()
        failed = store.enqueue("report.build", {"report": "finance"}, max_attempts=1)
        store.claim("worker")
        store.fail(failed.id, "invalid transient response", worker_id="worker", retryable=False)
        self.assertEqual("failed", store.get(failed.id).state)

        queued = store.retry_failed(failed.id)
        self.assertEqual("retry_wait", queued.state)
        self.assertEqual(2, queued.max_attempts)
        self.assertEqual([failed.id], [job.id for job in store.claim("worker-2")])

        unknown = store.enqueue("email.send", {}, requires_reconciliation=True)
        store.claim("crashed", kinds=["email.send"])
        store.mark_unknown(unknown.id, "provider result unknown", worker_id="crashed")
        with self.assertRaisesRegex(ValueError, "reconciled"):
            store.retry_failed(unknown.id)

    def test_outbox_unknown_delivery_is_never_blindly_retried(self) -> None:
        store = OutboxStore()
        first = store.enqueue(
            "google.gmail", "email.send", {"to": "ada@example.com"}, idempotency_key="mail-1"
        )
        duplicate = store.enqueue(
            "google.gmail", "email.send", {"to": "ada@example.com"}, idempotency_key="mail-1"
        )
        self.assertEqual(first.id, duplicate.id)
        with self.assertRaises(IdempotencyConflict):
            store.enqueue(
                "google.gmail",
                "email.send",
                {"to": "different@example.com"},
                idempotency_key="mail-1",
            )
        base = utc_now()
        store.claim("worker", lease_seconds=1, now=base)
        store.recover_expired(now=base + timedelta(seconds=2))
        self.assertEqual("unknown", store.get(first.id).state)
        self.assertEqual([], store.claim("worker-2", now=base + timedelta(minutes=1)))
        delivered = store.resolve_unknown(first.id, delivered=True, external_id="gmail-123")
        self.assertEqual("delivered", delivered.state)
        self.assertEqual("gmail-123", delivered.external_id)

    def test_google_fake_covers_pkce_gmail_calendar_drive_and_docs(self) -> None:
        credentials = CredentialStore.for_tests()
        state: dict = {}
        google = GoogleWorkspaceAdapter(
            client_id="desktop-client",
            redirect_uri="http://127.0.0.1:48123/callback",
            credentials=credentials,
            fake=True,
            fake_state=state,
        )
        oauth = google.begin_oauth()
        self.assertIn("code_challenge_method=S256", oauth.authorization_url)
        self.assertNotIn(oauth.verifier, oauth.authorization_url)
        self.assertTrue(google.exchange_code(oauth, "one-time-code")["connected"])
        self.assertTrue(google.connected())

        sent = google.send_gmail(
            to="ada@example.com",
            subject="Proposal",
            body_text="Attached",
            rfc_message_id="crm-message-1@example.local",
        )
        reconciled = google.send_gmail(
            to="ada@example.com",
            subject="Proposal",
            body_text="Attached",
            rfc_message_id="crm-message-1@example.local",
        )
        self.assertEqual(sent["id"], reconciled["id"])
        self.assertTrue(reconciled["reconciled"])
        self.assertEqual(1, len(google.list_gmail_threads()["threads"]))

        event = google.upsert_calendar_event(
            {
                "summary": "Review",
                "start": {"dateTime": "2026-07-10T09:00:00+01:00"},
                "end": {"dateTime": "2026-07-10T10:00:00+01:00"},
            }
        )
        self.assertEqual(event["id"], google.list_calendar_events()["items"][0]["id"])
        uploaded = google.upload_drive_file(
            name="issued.pdf", content=b"immutable", mime_type="application/pdf"
        )
        self.assertEqual(b"immutable", google.export_drive_file(uploaded["id"]))
        document = google.create_drive_document("Proposal draft")
        self.assertEqual("application/vnd.google-apps.document", document["mimeType"])

    def test_stripe_fake_uses_stable_idempotency_and_reconciliation(self) -> None:
        stripe = StripeAdapter(credentials=CredentialStore.for_tests(), fake=True)
        first = stripe.create_payment_link(
            invoice_id="INV-2026-0001",
            amount_minor=12_345,
            currency="GBP",
            idempotency_key="invoice:1:balance:12345",
        )
        duplicate = stripe.create_payment_link(
            invoice_id="INV-2026-0001",
            amount_minor=12_345,
            currency="GBP",
            idempotency_key="invoice:1:balance:12345",
        )
        self.assertEqual(first.remote_id, duplicate.remote_id)
        self.assertFalse(first.paid)
        stripe.mark_fake_paid(first.remote_id)
        reconciled = stripe.reconcile_payment(invoice_id="INV-2026-0001")
        self.assertTrue(reconciled.paid)
        self.assertEqual([first.remote_id], [payment.remote_id for payment in stripe.list_paid()])

    def test_application_worker_binds_fake_providers_and_periodic_reconciliation(self) -> None:
        credentials = CredentialStore.for_tests()
        google = GoogleWorkspaceAdapter(credentials=credentials, fake=True)
        stripe = StripeAdapter(credentials=credentials, fake=True)
        worker = Worker(google=google, stripe=stripe)
        invoice = self._issued_invoice(5_000)
        email = worker.jobs.enqueue(
            "google.gmail.send",
            {
                "to": "ada@example.com",
                "subject": "Queued",
                "body_text": "Body",
                "rfc_message_id": "worker-1@example.local",
            },
            idempotency_key="worker-mail-1",
            requires_reconciliation=True,
        )
        checkout = worker.jobs.enqueue(
            "stripe.checkout.create",
            {"invoice_id": invoice["id"], "amount_minor": 5000, "currency": "gbp"},
            idempotency_key="worker-checkout-1",
            requires_reconciliation=True,
        )

        self.assertGreaterEqual(worker.run_once(), 2)
        self.assertEqual("succeeded", worker.jobs.get(email.id).state)
        self.assertEqual("succeeded", worker.jobs.get(checkout.id).state)
        self.assertEqual(1, len(google.list_gmail_threads()["threads"]))
        self.assertEqual(1, len(stripe._fake["sessions"]))

    def test_stripe_checkout_and_paid_poll_are_locally_idempotent(self) -> None:
        invoice = self._issued_invoice(1_000)
        credentials = CredentialStore.for_tests()
        stripe = StripeAdapter(credentials=credentials, fake=True)
        worker = Worker(
            google=GoogleWorkspaceAdapter(credentials=credentials, fake=True),
            stripe=stripe,
        )
        checkout = worker.jobs.enqueue(
            "stripe.checkout.create",
            {
                "invoice_id": invoice["id"],
                "amount_minor": 1,
                "currency": "usd",
            },
            idempotency_key="stripe-link:invoice-1",
            requires_reconciliation=True,
        )
        worker._job_worker.run_once()
        remote_id = worker.jobs.get(checkout.id).result["remote_id"]
        remote = stripe.reconcile_payment(remote_id=remote_id, invoice_id=invoice["id"])
        self.assertEqual(1_000, remote.amount_minor)
        self.assertEqual("gbp", remote.currency)

        with connect() as conn:
            saved = conn.execute(
                "SELECT stripe_payment_url FROM invoices WHERE id=?", (invoice["id"],)
            ).fetchone()
            checkout_ref = conn.execute(
                """SELECT * FROM integration_external_refs
                   WHERE provider='stripe' AND resource_type='checkout_session'
                     AND local_type='invoice' AND local_id=?""",
                (str(invoice["id"]),),
            ).fetchone()
        self.assertEqual(remote.url, saved["stripe_payment_url"])
        self.assertEqual(remote_id, checkout_ref["external_id"])

        create_payment(
            PaymentCreate(
                amount_pence=400,
                currency="GBP",
                method="bank",
                reference="manual-before-stripe",
                invoice_id=invoice["id"],
            ),
            "manual-before-stripe",
        )
        stripe.mark_fake_paid(remote_id)
        first = worker._reconcile_stripe({}, None)
        second = worker._reconcile_stripe({}, None)
        self.assertEqual("recorded", first["local_reconciliation"][0]["state"])
        self.assertEqual("already_recorded", second["local_reconciliation"][0]["state"])

        with connect() as conn:
            payment = conn.execute(
                "SELECT * FROM payments WHERE reference=?", (remote_id,)
            ).fetchone()
            allocations = conn.execute(
                "SELECT * FROM payment_allocations WHERE payment_id=?", (payment["id"],)
            ).fetchall()
            local_ref = conn.execute(
                """SELECT * FROM integration_external_refs
                   WHERE provider='stripe' AND resource_type='payment' AND external_id=?""",
                (remote_id,),
            ).fetchone()
            audit_count = conn.execute(
                """SELECT COUNT(*) FROM audit_log
                   WHERE action='stripe.payment.reconciled' AND entity_id=?""",
                (str(payment["id"]),),
            ).fetchone()[0]
            invoice_after = conn.execute(
                "SELECT * FROM invoices WHERE id=?", (invoice["id"],)
            ).fetchone()
            journals = conn.execute(
                """SELECT j.id, SUM(l.debit_pence) debit, SUM(l.credit_pence) credit
                   FROM journals j JOIN journal_lines l ON l.journal_id=j.id
                   GROUP BY j.id"""
            ).fetchall()
        self.assertEqual(1_000, payment["amount_pence"])
        self.assertEqual([600], [row["amount_pence"] for row in allocations])
        self.assertEqual("Paid", invoice_after["status"])
        self.assertEqual(str(payment["id"]), local_ref["local_id"])
        self.assertEqual(1, audit_count)
        self.assertTrue(all(row["debit"] == row["credit"] for row in journals))

    def test_stripe_unknown_checkout_is_reconciled_without_recreating_or_double_posting(self) -> None:
        invoice = self._issued_invoice(700)
        credentials = CredentialStore.for_tests()
        stripe = StripeAdapter(credentials=credentials, fake=True)
        worker = Worker(
            google=GoogleWorkspaceAdapter(credentials=credentials, fake=True),
            stripe=stripe,
        )
        remote = stripe.create_payment_link(
            invoice_id=invoice["id"],
            amount_minor=700,
            currency="gbp",
            idempotency_key="unknown-checkout",
        )
        stripe.mark_fake_paid(remote.remote_id)
        job = worker.jobs.enqueue(
            "stripe.checkout.create",
            {"invoice_id": invoice["id"], "amount_minor": 700, "currency": "gbp"},
            idempotency_key="unknown-checkout",
            requires_reconciliation=True,
        )
        worker.jobs.claim("crashed-worker", kinds=["stripe.checkout.create"])
        worker.jobs.mark_unknown(job.id, "connection lost", worker_id="crashed-worker")

        result = worker._reconcile_stripe({}, None)
        self.assertEqual([job.id], result["resolved_unknown_checkouts"])
        self.assertEqual("succeeded", worker.jobs.get(job.id).state)
        self.assertEqual(1, len(stripe._fake["sessions"]))
        worker._reconcile_stripe({}, None)
        with connect() as conn:
            payment_count = conn.execute(
                "SELECT COUNT(*) FROM payments WHERE reference=?", (remote.remote_id,)
            ).fetchone()[0]
            journal_counts = dict(
                conn.execute(
                    """SELECT source_type, COUNT(*) FROM journals
                       WHERE source_type IN ('payment_receipt', 'payment_allocation')
                       GROUP BY source_type"""
                ).fetchall()
            )
        self.assertEqual(1, payment_count)
        self.assertEqual({"payment_allocation": 1, "payment_receipt": 1}, journal_counts)

    def test_stripe_currency_mismatch_stays_unposted_and_retryable_poll_recovers(self) -> None:
        invoice = self._issued_invoice(500)
        credentials = CredentialStore.for_tests()
        state: dict = {}
        stripe = StripeAdapter(
            credentials=credentials, fake=True, fake_state=state
        )
        remote = stripe.create_payment_link(
            invoice_id=invoice["id"],
            amount_minor=500,
            currency="gbp",
            idempotency_key="currency-mismatch",
        )
        state["sessions"][remote.remote_id]["currency"] = "usd"
        stripe.mark_fake_paid(remote.remote_id)
        worker = Worker(
            google=GoogleWorkspaceAdapter(credentials=credentials, fake=True),
            stripe=stripe,
        )
        mismatch = worker._reconcile_stripe({}, None)
        self.assertEqual("currency_mismatch", mismatch["local_reconciliation"][0]["state"])
        with connect() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0])
            metadata = json.loads(
                conn.execute(
                    """SELECT metadata_json FROM integration_external_refs
                       WHERE provider='stripe' AND resource_type='checkout_session'
                         AND local_type='invoice' AND local_id=?""",
                    (str(invoice["id"]),),
                ).fetchone()[0]
            )
        self.assertEqual("currency_mismatch", metadata["reconciliation_state"])

        class FlakyStripe(StripeAdapter):
            def __init__(self):
                super().__init__(credentials=credentials, fake=True, fake_state=state)
                self.failed = False

            def list_paid(self, **kwargs):
                if not self.failed:
                    self.failed = True
                    raise TimeoutError("temporary Stripe timeout")
                return super().list_paid(**kwargs)

        flaky_worker = Worker(
            google=GoogleWorkspaceAdapter(credentials=credentials, fake=True),
            stripe=FlakyStripe(),
        )
        with self.assertRaises(RetryableJobError):
            flaky_worker._reconcile_stripe({}, None)
        self.assertEqual(
            "currency_mismatch",
            flaky_worker._reconcile_stripe({}, None)["local_reconciliation"][0]["state"],
        )

    def test_connection_references_and_notification_deduplication(self) -> None:
        state = IntegrationStateStore()
        connection = state.set_connection(
            "google", status="connected", account_label="operator@example.com", scopes=["gmail"]
        )
        self.assertEqual("operator@example.com", connection.account_label)
        state.set_cursor("google", "gmail", "history-22")
        self.assertEqual("history-22", state.get_cursor("google", "gmail"))
        state.put_external_reference("google", "message", "activity", 12, "gmail-abc")
        reference = state.find_external_reference(
            "google", "message", local_type="activity", local_id=12
        )
        self.assertEqual("gmail-abc", reference.external_id)

        notifications = NotificationStore()
        first = notifications.create("billing", "Invoice overdue", dedupe_key="invoice:12:overdue")
        duplicate = notifications.create("billing", "Duplicate", dedupe_key="invoice:12:overdue")
        self.assertEqual(first.id, duplicate.id)
        self.assertEqual(1, len(notifications.list(unread_only=True)))
        notifications.mark_read(first.id, version=first.version)
        self.assertEqual([], notifications.list(unread_only=True))

    def test_automation_dry_run_allowlist_optimistic_version_and_cycle_guard(self) -> None:
        store = AutomationStore()
        rule = store.create_rule(
            "Flag overdue invoices",
            "invoice.overdue",
            conditions=[{"field": "amount_due_minor", "operator": "gt", "value": 0}],
            actions=[{"type": "create_task", "params": {"title": "Chase invoice"}}],
            enabled=True,
            dry_run=True,
        )
        calls: list[str] = []
        engine = AutomationEngine(
            store, {"create_task": lambda params, _record: calls.append(params["title"]) or {"id": 1}}
        )
        dry = engine.run("invoice.overdue", {"type": "invoice", "id": 4, "amount_due_minor": 100})
        self.assertEqual("matched", dry[0].outcome)
        self.assertEqual([], calls)
        self.assertTrue(engine.preview(rule.id, [{"id": 4, "amount_due_minor": 100}])[0]["matches"])

        live_rule = store.update_rule(
            rule.id,
            version=rule.version,
            name=rule.name,
            trigger_name=rule.trigger_name,
            conditions=rule.conditions,
            actions=rule.actions,
            enabled=True,
            dry_run=False,
        )
        with self.assertRaises(OptimisticRuleConflict):
            store.update_rule(
                rule.id,
                version=rule.version,
                name=rule.name,
                trigger_name=rule.trigger_name,
                conditions=rule.conditions,
                actions=rule.actions,
                enabled=True,
                dry_run=False,
            )
        live = engine.run(
            "invoice.overdue",
            {"type": "invoice", "id": 4, "amount_due_minor": 100},
            correlation_id="event-1",
        )
        engine.run(
            "invoice.overdue",
            {"type": "invoice", "id": 4, "amount_due_minor": 100},
            correlation_id="event-1",
        )
        self.assertEqual("succeeded", live[0].outcome)
        self.assertEqual(["Chase invoice"], calls)
        self.assertEqual(live_rule.version, 2)

        with self.assertRaises(ValueError):
            store.create_rule(
                "Unsafe",
                "invoice.overdue",
                actions=[{"type": "notify", "params": {"url": "https://example.com/hook"}}],
            )

    def test_backup_validation_and_atomic_restore(self) -> None:
        JobStore()
        with connect() as conn:
            conn.execute("CREATE TABLE backup_probe(value TEXT NOT NULL)")
            conn.execute("INSERT INTO backup_probe(value) VALUES ('preserved')")
        backup = create_backup(self.root / "backups")
        self.assertEqual("ok", validate_backup(backup.path, expected_sha256=backup.sha256).integrity)

        target = self.root / "restored.sqlite3"
        with closing(sqlite3.connect(target)) as conn:
            conn.execute("CREATE TABLE old_probe(value TEXT)")
            conn.commit()
        restored = restore_backup(backup.path, target)
        self.assertEqual(target.resolve(), restored)
        with closing(sqlite3.connect(target)) as conn:
            self.assertEqual("preserved", conn.execute("SELECT value FROM backup_probe").fetchone()[0])

        staged_target = self.root / "staged.sqlite3"
        with closing(sqlite3.connect(staged_target)) as conn:
            conn.execute("CREATE TABLE old_probe(value TEXT)")
            conn.commit()
        marker = stage_restore(backup.path, staged_target)
        self.assertTrue(marker.is_file())
        self.assertEqual(staged_target.resolve(), apply_staged_restore(staged_target))
        self.assertFalse(marker.exists())

        corrupt = self.root / "corrupt.sqlite3"
        corrupt.write_bytes(b"not a database")
        with self.assertRaises(ValueError):
            validate_backup(corrupt)

    def test_router_queues_external_actions_and_completes_fake_oauth(self) -> None:
        credentials = CredentialStore.for_tests()
        google = GoogleWorkspaceAdapter(
            client_id="desktop-client",
            redirect_uri="http://127.0.0.1:48123/callback",
            credentials=credentials,
            fake=True,
        )
        stripe = StripeAdapter(credentials=credentials, fake=True)
        app = FastAPI()
        app.include_router(
            create_router(google_factory=lambda: google, stripe_factory=lambda: stripe),
            prefix="/api/v1",
        )
        client = TestClient(app, headers={"X-CRM-Confirmed": "true"})

        missing_key = client.post(
            "/api/v1/integrations/google/email/send",
            json={
                "to": "ada@example.com",
                "subject": "Hello",
                "body_text": "Body",
                "rfc_message_id": "router-1@example.local",
            },
        )
        self.assertEqual(422, missing_key.status_code)
        headers = {"Idempotency-Key": "send:router-1"}
        queued = client.post(
            "/api/v1/integrations/google/email/send",
            headers=headers,
            json={
                "to": "ada@example.com",
                "subject": "Hello",
                "body_text": "Body",
                "rfc_message_id": "router-1@example.local",
            },
        )
        duplicate = client.post(
            "/api/v1/integrations/google/email/send",
            headers=headers,
            json={
                "to": "ada@example.com",
                "subject": "Hello",
                "body_text": "Body",
                "rfc_message_id": "router-1@example.local",
            },
        )
        self.assertEqual(202, queued.status_code)
        self.assertEqual(queued.json()["job_id"], duplicate.json()["job_id"])
        conflict = client.post(
            "/api/v1/integrations/google/email/send",
            headers=headers,
            json={
                "to": "ada@example.com",
                "subject": "Changed",
                "body_text": "Body",
                "rfc_message_id": "router-1@example.local",
            },
        )
        self.assertEqual(409, conflict.status_code)

        started = client.post("/api/v1/integrations/google/oauth/start")
        self.assertEqual(200, started.status_code)
        callback = client.get(
            "/api/v1/integrations/google/oauth/callback",
            params={"state": started.json()["state"], "code": "fake-code"},
        )
        self.assertEqual(200, callback.status_code)
        status = client.get("/api/v1/integrations/google").json()
        self.assertEqual("connected", status["status"])
        self.assertIn("https://www.googleapis.com/auth/gmail.modify", status["scopes"])


if __name__ == "__main__":
    unittest.main()
