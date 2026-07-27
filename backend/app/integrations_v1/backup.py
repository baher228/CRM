from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import uuid
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.platform_db import connect, utc_now


@dataclass(frozen=True)
class BackupInfo:
    path: str
    sha256: str
    size_bytes: int
    created_at: str
    integrity: str = "ok"


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_backup(path: str | Path, *, expected_sha256: str | None = None) -> BackupInfo:
    backup = Path(path).resolve()
    if not backup.is_file():
        raise FileNotFoundError(backup)
    actual = _checksum(backup)
    if expected_sha256 and not hmac.compare_digest(actual, expected_sha256):
        raise ValueError("Backup checksum does not match its manifest")
    try:
        with closing(sqlite3.connect(backup)) as conn:
            result = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    except sqlite3.DatabaseError as exc:
        raise ValueError("Backup is not a readable SQLite database") from exc
    if result.lower() != "ok":
        raise ValueError(f"Backup integrity check failed: {result}")
    created = datetime.fromtimestamp(backup.stat().st_mtime, tz=timezone.utc).isoformat()
    return BackupInfo(str(backup), actual, backup.stat().st_size, created)


def create_backup(
    destination_directory: str | Path,
    *,
    source_path: str | Path | None = None,
) -> BackupInfo:
    destination = Path(destination_directory).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    stamp = utc_now().strftime("%Y%m%dT%H%M%S.%fZ")
    target = destination / f"crm-{stamp}.sqlite3"
    try:
        with closing(sqlite3.connect(target)) as target_conn:
            if source_path is None:
                with connect() as source_conn:
                    source_conn.backup(target_conn)
            else:
                source = Path(source_path).resolve()
                if not source.is_file():
                    raise FileNotFoundError(source)
                with closing(sqlite3.connect(source)) as source_conn:
                    source_conn.backup(target_conn)
            target_conn.commit()
        info = validate_backup(target)
        manifest = target.with_suffix(target.suffix + ".json")
        manifest.write_text(json.dumps(asdict(info), indent=2), encoding="utf-8")
        return info
    except Exception:
        target.unlink(missing_ok=True)
        target.with_suffix(target.suffix + ".json").unlink(missing_ok=True)
        raise


def restore_backup(
    backup_path: str | Path,
    target_path: str | Path,
    *,
    offline: bool = False,
) -> Path:
    """Restore atomically; the application worker must be stopped first."""
    backup = Path(backup_path).resolve()
    target = Path(target_path).resolve()
    manifest_path = backup.with_suffix(backup.suffix + ".json")
    expected = None
    if manifest_path.is_file():
        expected = json.loads(manifest_path.read_text(encoding="utf-8")).get("sha256")
    validate_backup(backup, expected_sha256=expected)
    for sidecar_suffix in ("-wal", "-shm"):
        sidecar = Path(str(target) + sidecar_suffix)
        if sidecar.exists() and not offline:
            raise RuntimeError("Stop the application and checkpoint SQLite before restore")
        if sidecar.exists():
            sidecar.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.restore-{uuid.uuid4().hex}.tmp")
    safety_copy: Path | None = None
    try:
        with closing(sqlite3.connect(backup)) as source_conn, closing(
            sqlite3.connect(temporary)
        ) as target_conn:
            source_conn.backup(target_conn)
            target_conn.commit()
        validate_backup(temporary)
        if target.exists():
            stamp = utc_now().strftime("%Y%m%dT%H%M%S.%fZ")
            safety_copy = target.with_name(f"{target.stem}.pre-restore-{stamp}{target.suffix}")
            os.replace(target, safety_copy)
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        if safety_copy and safety_copy.exists() and not target.exists():
            os.replace(safety_copy, target)
        raise
    return target


def stage_restore(backup_path: str | Path, target_path: str | Path) -> Path:
    """Validate and stage a restore marker that must be applied before DB bootstrap."""
    backup, target = Path(backup_path).resolve(), Path(target_path).resolve()
    info = validate_backup(backup)
    target.parent.mkdir(parents=True, exist_ok=True)
    marker = target.parent / f".{target.name}.pending-restore.json"
    temporary = marker.with_suffix(marker.suffix + f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(
            {
                "backup_path": str(backup),
                "target_path": str(target),
                "sha256": info.sha256,
                "staged_at": utc_now().isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, marker)
    return marker


def apply_staged_restore(target_path: str | Path) -> Path | None:
    """Apply a staged restore before the application opens the target database."""
    target = Path(target_path).resolve()
    marker = target.parent / f".{target.name}.pending-restore.json"
    if not marker.is_file():
        return None
    payload = json.loads(marker.read_text(encoding="utf-8"))
    staged_target = Path(payload["target_path"]).resolve()
    if staged_target != target:
        raise ValueError("Staged restore target does not match this database")
    validate_backup(payload["backup_path"], expected_sha256=payload["sha256"])
    restored = restore_backup(payload["backup_path"], target, offline=True)
    marker.unlink()
    return restored


def prune_backups(
    directory: str | Path,
    *,
    daily: int = 30,
    monthly: int = 12,
    annual: int = 7,
) -> list[Path]:
    """Keep the newest snapshot for the requested day/month/year buckets."""
    if min(daily, monthly, annual) < 0:
        raise ValueError("Retention counts cannot be negative")
    files = sorted(Path(directory).resolve().glob("crm-*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
    keep: set[Path] = set()
    buckets: list[set[str]] = [set(), set(), set()]
    limits = [daily, monthly, annual]
    for path in files:
        timestamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        keys = [timestamp.strftime("%Y-%m-%d"), timestamp.strftime("%Y-%m"), timestamp.strftime("%Y")]
        for index, key in enumerate(keys):
            if len(buckets[index]) < limits[index] and key not in buckets[index]:
                buckets[index].add(key)
                keep.add(path)
    deleted: list[Path] = []
    for path in files:
        if path not in keep:
            path.unlink()
            path.with_suffix(path.suffix + ".json").unlink(missing_ok=True)
            deleted.append(path)
    return deleted
