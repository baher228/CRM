import os
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.operations import install_schema, router
from app.platform_db import connect, reset_bootstrap_for_tests


class OperationsV1Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CRM_DB_PATH"] = os.path.join(self.tmp.name, "operations.sqlite3")
        reset_bootstrap_for_tests()
        with connect() as conn:
            conn.execute(
                "UPDATE business_profile SET legal_name='Test Business Ltd', vat_registered=1, "
                "vat_number='GB123456789', vat_scheme='Standard', vat_effective_from='2000-01-01', "
                "tax_codes_approved=1 WHERE id=1"
            )
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        self.client = TestClient(app, headers={"X-CRM-Confirmed": "true"})
        self.headers = {"Idempotency-Key": "test-key"}

    def tearDown(self):
        self.client.close()
        reset_bootstrap_for_tests()
        os.environ.pop("CRM_DB_PATH", None)
        self.tmp.cleanup()

    def _action(self, path, key, json=None):
        return self.client.post(path, headers={"Idempotency-Key": key}, json=json)

    def _expense_journals(self, expense_id):
        return [
            item
            for item in self.client.get("/api/v1/ledger?limit=100").json()["items"]
            if item["source_id"] == expense_id and item["source_type"].startswith("expense")
        ]

    def test_delivery_records_and_optimistic_versioning(self):
        project = self.client.post(
            "/api/v1/projects",
            json={"name": "Launch", "account_id": 1, "billing_type": "hourly", "budget_pence": 100_000},
        ).json()
        self.assertEqual(project["version"], 1)

        changed = self.client.patch(
            f"/api/v1/projects/{project['id']}", json={"version": 1, "status": "Active"}
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["version"], 2)
        stale = self.client.patch(
            f"/api/v1/projects/{project['id']}", json={"version": 1, "status": "Blocked"}
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["code"], "conflict")
        self.assertEqual(2, stale.json()["current_version"])
        self.assertEqual("Active", stale.json()["current_record"]["status"])

        milestone = self.client.post(
            f"/api/v1/projects/{project['id']}/milestones",
            json={"title": "Go live", "amount_pence": 25_000},
        )
        self.assertEqual(milestone.status_code, 201)
        entry = self.client.post(
            "/api/v1/time-entries",
            json={"project_id": project["id"], "entry_date": date.today().isoformat(), "minutes": 90, "hourly_rate_pence": 8_000},
        )
        self.assertEqual(entry.status_code, 201)
        expense = self.client.post(
            "/api/v1/expenses",
            json={
                "project_id": project["id"],
                "expense_date": date.today().isoformat(),
                "description": "Hosting",
                "net_pence": 101,
                "tax_rate_bps": 2_000,
            },
        )
        self.assertEqual(expense.status_code, 201)
        self.assertEqual(expense.json()["vat_pence"], 20)

        detail = self.client.get(f"/api/v1/projects/{project['id']}").json()
        self.assertEqual(detail["time_minutes"], 90)
        self.assertEqual(detail["expense_pence"], 121)
        self.assertEqual(len(detail["milestones"]), 1)

    def test_expense_journals_create_adjust_and_reverse_once(self):
        expense = self.client.post(
            "/api/v1/expenses",
            json={
                "expense_date": date.today().isoformat(),
                "description": "Software",
                "net_pence": 101,
                "tax_rate_bps": 2_000,
            },
        ).json()

        journals = self._expense_journals(expense["id"])
        self.assertEqual([journal["source_type"] for journal in journals], ["expense"])
        self.assertEqual(
            {line["account_code"]: (line["debit_pence"], line["credit_pence"]) for line in journals[0]["lines"]},
            {"1200": (0, 121), "1300": (20, 0), "5000": (101, 0)},
        )

        changed = self.client.patch(
            f"/api/v1/expenses/{expense['id']}",
            json={"version": 1, "net_pence": 201},
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["version"], 2)
        stale = self.client.patch(
            f"/api/v1/expenses/{expense['id']}",
            json={"version": 1, "net_pence": 301},
        )
        self.assertEqual(stale.status_code, 409)

        journals = self._expense_journals(expense["id"])
        self.assertEqual(len(journals), 2)
        adjustment = journals[1]
        self.assertEqual(adjustment["source_type"], "expense_adjustment:2")
        self.assertEqual(
            {line["account_code"]: (line["debit_pence"], line["credit_pence"]) for line in adjustment["lines"]},
            {"1200": (0, 120), "1300": (20, 0), "5000": (100, 0)},
        )

        archived = self.client.post(
            f"/api/v1/expenses/{expense['id']}/archive", json={"version": 2}
        )
        self.assertEqual(archived.status_code, 200)
        self.assertEqual(archived.json()["version"], 3)
        stale_archive = self.client.post(
            f"/api/v1/expenses/{expense['id']}/archive", json={"version": 2}
        )
        self.assertEqual(stale_archive.status_code, 409)

        journals = self._expense_journals(expense["id"])
        self.assertEqual(len(journals), 3)
        reversal = journals[2]
        self.assertEqual(reversal["source_type"], "expense_reversal:3")
        self.assertEqual(
            {line["account_code"]: (line["debit_pence"], line["credit_pence"]) for line in reversal["lines"]},
            {"1200": (241, 0), "1300": (0, 40), "5000": (0, 201)},
        )
        for journal in journals:
            self.assertEqual(journal["debit_pence"], journal["credit_pence"])

    def test_expense_vat_is_not_reclaimed_when_vat_is_disabled(self):
        with connect() as conn:
            conn.execute("UPDATE business_profile SET vat_registered = 0 WHERE id = 1")
        expense = self.client.post(
            "/api/v1/expenses",
            json={
                "expense_date": date.today().isoformat(),
                "description": "Pre-registration cost",
                "net_pence": 100,
                "tax_rate_bps": 2_000,
            },
        ).json()
        self.assertEqual((expense["net_pence"], expense["vat_pence"], expense["total_pence"]), (100, 20, 120))
        journal = self._expense_journals(expense["id"])[0]
        self.assertEqual(
            {line["account_code"]: (line["debit_pence"], line["credit_pence"]) for line in journal["lines"]},
            {"1200": (0, 120), "5000": (120, 0)},
        )
        self.assertEqual(journal["debit_pence"], journal["credit_pence"])
        self.assertEqual(self.client.get("/api/v1/reports/finance").json()["vat"]["input_pence"], 0)

    def test_proposal_contract_project_lifecycle(self):
        proposal = self.client.post(
            "/api/v1/proposals",
            json={
                "account_id": 7,
                "title": "Analytics rollout",
                "lines": [{"description": "Implementation", "quantity": "3", "unit_price_pence": 101, "tax_rate_bps": 2_000}],
            },
        ).json()
        self.assertEqual((proposal["net_pence"], proposal["vat_pence"], proposal["total_pence"]), (303, 61, 364))

        sent = self._action(f"/api/v1/proposals/{proposal['id']}/send", "proposal-send")
        self.assertEqual(sent.status_code, 200)
        self.assertTrue(sent.json()["number"].startswith("PROP-"))
        duplicate = self._action(f"/api/v1/proposals/{proposal['id']}/send", "proposal-send")
        self.assertEqual(duplicate.json(), sent.json())

        accepted = self._action(f"/api/v1/proposals/{proposal['id']}/accept", "proposal-accept").json()
        contract = accepted["contract"]
        self.assertEqual(accepted["proposal"]["status"], "Accepted")
        contract = self._action(f"/api/v1/contracts/{contract['id']}/send", "contract-send").json()
        contract = self._action(f"/api/v1/contracts/{contract['id']}/sign", "contract-sign", {}).json()
        contract = self._action(f"/api/v1/contracts/{contract['id']}/activate", "contract-active").json()
        self.assertEqual(contract["status"], "Active")

        project = self._action(f"/api/v1/contracts/{contract['id']}/project", "contract-project")
        self.assertEqual(project.status_code, 201)
        self.assertEqual(project.json()["contract_id"], contract["id"])

    def test_invoice_credit_payment_refund_and_balanced_immutable_ledger(self):
        due = (date.today() + timedelta(days=14)).isoformat()
        invoice = self.client.post(
            "/api/v1/invoices",
            json={
                "account_id": 9,
                "due_on": due,
                "customer_name": "Northstar Ltd",
                "lines": [{"description": "Service", "quantity": "3", "unit_price_pence": 101, "tax_rate_bps": 2_000}],
            },
        ).json()
        self.assertEqual((invoice["net_pence"], invoice["vat_pence"], invoice["total_pence"]), (303, 61, 364))
        issued = self._action(f"/api/v1/invoices/{invoice['id']}/issue", "invoice-issue")
        self.assertEqual(issued.json()["status"], "Sent")

        payment = self._action(
            "/api/v1/payments",
            "payment-1",
            {"amount_pence": 100, "invoice_id": invoice["id"], "method": "stripe", "reference": "pi_test"},
        ).json()
        self.assertEqual(payment["allocated_pence"], 100)
        self.assertEqual(self.client.get(f"/api/v1/invoices/{invoice['id']}").json()["status"], "Part-paid")

        refund = self._action(
            f"/api/v1/payments/{payment['id']}/refund", "refund-1", {"amount_pence": 40, "reason": "Adjustment"}
        )
        self.assertEqual(refund.status_code, 200)
        self.assertEqual(refund.json()["refunded_pence"], 40)

        credit = self.client.post(
            "/api/v1/credit-notes",
            json={
                "invoice_id": invoice["id"],
                "reason": "Service credit",
                "lines": [{"description": "Credit", "unit_price_pence": 50, "tax_rate_bps": 2_000}],
            },
        ).json()
        credited = self._action(f"/api/v1/credit-notes/{credit['id']}/issue", "credit-issue")
        self.assertEqual(credited.status_code, 200)
        self.assertEqual(credited.json()["invoice"]["outstanding_pence"], 244)

        final_payment = self._action(
            "/api/v1/payments", "payment-2", {"amount_pence": 244, "invoice_id": invoice["id"], "method": "bank"}
        )
        self.assertEqual(final_payment.status_code, 201)
        self.assertEqual(self.client.get(f"/api/v1/invoices/{invoice['id']}").json()["status"], "Paid")

        ledger = self.client.get("/api/v1/ledger").json()
        self.assertGreaterEqual(len(ledger["items"]), 7)
        for journal in ledger["items"]:
            self.assertEqual(journal["debit_pence"], journal["credit_pence"])
        payment_journal = next(item for item in ledger["items"] if item["source_type"] == "payment_receipt")
        self.assertEqual(("payment", payment["id"], "Recorded"), (
            payment_journal["linked_type"],
            payment_journal["linked_id"],
            payment_journal["payment_status"],
        ))
        invoice_journals = self.client.get(
            "/api/v1/ledger", params={"source_type": "invoice", "q": str(invoice["id"])}
        ).json()["items"]
        self.assertTrue(invoice_journals)
        self.assertTrue(all(item["source_type"].startswith("invoice") for item in invoice_journals))

        with self.assertRaises(sqlite3.IntegrityError):
            with connect() as conn:
                conn.execute("UPDATE journal_lines SET debit_pence = debit_pence + 1 WHERE id = 1")

        report = self.client.get("/api/v1/reports/finance").json()
        self.assertEqual(report["outstanding_pence"], 0)
        self.assertEqual(report["vat"]["output_pence"], 51)
        csv_report = self.client.get("/api/v1/reports/finance.csv")
        self.assertEqual(200, csv_report.status_code, csv_report.text)
        self.assertIn("text/csv", csv_report.headers["content-type"])
        self.assertIn("vat.output_pence", csv_report.text)
        ledger_csv = self.client.get("/api/v1/reports/ledger.csv")
        self.assertEqual(200, ledger_csv.status_code, ledger_csv.text)
        self.assertIn("source_type", ledger_csv.text)

    def test_delivery_lists_support_search_and_cursor_boundaries(self):
        first = self.client.post("/api/v1/projects", json={"name": "Alpha migration"}).json()
        second = self.client.post("/api/v1/projects", json={"name": "Beta rollout"}).json()
        searched = self.client.get("/api/v1/projects", params={"q": "Beta"}).json()
        self.assertEqual([second["id"]], [item["id"] for item in searched["items"]])
        page_one = self.client.get("/api/v1/projects", params={"limit": 1}).json()
        self.assertEqual([first["id"]], [item["id"] for item in page_one["items"]])
        self.assertIsNotNone(page_one["next_cursor"])
        page_two = self.client.get(
            "/api/v1/projects", params={"limit": 1, "cursor": page_one["next_cursor"]}
        ).json()
        self.assertEqual([second["id"]], [item["id"] for item in page_two["items"]])
        self.assertIsNone(page_two["next_cursor"])

    def test_client_health_reflects_operational_risk(self):
        self.client.post(
            "/api/v1/projects", json={"name": "Recovery", "account_id": 42, "status": "Blocked"}
        )
        created = self.client.put(
            "/api/v1/client-success/42",
            json={"account_id": 42, "open_risks": 1, "renewal_on": (date.today() + timedelta(days=30)).isoformat()},
        )
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["computed_health"], "At risk")
        renewals = self.client.get("/api/v1/reports/renewals?days=90").json()
        self.assertEqual(renewals["items"][0]["account_id"], 42)

        with connect() as conn:
            old = (date.today() - timedelta(days=61)).isoformat()
            conn.execute(
                "INSERT INTO activities(entity_type, entity_id, kind, subject, occurred_at, created_at) "
                "VALUES ('account', 43, 'note', 'Old review', ?, ?)",
                (old, old),
            )
        inactive = self.client.put(
            "/api/v1/client-success/43",
            json={"account_id": 43, "open_risks": 0},
        )
        self.assertEqual("At risk", inactive.json()["computed_health"])
        self.assertIn("No client activity for 60 days", inactive.json()["health_reasons"])

    def test_schema_installer_adds_compatible_columns_to_existing_tables(self):
        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript(
                """
                CREATE TABLE invoices (
                    id INTEGER PRIMARY KEY, number TEXT, account_id INTEGER NOT NULL,
                    project_id INTEGER, contract_id INTEGER, status TEXT NOT NULL DEFAULT 'Draft',
                    currency TEXT NOT NULL DEFAULT 'GBP', issued_on TEXT, due_on TEXT NOT NULL,
                    customer_name TEXT NOT NULL, customer_address TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
                    net_pence INTEGER NOT NULL DEFAULT 0, vat_pence INTEGER NOT NULL DEFAULT 0,
                    total_pence INTEGER NOT NULL DEFAULT 0, paid_pence INTEGER NOT NULL DEFAULT 0,
                    credited_pence INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE credit_notes (
                    id INTEGER PRIMARY KEY, number TEXT, invoice_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'Draft',
                    reason TEXT NOT NULL, currency TEXT NOT NULL DEFAULT 'GBP', issued_on TEXT,
                    net_pence INTEGER NOT NULL DEFAULT 0, vat_pence INTEGER NOT NULL DEFAULT 0,
                    total_pence INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                """
            )
            install_schema(conn)
            invoice_columns = {row[1] for row in conn.execute("PRAGMA table_xinfo(invoices)")}
            credit_columns = {row[1] for row in conn.execute("PRAGMA table_xinfo(credit_notes)")}
            self.assertTrue({"archived_at", "state", "balance_minor"} <= invoice_columns)
            self.assertIn("version", credit_columns)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
