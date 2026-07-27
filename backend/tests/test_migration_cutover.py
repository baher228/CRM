import json
import os
import sqlite3
import tempfile
import unittest

from app import platform_db


class MigrationCutoverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "legacy.sqlite3")
        os.environ["CRM_DB_PATH"] = self.path
        os.environ["CRM_DATA_DIR"] = self.temp.name
        platform_db.reset_bootstrap_for_tests()

    def tearDown(self):
        platform_db.reset_bootstrap_for_tests()
        os.environ.pop("CRM_DB_PATH", None)
        os.environ.pop("CRM_DATA_DIR", None)
        self.temp.cleanup()

    def test_numbered_cutover_reconciles_money_and_retires_duplicate_tables(self):
        payload = {
            "name": "Facilities framework",
            "company": "Example Council",
            "contract_title": "Facilities framework",
            "buyer_name": "Example Council",
            "contract_url": "https://contracts.example.test/notice/1",
            "contract_value": "GBP 50,000 to GBP 100,000",
            "estimated_value": 2_000_000_000,
            "status": "New",
            "outreach_angle": "Deadline soon | legacy upsert complete",
        }
        conn = sqlite3.connect(self.path)
        conn.execute("CREATE TABLE leads (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        conn.execute("INSERT INTO leads(id, payload) VALUES (1, ?)", (json.dumps(payload),))
        conn.commit()
        conn.close()

        platform_db.bootstrap()

        with platform_db.connect() as database:
            opportunity = database.execute("SELECT value_minor FROM opportunities").fetchone()
            tender = database.execute(
                "SELECT estimated_value_minor, outreach_angle FROM tender_notices"
            ).fetchone()
            tables = {
                row["name"]
                for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            migrations = [
                row["version"]
                for row in database.execute("SELECT version FROM schema_migrations ORDER BY version")
            ]

        self.assertEqual(7_500_000, opportunity["value_minor"])
        self.assertEqual(7_500_000, tender["estimated_value_minor"])
        self.assertEqual("Deadline soon", tender["outreach_angle"])
        self.assertNotIn("leads", tables)
        self.assertEqual([1, 2, 3, 4], migrations)


if __name__ == "__main__":
    unittest.main()
