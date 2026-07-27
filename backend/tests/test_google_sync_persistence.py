from __future__ import annotations

import base64
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import platform_db
from app.communications.schema import install_schema as install_communications_schema
from app.integrations_v1.google import GoogleWorkspaceAdapter
from app.integrations_v1.schema import install_schema as install_integrations_schema
from app.integrations_v1.secrets import CredentialStore
from app.integrations_v1.worker import Worker
from app.operations.schema import install_schema as install_operations_schema
from app.v1 import core_service, models


def _encoded(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


class GoogleSyncPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        os.environ["CRM_DB_PATH"] = str(Path(self.temp.name) / "crm.sqlite3")
        platform_db.reset_bootstrap_for_tests()
        platform_db.bootstrap()
        with platform_db.connect() as conn:
            install_integrations_schema(conn)
            install_communications_schema(conn)
            install_operations_schema(conn)
        self.fake_state = {
            "history_id": 7,
            "gmail_messages": {
                "seed": self.gmail_message("gmail-1", "thread-1", 7, "Can we talk?"),
            },
            "calendar_events": {
                "calendar-1": self.calendar_event("Remote kickoff", 7),
            },
            "drive_files": {},
        }
        self.google = GoogleWorkspaceAdapter(
            credentials=CredentialStore.for_tests(), fake=True, fake_state=self.fake_state
        )
        self.worker = Worker(google=self.google)

    def tearDown(self) -> None:
        platform_db.reset_bootstrap_for_tests()
        os.environ.pop("CRM_DB_PATH", None)
        self.temp.cleanup()

    @staticmethod
    def gmail_message(message_id: str, thread_id: str, history_id: int, text: str) -> dict:
        return {
            "id": message_id,
            "threadId": thread_id,
            "historyId": str(history_id),
            "internalDate": "1783328400000",
            "labelIds": ["INBOX", "UNREAD"],
            "snippet": text,
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": [
                    {"name": "From", "value": "Alex Buyer <alex@example.com>"},
                    {"name": "To", "value": "operator@example.com"},
                    {"name": "Subject", "value": "A useful conversation"},
                    {"name": "Message-ID", "value": f"<{message_id}@example.com>"},
                ],
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": _encoded(text)}},
                    {
                        "mimeType": "application/pdf",
                        "filename": "brief.pdf",
                        "body": {"attachmentId": f"attachment-{message_id}", "size": 42},
                    },
                ],
            },
        }

    @staticmethod
    def calendar_event(summary: str, change_id: int) -> dict:
        return {
            "id": "calendar-1",
            "etag": f'"etag-{change_id}"',
            "status": "confirmed",
            "summary": summary,
            "description": "Agenda",
            "location": "Google Meet",
            "start": {"dateTime": "2026-07-20T09:00:00+01:00", "timeZone": "Europe/London"},
            "end": {"dateTime": "2026-07-20T10:00:00+01:00", "timeZone": "Europe/London"},
            "created": "2026-07-01T08:00:00Z",
            "updated": f"2026-07-{change_id:02d}T08:00:00Z",
            "htmlLink": "https://calendar.google.com/event?eid=calendar-1",
            "_change_id": change_id,
        }

    def add_contact(self) -> int:
        now = platform_db.utc_now().isoformat()
        with platform_db.connect() as conn:
            account_id = conn.execute(
                "INSERT INTO accounts(name, created_at, updated_at) VALUES ('Example Ltd', ?, ?)",
                (now, now),
            ).lastrowid
            return int(
                conn.execute(
                    """INSERT INTO contacts
                       (account_id, display_name, email, created_at, updated_at)
                       VALUES (?, 'Alex Buyer', 'alex@example.com', ?, ?)""",
                    (account_id, now, now),
                ).lastrowid
            )

    def test_initial_and_incremental_sync_cache_real_api_shapes_idempotently(self) -> None:
        contact_id = self.add_contact()
        first = self.worker._reconcile_google({}, None)  # type: ignore[arg-type]
        self.assertEqual(["thread-1"], first["cached_threads"])
        self.assertEqual("created", first["calendar_results"][0]["state"])
        with platform_db.connect() as conn:
            thread = conn.execute("SELECT * FROM gmail_threads").fetchone()
            message = conn.execute("SELECT * FROM gmail_messages").fetchone()
            links = conn.execute(
                "SELECT entity_type, entity_id FROM gmail_thread_links WHERE thread_id=?",
                (thread["id"],),
            ).fetchall()
            event = conn.execute("SELECT * FROM calendar_events WHERE google_event_id='calendar-1'").fetchone()
        self.assertEqual("Can we talk?", message["body_text"])
        self.assertEqual(1, len(__import__("json").loads(message["attachments_json"])))
        self.assertIn(("contact", contact_id), [tuple(row) for row in links])
        self.assertEqual("2026-07-20T08:00:00+00:00", event["starts_at"])
        self.assertEqual("Synced", event["sync_state"])

        unchanged = self.worker._reconcile_google({}, None)  # type: ignore[arg-type]
        self.assertEqual([], unchanged["cached_threads"])
        self.assertEqual([], unchanged["calendar_results"])

        self.fake_state["history_id"] = 8
        self.fake_state["gmail_messages"]["second"] = self.gmail_message(
            "gmail-2", "thread-1", 8, "Tomorrow works."
        )
        self.fake_state["calendar_events"]["calendar-1"] = self.calendar_event(
            "Updated kickoff", 8
        )
        incremental = self.worker._reconcile_google({}, None)  # type: ignore[arg-type]
        self.assertEqual(["thread-1"], incremental["cached_threads"])
        self.assertEqual("updated", incremental["calendar_results"][0]["state"])
        with platform_db.connect() as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM gmail_threads").fetchone()[0])
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM gmail_messages").fetchone()[0])
            self.assertEqual(
                "Updated kickoff",
                conn.execute("SELECT title FROM calendar_events WHERE google_event_id='calendar-1'").fetchone()[0],
            )

    def test_remote_calendar_change_never_overwrites_pending_local_edit(self) -> None:
        self.worker._reconcile_google({}, None)  # type: ignore[arg-type]
        local_time = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
        with platform_db.connect() as conn:
            conn.execute(
                """UPDATE calendar_events SET title='Local working title', sync_state='Pending',
                   local_updated_at=?, updated_at=?, version=version+1 WHERE google_event_id='calendar-1'""",
                (local_time, local_time),
            )
        self.fake_state["history_id"] = 9
        self.fake_state["calendar_events"]["calendar-1"] = self.calendar_event(
            "Conflicting remote title", 9
        )
        result = self.worker._reconcile_google({}, None)  # type: ignore[arg-type]
        self.assertEqual("conflict", result["calendar_results"][0]["state"])
        with platform_db.connect() as conn:
            event = conn.execute(
                "SELECT title, sync_state, google_etag FROM calendar_events WHERE google_event_id='calendar-1'"
            ).fetchone()
        self.assertEqual("Local working title", event["title"])
        self.assertEqual("Conflict", event["sync_state"])
        self.assertEqual('"etag-9"', event["google_etag"])

    def test_local_calendar_work_queues_and_pushes_with_a_stable_remote_id(self) -> None:
        with platform_db.connect() as conn:
            event = core_service.create_event(
                conn,
                models.CalendarEventCreate(
                    title="Offline planning",
                    starts_at=datetime(2026, 7, 21, 9, tzinfo=timezone.utc),
                    ends_at=datetime(2026, 7, 21, 10, tzinfo=timezone.utc),
                ),
            )
        with platform_db.connect() as conn:
            queued = conn.execute(
                "SELECT * FROM integration_jobs WHERE kind='google.calendar.push' AND json_extract(payload_json, '$.event_id')=?",
                (event["id"],),
            ).fetchone()
        self.assertIsNotNone(queued)
        job = self.worker.jobs.get(queued["id"])
        first = self.worker._push_calendar_event(job.payload, job)
        second = self.worker._push_calendar_event(job.payload, job)
        self.assertEqual(first["google_event_id"], second["google_event_id"])
        self.assertEqual(2, len(self.fake_state["calendar_events"]))
        with platform_db.connect() as conn:
            saved = conn.execute("SELECT * FROM calendar_events WHERE id=?", (event["id"],)).fetchone()
        self.assertEqual("Synced", saved["sync_state"])
        self.assertEqual(f"crm{event['id']:016x}", saved["google_event_id"])

    def test_renewal_worker_emits_deduplicated_90_60_30_bucket_notifications(self) -> None:
        today = platform_db.utc_now().date()
        now = platform_db.utc_now().isoformat()
        with platform_db.connect() as conn:
            account_id = conn.execute(
                "INSERT INTO accounts(name, created_at, updated_at) VALUES ('Renewal Ltd', ?, ?)",
                (now, now),
            ).lastrowid
            success_id = conn.execute(
                """INSERT INTO client_success(account_id, renewal_on, created_at, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (account_id, (today + timedelta(days=55)).isoformat(), now, now),
            ).lastrowid
        first_job = self.worker.jobs.enqueue("renewals.process", {"days": 90}, idempotency_key="renewal-a")
        second_job = self.worker.jobs.enqueue("renewals.process", {"days": 90}, idempotency_key="renewal-b")
        self.worker._process_renewals(first_job.payload, first_job)
        self.worker._process_renewals(second_job.payload, second_job)
        with platform_db.connect() as conn:
            reminders = conn.execute(
                "SELECT * FROM integration_notifications WHERE dedupe_key LIKE ?",
                (f"renewal:{success_id}:%:60",),
            ).fetchall()
        self.assertEqual(1, len(reminders))


if __name__ == "__main__":
    unittest.main()
