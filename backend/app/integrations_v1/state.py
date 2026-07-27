from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from app.platform_db import connect, utc_now

from .schema import install_schema


def _iso() -> str:
    return utc_now().isoformat()


def _dump(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class IntegrationConnection:
    provider: str
    status: str
    account_label: str
    scopes: list[str]
    last_sync_at: str | None
    last_error: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ExternalReference:
    provider: str
    resource_type: str
    local_type: str
    local_id: str
    external_id: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class Notification:
    id: str
    category: str
    title: str
    body: str
    severity: str
    action_url: str
    dedupe_key: str | None
    read_at: str | None
    created_at: str
    updated_at: str
    version: int


class NotificationConflict(RuntimeError):
    pass


class IntegrationStateStore:
    def __init__(self, *, ensure_schema: bool = True) -> None:
        if ensure_schema:
            install_schema()

    def set_connection(
        self,
        provider: str,
        *,
        status: str,
        account_label: str = "",
        scopes: list[str] | None = None,
        last_error: str = "",
        synced: bool = False,
    ) -> IntegrationConnection:
        if status not in {"disconnected", "connecting", "connected", "degraded", "error"}:
            raise ValueError("Invalid integration status")
        if not provider.strip():
            raise ValueError("provider is required")
        now = _iso()
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO integration_connections
                    (provider, status, account_label, scopes_json, last_sync_at, last_error, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    status = excluded.status,
                    account_label = excluded.account_label,
                    scopes_json = excluded.scopes_json,
                    last_sync_at = CASE WHEN ? THEN excluded.last_sync_at ELSE integration_connections.last_sync_at END,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                (
                    provider,
                    status,
                    account_label,
                    _dump(scopes or []),
                    now if synced else None,
                    last_error,
                    now,
                    now,
                    int(synced),
                ),
            )
            row = conn.execute(
                "SELECT * FROM integration_connections WHERE provider = ?", (provider,)
            ).fetchone()
        return self._connection(row)

    def get_connection(self, provider: str) -> IntegrationConnection | None:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM integration_connections WHERE provider = ?", (provider,)
            ).fetchone()
        return self._connection(row) if row else None

    def set_cursor(self, provider: str, resource: str, cursor: str) -> None:
        if not provider or not resource or not cursor:
            raise ValueError("provider, resource and cursor are required")
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO integration_sync_cursors(provider, resource, cursor, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(provider, resource) DO UPDATE SET cursor = excluded.cursor, updated_at = excluded.updated_at
                """,
                (provider, resource, cursor, _iso()),
            )

    def get_cursor(self, provider: str, resource: str) -> str | None:
        with connect() as conn:
            row = conn.execute(
                "SELECT cursor FROM integration_sync_cursors WHERE provider = ? AND resource = ?",
                (provider, resource),
            ).fetchone()
        return row["cursor"] if row else None

    def clear_cursor(self, provider: str, resource: str) -> None:
        with connect() as conn:
            conn.execute(
                "DELETE FROM integration_sync_cursors WHERE provider = ? AND resource = ?",
                (provider, resource),
            )

    def put_external_reference(
        self,
        provider: str,
        resource_type: str,
        local_type: str,
        local_id: str | int,
        external_id: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> ExternalReference:
        values = [provider, resource_type, local_type, str(local_id), external_id]
        if any(not value.strip() for value in values):
            raise ValueError("External reference fields are required")
        now = _iso()
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO integration_external_refs
                    (provider, resource_type, local_type, local_id, external_id, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, resource_type, local_type, local_id) DO UPDATE SET
                    external_id = excluded.external_id,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (*values, _dump(dict(metadata or {})), now, now),
            )
            row = conn.execute(
                """
                SELECT * FROM integration_external_refs
                WHERE provider = ? AND resource_type = ? AND local_type = ? AND local_id = ?
                """,
                (provider, resource_type, local_type, str(local_id)),
            ).fetchone()
        return self._external(row)

    def find_external_reference(
        self,
        provider: str,
        resource_type: str,
        *,
        local_type: str | None = None,
        local_id: str | int | None = None,
        external_id: str | None = None,
    ) -> ExternalReference | None:
        if external_id is not None:
            sql, params = (
                "SELECT * FROM integration_external_refs WHERE provider = ? AND resource_type = ? AND external_id = ?",
                (provider, resource_type, external_id),
            )
        elif local_type is not None and local_id is not None:
            sql, params = (
                """SELECT * FROM integration_external_refs
                   WHERE provider = ? AND resource_type = ? AND local_type = ? AND local_id = ?""",
                (provider, resource_type, local_type, str(local_id)),
            )
        else:
            raise ValueError("Provide external_id or local_type and local_id")
        with connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return self._external(row) if row else None

    @staticmethod
    def _connection(row: Any) -> IntegrationConnection:
        return IntegrationConnection(
            provider=row["provider"],
            status=row["status"],
            account_label=row["account_label"],
            scopes=json.loads(row["scopes_json"]),
            last_sync_at=row["last_sync_at"],
            last_error=row["last_error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _external(row: Any) -> ExternalReference:
        return ExternalReference(
            provider=row["provider"],
            resource_type=row["resource_type"],
            local_type=row["local_type"],
            local_id=row["local_id"],
            external_id=row["external_id"],
            metadata=json.loads(row["metadata_json"]),
        )


class NotificationStore:
    def __init__(self, *, ensure_schema: bool = True) -> None:
        if ensure_schema:
            install_schema()

    def create(
        self,
        category: str,
        title: str,
        *,
        body: str = "",
        severity: str = "info",
        action_url: str = "",
        dedupe_key: str | None = None,
    ) -> Notification:
        if severity not in {"info", "success", "warning", "error"}:
            raise ValueError("Invalid notification severity")
        if not category.strip() or not title.strip():
            raise ValueError("category and title are required")
        notification_id, now = str(uuid.uuid4()), _iso()
        with connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO integration_notifications
                    (id, category, title, body, severity, action_url, dedupe_key, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (notification_id, category, title, body, severity, action_url, dedupe_key, now, now),
            )
            if dedupe_key:
                row = conn.execute(
                    "SELECT * FROM integration_notifications WHERE dedupe_key = ?", (dedupe_key,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM integration_notifications WHERE id = ?", (notification_id,)
                ).fetchone()
        return self._notification(row)

    def list(self, *, unread_only: bool = False, limit: int = 100) -> list[Notification]:
        where = "WHERE read_at IS NULL" if unread_only else ""
        with connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM integration_notifications {where} ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [self._notification(row) for row in rows]

    def mark_read(
        self, notification_id: str, *, version: int, read: bool = True
    ) -> Notification | None:
        with connect() as conn:
            cursor = conn.execute(
                """
                UPDATE integration_notifications
                SET read_at = ?, updated_at = ?, version = version + 1
                WHERE id = ? AND version = ?
                """,
                (_iso() if read else None, _iso(), notification_id, version),
            )
            row = conn.execute(
                "SELECT * FROM integration_notifications WHERE id = ?", (notification_id,)
            ).fetchone()
            if row is not None and cursor.rowcount != 1:
                raise NotificationConflict("Notification changed; reload and retry")
        return self._notification(row) if row else None

    @staticmethod
    def _notification(row: Any) -> Notification:
        return Notification(
            id=row["id"],
            category=row["category"],
            title=row["title"],
            body=row["body"],
            severity=row["severity"],
            action_url=row["action_url"],
            dedupe_key=row["dedupe_key"],
            read_at=row["read_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            version=row["version"],
        )
