"""Durable external-integration primitives for the local CRM."""

from .automation import AutomationEngine, AutomationStore
from .backup import (
    apply_staged_restore,
    create_backup,
    prune_backups,
    restore_backup,
    stage_restore,
    validate_backup,
)
from .google import GoogleWorkspaceAdapter
from .jobs import (
    JobStore,
    JobWorker,
    IdempotencyConflict,
    OutboxStore,
    OutboxWorker,
    PermanentJobError,
    RetryableJobError,
    UnknownExternalOutcome,
)
from .router import create_router, router
from .schema import install_schema
from .secrets import CredentialStore, MemoryCredentialBackend
from .state import IntegrationStateStore, NotificationStore
from .stripe import StripeAdapter
from .worker import Worker

__all__ = [
    "AutomationEngine",
    "AutomationStore",
    "CredentialStore",
    "GoogleWorkspaceAdapter",
    "IntegrationStateStore",
    "IdempotencyConflict",
    "JobStore",
    "JobWorker",
    "MemoryCredentialBackend",
    "NotificationStore",
    "OutboxStore",
    "OutboxWorker",
    "PermanentJobError",
    "RetryableJobError",
    "StripeAdapter",
    "UnknownExternalOutcome",
    "Worker",
    "apply_staged_restore",
    "create_backup",
    "create_router",
    "install_schema",
    "prune_backups",
    "restore_backup",
    "router",
    "stage_restore",
    "validate_backup",
]
