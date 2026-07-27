import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from app import platform_db
from app.main import app


class PlatformResilienceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["CRM_DATA_DIR"] = self.temp.name
        os.environ["CRM_DB_PATH"] = str(Path(self.temp.name) / "crm.sqlite3")
        platform_db.reset_bootstrap_for_tests()
        platform_db.bootstrap()

    def tearDown(self):
        platform_db.reset_bootstrap_for_tests()
        os.environ.pop("CRM_DB_PATH", None)
        os.environ.pop("CRM_DATA_DIR", None)
        self.temp.cleanup()

    def test_wal_busy_timeout_and_concurrent_transactional_writes(self):
        def create(index):
            with platform_db.connect() as conn:
                now = platform_db.utc_now().isoformat()
                return conn.execute(
                    "INSERT INTO accounts(name, created_at, updated_at) VALUES (?, ?, ?)",
                    (f"Concurrent account {index}", now, now),
                ).lastrowid

        with ThreadPoolExecutor(max_workers=8) as pool:
            identifiers = list(pool.map(create, range(40)))

        self.assertEqual(40, len(set(identifiers)))
        with platform_db.connect() as conn:
            self.assertEqual("wal", conn.execute("PRAGMA journal_mode").fetchone()[0].lower())
            self.assertEqual(5000, conn.execute("PRAGMA busy_timeout").fetchone()[0])
            self.assertEqual(40, conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0])

    def test_foreign_keys_reject_orphans_and_archive_preserves_relationships(self):
        with platform_db.connect() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO contacts(account_id, display_name, created_at, updated_at) VALUES (999, 'Orphan', 'x', 'x')"
                )

        with TestClient(app) as client:
            account = client.post("/api/v1/accounts", json={"name": "Archive Co"}).json()
            contact = client.post(
                "/api/v1/contacts",
                json={"account_id": account["id"], "display_name": "Archive Contact"},
            ).json()
            archived = client.post(
                f"/api/v1/accounts/{account['id']}/archive",
                json={"version": account["version"]},
            )
            self.assertEqual(200, archived.status_code, archived.text)
            self.assertIsNotNone(archived.json()["archived_at"])
            linked = client.get(f"/api/v1/contacts/{contact['id']}")
            self.assertEqual(200, linked.status_code, linked.text)
            self.assertEqual(account["id"], linked.json()["account_id"])


if __name__ == "__main__":
    unittest.main()
