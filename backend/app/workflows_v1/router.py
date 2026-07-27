from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse

from app import platform_db

from .models import CsvImportRequest, DiscoveryRunRequest
from .schema import install_schema
from .service import (
    ConnectionFactory,
    DiscoveryCoordinator,
    WorkflowConflict,
    WorkflowNotFound,
    WorkflowValidationError,
    export_records,
    import_csv,
    records_to_csv,
)


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, WorkflowNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, WorkflowConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, WorkflowValidationError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def create_router(
    *,
    connection_factory: ConnectionFactory = platform_db.connect,
    coordinator: DiscoveryCoordinator | None = None,
) -> APIRouter:
    api = APIRouter()
    discovery = coordinator or DiscoveryCoordinator(connection_factory=connection_factory)

    @api.get("/discovery/runs")
    def list_discovery_runs(
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        return discovery.list(limit)

    @api.get("/discovery/portals")
    def list_discovery_portals(niche: str = "", region: str | None = None) -> dict[str, Any]:
        from app.lead_discovery.search_companies_with_tavily import portal_metadata

        return {"items": portal_metadata(niche=niche, region=region), "next_cursor": None}

    @api.post("/discovery/runs", status_code=202)
    def create_discovery_run(
        request: DiscoveryRunRequest,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ) -> dict[str, Any]:
        try:
            return discovery.create(request.model_dump(mode="json"), idempotency_key)
        except Exception as exc:
            _raise_http(exc)
            raise

    @api.get("/discovery/runs/{run_id}")
    def get_discovery_run(run_id: str) -> dict[str, Any]:
        try:
            return discovery.get(run_id)
        except Exception as exc:
            _raise_http(exc)
            raise

    @api.post("/discovery/runs/{run_id}/cancel")
    def cancel_discovery_run(
        run_id: str,
        _idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ) -> dict[str, Any]:
        try:
            return discovery.cancel(run_id)
        except Exception as exc:
            _raise_http(exc)
            raise

    @api.post("/discovery/runs/{run_id}/import")
    def import_discovery_results(
        run_id: str,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ) -> dict[str, Any]:
        try:
            return discovery.import_results(run_id, idempotency_key)
        except Exception as exc:
            _raise_http(exc)
            raise

    def run_csv_import(
        request: CsvImportRequest, dry_run: bool, idempotency_key: str | None
    ) -> dict[str, Any]:
        if not dry_run and not idempotency_key:
            raise HTTPException(status_code=422, detail="Idempotency-Key header is required")
        try:
            with connection_factory() as conn:
                install_schema(conn)
                return import_csv(
                    conn,
                    entity_type=request.entity_type,
                    csv_text=request.csv_text,
                    mapping=request.mapping,
                    filename=request.filename,
                    dry_run=dry_run,
                    idempotency_key=idempotency_key,
                )
        except Exception as exc:
            _raise_http(exc)
            raise

    @api.post("/imports/csv/preview")
    @api.post("/imports/csv/dry-run", include_in_schema=False)
    @api.post("/imports/preview", include_in_schema=False)
    def preview_csv_import(request: CsvImportRequest) -> dict[str, Any]:
        return run_csv_import(request, True, None)

    @api.post("/imports/csv/commit")
    @api.post("/imports/commit", include_in_schema=False)
    def commit_csv_import(
        request: CsvImportRequest,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ) -> dict[str, Any]:
        return run_csv_import(request, False, idempotency_key)

    @api.post("/imports/csv")
    def csv_import(
        request: CsvImportRequest,
        dry_run: bool = True,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=255),
    ) -> dict[str, Any]:
        return run_csv_import(request, dry_run, idempotency_key)

    def exported(entity_type: str, include_archived: bool, output_format: str) -> Response:
        try:
            with connection_factory() as conn:
                items = export_records(conn, entity_type, include_archived=include_archived)
        except Exception as exc:
            _raise_http(exc)
            raise
        filename = re.sub(r"[^a-z0-9_-]+", "-", entity_type.lower()).strip("-") or "records"
        headers = {"Content-Disposition": f'attachment; filename="{filename}.{output_format}"'}
        if output_format == "csv":
            return Response(records_to_csv(items), media_type="text/csv; charset=utf-8", headers=headers)
        return JSONResponse(
            {"items": items, "next_cursor": None},
            headers=headers,
        )

    # Suffix routes precede the generic route so `/accounts.csv` cannot be
    # interpreted as an entity name.
    @api.get("/exports/{entity_type}.csv")
    @api.get("/exports/{entity_type}/csv", include_in_schema=False)
    @api.get("/exports/csv/{entity_type}", include_in_schema=False)
    def export_csv(entity_type: str, include_archived: bool = False) -> Response:
        return exported(entity_type, include_archived, "csv")

    @api.get("/exports/{entity_type}.json")
    @api.get("/exports/{entity_type}/json", include_in_schema=False)
    @api.get("/exports/json/{entity_type}", include_in_schema=False)
    def export_json(entity_type: str, include_archived: bool = False) -> Response:
        return exported(entity_type, include_archived, "json")

    @api.get("/exports/{entity_type}")
    def export_data(
        entity_type: str,
        format: str = Query(default="json", pattern="^(csv|json)$"),
        include_archived: bool = False,
    ) -> Response:
        return exported(entity_type, include_archived, format)

    return api


discovery_coordinator = DiscoveryCoordinator()
router = create_router(coordinator=discovery_coordinator)
