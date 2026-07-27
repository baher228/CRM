import os
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app import platform_db
from app.main import app
from app.v1 import core_service


class TodayDashboardTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["CRM_DB_PATH"] = str(Path(self.tempdir.name) / "crm.sqlite3")
        platform_db.reset_bootstrap_for_tests()
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        platform_db.reset_bootstrap_for_tests()
        os.environ.pop("CRM_DB_PATH", None)
        self.tempdir.cleanup()

    def test_dashboard_returns_bounded_actionable_signals_and_counts(self):
        account_response = self.client.post("/api/v1/accounts", json={"name": "Signal Works"})
        self.assertEqual(201, account_response.status_code, account_response.text)
        account_id = account_response.json()["id"]
        now = platform_db.utc_now()
        today = now.astimezone(ZoneInfo("Europe/London")).date()
        yesterday = (today - timedelta(days=1)).isoformat()
        tomorrow_at = (now + timedelta(days=1)).isoformat()
        now_text = now.isoformat()

        with platform_db.connect() as conn:
            stage_id = conn.execute("SELECT id FROM pipeline_stages WHERE kind='open' ORDER BY position LIMIT 1").fetchone()["id"]
            conn.execute(
                """INSERT INTO opportunities
                   (account_id,stage_id,title,value_minor,expected_close_date,next_action,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (account_id, stage_id, "Renewal rescue", 250_000, yesterday, "", now_text, now_text),
            )
            conn.execute(
                """INSERT INTO opportunities
                   (account_id,stage_id,title,value_minor,expected_close_date,next_action,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (account_id, stage_id, "Healthy expansion", 500_000, (today + timedelta(days=30)).isoformat(), "Call sponsor", now_text, now_text),
            )
            conn.execute(
                """INSERT INTO work_tasks(title,description,priority,due_at,created_at,updated_at)
                   VALUES (?,?,?,?,?,?)""",
                ("Send recovery plan", "The promised follow-up is late", "High", yesterday, now_text, now_text),
            )
            conn.execute(
                """INSERT INTO tender_notices
                   (title,buyer_name,deadline,dedupe_key,first_seen_at,last_seen_at,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                ("Library services framework", "City Library", (today + timedelta(days=3)).isoformat(), "today-tender", now_text, now_text, now_text, now_text),
            )
            conn.execute(
                """INSERT INTO calendar_events(title,location,starts_at,ends_at,created_at,updated_at)
                   VALUES (?,?,?,?,?,?)""",
                ("Commercial review", "Video call", tomorrow_at, (now + timedelta(days=1, hours=1)).isoformat(), now_text, now_text),
            )
            reply_thread = conn.execute(
                """INSERT INTO gmail_threads
                   (gmail_thread_id,subject,snippet,last_message_at,unread,message_count,created_at,updated_at)
                   VALUES (?,?,?,?,1,1,?,?)""",
                ("gmail-reply", "Re: recovery plan", "Can we meet tomorrow?", now_text, now_text, now_text),
            ).lastrowid
            conn.execute(
                """INSERT INTO gmail_messages
                   (thread_id,gmail_message_id,direction,from_email,subject,snippet,sent_at,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (reply_thread, "message-reply", "inbound", "buyer@example.com", "Re: recovery plan", "Can we meet tomorrow?", now_text, now_text, now_text),
            )
            outbound_thread = conn.execute(
                """INSERT INTO gmail_threads
                   (gmail_thread_id,subject,snippet,last_message_at,unread,message_count,created_at,updated_at)
                   VALUES (?,?,?,?,1,2,?,?)""",
                ("gmail-outbound", "Not a reply", "Our most recent message", now_text, now_text, now_text),
            ).lastrowid
            conn.execute(
                """INSERT INTO gmail_messages
                   (thread_id,gmail_message_id,direction,from_email,subject,sent_at,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (outbound_thread, "message-inbound-old", "inbound", "buyer@example.com", "Not a reply", (now - timedelta(hours=2)).isoformat(), now_text, now_text),
            )
            conn.execute(
                """INSERT INTO gmail_messages
                   (thread_id,gmail_message_id,direction,from_email,subject,sent_at,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (outbound_thread, "message-outbound-new", "outbound", "operator@example.com", "Not a reply", now_text, now_text, now_text),
            )
            conn.execute(
                """INSERT INTO projects(account_id,name,status,due_on,notes,created_at,updated_at)
                   VALUES (?,?,'Blocked',?,?,?,?)""",
                (account_id, "Onboarding rollout", (today + timedelta(days=5)).isoformat(), "Waiting for data access", now_text, now_text),
            )
            conn.execute(
                """INSERT INTO invoices
                   (account_id,status,due_on,customer_name,total_pence,paid_pence,created_at,updated_at)
                   VALUES (?,'Sent',?,?,?,?,?,?)""",
                (account_id, yesterday, "Signal Works", 12_000, 2_000, now_text, now_text),
            )
            conn.execute(
                """INSERT INTO client_success
                   (account_id,open_risks,renewal_on,created_at,updated_at)
                   VALUES (?,?,?,?,?)""",
                (account_id, 1, (today + timedelta(days=60)).isoformat(), now_text, now_text),
            )

        response = self.client.get("/api/v1/dashboard")
        self.assertEqual(200, response.status_code, response.text)
        dashboard = response.json()
        self.assertEqual(
            {
                "unread_replies": 1,
                "overdue_tasks": 1,
                "tender_deadlines": 1,
                "risky_deals": 1,
                "upcoming_meetings": 1,
                "blocked_projects": 1,
                "unpaid_invoices": 1,
                "overdue_invoices": 1,
                "renewals": 1,
            },
            {key: dashboard["counts"][key] for key in (
                "unread_replies", "overdue_tasks", "tender_deadlines", "risky_deals",
                "upcoming_meetings", "blocked_projects", "unpaid_invoices",
                "overdue_invoices", "renewals",
            )},
        )
        self.assertEqual(7, dashboard["counts"]["needs_action"])
        self.assertEqual(2, dashboard["open_deals"])
        self.assertEqual("Re: recovery plan", dashboard["unread_replies"][0]["title"])
        self.assertEqual("Renewal rescue", dashboard["deal_risks"][0]["title"])
        self.assertIn("Expected close was", dashboard["deal_risks"][0]["reason"])
        self.assertIn("Overdue since", dashboard["overdue_invoice_items"][0]["reason"])
        self.assertNotIn("Not a reply", [item["title"] for item in dashboard["unread_replies"]])

        signal_lists = (
            "unread_replies", "overdue_work", "tender_deadline_items", "deal_risks",
            "upcoming_meetings", "project_blockers", "unpaid_invoice_items", "renewals",
            "priorities", "risk_signals",
        )
        for list_name in signal_lists:
            self.assertLessEqual(len(dashboard[list_name]), 20)
            for item in dashboard[list_name]:
                self.assertTrue({"type", "id", "title", "reason", "route"}.issubset(item), (list_name, item))
                self.assertTrue(item["route"].startswith("/"), (list_name, item))

    def test_dashboard_tolerates_optional_modules_not_installed(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            platform_db.install_core_schema(conn)
            result = core_service.dashboard(conn)
        finally:
            conn.close()
        self.assertEqual(0, result["counts"]["unread_replies"])
        self.assertEqual(0, result["counts"]["blocked_projects"])
        self.assertEqual(0, result["counts"]["unpaid_invoices"])
        self.assertEqual(0, result["counts"]["renewals"])
        self.assertEqual([], result["unread_replies"])
        self.assertEqual([], result["project_blockers"])
        self.assertEqual([], result["unpaid_invoice_items"])
        self.assertEqual([], result["renewals"])


if __name__ == "__main__":
    unittest.main()
