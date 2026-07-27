from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import platform_db
from app.communications import install_schema, router


class CommunicationsV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        os.environ["CRM_DB_PATH"] = str(Path(self.temp.name) / "crm.sqlite3")
        platform_db.reset_bootstrap_for_tests()
        platform_db.bootstrap()
        with platform_db.connect() as conn:
            install_schema(conn)
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        self.context = TestClient(app, headers={"X-CRM-Confirmed": "true"})
        self.client = self.context.__enter__()

    def tearDown(self) -> None:
        self.context.__exit__(None, None, None)
        platform_db.reset_bootstrap_for_tests()
        os.environ.pop("CRM_DB_PATH", None)
        self.temp.cleanup()

    def contact(self, email: str = "alex@example.com") -> int:
        now = platform_db.utc_now().isoformat()
        with platform_db.connect() as conn:
            account_id = conn.execute(
                "INSERT INTO accounts(name, created_at, updated_at) VALUES ('Example Ltd', ?, ?)",
                (now, now),
            ).lastrowid
            return conn.execute(
                """INSERT INTO contacts
                   (account_id, display_name, first_name, last_name, email, created_at, updated_at)
                   VALUES (?, 'Alex Buyer', 'Alex', 'Buyer', ?, ?, ?)""",
                (account_id, email, now, now),
            ).lastrowid

    def test_gmail_cache_links_matching_contacts_and_preserves_messages(self) -> None:
        contact_id = self.contact()
        response = self.client.post(
            "/api/v1/email/threads/cache",
            json={
                "gmail_thread_id": "thread-1",
                "history_id": "17",
                "subject": "A useful conversation",
                "participants": ["Alex Buyer <alex@example.com>", "operator@example.com"],
                "unread": True,
                "messages": [
                    {
                        "gmail_message_id": "message-1",
                        "rfc_message_id": "<message-1@example.com>",
                        "from_email": "Alex Buyer <alex@example.com>",
                        "to": ["operator@example.com"],
                        "subject": "A useful conversation",
                        "body_text": "Can we talk?",
                        "sent_at": "2026-07-06T09:00:00Z",
                        "labels": ["INBOX", "UNREAD"],
                    }
                ],
            },
        )
        self.assertEqual(200, response.status_code, response.text)
        thread = response.json()
        self.assertEqual(1, thread["message_count"])
        self.assertTrue(thread["unread"])
        self.assertEqual("Can we talk?", thread["messages"][0]["body_text"])
        self.assertIn(
            ("contact", contact_id, "email_match"),
            [(link["entity_type"], link["entity_id"], link["link_source"]) for link in thread["links"]],
        )

        read = self.client.post("/api/v1/email/threads/thread-1/read", headers={"Idempotency-Key": "read-thread-1"})
        self.assertEqual(200, read.status_code, read.text)
        self.assertFalse(read.json()["unread"])
        unread = self.client.get("/api/v1/email/threads", params={"unread": True})
        self.assertEqual([], unread.json()["items"])

        manual = self.client.post(
            "/api/v1/email/threads/thread-1/links",
            json={"entity_type": "opportunity", "entity_id": 42},
        )
        self.assertEqual(201, manual.status_code, manual.text)
        filtered = self.client.get(
            "/api/v1/email/threads", params={"entity_type": "opportunity", "entity_id": 42}
        )
        self.assertEqual([thread["id"]], [item["id"] for item in filtered.json()["items"]])

    def test_templates_sequences_jobs_pause_resume_and_opt_out(self) -> None:
        contact_id = self.contact()
        template = self.client.post(
            "/api/v1/email/templates",
            json={
                "name": "Introduction",
                "subject": "Hello {{first_name}}",
                "body_text": "A note for {{account_name}}.",
            },
        )
        self.assertEqual(201, template.status_code, template.text)
        preview = self.client.post(
            f"/api/v1/email/templates/{template.json()['id']}/preview",
            json={"values": {"first_name": "Ada", "account_name": "Northstar"}},
        )
        self.assertEqual("Hello Ada", preview.json()["subject"])

        sequence = self.client.post(
            "/api/v1/sequences",
            json={
                "name": "Two-touch outreach",
                "steps": [
                    {"step_type": "email", "template_id": template.json()["id"]},
                    {"step_type": "delay", "delay_minutes": 60},
                    {"step_type": "manual-task", "task_title": "Call {{display_name}}"},
                ],
            },
        )
        self.assertEqual(201, sequence.status_code, sequence.text)
        active = self.client.post(
            f"/api/v1/sequences/{sequence.json()['id']}/activate",
            json={"version": sequence.json()["version"]},
        )
        self.assertEqual(200, active.status_code, active.text)

        enrollment = self.client.post(
            f"/api/v1/sequences/{sequence.json()['id']}/enrollments",
            headers={"Idempotency-Key": "enrol-alex"},
            json={"contact_id": contact_id, "start_at": "2026-07-06T08:00:00Z"},
        )
        self.assertEqual(202, enrollment.status_code, enrollment.text)
        enrolled = enrollment.json()
        duplicate = self.client.post(
            f"/api/v1/sequences/{sequence.json()['id']}/enrollments",
            headers={"Idempotency-Key": "enrol-alex"},
            json={"contact_id": contact_id, "start_at": "2026-07-06T08:00:00Z"},
        )
        self.assertEqual(enrolled["id"], duplicate.json()["id"])
        self.assertEqual(1, len(enrolled["scheduled_sends"]))
        with platform_db.connect() as conn:
            job = conn.execute(
                "SELECT kind, state, requires_reconciliation FROM integration_jobs WHERE id=?",
                (enrolled["scheduled_sends"][0]["job_id"],),
            ).fetchone()
            task = conn.execute(
                "SELECT title FROM work_tasks WHERE entity_type='sequence_enrollment' AND entity_id=?",
                (enrolled["id"],),
            ).fetchone()
        self.assertEqual(("google.gmail.send", "queued", 1), tuple(job))
        self.assertEqual("Call Alex Buyer", task["title"])

        paused = self.client.post(
            f"/api/v1/sequences/enrollments/{enrolled['id']}/pause",
            json={"version": enrolled["version"], "reason": "Holiday"},
        )
        self.assertEqual("Paused", paused.json()["state"])
        resumed = self.client.post(
            f"/api/v1/sequences/enrollments/{enrolled['id']}/resume",
            json={"version": paused.json()["version"]},
        )
        self.assertEqual(202, resumed.status_code, resumed.text)
        self.assertEqual("Active", resumed.json()["state"])

        suppressed = self.client.post(
            "/api/v1/email/suppressions",
            json={"email": "alex@example.com", "reason": "Requested", "source": "reply"},
        )
        self.assertEqual(201, suppressed.status_code, suppressed.text)
        with platform_db.connect() as conn:
            stopped = conn.execute(
                "SELECT state FROM sequence_enrollments WHERE id=?", (enrolled["id"],)
            ).fetchone()[0]
            opt_out = conn.execute(
                "SELECT email_opt_out_at FROM contacts WHERE id=?", (contact_id,)
            ).fetchone()[0]
        self.assertEqual("Opted out", stopped)
        self.assertTrue(opt_out)
        blocked = self.client.post(
            "/api/v1/email/send",
            headers={"Idempotency-Key": "blocked-send"},
            json={"to": "alex@example.com", "subject": "Should not send"},
        )
        self.assertEqual(409, blocked.status_code)

    def test_scheduled_send_and_drive_jobs_are_idempotent_and_versions_immutable(self) -> None:
        send = self.client.post(
            "/api/v1/email/send",
            headers={"Idempotency-Key": "send-once"},
            json={
                "to": "recipient@example.com",
                "subject": "Scheduled note",
                "body_text": "Hello",
                "schedule_at": "2026-07-13T09:30:00Z",
            },
        )
        self.assertEqual(202, send.status_code, send.text)
        repeated = self.client.post(
            "/api/v1/email/send",
            headers={"Idempotency-Key": "send-once"},
            json={"to": "recipient@example.com", "subject": "Ignored duplicate body"},
        )
        self.assertEqual(send.json()["job_id"], repeated.json()["job_id"])

        template = self.client.post(
            "/api/v1/document-templates",
            json={"name": "Proposal", "google_file_id": "drive-template-1"},
        )
        document = self.client.post(
            "/api/v1/documents",
            headers={"Idempotency-Key": "drive-document-1"},
            json={
                "title": "Proposal for Example Ltd",
                "template_id": template.json()["id"],
                "entity_type": "opportunity",
                "entity_id": 7,
                "merge_data": {"account_name": "Example Ltd"},
            },
        )
        self.assertEqual(201, document.status_code, document.text)
        self.assertEqual("Queued", document.json()["sync_state"])
        version = self.client.post(
            f"/api/v1/documents/{document.json()['id']}/versions",
            headers={"Idempotency-Key": "drive-version-1"},
            json={
                "local_path": "snapshots/proposal.pdf",
                "checksum_sha256": "a" * 64,
                "size_bytes": 1024,
                "issued": True,
                "queue_drive": True,
            },
        )
        self.assertEqual(201, version.status_code, version.text)
        self.assertTrue(version.json()["drive_job_id"])
        with platform_db.connect() as conn:
            kinds = {
                row[0]
                for row in conn.execute(
                    "SELECT kind FROM integration_jobs WHERE id IN (?, ?)",
                    (document.json()["drive_job_id"], version.json()["drive_job_id"]),
                )
            }
            with self.assertRaises(Exception):
                conn.execute(
                    "UPDATE document_versions SET checksum_sha256=? WHERE id=?",
                    ("b" * 64, version.json()["id"]),
                )
        self.assertEqual({"google.drive.document.create", "google.drive.version.upload"}, kinds)
        archived = self.client.post(
            f"/api/v1/document-templates/{template.json()['id']}/archive",
            json={"version": template.json()["version"]},
        )
        self.assertEqual(200, archived.status_code, archived.text)
        self.assertIsNotNone(archived.json()["archived_at"])
        self.assertEqual([], self.client.get("/api/v1/document-templates").json()["items"])
        stale = self.client.post(
            f"/api/v1/document-templates/{template.json()['id']}/archive",
            json={"version": template.json()["version"]},
        )
        self.assertEqual(409, stale.status_code, stale.text)

    def test_bounce_is_idempotent_and_suppresses_future_delivery(self) -> None:
        contact_id = self.contact("bounce@example.com")
        now = platform_db.utc_now().isoformat()
        with platform_db.connect() as conn:
            sequence_id = conn.execute(
                "INSERT INTO sales_sequences(name, state, created_at, updated_at) VALUES ('Bounce test', 'Active', ?, ?)",
                (now, now),
            ).lastrowid
            enrollment_id = conn.execute(
                "INSERT INTO sequence_enrollments(sequence_id, contact_id, email, created_at, updated_at) VALUES (?, ?, 'bounce@example.com', ?, ?)",
                (sequence_id, contact_id, now, now),
            ).lastrowid
        headers = {"Idempotency-Key": "bounce-once"}
        first = self.client.post(
            f"/api/v1/sequences/enrollments/{enrollment_id}/bounce",
            headers=headers,
            json={"version": 1, "reason": "Mailbox rejected recipient"},
        )
        repeated = self.client.post(
            f"/api/v1/sequences/enrollments/{enrollment_id}/bounce",
            headers=headers,
            json={"version": 1, "reason": "Mailbox rejected recipient"},
        )
        self.assertEqual(200, first.status_code, first.text)
        self.assertEqual(first.json(), repeated.json())
        suppression = self.client.get("/api/v1/email/suppressions").json()["items"]
        self.assertEqual("bounce@example.com", suppression[0]["email"])
        blocked = self.client.post(
            "/api/v1/email/send",
            headers={"Idempotency-Key": "bounce-blocked"},
            json={"to": "bounce@example.com", "subject": "Do not send"},
        )
        self.assertEqual(409, blocked.status_code, blocked.text)


if __name__ == "__main__":
    unittest.main()
