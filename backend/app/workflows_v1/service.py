from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ValidationError

from app import platform_db
from app.v1 import core_service
from app.v1 import models as core_models

from .schema import install_schema


ConnectionFactory = Callable[[], AbstractContextManager[sqlite3.Connection]]
Runner = Callable[..., Awaitable[Any]]


class WorkflowNotFound(Exception):
    pass


class WorkflowConflict(Exception):
    pass


class WorkflowValidationError(Exception):
    pass


class DiscoveryCancelled(Exception):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _as_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def _idempotent_response(
    conn: sqlite3.Connection, scope: str, key: str, request_hash: str
) -> tuple[str, dict[str, Any]] | None:
    row = conn.execute(
        "SELECT request_hash, resource_id, response_json FROM workflow_idempotency WHERE scope = ? AND idempotency_key = ?",
        (scope, key),
    ).fetchone()
    if row is None:
        return None
    if row["request_hash"] != request_hash:
        raise WorkflowConflict("Idempotency key was already used with a different request")
    try:
        response = json.loads(row["response_json"] or "{}")
    except json.JSONDecodeError:
        response = {}
    return str(row["resource_id"]), response


def _remember_idempotency(
    conn: sqlite3.Connection,
    scope: str,
    key: str,
    request_hash: str,
    resource_id: str,
    response: dict[str, Any],
) -> None:
    conn.execute(
        """INSERT INTO workflow_idempotency
           (scope, idempotency_key, request_hash, resource_id, response_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (scope, key, request_hash, resource_id, _json(response), platform_db.utc_now().isoformat()),
    )


def _begin_write(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


async def _fake_discovery_runner(*, progress_callback=None, **recipe: Any) -> dict[str, Any]:
    niche = str(recipe["niche"])
    region = recipe.get("region")
    delay = max(0.0, float(os.getenv("CRM_DISCOVERY_FAKE_DELAY_MS", "20")) / 1000)
    if progress_callback:
        await progress_callback({"phase": "searching", "message": "Searching fake procurement data", "total": 1})
    if delay:
        await asyncio.sleep(delay)
    slug = re.sub(r"[^a-z0-9]+", "-", niche.lower()).strip("-") or "opportunity"
    result = {
        "domain": "contracts.example.test",
        "company_name": "Example Council",
        "status": "dry_run",
        "message": "Preview ready",
        "confidence_score": 92,
        "source_urls": [f"https://contracts.example.test/notices/{slug}-1"],
        "contract_title": f"{niche.title()} framework",
        "buyer_name": "Example Council",
        "portal_name": "Fake Contracts Portal",
        "portal_domain": "contracts.example.test",
        "contract_url": f"https://contracts.example.test/notices/{slug}-1",
        "contract_value": "GBP 125,000",
        "deadline": "2030-01-31",
        "procurement_stage": "Open",
        "contract_status": "Open",
        "availability_status": "Available",
        "availability_reason": "Fake test fixture",
        "priority_score": 80,
        "priority_reasons": ["Good niche match"],
        "dedupe_key": f"fake:{slug}:1",
    }
    if progress_callback:
        await progress_callback({"phase": "saving", "message": "Preview ready", "result": result})
    return {
        "dry_run": True,
        "niche": niche,
        "region": region,
        "requested_limit": recipe.get("limit", 10),
        "discovered": 1,
        "upserted": 1,
        "skipped": 0,
        "failed": 0,
        "results": [result],
    }


async def default_discovery_runner(**recipe: Any) -> Any:
    if _truthy_env("CRM_DISCOVERY_FAKE"):
        return await _fake_discovery_runner(**recipe)
    # Import lazily so portability-only operations do not initialise API clients.
    from app.lead_discovery.runner import run_discovery

    return await run_discovery(dry_run=True, **recipe)


def _result_key(result: dict[str, Any], index: int = 0) -> str:
    stable = (
        result.get("dedupe_key")
        or result.get("contract_url")
        or "|".join(
            str(result.get(key) or "")
            for key in ("portal_domain", "contract_title", "buyer_name", "deadline")
        )
    )
    return hashlib.sha256(f"{stable}|{index if not stable else ''}".encode("utf-8")).hexdigest()


def _decode_run(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for source, target, fallback in (
        ("recipe_json", "recipe", {}),
        ("result_json", "results", []),
    ):
        raw = item.pop(source, None)
        try:
            item[target] = json.loads(raw or _json(fallback))
        except json.JSONDecodeError:
            item[target] = fallback
    return item


class DiscoveryCoordinator:
    """Persist discovery runs and execute the existing runner outside request transactions."""

    def __init__(
        self,
        connection_factory: ConnectionFactory = platform_db.connect,
        runner: Runner = default_discovery_runner,
    ) -> None:
        self.connection_factory = connection_factory
        self.runner = runner
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def create(self, recipe: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        request_hash = _hash(recipe)
        with self.connection_factory() as conn:
            install_schema(conn)
            _begin_write(conn)
            previous = _idempotent_response(conn, "discovery.start", idempotency_key, request_hash)
            if previous:
                run_id, _ = previous
                return self._get(conn, run_id)
            run_id = uuid.uuid4().hex
            now = platform_db.utc_now().isoformat()
            conn.execute(
                """INSERT INTO discovery_runs
                   (id, recipe_json, state, phase, message, progress, result_json, created_at, updated_at)
                   VALUES (?, ?, 'queued', 'queued', 'Queued for discovery', 0, '[]', ?, ?)""",
                (run_id, _json(recipe), now, now),
            )
            initial = self._get(conn, run_id)
            _remember_idempotency(
                conn, "discovery.start", idempotency_key, request_hash, run_id, initial
            )
            # The worker uses another SQLite connection, so release this write first.
            conn.commit()
        self.start(run_id)
        return initial

    def start(self, run_id: str) -> None:
        with self._lock:
            current = self._threads.get(run_id)
            if current and current.is_alive():
                return
            thread = threading.Thread(
                target=self._execute,
                args=(run_id,),
                name=f"crm-discovery-{run_id[:8]}",
                daemon=True,
            )
            self._threads[run_id] = thread
            thread.start()

    def recover(self) -> int:
        """Restart durable queued/running work after application startup."""
        with self.connection_factory() as conn:
            install_schema(conn)
            rows = conn.execute(
                "SELECT id FROM discovery_runs WHERE state IN ('queued', 'running') ORDER BY created_at"
            ).fetchall()
            if rows:
                conn.execute(
                    """UPDATE discovery_runs SET state = 'queued', phase = 'queued',
                       message = 'Recovered after restart', updated_at = ?
                       WHERE state = 'running'""",
                    (platform_db.utc_now().isoformat(),),
                )
                conn.commit()
        for row in rows:
            self.start(str(row["id"]))
        return len(rows)

    def list(self, limit: int = 50) -> dict[str, Any]:
        with self.connection_factory() as conn:
            install_schema(conn)
            rows = conn.execute(
                "SELECT * FROM discovery_runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return {"items": [_decode_run(row) for row in rows], "next_cursor": None}

    def get(self, run_id: str) -> dict[str, Any]:
        with self.connection_factory() as conn:
            install_schema(conn)
            return self._get(conn, run_id)

    @staticmethod
    def _get(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM discovery_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise WorkflowNotFound("Discovery run not found")
        return _decode_run(row)

    def cancel(self, run_id: str) -> dict[str, Any]:
        with self.connection_factory() as conn:
            install_schema(conn)
            self._get(conn, run_id)
            now = platform_db.utc_now().isoformat()
            conn.execute(
                """UPDATE discovery_runs SET state = 'cancelled', phase = 'cancelled',
                   message = 'Cancelled by operator', updated_at = ?, completed_at = ?
                   WHERE id = ? AND state IN ('queued', 'running')""",
                (now, now, run_id),
            )
            return self._get(conn, run_id)

    def import_results(self, run_id: str, idempotency_key: str) -> dict[str, Any]:
        request_hash = _hash({"run_id": run_id})
        with self.connection_factory() as conn:
            install_schema(conn)
            _begin_write(conn)
            run = self._get(conn, run_id)
            previous = _idempotent_response(
                conn, "discovery.import", idempotency_key, request_hash
            )
            if previous:
                return previous[1]
            if run["state"] not in {"completed", "cancelled"}:
                raise WorkflowConflict("Discovery results can be imported only after the run stops")
            report: dict[str, Any] = {
                "run_id": run_id,
                "total_results": len(run["results"]),
                "imported": 0,
                "already_imported": 0,
                "skipped": 0,
                "failed": 0,
                "items": [],
            }
            for index, raw in enumerate(run["results"], start=1):
                result = dict(raw)
                status = str(result.get("status") or "")
                if status not in {"dry_run", "upserted"} or result.get("availability_status") == "Unavailable":
                    report["skipped"] += 1
                    report["items"].append({"row": index, "status": "skipped", "message": result.get("message", "")})
                    continue
                result_key = _result_key(result, index)
                linked = conn.execute(
                    "SELECT tender_id FROM workflow_discovery_imports WHERE run_id = ? AND result_key = ?",
                    (run_id, result_key),
                ).fetchone()
                if linked:
                    report["already_imported"] += 1
                    report["items"].append(
                        {"row": index, "status": "already_imported", "tender_id": int(linked["tender_id"])}
                    )
                    continue
                conn.execute("SAVEPOINT discovery_result")
                try:
                    tender = core_service.create_tender(
                        conn, core_models.TenderCreate.model_validate(_tender_payload(run, result))
                    )
                    conn.execute(
                        """UPDATE tender_notices SET discovery_run_id = COALESCE(discovery_run_id, ?)
                           WHERE id = ?""",
                        (run_id, tender["id"]),
                    )
                    conn.execute(
                        """INSERT INTO workflow_discovery_imports
                           (run_id, result_key, tender_id, created_at) VALUES (?, ?, ?, ?)""",
                        (run_id, result_key, tender["id"], platform_db.utc_now().isoformat()),
                    )
                    conn.execute("RELEASE discovery_result")
                    report["imported"] += 1
                    report["items"].append(
                        {"row": index, "status": "imported", "tender_id": tender["id"]}
                    )
                except Exception as exc:  # keep useful rows when one provider result is malformed
                    conn.execute("ROLLBACK TO discovery_result")
                    conn.execute("RELEASE discovery_result")
                    report["failed"] += 1
                    report["items"].append(
                        {"row": index, "status": "error", "message": str(exc)}
                    )
            _remember_idempotency(
                conn,
                "discovery.import",
                idempotency_key,
                request_hash,
                run_id,
                report,
            )
            return report

    def _execute(self, run_id: str) -> None:
        try:
            with self.connection_factory() as conn:
                row = conn.execute(
                    "SELECT recipe_json, state FROM discovery_runs WHERE id = ?", (run_id,)
                ).fetchone()
                if row is None or row["state"] == "cancelled":
                    return
                recipe = json.loads(row["recipe_json"])
                now = platform_db.utc_now().isoformat()
                conn.execute(
                    """UPDATE discovery_runs SET state = 'running', phase = 'searching',
                       message = 'Discovery started', updated_at = ? WHERE id = ? AND state = 'queued'""",
                    (now, run_id),
                )

            async def progress(event: dict[str, Any]) -> None:
                payload = {key: _as_jsonable(value) for key, value in event.items()}
                with self.connection_factory() as conn:
                    row = conn.execute(
                        "SELECT state, result_json, recipe_json FROM discovery_runs WHERE id = ?", (run_id,)
                    ).fetchone()
                    if row is None or row["state"] == "cancelled":
                        raise DiscoveryCancelled()
                    results = json.loads(row["result_json"] or "[]")
                    incoming = payload.get("result")
                    if isinstance(incoming, dict):
                        key = _result_key(incoming)
                        replaced = False
                        for position, current in enumerate(results):
                            if _result_key(current) == key:
                                results[position] = incoming
                                replaced = True
                                break
                        if not replaced:
                            results.append(incoming)
                    requested = max(1, int(json.loads(row["recipe_json"]).get("limit", 1)))
                    finished = sum(
                        1
                        for item in results
                        if item.get("status") in {"dry_run", "upserted", "skipped", "failed"}
                    )
                    conn.execute(
                        """UPDATE discovery_runs SET phase = ?, message = ?, progress = ?,
                           result_json = ?, updated_at = ? WHERE id = ? AND state = 'running'""",
                        (
                            str(payload.get("phase") or "running"),
                            str(payload.get("message") or ""),
                            min(99, int(finished * 100 / requested)),
                            _json(results),
                            platform_db.utc_now().isoformat(),
                            run_id,
                        ),
                    )

            response = asyncio.run(self.runner(progress_callback=progress, **recipe))
            payload = _as_jsonable(response)
            results = payload.get("results", []) if isinstance(payload, dict) else []
            now = platform_db.utc_now().isoformat()
            with self.connection_factory() as conn:
                conn.execute(
                    """UPDATE discovery_runs SET state = 'completed', phase = 'completed',
                       message = ?, progress = 100, result_json = ?, updated_at = ?, completed_at = ?
                       WHERE id = ? AND state = 'running'""",
                    (
                        f"Discovery completed with {len(results)} result(s)",
                        _json(results),
                        now,
                        now,
                        run_id,
                    ),
                )
        except DiscoveryCancelled:
            return
        except Exception as exc:  # durable failure is visible and can be started again by a future retry hook
            now = platform_db.utc_now().isoformat()
            with self.connection_factory() as conn:
                conn.execute(
                    """UPDATE discovery_runs SET state = 'failed', phase = 'failed', message = ?,
                       error = ?, updated_at = ?, completed_at = ?
                       WHERE id = ? AND state != 'cancelled'""",
                    ("Discovery failed", str(exc), now, now, run_id),
                )
        finally:
            with self._lock:
                self._threads.pop(run_id, None)


def _known(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"unknown", "n/a", "none", "null"} else text


def _http_url(value: Any) -> str:
    text = _known(value)
    parsed = urlparse(text)
    return text if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _minor_units(value: Any) -> int:
    if isinstance(value, int):
        return max(0, value)
    text = _known(value).lower().replace(",", "")
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*(bn|b|m|million|k|thousand)?", text)
    if not match:
        return 0
    try:
        amount = Decimal(match.group(1))
    except InvalidOperation:
        return 0
    multiplier = {
        "bn": Decimal("1000000000"),
        "b": Decimal("1000000000"),
        "m": Decimal("1000000"),
        "million": Decimal("1000000"),
        "k": Decimal("1000"),
        "thousand": Decimal("1000"),
    }.get(match.group(2) or "", Decimal(1))
    return max(0, int(amount * multiplier * 100))


def _deadline(value: Any) -> str | None:
    text = _known(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _score(value: Any) -> int:
    try:
        return max(0, min(100, int(value or 0)))
    except (TypeError, ValueError):
        return 0


def _tender_payload(run: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    recipe = run.get("recipe") or {}
    contract_url = _http_url(result.get("contract_url"))
    sources = [
        url
        for url in dict.fromkeys(
            _http_url(value) for value in (result.get("source_urls") or [])
        )
        if url
    ]
    return {
        "title": _known(result.get("contract_title"))
        or _known(result.get("company_name"))
        or f"{recipe.get('niche', 'Tender')} opportunity",
        "buyer_name": _known(result.get("buyer_name")) or _known(result.get("company_name")),
        "portal_name": _known(result.get("portal_name")),
        "contract_url": contract_url,
        "contract_value_text": _known(result.get("contract_value")),
        "estimated_value_minor": _minor_units(
            result.get("estimated_value_minor", result.get("contract_value"))
        ),
        "deadline": _deadline(result.get("deadline")),
        "procurement_stage": _known(result.get("procurement_stage")),
        "contract_status": _known(result.get("contract_status")),
        "availability_status": _known(result.get("availability_status")) or "Unverified",
        "availability_reason": _known(result.get("availability_reason")),
        "niche": str(recipe.get("niche") or ""),
        "region": str(recipe.get("region") or ""),
        "location": _known(result.get("location")),
        "confidence_score": _score(result.get("confidence_score")),
        "priority_score": _score(result.get("priority_score")),
        "priority_reasons": list(result.get("priority_reasons") or []),
        "outreach_angle": _known(result.get("outreach_angle")),
        "source_urls": sources,
        "dedupe_key": _known(result.get("dedupe_key")),
    }


ENTITY_ALIASES = {
    "account": "accounts",
    "accounts": "accounts",
    "contact": "contacts",
    "contacts": "contacts",
    "lead": "leads",
    "leads": "leads",
    "opportunity": "opportunities",
    "opportunities": "opportunities",
    "deal": "opportunities",
    "deals": "opportunities",
    "tender": "tenders",
    "tenders": "tenders",
}


IMPORT_FIELDS: dict[str, set[str]] = {
    "accounts": {
        "name", "legal_name", "domain", "website", "phone", "billing_email",
        "company_number", "vat_number", "source", "payment_terms_days", "status",
        "health_status", "health_score", "renewal_date", "notes", "roles", "custom",
    },
    "contacts": {
        "account_id", "account_name", "first_name", "last_name", "display_name",
        "job_title", "email", "phone", "mobile", "preferred_channel", "source",
        "lawful_basis", "status", "notes", "custom",
    },
    "leads": {
        "account_id", "account_name", "contact_id", "contact_email", "title", "company",
        "email", "phone", "source", "status", "score", "estimated_value_minor",
        "next_action", "notes",
    },
    "opportunities": {
        "account_id", "account_name", "primary_contact_id", "contact_email", "tender_id",
        "stage_id", "stage_name", "type", "title", "value_minor", "probability_bps",
        "expected_close_date", "source", "next_action", "notes",
    },
}


FIELD_ALIASES = {
    "account": "account_name",
    "company_name": "account_name",
    "contact": "contact_email",
    "deal_name": "title",
    "opportunity_name": "title",
    "lead_name": "title",
    "name_of_account": "name",
}


MODEL_BY_ENTITY = {
    "accounts": core_models.AccountCreate,
    "contacts": core_models.ContactCreate,
    "leads": core_models.LeadCreate,
    "opportunities": core_models.OpportunityCreate,
}


CREATE_BY_ENTITY = {
    "accounts": core_service.create_account,
    "contacts": core_service.create_contact,
    "leads": core_service.create_lead,
    "opportunities": core_service.create_opportunity,
}


def _field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _canonical_entity(entity_type: str, *, imports: bool = False) -> str:
    canonical = ENTITY_ALIASES.get(_field_name(entity_type))
    if canonical is None or (imports and canonical not in IMPORT_FIELDS):
        choices = ", ".join(sorted(IMPORT_FIELDS if imports else set(ENTITY_ALIASES.values())))
        raise WorkflowValidationError(f"Unsupported entity type; choose one of: {choices}")
    return canonical


def _normalise_mapping(
    entity_type: str, headers: list[str], mapping: dict[str, str]
) -> dict[str, str]:
    allowed = IMPORT_FIELDS[entity_type]
    by_normal = {_field_name(header): header for header in headers}
    result: dict[str, str] = {}
    if not mapping:
        for normal, source in by_normal.items():
            target = FIELD_ALIASES.get(normal, normal)
            if target in allowed:
                result[target] = source
        return result
    for left, right in mapping.items():
        left_field = FIELD_ALIASES.get(_field_name(left), _field_name(left))
        right_field = FIELD_ALIASES.get(_field_name(right), _field_name(right))
        if left_field in allowed:
            target, source = left_field, right
        elif right_field in allowed:
            target, source = right_field, left
        else:
            raise WorkflowValidationError(f"Mapping '{left}' -> '{right}' has no supported target field")
        actual_source = by_normal.get(_field_name(source))
        if actual_source is None:
            raise WorkflowValidationError(f"CSV column '{source}' was not found")
        result[target] = actual_source
    return result


def _lookup_id(
    conn: sqlite3.Connection, table: str, column: str, value: str
) -> int | None:
    row = conn.execute(
        f"SELECT id FROM {table} WHERE lower({column}) = lower(?) AND archived_at IS NULL ORDER BY id LIMIT 1",
        (value,),
    ).fetchone()
    return int(row["id"]) if row else None


def _integer(value: Any, field: str) -> int:
    text = str(value).strip().replace(",", "")
    try:
        return int(text)
    except ValueError as exc:
        raise WorkflowValidationError(f"{field} must be a whole number") from exc


def _prepare_import_row(
    conn: sqlite3.Connection, entity_type: str, mapped: dict[str, str]
) -> tuple[BaseModel, dict[str, Any]]:
    data: dict[str, Any] = {key: value.strip() for key, value in mapped.items() if value.strip()}
    if "custom" in data:
        try:
            data["custom"] = json.loads(data["custom"])
        except json.JSONDecodeError as exc:
            raise WorkflowValidationError("custom must be a JSON object") from exc
    if "roles" in data:
        data["roles"] = [item.strip().lower() for item in re.split(r"[;,|]", data["roles"]) if item.strip()]
    for field in {
        "account_id", "contact_id", "primary_contact_id", "tender_id", "stage_id",
        "payment_terms_days", "health_score", "score", "estimated_value_minor",
        "value_minor", "probability_bps",
    } & data.keys():
        data[field] = _integer(data[field], field)

    account_name = str(data.pop("account_name", "")).strip()
    if account_name and not data.get("account_id"):
        account_id = _lookup_id(conn, "accounts", "name", account_name)
        if account_id is None:
            raise WorkflowValidationError(f"Account '{account_name}' was not found")
        data["account_id"] = account_id
    if entity_type == "leads" and account_name and not data.get("company"):
        data["company"] = account_name

    contact_email = str(data.pop("contact_email", "")).strip()
    if contact_email:
        contact_id = _lookup_id(conn, "contacts", "email", contact_email)
        if contact_id is None:
            raise WorkflowValidationError(f"Contact '{contact_email}' was not found")
        field = "primary_contact_id" if entity_type == "opportunities" else "contact_id"
        data.setdefault(field, contact_id)

    stage_name = str(data.pop("stage_name", "")).strip()
    if stage_name and not data.get("stage_id"):
        row = conn.execute(
            "SELECT id FROM pipeline_stages WHERE lower(name) = lower(?) AND archived_at IS NULL",
            (stage_name,),
        ).fetchone()
        if row is None:
            raise WorkflowValidationError(f"Pipeline stage '{stage_name}' was not found")
        data["stage_id"] = int(row["id"])

    if entity_type == "contacts" and not data.get("display_name"):
        data["display_name"] = " ".join(
            part for part in (data.get("first_name", ""), data.get("last_name", "")) if part
        )
    try:
        model = MODEL_BY_ENTITY[entity_type].model_validate(data)
    except ValidationError as exc:
        messages = [
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        ]
        raise WorkflowValidationError("; ".join(messages)) from exc
    return model, model.model_dump(mode="json")


def _dedupe(
    conn: sqlite3.Connection, entity_type: str, data: dict[str, Any]
) -> tuple[int | None, str, str]:
    if entity_type == "accounts":
        if data.get("domain"):
            column, value = "domain", data["domain"]
        else:
            column, value = "name", data.get("name", "")
        row = conn.execute(
            f"SELECT id FROM accounts WHERE lower({column}) = lower(?) AND archived_at IS NULL",
            (value,),
        ).fetchone()
        return (int(row["id"]) if row else None, column, f"{column}:{str(value).lower()}")
    if entity_type == "contacts":
        if data.get("email"):
            row = conn.execute(
                "SELECT id FROM contacts WHERE lower(email) = lower(?) AND archived_at IS NULL",
                (data["email"],),
            ).fetchone()
            return (int(row["id"]) if row else None, "email", f"email:{str(data['email']).lower()}")
        row = conn.execute(
            """SELECT id FROM contacts WHERE lower(display_name) = lower(?)
               AND account_id IS ? AND archived_at IS NULL""",
            (data.get("display_name", ""), data.get("account_id")),
        ).fetchone()
        key = f"name:{str(data.get('display_name', '')).lower()}:{data.get('account_id')}"
        return (int(row["id"]) if row else None, "display_name + account", key)
    if entity_type == "leads":
        row = conn.execute(
            """SELECT id FROM sales_leads WHERE lower(title) = lower(?) AND lower(company) = lower(?)
               AND lower(email) = lower(?) AND archived_at IS NULL""",
            (data.get("title", ""), data.get("company", ""), data.get("email") or ""),
        ).fetchone()
        key = "lead:" + "|".join(
            str(data.get(field) or "").lower() for field in ("title", "company", "email")
        )
        return (int(row["id"]) if row else None, "title + company + email", key)
    row = conn.execute(
        """SELECT id FROM opportunities WHERE account_id = ? AND lower(title) = lower(?)
           AND archived_at IS NULL""",
        (data.get("account_id"), data.get("title", "")),
    ).fetchone()
    key = f"opportunity:{data.get('account_id')}:{str(data.get('title', '')).lower()}"
    return (int(row["id"]) if row else None, "account + title", key)


def import_csv(
    conn: sqlite3.Connection,
    *,
    entity_type: str,
    csv_text: str,
    mapping: dict[str, str] | None = None,
    filename: str = "import.csv",
    dry_run: bool = True,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    install_schema(conn)
    entity = _canonical_entity(entity_type, imports=True)
    request = {
        "entity_type": entity,
        "csv_text": csv_text,
        "mapping": mapping or {},
        "filename": filename,
    }
    request_hash = _hash(request)
    if not dry_run:
        if not idempotency_key:
            raise WorkflowValidationError("An Idempotency-Key is required when committing an import")
        _begin_write(conn)
        previous = _idempotent_response(
            conn, f"csv.import.{entity}", idempotency_key, request_hash
        )
        if previous:
            return previous[1]

    try:
        reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
        headers = list(reader.fieldnames or [])
        rows = list(reader)
    except csv.Error as exc:
        raise WorkflowValidationError(f"Invalid CSV: {exc}") from exc
    if not headers:
        raise WorkflowValidationError("CSV must include a header row")
    normalised_mapping = _normalise_mapping(entity, headers, mapping or {})
    if not normalised_mapping:
        raise WorkflowValidationError("No CSV columns map to supported fields")

    import_id = uuid.uuid4().hex if not dry_run else None
    report: dict[str, Any] = {
        "import_id": import_id,
        "entity_type": entity,
        "dry_run": dry_run,
        "total_rows": len(rows),
        "valid_rows": 0,
        "create_count": 0,
        "created_count": 0,
        "duplicate_count": 0,
        "error_count": 0,
        "mapping": normalised_mapping,
        "rows": [],
    }
    seen: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        raw = {target: str(row.get(source) or "") for target, source in normalised_mapping.items()}
        try:
            model, data = _prepare_import_row(conn, entity, raw)
            matched_id, reason, dedupe_key = _dedupe(conn, entity, data)
            if matched_id is not None or dedupe_key in seen:
                report["valid_rows"] += 1
                report["duplicate_count"] += 1
                report["rows"].append(
                    {
                        "row": row_number,
                        "status": "duplicate",
                        "matched_id": matched_id,
                        "reason": reason if matched_id is not None else "duplicate in CSV",
                    }
                )
                continue
            seen.add(dedupe_key)
            report["valid_rows"] += 1
            report["create_count"] += 1
            if dry_run:
                report["rows"].append({"row": row_number, "status": "would_create", "data": data})
                continue
            conn.execute("SAVEPOINT csv_import_row")
            try:
                created = CREATE_BY_ENTITY[entity](conn, model)
                conn.execute("RELEASE csv_import_row")
            except Exception:
                conn.execute("ROLLBACK TO csv_import_row")
                conn.execute("RELEASE csv_import_row")
                raise
            report["created_count"] += 1
            report["rows"].append({"row": row_number, "status": "created", "id": created["id"]})
        except Exception as exc:
            report["error_count"] += 1
            report["rows"].append({"row": row_number, "status": "error", "message": str(exc)})

    if not dry_run:
        now = platform_db.utc_now().isoformat()
        conn.execute(
            """INSERT INTO import_jobs
               (id, entity_type, filename, state, report_json, created_at, completed_at)
               VALUES (?, ?, ?, 'completed', ?, ?, ?)""",
            (import_id, entity, filename, _json(report), now, now),
        )
        _remember_idempotency(
            conn,
            f"csv.import.{entity}",
            idempotency_key or "",
            request_hash,
            import_id or "",
            report,
        )
    return report


def export_records(
    conn: sqlite3.Connection, entity_type: str, *, include_archived: bool = False
) -> list[dict[str, Any]]:
    entity = _canonical_entity(entity_type)
    archived = "" if include_archived else " WHERE archived_at IS NULL"
    if entity == "accounts":
        items = core_service.records(conn.execute(f"SELECT * FROM accounts{archived} ORDER BY id"))
        for item in items:
            item["roles"] = [
                row["role"]
                for row in conn.execute(
                    "SELECT role FROM account_roles WHERE account_id = ? ORDER BY role", (item["id"],)
                )
            ]
        return items
    if entity == "contacts":
        where = "" if include_archived else " WHERE c.archived_at IS NULL"
        return core_service.records(
            conn.execute(
                f"""SELECT c.*, a.name AS account_name FROM contacts c
                    LEFT JOIN accounts a ON a.id = c.account_id{where} ORDER BY c.id"""
            )
        )
    if entity == "leads":
        return core_service.records(
            conn.execute(f"SELECT * FROM sales_leads{archived} ORDER BY id")
        )
    if entity == "opportunities":
        where = "" if include_archived else " WHERE o.archived_at IS NULL"
        return core_service.records(
            conn.execute(
                f"""SELECT o.*, a.name AS account_name, c.email AS contact_email,
                           s.name AS stage_name
                    FROM opportunities o JOIN accounts a ON a.id = o.account_id
                    JOIN pipeline_stages s ON s.id = o.stage_id
                    LEFT JOIN contacts c ON c.id = o.primary_contact_id{where}
                    ORDER BY o.id"""
            )
        )
    return core_service.records(
        conn.execute(f"SELECT * FROM tender_notices{archived} ORDER BY id")
    )


def records_to_csv(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    fields = list(dict.fromkeys(key for item in items for key in item))
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore", lineterminator="\r\n")
    writer.writeheader()
    for item in items:
        writer.writerow(
            {
                key: _json(value) if isinstance(value, (dict, list)) else "" if value is None else value
                for key, value in item.items()
            }
        )
    return output.getvalue()


def wait_for_discovery(
    coordinator: DiscoveryCoordinator, run_id: str, timeout: float = 5.0
) -> dict[str, Any]:
    """Small deterministic hook for CLI/tests; the HTTP API remains polling based."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = coordinator.get(run_id)
        if run["state"] in {"completed", "failed", "cancelled"}:
            return run
        time.sleep(0.01)
    raise TimeoutError(f"Discovery run {run_id} did not stop within {timeout} seconds")
