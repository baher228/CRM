import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app import platform_db
from app.main import app


class FullLifecycleV1AcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.previous_environment = {
            name: os.environ.get(name)
            for name in ("CRM_DB_PATH", "CRM_DATA_DIR", "CRM_INTEGRATIONS_FAKE")
        }
        os.environ["CRM_DATA_DIR"] = self.tempdir.name
        os.environ["CRM_DB_PATH"] = str(Path(self.tempdir.name) / "crm.sqlite3")
        os.environ["CRM_INTEGRATIONS_FAKE"] = "true"
        platform_db.reset_bootstrap_for_tests()
        self.client_context = TestClient(app, headers={"X-CRM-Confirmed": "true"})
        self.client = self.client_context.__enter__()
        with platform_db.connect() as conn:
            conn.execute(
                "UPDATE business_profile SET legal_name='Test Business Ltd', vat_registered=1, "
                "vat_number='GB123456789', vat_scheme='Standard', vat_effective_from='2000-01-01', "
                "tax_codes_approved=1 WHERE id=1"
            )

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        platform_db.reset_bootstrap_for_tests()
        for name, value in self.previous_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tempdir.cleanup()

    def json(self, method, path, *, status=200, **kwargs):
        response = self.client.request(method, path, **kwargs)
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def action(self, path, key, payload=None, *, status=200):
        return self.json(
            "POST",
            path,
            status=status,
            headers={"Idempotency-Key": key},
            json=payload,
        )

    def transition(self, opportunity_id, stage_name, stages):
        opportunity = self.json("GET", f"/api/v1/opportunities/{opportunity_id}")
        return self.json(
            "POST",
            f"/api/v1/opportunities/{opportunity_id}/transition",
            json={"version": opportunity["version"], "stage_id": stages[stage_name]["id"]},
        )

    def test_tender_to_paid_invoice_and_renewal(self):
        today = date.today()
        tender = self.json(
            "POST",
            "/api/v1/tenders",
            status=201,
            json={
                "title": "Digital service delivery framework",
                "buyer_name": "Northstar Council",
                "portal_name": "Contracts Finder",
                "notice_reference": "CF-ACCEPTANCE-001",
                "contract_url": "https://contracts.example/notices/acceptance-001",
                "estimated_value_minor": 2_400_000,
                "deadline": (today + timedelta(days=21)).isoformat(),
                "source_urls": ["https://contracts.example/notices/acceptance-001"],
            },
        )
        opportunity = self.json(
            "POST",
            f"/api/v1/tenders/{tender['id']}/qualify",
            json={
                "account_name": "Northstar Council",
                "contact_name": "Alex Morgan",
                "contact_email": "alex@northstar.example",
                "opportunity_title": "Northstar digital delivery",
                "next_action": "Prepare discovery workshop",
            },
        )
        self.assertEqual(opportunity["type"], "Tender")
        self.assertEqual(opportunity["tender_id"], tender["id"])
        account_id, opportunity_id = opportunity["account_id"], opportunity["id"]

        stages = {stage["name"]: stage for stage in self.json("GET", "/api/v1/pipeline/stages")["items"]}
        for stage_name in ("Qualified", "Proposal"):
            opportunity = self.transition(opportunity_id, stage_name, stages)
            self.assertEqual(opportunity["stage_name"], stage_name)

        proposal = self.json(
            "POST",
            "/api/v1/proposals",
            status=201,
            json={
                "account_id": account_id,
                "opportunity_id": opportunity_id,
                "title": "Northstar digital delivery",
                "valid_until": (today + timedelta(days=14)).isoformat(),
                "lines": [
                    {
                        "description": "Discovery and implementation",
                        "quantity": "2",
                        "unit_price_pence": 10_000,
                        "tax_rate_bps": 2_000,
                    }
                ],
            },
        )
        proposal = self.action(f"/api/v1/proposals/{proposal['id']}/send", "lifecycle-proposal-send")
        accepted = self.action(f"/api/v1/proposals/{proposal['id']}/accept", "lifecycle-proposal-accept")
        self.assertEqual(accepted["proposal"]["status"], "Accepted")
        contract = accepted["contract"]
        opportunity = self.json("GET", f"/api/v1/opportunities/{opportunity_id}")
        self.assertEqual(opportunity["stage_name"], "Contract")
        contract = self.action(f"/api/v1/contracts/{contract['id']}/send", "lifecycle-contract-send")
        contract = self.action(f"/api/v1/contracts/{contract['id']}/sign", "lifecycle-contract-sign", {})
        contract = self.action(f"/api/v1/contracts/{contract['id']}/activate", "lifecycle-contract-activate")
        self.assertEqual(contract["status"], "Active")

        opportunity = self.transition(opportunity_id, "Won", stages)
        self.assertEqual(opportunity["status"], "Won")
        opportunity = self.transition(opportunity_id, "Won", stages)
        account = self.json("GET", f"/api/v1/accounts/{account_id}")
        self.assertIn("client", account["roles"])

        projects = self.json("GET", "/api/v1/projects")["items"]
        won_projects = [item for item in projects if item["opportunity_id"] == opportunity_id]
        self.assertEqual(len(won_projects), 1)
        project = won_projects[0]
        contract_project = self.action(
            f"/api/v1/contracts/{contract['id']}/project",
            "lifecycle-project-create",
            status=201,
        )
        self.assertEqual(contract_project["id"], project["id"])
        project = self.json(
            "PATCH",
            f"/api/v1/projects/{project['id']}",
            json={"version": project["version"], "status": "Active", "billing_type": "hourly"},
        )
        self.json(
            "POST",
            f"/api/v1/projects/{project['id']}/milestones",
            status=201,
            json={"title": "Discovery complete", "amount_pence": 8_000, "status": "Complete"},
        )
        self.json(
            "POST",
            "/api/v1/time-entries",
            status=201,
            json={
                "project_id": project["id"],
                "entry_date": today.isoformat(),
                "minutes": 120,
                "description": "Discovery workshop",
                "hourly_rate_pence": 7_500,
            },
        )
        self.json(
            "POST",
            "/api/v1/expenses",
            status=201,
            json={
                "project_id": project["id"],
                "account_id": account_id,
                "expense_date": today.isoformat(),
                "vendor": "Travel Co",
                "description": "Workshop travel",
                "net_pence": 1_000,
                "tax_rate_bps": 2_000,
                "billable": True,
            },
        )
        project_detail = self.json("GET", f"/api/v1/projects/{project['id']}")
        self.assertEqual(project_detail["time_minutes"], 120)
        self.assertEqual(project_detail["expense_pence"], 1_200)

        invoice = self.json(
            "POST",
            "/api/v1/invoices",
            status=201,
            json={
                "account_id": account_id,
                "project_id": project["id"],
                "contract_id": contract["id"],
                "due_on": (today + timedelta(days=14)).isoformat(),
                "customer_name": "Northstar Council",
                "customer_address": "1 Civic Square\nLondon",
                "lines": [
                    {
                        "description": "Discovery and implementation",
                        "quantity": "2",
                        "unit_price_pence": 10_000,
                        "tax_rate_bps": 2_000,
                    }
                ],
            },
        )
        issued = self.action(f"/api/v1/invoices/{invoice['id']}/issue", "lifecycle-invoice-issue")
        self.assertEqual((issued["net_pence"], issued["vat_pence"], issued["total_pence"]), (20_000, 4_000, 24_000))
        self.assertEqual(issued["status"], "Sent")
        self.assertEqual(len(issued["pdf_sha256"]), 64)
        self.assertTrue(Path(issued["pdf_path"]).is_file())
        pdf = self.client.get(f"/api/v1/invoices/{invoice['id']}/pdf")
        self.assertEqual(pdf.status_code, 200, pdf.text)
        self.assertTrue(pdf.content.startswith(b"%PDF"))

        first_payment = self.action(
            "/api/v1/payments",
            "lifecycle-payment-partial",
            {
                "amount_pence": 9_000,
                "invoice_id": invoice["id"],
                "method": "stripe",
                "reference": "pi_acceptance_partial",
            },
            status=201,
        )
        self.assertEqual(first_payment["allocated_pence"], 9_000)
        part_paid = self.json("GET", f"/api/v1/invoices/{invoice['id']}")
        self.assertEqual((part_paid["status"], part_paid["outstanding_pence"]), ("Part-paid", 15_000))

        self.action(
            "/api/v1/payments",
            "lifecycle-payment-final",
            {
                "amount_pence": 15_000,
                "invoice_id": invoice["id"],
                "method": "bank",
                "reference": "bank_acceptance_final",
            },
            status=201,
        )
        paid = self.json("GET", f"/api/v1/invoices/{invoice['id']}")
        self.assertEqual((paid["status"], paid["outstanding_pence"]), ("Paid", 0))

        renewal_on = today + timedelta(days=60)
        success = self.json(
            "PUT",
            f"/api/v1/client-success/{account_id}",
            json={
                "account_id": account_id,
                "onboarding_status": "Complete",
                "next_review_on": (today + timedelta(days=30)).isoformat(),
                "renewal_on": renewal_on.isoformat(),
                "notes": "Operational handover complete",
            },
        )
        self.assertEqual(success["computed_health"], "Healthy")
        self.assertEqual(success["account_name"], "Northstar Council")
        renewals = self.json("GET", "/api/v1/reports/renewals", params={"days": 90})
        self.assertEqual([item["account_id"] for item in renewals["items"]], [account_id])
        processed = self.action(
            "/api/v1/client-success/renewals/process",
            "lifecycle-renewals-process",
            {"days": 90},
        )
        self.assertEqual((processed["created_count"], processed["existing_count"]), (1, 0))
        renewal = processed["items"][0]
        self.assertEqual((renewal["type"], renewal["account_id"]), ("Renewal", account_id))
        cached = self.action(
            "/api/v1/client-success/renewals/process",
            "lifecycle-renewals-process",
            {"days": 90},
        )
        self.assertEqual(cached, processed)
        repeated = self.action(
            "/api/v1/client-success/renewals/process",
            "lifecycle-renewals-process-again",
            {"days": 90},
        )
        self.assertEqual((repeated["created_count"], repeated["existing_count"]), (0, 1))
        opportunities = self.json("GET", "/api/v1/opportunities")["items"]
        renewal_deals = [
            item for item in opportunities
            if item["account_id"] == account_id and item["type"] == "Renewal"
        ]
        self.assertEqual(len(renewal_deals), 1)

        ledger = self.json("GET", "/api/v1/ledger")
        self.assertGreaterEqual(len(ledger["items"]), 5)
        for journal in ledger["items"]:
            self.assertEqual(journal["debit_pence"], journal["credit_pence"])
        finance = self.json("GET", "/api/v1/reports/finance")
        self.assertEqual((finance["collected_pence"], finance["outstanding_pence"]), (24_000, 0))


if __name__ == "__main__":
    unittest.main()
