from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.integrations_v1.automation import AutomationStore
from app.integrations_v1.jobs import JobStore, JobWorker
from app.integrations_v1.state import NotificationStore
from app.integrations_v1.worker import Worker
from app.platform_db import connect, reset_bootstrap_for_tests
from app.v1 import core_service, models


class AutomationRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        os.environ["CRM_DATA_DIR"] = self.temp.name
        os.environ["CRM_DB_PATH"] = str(Path(self.temp.name) / "crm.sqlite3")
        reset_bootstrap_for_tests()

    def tearDown(self) -> None:
        reset_bootstrap_for_tests()
        os.environ.pop("CRM_DATA_DIR", None)
        os.environ.pop("CRM_DB_PATH", None)
        self.temp.cleanup()

    @staticmethod
    def _run_automation_jobs() -> None:
        jobs = JobStore()
        worker = JobWorker("automation-test", jobs, {"automation.event": Worker._run_automation})
        while worker.run_once(limit=20):
            if not jobs.list(state="queued") and not jobs.list(state="retry_wait"):
                break

    def test_domain_event_runs_live_allowlisted_action_once(self) -> None:
        store = AutomationStore()
        rule = store.create_rule(
            "Create lead follow-up",
            "lead.created",
            actions=[
                {
                    "type": "create_task",
                    "params": {
                        "title": "Follow up {{title}}",
                        "priority": "High",
                    },
                },
                {
                    "type": "notify",
                    "params": {"title": "New lead: {{title}}", "severity": "info"},
                },
            ],
            enabled=True,
            dry_run=False,
        )
        with connect() as conn:
            account = core_service.create_account(conn, models.AccountCreate(name="Automation Ltd"))
            lead = core_service.create_lead(
                conn,
                models.LeadCreate(account_id=account["id"], title="Automation enquiry", company="Automation Ltd"),
            )

        jobs = JobStore()
        queued = [job for job in jobs.list(state="queued") if job.kind == "automation.event"]
        self.assertEqual(1, len(queued))
        self._run_automation_jobs()

        with connect() as conn:
            tasks = conn.execute("SELECT * FROM work_tasks WHERE title=?", ("Follow up Automation enquiry",)).fetchall()
        self.assertEqual(1, len(tasks))
        self.assertEqual("High", tasks[0]["priority"])
        self.assertEqual("lead", tasks[0]["entity_type"])
        self.assertEqual(lead["id"], tasks[0]["entity_id"])
        self.assertEqual("New lead: Automation enquiry", NotificationStore().list()[0].title)
        self.assertEqual("succeeded", store.list_executions(rule_id=rule.id)[0].outcome)

        # Replaying the same durable payload with another execution attempt cannot
        # duplicate an already completed action.
        Worker._run_automation(queued[0].payload, type("Job", (), {"attempts": 2})())
        with connect() as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM work_tasks WHERE title=?", ("Follow up Automation enquiry",)).fetchone()[0])

    def test_dry_run_records_match_without_side_effect(self) -> None:
        store = AutomationStore()
        rule = store.create_rule(
            "Preview lead alert",
            "lead.created",
            actions=[{"type": "notify", "params": {"title": "Would notify"}}],
            enabled=True,
            dry_run=True,
        )
        with connect() as conn:
            account = core_service.create_account(conn, models.AccountCreate(name="Dry Run Ltd"))
            core_service.create_lead(conn, models.LeadCreate(account_id=account["id"], title="Preview only"))
        self._run_automation_jobs()
        self.assertEqual([], NotificationStore().list())
        self.assertEqual("matched", store.list_executions(rule_id=rule.id)[0].outcome)


if __name__ == "__main__":
    unittest.main()
