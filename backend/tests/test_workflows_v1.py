import os
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import platform_db
from app.workflows_v1 import DiscoveryCoordinator, create_router, wait_for_discovery


class WorkflowV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["CRM_DB_PATH"] = str(Path(self.tempdir.name) / "crm.sqlite3")
        os.environ["CRM_DISCOVERY_FAKE"] = "true"
        os.environ["CRM_DISCOVERY_FAKE_DELAY_MS"] = "10"
        platform_db.reset_bootstrap_for_tests()
        self.coordinator = DiscoveryCoordinator()
        app = FastAPI()
        app.include_router(create_router(coordinator=self.coordinator), prefix="/api/v1")
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        # Cancellation is cooperative; let a fake runner observe it before its
        # temporary SQLite file is removed.
        for thread in list(self.coordinator._threads.values()):
            thread.join(timeout=1)
        platform_db.reset_bootstrap_for_tests()
        os.environ.pop("CRM_DB_PATH", None)
        os.environ.pop("CRM_DISCOVERY_FAKE", None)
        os.environ.pop("CRM_DISCOVERY_FAKE_DELAY_MS", None)
        self.tempdir.cleanup()

    def test_durable_discovery_cancel_and_idempotent_tender_import(self) -> None:
        headers = {"Idempotency-Key": "discover-estates-1"}
        started = self.client.post(
            "/api/v1/discovery/runs",
            headers=headers,
            json={"niche": "estate maintenance", "region": "North West", "limit": 3},
        )
        self.assertEqual(202, started.status_code, started.text)
        duplicate = self.client.post(
            "/api/v1/discovery/runs",
            headers=headers,
            json={"niche": "estate maintenance", "region": "North West", "limit": 3},
        )
        self.assertEqual(started.json()["id"], duplicate.json()["id"])
        run = wait_for_discovery(self.coordinator, started.json()["id"])
        self.assertEqual("completed", run["state"])
        self.assertEqual(1, len(run["results"]))

        imported = self.client.post(
            f"/api/v1/discovery/runs/{run['id']}/import",
            headers={"Idempotency-Key": "import-estates-1"},
        )
        replay = self.client.post(
            f"/api/v1/discovery/runs/{run['id']}/import",
            headers={"Idempotency-Key": "import-estates-1"},
        )
        self.assertEqual(200, imported.status_code, imported.text)
        self.assertEqual(imported.json(), replay.json())
        self.assertEqual(1, imported.json()["imported"])

        relink = self.client.post(
            f"/api/v1/discovery/runs/{run['id']}/import",
            headers={"Idempotency-Key": "import-estates-2"},
        )
        self.assertEqual(1, relink.json()["already_imported"])
        with platform_db.connect() as conn:
            tender = conn.execute("SELECT seen_count, discovery_run_id FROM tender_notices").fetchone()
            self.assertEqual(1, tender["seen_count"])
            self.assertEqual(run["id"], tender["discovery_run_id"])
            durable = conn.execute(
                "SELECT state, result_json FROM discovery_runs WHERE id = ?", (run["id"],)
            ).fetchone()
            self.assertEqual("completed", durable["state"])
            self.assertIn("Estate Maintenance", durable["result_json"])

        os.environ["CRM_DISCOVERY_FAKE_DELAY_MS"] = "100"
        cancel_started = self.client.post(
            "/api/v1/discovery/runs",
            headers={"Idempotency-Key": "discover-cancel-1"},
            json={"niche": "cancel me"},
        )
        cancelled = self.client.post(
            f"/api/v1/discovery/runs/{cancel_started.json()['id']}/cancel",
            headers={"Idempotency-Key": "cancel-1"},
        )
        self.assertEqual("cancelled", cancelled.json()["state"])

    def test_csv_preview_commit_dedupe_and_csv_json_exports(self) -> None:
        accounts = {
            "entity_type": "accounts",
            "filename": "accounts.csv",
            "csv_text": (
                "Organisation,Website Domain,Billing Email\n"
                "Northstar Council,northstar.example,finance@northstar.example\n"
                "Northstar duplicate,NORTHSTAR.EXAMPLE,other@northstar.example\n"
                ",,\n"
            ),
            # Source-to-target mappings are accepted (target-to-source works too).
            "mapping": {
                "Organisation": "name",
                "Website Domain": "domain",
                "Billing Email": "billing_email",
            },
        }
        preview = self.client.post("/api/v1/imports/csv/preview", json=accounts)
        self.assertEqual(200, preview.status_code, preview.text)
        self.assertEqual(1, preview.json()["create_count"])
        self.assertEqual(1, preview.json()["duplicate_count"])
        self.assertEqual(1, preview.json()["error_count"])
        with platform_db.connect() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0])

        headers = {"Idempotency-Key": "accounts-import-1"}
        committed = self.client.post("/api/v1/imports/csv/commit", headers=headers, json=accounts)
        replay = self.client.post("/api/v1/imports/csv/commit", headers=headers, json=accounts)
        self.assertEqual(200, committed.status_code, committed.text)
        self.assertEqual(committed.json(), replay.json())
        self.assertEqual(1, committed.json()["created_count"])

        contact = {
            "entity_type": "contacts",
            "csv_text": "display_name,email,account_name\nAlex Buyer,alex@northstar.example,Northstar Council\n",
        }
        lead = {
            "entity_type": "leads",
            "csv_text": "title,company,email,estimated_value_minor\nHeating programme,Northstar Council,alex@northstar.example,2500000\n",
        }
        opportunity = {
            "entity_type": "opportunities",
            "csv_text": "title,account_name,contact_email,stage_name,value_minor\nHeating bid,Northstar Council,alex@northstar.example,Qualified,2500000\n",
        }
        for key, payload in (
            ("contacts-import-1", contact),
            ("leads-import-1", lead),
            ("opportunities-import-1", opportunity),
        ):
            response = self.client.post(
                "/api/v1/imports/csv/commit",
                headers={"Idempotency-Key": key},
                json=payload,
            )
            self.assertEqual(200, response.status_code, response.text)
            self.assertEqual(1, response.json()["created_count"])

        json_export = self.client.get("/api/v1/exports/accounts.json")
        self.assertEqual(200, json_export.status_code, json_export.text)
        self.assertEqual("Northstar Council", json_export.json()["items"][0]["name"])
        self.assertEqual(["prospect"], json_export.json()["items"][0]["roles"])
        csv_export = self.client.get("/api/v1/exports/contacts.csv")
        self.assertEqual(200, csv_export.status_code, csv_export.text)
        self.assertIn("Alex Buyer", csv_export.text)
        self.assertIn("attachment;", csv_export.headers["content-disposition"])


if __name__ == "__main__":
    unittest.main()
