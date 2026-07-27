import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import platform_db
from app.main import app


class CoreV1Tests(unittest.TestCase):
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

    def create_account(self, name="Northstar Council"):
        response = self.client.post("/api/v1/accounts", json={"name": name, "domain": "northstar.example"})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_complete_lead_qualification_and_pipeline_transition(self):
        lead = self.client.post(
            "/api/v1/leads",
            json={
                "title": "Estates modernization",
                "company": "Northstar Council",
                "email": "buyer@northstar.example",
                "estimated_value_minor": 125_000_00,
            },
        )
        self.assertEqual(lead.status_code, 201, lead.text)
        qualified = self.client.post(
            f"/api/v1/leads/{lead.json()['id']}/qualify",
            json={"account_name": "Northstar Council", "contact_name": "Alex Buyer", "contact_email": "buyer@northstar.example"},
        )
        self.assertEqual(qualified.status_code, 200, qualified.text)
        deal = qualified.json()
        self.assertEqual(deal["type"], "New business")
        won = next(stage for stage in self.client.get("/api/v1/pipeline/stages").json()["items"] if stage["name"] == "Won")
        transition = self.client.post(
            f"/api/v1/opportunities/{deal['id']}/transition",
            json={"version": deal["version"], "stage_id": won["id"]},
        )
        self.assertEqual(transition.status_code, 200, transition.text)
        self.assertEqual(transition.json()["status"], "Won")
        account = self.client.get(f"/api/v1/accounts/{deal['account_id']}").json()
        self.assertIn("client", account["roles"])

    def test_tender_dedupe_and_qualification(self):
        tender_payload = {
            "title": "Housing repairs framework",
            "buyer_name": "Northstar Council",
            "contract_url": "https://contracts.example/notices/123",
            "estimated_value_minor": 500_000_00,
            "source_urls": ["https://contracts.example/notices/123"],
        }
        first = self.client.post("/api/v1/tenders", json=tender_payload)
        second = self.client.post("/api/v1/tenders", json=tender_payload)
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(second.json()["seen_count"], 2)
        qualified = self.client.post(
            f"/api/v1/tenders/{first.json()['id']}/qualify",
            json={"account_name": "Northstar Council", "opportunity_title": "Housing repairs bid"},
        )
        self.assertEqual(qualified.status_code, 200, qualified.text)
        self.assertEqual(qualified.json()["type"], "Tender")

    def test_vat_requires_complete_approved_effective_configuration(self):
        account = self.create_account("VAT Customer")
        blocked_invoice = self.client.post(
            "/api/v1/invoices",
            json={
                "account_id": account["id"],
                "due_on": "2030-01-31",
                "customer_name": "VAT Customer",
                "lines": [
                    {
                        "description": "Taxable service",
                        "quantity": "1",
                        "unit_price_pence": 10_000,
                        "tax_rate_bps": 2_000,
                    }
                ],
            },
        )
        self.assertEqual(409, blocked_invoice.status_code, blocked_invoice.text)

        profile = self.client.get("/api/v1/settings/business").json()
        incomplete = self.client.patch(
            "/api/v1/settings/business",
            json={"version": profile["version"], "vat_registered": True, "vat_number": "GB123456789"},
        )
        self.assertEqual(422, incomplete.status_code, incomplete.text)
        enabled = self.client.patch(
            "/api/v1/settings/business",
            json={
                "version": profile["version"],
                "legal_name": "Test Business Ltd",
                "registered_address": {"line1": "1 Test Street", "city": "London"},
                "vat_registered": True,
                "vat_number": "GB123456789",
                "vat_scheme": "Standard",
                "vat_effective_from": "2000-01-01",
                "tax_codes_approved": True,
            },
        )
        self.assertEqual(200, enabled.status_code, enabled.text)
        self.assertTrue(enabled.json()["tax_codes_approved"])
        integrity = self.client.post("/api/v1/settings/integrity", json={})
        self.assertEqual(200, integrity.status_code, integrity.text)
        self.assertEqual("integrity_check", integrity.json()["check"])
        self.assertEqual("ok", integrity.json()["database"])

    def test_optimistic_conflict_and_blank_validation(self):
        blank = self.client.post("/api/v1/accounts", json={"name": "   "})
        self.assertEqual(blank.status_code, 422)
        account = self.create_account()
        first = self.client.patch(
            f"/api/v1/accounts/{account['id']}",
            json={"version": account["version"], "notes": "First edit"},
        )
        self.assertEqual(first.status_code, 200, first.text)
        stale = self.client.patch(
            f"/api/v1/accounts/{account['id']}",
            json={"version": account["version"], "notes": "Stale edit"},
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["code"], "conflict")
        self.assertEqual(first.json()["version"], stale.json()["current_version"])
        self.assertEqual("First edit", stale.json()["current_record"]["notes"])

    def test_dashboard_search_and_relationship_workspace(self):
        account = self.create_account()
        contact = self.client.post(
            "/api/v1/contacts",
            json={"account_id": account["id"], "display_name": "Sam Decision", "email": "sam@northstar.example"},
        )
        self.assertEqual(contact.status_code, 201, contact.text)
        task = self.client.post(
            "/api/v1/tasks",
            json={"entity_type": "account", "entity_id": account["id"], "title": "Prepare proposal", "priority": "High"},
        )
        self.assertEqual(task.status_code, 201, task.text)
        workspace = self.client.get(f"/api/v1/accounts/{account['id']}")
        self.assertEqual(workspace.status_code, 200, workspace.text)
        self.assertEqual(len(workspace.json()["contacts"]), 1)
        self.assertEqual(len(workspace.json()["tasks"]), 1)
        search = self.client.get("/api/v1/search", params={"q": "Northstar"})
        self.assertEqual(search.status_code, 200, search.text)
        self.assertTrue(any(item["entity_type"] == "account" for item in search.json()["items"]))
        dashboard = self.client.get("/api/v1/dashboard")
        self.assertEqual(dashboard.status_code, 200, dashboard.text)
        self.assertEqual(dashboard.json()["open_tasks"], 1)

    def test_contact_and_task_filters_reset_cleanly_across_cursor_pages(self):
        account = self.create_account("Paging Account")
        for name, status in (("Alex Active", "Active"), ("Blair Inactive", "Inactive")):
            created = self.client.post(
                "/api/v1/contacts",
                json={"account_id": account["id"], "display_name": name, "status": status},
            )
            self.assertEqual(201, created.status_code, created.text)
        active = self.client.get("/api/v1/contacts", params={"q": "Alex", "status": "Active"}).json()
        self.assertEqual(["Alex Active"], [item["display_name"] for item in active["items"]])
        first_page = self.client.get("/api/v1/contacts", params={"limit": 1}).json()
        second_page = self.client.get(
            "/api/v1/contacts",
            params={"limit": 1, "cursor": first_page["next_cursor"]},
        ).json()
        self.assertNotEqual(first_page["items"][0]["id"], second_page["items"][0]["id"])

        self.client.post("/api/v1/tasks", json={"title": "Prepare proposal", "description": "Northstar"})
        self.client.post("/api/v1/tasks", json={"title": "Call supplier", "description": "Logistics"})
        matching_tasks = self.client.get("/api/v1/tasks", params={"q": "Northstar"}).json()
        self.assertEqual(["Prepare proposal"], [item["title"] for item in matching_tasks["items"]])

    def test_archived_contacts_tags_saved_views_and_custom_field_definitions(self):
        account = self.create_account("Control Surface Ltd")
        contact_response = self.client.post(
            "/api/v1/contacts",
            json={"account_id": account["id"], "display_name": "Morgan Control", "email": "morgan@northstar.example"},
        )
        self.assertEqual(201, contact_response.status_code, contact_response.text)
        contact = contact_response.json()

        tag = self.client.post("/api/v1/tags", json={"name": "Decision maker", "color": "cyan"})
        self.assertEqual(201, tag.status_code, tag.text)
        assigned = self.client.put(f"/api/v1/contact/{contact['id']}/tags", json=[tag.json()["id"]])
        self.assertEqual(200, assigned.status_code, assigned.text)
        current = self.client.get(f"/api/v1/contact/{contact['id']}/tags")
        self.assertEqual(["Decision maker"], [item["name"] for item in current.json()["items"]])

        field = self.client.post(
            "/api/v1/custom-fields",
            json={"entity_type": "contact", "name": "Buying committee", "field_type": "select", "options": ["Sponsor", "Evaluator"]},
        )
        self.assertEqual(201, field.status_code, field.text)
        self.assertEqual("Buying committee", self.client.get("/api/v1/custom-fields", params={"entity_type": "contact"}).json()["items"][0]["name"])

        view = self.client.post(
            "/api/v1/saved-views",
            json={"entity_type": "contacts", "name": "Active sponsors", "config": {"status": "Active", "columns": ["Account", "Email"]}},
        )
        self.assertEqual(201, view.status_code, view.text)
        self.assertEqual(["Account", "Email"], self.client.get("/api/v1/saved-views", params={"entity_type": "contacts"}).json()["items"][0]["config"]["columns"])

        archived = self.client.post(
            f"/api/v1/contacts/{contact['id']}/archive",
            json={"version": contact["version"]},
        )
        self.assertEqual(200, archived.status_code, archived.text)
        self.assertEqual([], self.client.get("/api/v1/contacts").json()["items"])
        included = self.client.get("/api/v1/contacts", params={"include_archived": True}).json()["items"]
        self.assertEqual(contact["id"], included[0]["id"])
        self.assertIsNotNone(included[0]["archived_at"])
        self.assertEqual(200, self.client.get(f"/api/v1/contacts/{contact['id']}").status_code)

        stale = self.client.post(f"/api/v1/contacts/{contact['id']}/restore", json={"version": contact["version"]})
        self.assertEqual(409, stale.status_code, stale.text)
        restored = self.client.post(
            f"/api/v1/contacts/{contact['id']}/restore",
            json={"version": archived.json()["version"]},
        )
        self.assertEqual(200, restored.status_code, restored.text)
        self.assertIsNone(restored.json()["archived_at"])

    def test_merge_rejects_stale_source_or_target_versions(self):
        source = self.create_account("Merge Source")
        target_response = self.client.post("/api/v1/accounts", json={"name": "Merge Target", "domain": "merge-target.example"})
        self.assertEqual(201, target_response.status_code, target_response.text)
        target = target_response.json()
        edited = self.client.patch(
            f"/api/v1/accounts/{target['id']}",
            json={"version": target["version"], "notes": "Changed after selection"},
        )
        self.assertEqual(200, edited.status_code, edited.text)
        stale = self.client.post(
            "/api/v1/accounts/merge",
            json={
                "source_id": source["id"],
                "target_id": target["id"],
                "source_version": source["version"],
                "target_version": target["version"],
            },
        )
        self.assertEqual(409, stale.status_code, stale.text)
        merged = self.client.post(
            "/api/v1/accounts/merge",
            json={
                "source_id": source["id"],
                "target_id": target["id"],
                "source_version": source["version"],
                "target_version": edited.json()["version"],
            },
        )
        self.assertEqual(200, merged.status_code, merged.text)
        self.assertEqual(target["id"], merged.json()["id"])


if __name__ == "__main__":
    unittest.main()
