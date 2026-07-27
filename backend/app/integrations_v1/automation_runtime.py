from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Mapping

from app import platform_db
from app.communications.router import EnrollmentCreate, enroll_sequence
from app.communications.schema import install_schema as install_communications_schema
from app.operations.schema import install_schema as install_operations_schema
from app.v1 import core_service, models as core_models

from .automation import AutomationEngine, AutomationExecution, AutomationStore
from .state import NotificationStore


_TOKEN = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*}}")
_TABLES = {
    "account": "accounts",
    "contact": "contacts",
    "lead": "sales_leads",
    "opportunity": "opportunities",
    "deal": "opportunities",
    "project": "projects",
    "client_success": "client_success",
}
_FIELDS = {
    "account": {"name", "status", "health_status", "renewal_date", "notes"},
    "contact": {"status", "job_title", "preferred_channel", "notes"},
    "lead": {"status", "score", "next_action", "notes"},
    "opportunity": {"probability_bps", "expected_close_date", "next_action", "notes"},
    "deal": {"probability_bps", "expected_close_date", "next_action", "notes"},
    "project": {"status", "due_on", "notes"},
    "client_success": {"manual_health", "open_risks", "next_review_on", "renewal_on", "notes"},
}
_ENUM_VALUES = {
    ("account", "health_status"): {"Healthy", "Watch", "At risk"},
    ("lead", "status"): {"New", "Working", "Qualified", "Nurture", "Disqualified"},
    ("project", "status"): {"Planned", "Active", "Blocked", "Complete", "Cancelled"},
    ("client_success", "manual_health"): {"Healthy", "Watch", "At risk", None, ""},
}
_DATE_FIELDS = {"renewal_date", "expected_close_date", "due_on", "next_review_on", "renewal_on"}


def _plain(value: Any) -> Any:
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return value


def _path(record: Mapping[str, Any], name: str) -> Any:
    current: Any = record
    for part in name.split("."):
        if not isinstance(current, Mapping):
            return ""
        current = current.get(part)
    return current if current is not None else ""


def _render(value: Any, record: Mapping[str, Any]) -> Any:
    if not isinstance(value, str):
        return value
    return _TOKEN.sub(lambda match: str(_path(record, match.group(1))), value)


def _record_type(params: Mapping[str, Any], record: Mapping[str, Any]) -> str:
    value = str(params.get("entity_type") or record.get("type") or record.get("record_type") or "").lower()
    aliases = {"accounts": "account", "contacts": "contact", "leads": "lead", "deals": "opportunity", "opportunities": "opportunity", "projects": "project", "client-success": "client_success"}
    return aliases.get(value, value)


def _record_id(params: Mapping[str, Any], record: Mapping[str, Any]) -> int:
    value = params.get("entity_id") or params.get("record_id") or record.get("id") or record.get("record_id")
    if value is None:
        raise ValueError("Automation action requires a record id")
    return int(value)


def _action_key(params: Mapping[str, Any]) -> str:
    metadata = params.get("_automation") or {}
    return f"{metadata.get('rule_id', 'rule')}:{metadata.get('correlation_id', 'event')}:{metadata.get('action_index', 0)}"


def _cached(conn, key: str) -> Any | None:
    row = conn.execute(
        "SELECT response_json FROM operation_idempotency WHERE action='automation.action' AND key=?",
        (key,),
    ).fetchone()
    return json.loads(row["response_json"]) if row else None


def _remember(conn, key: str, result: Any) -> Any:
    plain = _plain(result)
    conn.execute(
        "INSERT OR REPLACE INTO operation_idempotency(action,key,response_json,created_at) VALUES ('automation.action',?,?,?)",
        (key, json.dumps(plain, default=str), platform_db.utc_now().isoformat()),
    )
    return plain


class AutomationRuntime:
    """Executes versioned allowlisted rules without arbitrary code or destinations."""

    def __init__(self) -> None:
        self.store = AutomationStore()
        self.notifications = NotificationStore()
        self.engine = AutomationEngine(
            self.store,
            {
                "create_task": self.create_task,
                "schedule_reminder": self.schedule_reminder,
                "notify": self.notify,
                "update_field": self.update_field,
                "transition_deal": self.transition_deal,
                "create_document": self.create_document,
                "create_project": self.create_project,
                "enroll_sequence": self.enroll_sequence,
            },
        )

    def run_event(self, payload: Mapping[str, Any], *, attempt: int = 1) -> list[AutomationExecution]:
        trigger_name = str(payload.get("trigger_name") or "")
        stable_correlation = str(payload.get("correlation_id") or "")
        record = dict(payload.get("record") or {})
        record["_automation_correlation_id"] = stable_correlation
        with platform_db.suppress_automation_events():
            return self.engine.run(
                trigger_name,
                record,
                correlation_id=f"{stable_correlation}:attempt:{max(1, int(attempt))}",
            )

    def create_task(self, params: dict[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
        return self._task(params, record, reminder=False)

    def schedule_reminder(self, params: dict[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
        return self._task(params, record, reminder=True)

    def _task(self, params: dict[str, Any], record: Mapping[str, Any], *, reminder: bool) -> dict[str, Any]:
        key, now = _action_key(params), platform_db.utc_now().isoformat()
        title = str(_render(params.get("title") or ("Reminder" if reminder else "Automation follow-up"), record)).strip()
        if not title:
            raise ValueError("Task title is required")
        entity_type = _record_type(params, record)
        entity_id = params.get("entity_id") or record.get("id") or record.get("record_id")
        priority = str(params.get("priority") or "Medium").title()
        if priority not in {"Low", "Medium", "High"}:
            raise ValueError("Unsupported task priority")
        due_at = _render(params.get("due_at") or params.get("remind_at") or "", record) or None
        with platform_db.connect() as conn:
            install_operations_schema(conn)
            if cached := _cached(conn, key):
                return cached
            task_id = int(conn.execute(
                "INSERT INTO work_tasks(entity_type,entity_id,title,description,status,priority,due_at,created_at,updated_at) VALUES (?,?,?,?,'Open',?,?,?,?)",
                (entity_type, int(entity_id) if entity_id is not None else None, title, str(_render(params.get("description") or "", record)), priority, due_at, now, now),
            ).lastrowid)
            result = dict(conn.execute("SELECT * FROM work_tasks WHERE id=?", (task_id,)).fetchone())
            platform_db.write_audit(conn, "automation_create", "task", task_id, after=result)
            return _remember(conn, key, result)

    def notify(self, params: dict[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
        key = _action_key(params)
        title = str(_render(params.get("title") or "CRM automation alert", record)).strip()
        notification = self.notifications.create(
            "automation",
            title,
            body=str(_render(params.get("body") or "", record)),
            severity=str(params.get("severity") or "info"),
            action_url=str(_render(params.get("action_url") or "", record)),
            dedupe_key=f"automation:{key}",
        )
        return _plain(notification)

    def update_field(self, params: dict[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
        key = _action_key(params)
        entity_type = _record_type(params, record)
        field = str(params.get("field") or "")
        if entity_type not in _TABLES or field not in _FIELDS.get(entity_type, set()):
            raise ValueError("Automation field update is not allowlisted")
        value = _render(params.get("value"), record)
        if (entity_type, field) in _ENUM_VALUES and value not in _ENUM_VALUES[(entity_type, field)]:
            raise ValueError(f"Unsupported {field} value")
        if field in _DATE_FIELDS and value not in {None, ""}:
            value = date.fromisoformat(str(value)).isoformat()
        if field in {"score", "probability_bps"}:
            value = int(value)
            upper = 100 if field == "score" else 10_000
            if not 0 <= value <= upper:
                raise ValueError(f"{field} is outside its allowed range")
        item_id, table = _record_id(params, record), _TABLES[entity_type]
        now = platform_db.utc_now().isoformat()
        with platform_db.connect() as conn:
            install_operations_schema(conn)
            if cached := _cached(conn, key):
                return cached
            before = conn.execute(f"SELECT * FROM {table} WHERE id=? AND archived_at IS NULL", (item_id,)).fetchone()
            if before is None:
                raise ValueError("Automation target was not found")
            conn.execute(f"UPDATE {table} SET {field}=?, updated_at=?, version=version+1 WHERE id=?", (value, now, item_id))
            result = dict(conn.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone())
            platform_db.write_audit(conn, "automation_update", entity_type, item_id, dict(before), result)
            return _remember(conn, key, result)

    def transition_deal(self, params: dict[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
        key, item_id = _action_key(params), _record_id(params, record)
        with platform_db.connect() as conn:
            install_operations_schema(conn)
            if cached := _cached(conn, key):
                return cached
            current = core_service.get_opportunity(conn, item_id)
            stage_id = params.get("stage_id")
            if stage_id is None and params.get("stage"):
                row = conn.execute("SELECT id FROM pipeline_stages WHERE lower(name)=lower(?) AND archived_at IS NULL", (str(params["stage"]),)).fetchone()
                stage_id = row["id"] if row else None
            if stage_id is None:
                raise ValueError("transition_deal requires an allowlisted pipeline stage")
            result = core_service.transition_opportunity(conn, item_id, core_models.TransitionRequest(
                version=current["version"],
                stage_id=int(stage_id),
                probability_bps=params.get("probability_bps"),
                loss_reason=str(_render(params.get("loss_reason") or "", record)),
            ))
            return _remember(conn, key, result)

    def create_document(self, params: dict[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
        key, now = _action_key(params), platform_db.utc_now().isoformat()
        title = str(_render(params.get("title") or "Automation document", record)).strip()
        entity_type = _record_type(params, record)
        entity_id = params.get("entity_id") or record.get("id") or record.get("record_id")
        with platform_db.connect() as conn:
            install_operations_schema(conn)
            install_communications_schema(conn)
            if cached := _cached(conn, key):
                return cached
            document_id = int(conn.execute(
                "INSERT INTO documents(entity_type,entity_id,title,mime_type,sync_state,created_at,updated_at) VALUES (?,?,?,'application/vnd.google-apps.document','Local',?,?)",
                (entity_type, int(entity_id) if entity_id is not None else None, title, now, now),
            ).lastrowid)
            result = dict(conn.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone())
            return _remember(conn, key, result)

    def create_project(self, params: dict[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
        key, now = _action_key(params), platform_db.utc_now().isoformat()
        account_id = params.get("account_id") or record.get("account_id")
        if account_id is None:
            raise ValueError("create_project requires an account id")
        name = str(_render(params.get("name") or record.get("title") or "Client project", record)).strip()
        billing_type = str(params.get("billing_type") or "fixed").lower()
        if billing_type not in {"fixed", "milestone", "hourly", "retainer"}:
            raise ValueError("Unsupported project billing type")
        with platform_db.connect() as conn:
            install_operations_schema(conn)
            if cached := _cached(conn, key):
                return cached
            project_id = int(conn.execute(
                "INSERT INTO projects(account_id,opportunity_id,contract_id,name,status,billing_type,budget_pence,currency,starts_on,due_on,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (int(account_id), params.get("opportunity_id") or record.get("opportunity_id") or (record.get("id") if _record_type(params, record) == "opportunity" else None), params.get("contract_id") or record.get("contract_id"), name, "Planned", billing_type, int(params.get("budget_pence") or record.get("value_minor") or 0), str(params.get("currency") or record.get("currency") or "GBP").upper(), params.get("starts_on"), params.get("due_on"), str(_render(params.get("notes") or "", record)), now, now),
            ).lastrowid)
            result = dict(conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone())
            return _remember(conn, key, result)

    def enroll_sequence(self, params: dict[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
        sequence_id = params.get("sequence_id")
        if sequence_id is None:
            raise ValueError("enroll_sequence requires a sequence id")
        contact_id = params.get("contact_id") or record.get("contact_id") or record.get("primary_contact_id")
        email = _render(params.get("email") or record.get("email") or "", record) or None
        key = _action_key(params)
        with platform_db.connect() as conn:
            install_communications_schema(conn)
            result = enroll_sequence(
                int(sequence_id),
                EnrollmentCreate(contact_id=int(contact_id) if contact_id else None, email=email, merge_fields=dict(params.get("merge_fields") or {})),
                key,
                conn,
            )
            return dict(result)
