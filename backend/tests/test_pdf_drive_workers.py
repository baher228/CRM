from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import platform_db
from app.integrations_v1.google import GoogleWorkspaceAdapter
from app.integrations_v1.jobs import PermanentJobError
from app.integrations_v1.secrets import CredentialStore
from app.integrations_v1.stripe import StripeAdapter
from app.integrations_v1.worker import Worker
from app.operations import router as operations_router


class PdfAndDriveWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        os.environ["CRM_DATA_DIR"] = str(self.root)
        os.environ["CRM_DB_PATH"] = str(self.root / "crm.sqlite3")
        platform_db.reset_bootstrap_for_tests()
        credentials = CredentialStore.for_tests()
        self.google = GoogleWorkspaceAdapter(credentials=credentials, fake=True)
        self.worker = Worker(
            google=self.google,
            stripe=StripeAdapter(credentials=credentials, fake=True),
        )
        with platform_db.connect() as conn:
            conn.execute(
                "UPDATE business_profile SET legal_name='Test Business Ltd', vat_registered=1, "
                "vat_number='GB123456789', vat_scheme='Standard', vat_effective_from='2000-01-01', "
                "tax_codes_approved=1 WHERE id=1"
            )

    def tearDown(self) -> None:
        platform_db.reset_bootstrap_for_tests()
        os.environ.pop("CRM_DB_PATH", None)
        os.environ.pop("CRM_DATA_DIR", None)
        self.temp.cleanup()

    def _document(self, title: str = "Statement of work") -> int:
        now = platform_db.utc_now().isoformat()
        with platform_db.connect() as conn:
            return int(
                conn.execute(
                    "INSERT INTO documents(title, sync_state, created_at, updated_at) "
                    "VALUES (?, 'Queued', ?, ?)",
                    (title, now, now),
                ).lastrowid
            )

    def test_invoice_pdf_is_checksums_backed_and_not_regenerated_by_idempotent_issue(self) -> None:
        app = FastAPI()
        app.include_router(operations_router, prefix="/api/v1")
        with TestClient(app, headers={"X-CRM-Confirmed": "true"}) as client:
            invoice = client.post(
                "/api/v1/invoices",
                json={
                    "account_id": 10,
                    "due_on": (date.today() + timedelta(days=14)).isoformat(),
                    "customer_name": "Northstar Ltd",
                    "customer_address": "1 Test Street\nLondon",
                    "lines": [
                        {
                            "description": "Implementation",
                            "quantity": "2.5",
                            "unit_price_pence": 12_345,
                            "tax_rate_bps": 2_000,
                        }
                    ],
                },
            ).json()
            headers = {"Idempotency-Key": "issue-pdf-once"}
            issued = client.post(
                f"/api/v1/invoices/{invoice['id']}/issue", headers=headers
            )
            self.assertEqual(200, issued.status_code, issued.text)
            snapshot = issued.json()
            pdf = Path(snapshot["pdf_path"]).resolve()
            self.assertTrue(pdf.is_relative_to(self.root))
            self.assertTrue(pdf.is_file())
            self.assertTrue(pdf.read_bytes().startswith(b"%PDF"))
            digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
            self.assertEqual(digest, snapshot["pdf_sha256"])

            immutable = client.patch(
                f"/api/v1/invoices/{invoice['id']}",
                json={"version": snapshot["version"], "customer_name": "Changed"},
            )
            self.assertEqual(409, immutable.status_code)
            with platform_db.connect() as conn:
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        "UPDATE invoice_lines SET description='Changed' WHERE invoice_id=?",
                        (invoice["id"],),
                    )

            with patch("app.operations.invoice_pdf.render_invoice") as renderer:
                replay = client.post(
                    f"/api/v1/invoices/{invoice['id']}/issue", headers=headers
                )
            renderer.assert_not_called()
            self.assertEqual(200, replay.status_code, replay.text)
            self.assertEqual(snapshot["pdf_path"], replay.json()["pdf_path"])
            self.assertEqual(digest, replay.json()["pdf_sha256"])

    def test_drive_create_and_sync_persist_refs_versions_and_managed_files(self) -> None:
        document_id = self._document()
        template = self.google.upload_drive_file(
            name="Proposal template",
            content=b"Hello {{client.name}}",
            mime_type="application/vnd.google-apps.document",
        )
        created = self.worker._create_drive_document(  # noqa: SLF001 - handler contract
            {
                "document_id": document_id,
                "title": "Statement of work",
                "parent_google_file_id": "folder-42",
                "template_google_file_id": template["id"],
                "merge_data": {"client.name": "Northstar Ltd"},
            },
            None,  # type: ignore[arg-type]
        )
        external_id = created["google_file_id"]
        self.google._fake["drive_files"][external_id]["content"] = b"%PDF-1.4\nfirst"

        first = self.worker._sync_drive_document(  # noqa: SLF001 - handler contract
            {"document_id": document_id, "google_file_id": external_id},
            None,  # type: ignore[arg-type]
        )
        first_path = Path(first["local_path"]).resolve()
        self.assertTrue(first_path.is_relative_to(self.root))
        self.assertTrue(first_path.is_relative_to(platform_db.data_root().resolve()))
        self.assertEqual(b"%PDF-1.4\nfirst", first_path.read_bytes())

        self.google._fake["drive_files"][external_id]["content"] = b"%PDF-1.4\nsecond"
        second = self.worker._sync_drive_document(  # noqa: SLF001 - handler contract
            {"document_id": document_id, "google_file_id": external_id},
            None,  # type: ignore[arg-type]
        )
        with platform_db.connect() as conn:
            document = conn.execute(
                "SELECT * FROM documents WHERE id=?", (document_id,)
            ).fetchone()
            versions = conn.execute(
                "SELECT * FROM document_versions WHERE document_id=? ORDER BY version_number",
                (document_id,),
            ).fetchall()
            reference = conn.execute(
                "SELECT * FROM integration_external_refs "
                "WHERE provider='google' AND resource_type='drive_file' "
                "AND local_type='document' AND local_id=?",
                (str(document_id),),
            ).fetchone()
        self.assertEqual(external_id, document["google_file_id"])
        self.assertEqual("Ready", document["sync_state"])
        self.assertEqual(4, document["version"])
        self.assertEqual(second["local_path"], document["local_path"])
        self.assertEqual([1, 2], [row["version_number"] for row in versions])
        self.assertEqual(["drive-sync", "drive-sync"], [row["source"] for row in versions])
        self.assertEqual(second["checksum_sha256"], versions[-1]["checksum_sha256"])
        self.assertEqual(external_id, reference["external_id"])
        self.assertEqual(["folder-42"], self.google._fake["drive_files"][external_id]["parents"])
        self.assertEqual(template["id"], self.google._fake["drive_files"][external_id]["copiedFrom"])
        self.assertEqual(
            {"client.name": "Northstar Ltd"},
            self.google._fake["drive_files"][external_id]["mergeData"],
        )

    def test_drive_version_upload_rejects_bad_checksum_and_records_external_ref(self) -> None:
        document_id = self._document("Issued proposal")
        managed = platform_db.documents_root() / str(document_id) / "proposal.pdf"
        managed.parent.mkdir(parents=True, exist_ok=True)
        managed.write_bytes(b"%PDF-1.4\nimmutable proposal")
        digest = hashlib.sha256(managed.read_bytes()).hexdigest()
        now = platform_db.utc_now().isoformat()
        with platform_db.connect() as conn:
            version_id = int(
                conn.execute(
                    "INSERT INTO document_versions"
                    "(document_id, version_number, local_path, mime_type, checksum_sha256, "
                    "size_bytes, issued, source, created_at) VALUES (?, 1, ?, ?, ?, ?, 1, 'local', ?)",
                    (
                        document_id,
                        str(managed),
                        "application/pdf",
                        digest,
                        managed.stat().st_size,
                        now,
                    ),
                ).lastrowid
            )

        payload = {
            "document_id": document_id,
            "version_id": version_id,
            "local_path": str(managed),
            "mime_type": "application/pdf",
            "checksum_sha256": "0" * 64,
        }
        with self.assertRaisesRegex(PermanentJobError, "checksum"):
            self.worker._upload_drive_version(payload, None)  # type: ignore[arg-type]  # noqa: SLF001
        self.assertEqual({}, self.google._fake["drive_files"])

        outside = self.root.parent / f"outside-{self.root.name}.pdf"
        try:
            outside.write_bytes(managed.read_bytes())
            with self.assertRaisesRegex(PermanentJobError, "CRM-managed"):
                self.worker._upload_drive_version(  # noqa: SLF001
                    {**payload, "local_path": str(outside), "checksum_sha256": digest},
                    None,  # type: ignore[arg-type]
                )
        finally:
            outside.unlink(missing_ok=True)

        uploaded = self.worker._upload_drive_version(  # noqa: SLF001 - handler contract
            {**payload, "checksum_sha256": digest},
            None,  # type: ignore[arg-type]
        )
        with platform_db.connect() as conn:
            reference = conn.execute(
                "SELECT * FROM integration_external_refs "
                "WHERE provider='google' AND resource_type='drive_file' "
                "AND local_type='document_version' AND local_id=?",
                (str(version_id),),
            ).fetchone()
        self.assertEqual(digest, uploaded["checksum_sha256"])
        self.assertEqual(uploaded["google_file_id"], reference["external_id"])
        remote = self.google._fake["drive_files"][uploaded["google_file_id"]]
        self.assertEqual(digest, remote["sha256Checksum"])
        self.assertEqual(managed.read_bytes(), remote["content"])


if __name__ == "__main__":
    unittest.main()
