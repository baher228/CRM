from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from app.platform_db import connect, utc_now

from .schema import install_schema


ALLOWED_TRIGGERS = {
    "lead.created",
    "lead.qualified",
    "tender.qualified",
    "deal.stage_changed",
    "proposal.accepted",
    "contract.activated",
    "project.blocked",
    "invoice.overdue",
    "payment.received",
    "renewal.due",
}
ALLOWED_ACTIONS = {
    "create_task",
    "update_field",
    "transition_deal",
    "create_document",
    "create_project",
    "enroll_sequence",
    "schedule_reminder",
    "notify",
}
ALLOWED_OPERATORS = {
    "equals",
    "not_equals",
    "in",
    "not_in",
    "contains",
    "lt",
    "lte",
    "gt",
    "gte",
    "is_empty",
}
SAFE_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")


class OptimisticRuleConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class AutomationRule:
    id: str
    name: str
    trigger_name: str
    conditions: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    enabled: bool
    dry_run: bool
    version: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AutomationExecution:
    id: str
    rule_id: str
    trigger_name: str
    record_key: str
    correlation_id: str
    mode: str
    outcome: str
    result: dict[str, Any]
    error: str
    created_at: str


def _dump(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


class AutomationStore:
    def __init__(self, *, ensure_schema: bool = True) -> None:
        if ensure_schema:
            install_schema()

    def create_rule(
        self,
        name: str,
        trigger_name: str,
        *,
        conditions: list[dict[str, Any]] | None = None,
        actions: list[dict[str, Any]] | None = None,
        enabled: bool = False,
        dry_run: bool = True,
    ) -> AutomationRule:
        conditions, actions = conditions or [], actions or []
        self._validate(name, trigger_name, conditions, actions)
        rule_id, now = str(uuid.uuid4()), utc_now().isoformat()
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO integration_automation_rules
                    (id, name, trigger_name, conditions_json, actions_json, enabled, dry_run, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule_id,
                    name,
                    trigger_name,
                    _dump(conditions),
                    _dump(actions),
                    int(enabled),
                    int(dry_run),
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM integration_automation_rules WHERE id = ?", (rule_id,)
            ).fetchone()
        return self._rule(row)

    def update_rule(
        self,
        rule_id: str,
        *,
        version: int,
        name: str,
        trigger_name: str,
        conditions: list[dict[str, Any]],
        actions: list[dict[str, Any]],
        enabled: bool,
        dry_run: bool,
    ) -> AutomationRule:
        self._validate(name, trigger_name, conditions, actions)
        with connect() as conn:
            cursor = conn.execute(
                """
                UPDATE integration_automation_rules
                SET name = ?, trigger_name = ?, conditions_json = ?, actions_json = ?, enabled = ?,
                    dry_run = ?, version = version + 1, updated_at = ?
                WHERE id = ? AND version = ?
                """,
                (
                    name,
                    trigger_name,
                    _dump(conditions),
                    _dump(actions),
                    int(enabled),
                    int(dry_run),
                    utc_now().isoformat(),
                    rule_id,
                    version,
                ),
            )
            if cursor.rowcount != 1:
                raise OptimisticRuleConflict("Automation rule changed; reload and retry")
            row = conn.execute(
                "SELECT * FROM integration_automation_rules WHERE id = ?", (rule_id,)
            ).fetchone()
        return self._rule(row)

    def get_rule(self, rule_id: str) -> AutomationRule | None:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM integration_automation_rules WHERE id = ?", (rule_id,)
            ).fetchone()
        return self._rule(row) if row else None

    def list_rules(
        self, *, trigger_name: str | None = None, enabled_only: bool = False
    ) -> list[AutomationRule]:
        clauses, params = [], []
        if trigger_name:
            clauses.append("trigger_name = ?")
            params.append(trigger_name)
        if enabled_only:
            clauses.append("enabled = 1")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM integration_automation_rules{where} ORDER BY lower(name)", params
            ).fetchall()
        return [self._rule(row) for row in rows]

    def save_execution(
        self,
        rule: AutomationRule,
        *,
        record_key: str,
        correlation_id: str,
        mode: str,
        outcome: str,
        result: Mapping[str, Any] | None = None,
        error: str = "",
    ) -> AutomationExecution:
        execution_id, now = str(uuid.uuid4()), utc_now().isoformat()
        with connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO integration_automation_executions
                        (id, rule_id, trigger_name, record_key, correlation_id, mode,
                         outcome, result_json, error, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        execution_id,
                        rule.id,
                        rule.trigger_name,
                        record_key,
                        correlation_id,
                        mode,
                        outcome,
                        _dump(dict(result or {})),
                        error,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                row = conn.execute(
                    """
                    SELECT * FROM integration_automation_executions
                    WHERE rule_id = ? AND record_key = ? AND correlation_id = ? AND mode = ?
                    """,
                    (rule.id, record_key, correlation_id, mode),
                ).fetchone()
                return self._execution(row)
            row = conn.execute(
                "SELECT * FROM integration_automation_executions WHERE id = ?", (execution_id,)
            ).fetchone()
        return self._execution(row)

    def find_execution(
        self,
        rule_id: str,
        *,
        record_key: str,
        correlation_id: str,
        mode: str,
    ) -> AutomationExecution | None:
        with connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM integration_automation_executions
                WHERE rule_id = ? AND record_key = ? AND correlation_id = ? AND mode = ?
                """,
                (rule_id, record_key, correlation_id, mode),
            ).fetchone()
        return self._execution(row) if row else None

    def list_executions(self, *, rule_id: str | None = None, limit: int = 100) -> list[AutomationExecution]:
        where, params = "", []
        if rule_id:
            where = "WHERE rule_id = ?"
            params.append(rule_id)
        params.append(max(1, min(int(limit), 100)))
        with connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM integration_automation_executions {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._execution(row) for row in rows]

    @staticmethod
    def _validate(
        name: str,
        trigger_name: str,
        conditions: list[dict[str, Any]],
        actions: list[dict[str, Any]],
    ) -> None:
        if not name.strip():
            raise ValueError("Rule name is required")
        if trigger_name not in ALLOWED_TRIGGERS:
            raise ValueError("Unsupported automation trigger")
        for condition in conditions:
            if not isinstance(condition, dict):
                raise ValueError("Conditions must be objects")
            if not SAFE_PATH.fullmatch(str(condition.get("field", ""))):
                raise ValueError("Condition field must be a dotted record path")
            if condition.get("operator") not in ALLOWED_OPERATORS:
                raise ValueError("Unsupported condition operator")
            operator = condition["operator"]
            if operator != "is_empty" and "value" not in condition:
                raise ValueError("Condition value is required")
            if operator in {"in", "not_in"} and not isinstance(condition.get("value"), list):
                raise ValueError(f"{operator} conditions require a list value")
        if not actions:
            raise ValueError("At least one action is required")
        for action in actions:
            if not isinstance(action, dict) or action.get("type") not in ALLOWED_ACTIONS:
                raise ValueError("Unsupported automation action")
            params = action.get("params", {})
            if not isinstance(params, dict):
                raise ValueError("Action params must be an object")
            AutomationStore._reject_unsafe_values(params)

    @staticmethod
    def _reject_unsafe_values(value: Any, key: str = "") -> None:
        if key.lower() in {"sql", "code", "script", "shell", "command", "url", "webhook_url"}:
            raise ValueError("Automation actions cannot contain code, commands, SQL, or URLs")
        if isinstance(value, str) and value.strip().lower().startswith(("http://", "https://", "file://")):
            raise ValueError("Automation actions cannot contain arbitrary URLs")
        if isinstance(value, dict):
            for child_key, child in value.items():
                AutomationStore._reject_unsafe_values(child, str(child_key))
        elif isinstance(value, list):
            for child in value:
                AutomationStore._reject_unsafe_values(child, key)

    @staticmethod
    def _rule(row: Any) -> AutomationRule:
        return AutomationRule(
            id=row["id"],
            name=row["name"],
            trigger_name=row["trigger_name"],
            conditions=json.loads(row["conditions_json"]),
            actions=json.loads(row["actions_json"]),
            enabled=bool(row["enabled"]),
            dry_run=bool(row["dry_run"]),
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _execution(row: Any) -> AutomationExecution:
        return AutomationExecution(
            id=row["id"],
            rule_id=row["rule_id"],
            trigger_name=row["trigger_name"],
            record_key=row["record_key"],
            correlation_id=row["correlation_id"],
            mode=row["mode"],
            outcome=row["outcome"],
            result=json.loads(row["result_json"]),
            error=row["error"],
            created_at=row["created_at"],
        )


ActionHandler = Callable[[dict[str, Any], Mapping[str, Any]], Any]


class AutomationEngine:
    def __init__(
        self,
        store: AutomationStore,
        handlers: Mapping[str, ActionHandler],
        *,
        max_depth: int = 5,
    ) -> None:
        self.store, self.handlers, self.max_depth = store, dict(handlers), max_depth

    def preview(
        self, rule_id: str, records: Iterable[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        rule = self.store.get_rule(rule_id)
        if rule is None:
            raise KeyError(rule_id)
        result = []
        for record in records:
            matches = self.matches(rule, record)
            result.append(
                {
                    "record_key": self._record_key(record),
                    "matches": matches,
                    "actions": rule.actions if matches else [],
                }
            )
        return result

    def run(
        self,
        trigger_name: str,
        record: Mapping[str, Any],
        *,
        correlation_id: str | None = None,
        depth: int = 0,
    ) -> list[AutomationExecution]:
        if trigger_name not in ALLOWED_TRIGGERS:
            raise ValueError("Unsupported automation trigger")
        correlation_id = correlation_id or str(uuid.uuid4())
        record_key = self._record_key(record)
        executions: list[AutomationExecution] = []
        for rule in self.store.list_rules(trigger_name=trigger_name, enabled_only=True):
            mode = "dry_run" if rule.dry_run else "live"
            previous = self.store.find_execution(
                rule.id,
                record_key=record_key,
                correlation_id=correlation_id,
                mode=mode,
            )
            if previous is not None:
                executions.append(previous)
                continue
            if depth >= self.max_depth:
                executions.append(
                    self.store.save_execution(
                        rule,
                        record_key=record_key,
                        correlation_id=correlation_id,
                        mode=mode,
                        outcome="cycle_blocked",
                        error="Automation recursion limit reached",
                    )
                )
                continue
            if not self.matches(rule, record):
                executions.append(
                    self.store.save_execution(
                        rule,
                        record_key=record_key,
                        correlation_id=correlation_id,
                        mode=mode,
                        outcome="skipped",
                    )
                )
                continue
            if rule.dry_run:
                executions.append(
                    self.store.save_execution(
                        rule,
                        record_key=record_key,
                        correlation_id=correlation_id,
                        mode=mode,
                        outcome="matched",
                        result={"actions": rule.actions},
                    )
                )
                continue
            action_results: list[Any] = []
            try:
                for action_index, action in enumerate(rule.actions):
                    handler = self.handlers.get(action["type"])
                    if handler is None:
                        raise RuntimeError(f"No handler registered for {action['type']}")
                    params = dict(action.get("params", {}))
                    params["_automation"] = {
                        "rule_id": rule.id,
                        "correlation_id": str(record.get("_automation_correlation_id") or correlation_id),
                        "action_index": action_index,
                    }
                    action_results.append(handler(params, record))
                execution = self.store.save_execution(
                    rule,
                    record_key=record_key,
                    correlation_id=correlation_id,
                    mode=mode,
                    outcome="succeeded",
                    result={"actions": action_results},
                )
            except Exception as exc:
                execution = self.store.save_execution(
                    rule,
                    record_key=record_key,
                    correlation_id=correlation_id,
                    mode=mode,
                    outcome="failed",
                    error=str(exc),
                )
            executions.append(execution)
        return executions

    @classmethod
    def matches(cls, rule: AutomationRule, record: Mapping[str, Any]) -> bool:
        return all(cls._condition_matches(condition, record) for condition in rule.conditions)

    @classmethod
    def _condition_matches(cls, condition: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
        actual = cls._get_path(record, str(condition["field"]))
        expected, operator = condition.get("value"), condition["operator"]
        if operator == "equals":
            return actual == expected
        if operator == "not_equals":
            return actual != expected
        if operator == "in":
            return isinstance(expected, (list, tuple, set)) and actual in expected
        if operator == "not_in":
            return isinstance(expected, (list, tuple, set)) and actual not in expected
        if operator == "contains":
            try:
                return expected in actual
            except (TypeError, ValueError):
                return False
        if operator == "is_empty":
            return actual is None or actual == "" or actual == [] or actual == {}
        try:
            return {
                "lt": actual < expected,
                "lte": actual <= expected,
                "gt": actual > expected,
                "gte": actual >= expected,
            }[operator]
        except (TypeError, KeyError):
            return False

    @staticmethod
    def _get_path(record: Mapping[str, Any], path: str) -> Any:
        value: Any = record
        for part in path.split("."):
            if not isinstance(value, Mapping):
                return None
            value = value.get(part)
        return value

    @staticmethod
    def _record_key(record: Mapping[str, Any]) -> str:
        record_type = str(record.get("type") or record.get("record_type") or "record")
        record_id = str(record.get("id") or record.get("record_id") or "unknown")
        return f"{record_type}:{record_id}"
