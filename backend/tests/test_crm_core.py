import os
import tempfile
import unittest
from datetime import date

from fastapi.testclient import TestClient

from app.main import app
from app.services import crm_store, emails_service


class CrmCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CRM_DB_PATH"] = os.path.join(self.tmp.name, "crm.sqlite3")
        os.environ["CRM_INCLUDE_DEMO_DATA"] = "false"
        os.environ["CRM_INCLUDE_DEMO_LEADS"] = "false"
        crm_store.reset_for_tests()
        self.client = TestClient(app)

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("CRM_DB_PATH", None)
        for name in ["MAIL_IMAP_HOST", "MAIL_IMAP_PORT", "MAIL_IMAP_USERNAME", "MAIL_IMAP_PASSWORD", "MAIL_IMAP_FOLDER", "MAIL_IMAP_USE_SSL"]:
            os.environ.pop(name, None)

    def test_contact_lead_task_note_search_dashboard_flow(self):
        contact_response = self.client.post(
            "/api/clients",
            json={
                "name": "Ada Lovelace",
                "company": "Analytical Engines Ltd",
                "email": "ada@example.com",
                "source": "Referral",
                "next_action": "Send proposal",
            },
        )
        self.assertEqual(contact_response.status_code, 200)
        contact = contact_response.json()

        lead_response = self.client.post(
            "/api/leads",
            json={
                "name": "Analytics tender",
                "company": "Analytical Engines Ltd",
                "source": "Manual",
                "estimated_value": 50000,
                "next_action": "Qualify",
            },
        )
        self.assertEqual(lead_response.status_code, 200)
        lead = lead_response.json()

        task_response = self.client.post(
            "/api/tasks",
            json={
                "title": "Call Ada",
                "due_date": date.today().isoformat(),
                "related_type": "client",
                "related_id": contact["id"],
                "related_to": contact["name"],
                "priority": "High",
            },
        )
        self.assertEqual(task_response.status_code, 200)

        note_response = self.client.post(
            "/api/notes",
            json={"related_type": "lead", "related_id": lead["id"], "body": "Strong fit for analytics work."},
        )
        self.assertEqual(note_response.status_code, 200)

        calendar_response = self.client.post(
            "/api/calendar",
            json={
                "title": "Analytics review",
                "date": date.today().isoformat(),
                "start_time": "10:00:00",
                "end_time": "10:30:00",
                "related_to": contact["company"],
            },
        )
        self.assertEqual(calendar_response.status_code, 200)
        events = self.client.get("/api/events").json()
        self.assertTrue(any(event["title"] == "Analytics review" for event in events))

        search = self.client.get("/api/search", params={"q": "analytics"}).json()
        self.assertTrue(any(item["type"] == "lead" for item in search))

        dashboard = self.client.get("/api/dashboard").json()
        self.assertGreaterEqual(dashboard["open_leads"], 1)
        self.assertGreaterEqual(dashboard["upcoming_calendar"], 0)

        activity = self.client.get(f"/api/activity/lead/{lead['id']}").json()
        self.assertEqual(activity[0]["type"], "note")

    def test_bulk_lead_update(self):
        created = self.client.post("/api/leads", json={"name": "Bulk lead", "company": "Bulk Co"}).json()

        response = self.client.post(
            "/api/leads/bulk",
            json={"lead_ids": [created["id"]], "action": "status", "status": "Reviewing"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["updated"], 1)
        self.assertEqual(payload["leads"][0]["status"], "Reviewing")

    def test_email_requires_mail_configuration(self):
        response = self.client.get("/api/emails")
        self.assertEqual(response.status_code, 503)
        self.assertIn("MAIL_IMAP_HOST", response.json()["detail"])

    def test_email_imap_failure_keeps_cors_response(self):
        os.environ["MAIL_IMAP_HOST"] = "imap.example.com"
        os.environ["MAIL_IMAP_USERNAME"] = "user@example.com"
        os.environ["MAIL_IMAP_PASSWORD"] = "secret"
        original_connection = emails_service.imaplib.IMAP4_SSL

        class FailingMailbox:
            def __init__(self, *_args, **_kwargs):
                raise emails_service.imaplib.IMAP4.error("login failed")

        try:
            emails_service.imaplib.IMAP4_SSL = FailingMailbox
            response = self.client.get("/api/emails", headers={"Origin": "http://127.0.0.1:5173"})
        finally:
            emails_service.imaplib.IMAP4_SSL = original_connection

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://127.0.0.1:5173")
        self.assertIn("Could not load mailbox", response.json()["detail"])
        self.assertIn("login failed", response.json()["detail"])

    def test_can_save_mail_settings(self):
        response = self.client.post(
            "/api/settings/mail",
            json={
                "host": "imap.example.com",
                "port": 993,
                "username": "user@example.com",
                "password": "app-password",
                "folder": "INBOX",
                "use_ssl": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["configured"])
        self.assertTrue(payload["password_saved"])
        self.assertNotIn("app-password", str(payload))

        saved = self.client.get("/api/settings/mail").json()
        self.assertEqual(saved["host"], "imap.example.com")
        self.assertEqual(saved["username"], "user@example.com")

    def test_settings_reports_mail_configuration(self):
        missing = self.client.get("/api/settings/health").json()
        mail = next(item for item in missing["integrations"] if item["name"] == "Mail")
        self.assertFalse(mail["configured"])

        os.environ["MAIL_IMAP_HOST"] = "imap.example.com"
        os.environ["MAIL_IMAP_USERNAME"] = "user@example.com"
        os.environ["MAIL_IMAP_PASSWORD"] = "secret"
        configured = self.client.get("/api/settings/health").json()
        mail = next(item for item in configured["integrations"] if item["name"] == "Mail")
        self.assertTrue(mail["configured"])


if __name__ == "__main__":
    unittest.main()
