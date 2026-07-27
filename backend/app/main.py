import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.routes import health
from app import platform_db
from app.local_security import install_local_security
from app.v1 import core_service as v1_service
from app.v1.router import router as v1_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    restored = False
    try:
        from app.integrations_v1.backup import apply_staged_restore

        restored = apply_staged_restore(platform_db.db_path()) is not None
    except ImportError:
        pass
    platform_db.bootstrap()
    if restored:
        from app.integrations_v1.secrets import (
            GEMINI_API_KEY,
            GOOGLE_CLIENT_SECRET_KEY,
            STRIPE_API_KEY,
            TAVILY_API_KEY,
            application_credential_store,
        )

        credentials = application_credential_store()
        for key in (
            "google.oauth.credentials",
            GOOGLE_CLIENT_SECRET_KEY,
            STRIPE_API_KEY,
            TAVILY_API_KEY,
            GEMINI_API_KEY,
        ):
            credentials.delete(key)
        with platform_db.connect() as conn:
            conn.execute(
                "UPDATE integration_connections SET status='disconnected', last_error='', updated_at=?",
                (platform_db.utc_now().isoformat(),),
            )
            platform_db.write_audit(
                conn,
                "restore.applied",
                "system",
                0,
                None,
                {"integrations_reauthorization_required": True},
            )
    try:
        from app.workflows_v1 import discovery_coordinator

        discovery_coordinator.recover()
    except ImportError:
        pass
    worker = None
    try:
        from app.integrations_v1.worker import Worker

        worker = Worker()
        worker.start()
    except ImportError:
        worker = None
    try:
        yield
    finally:
        if worker:
            worker.stop()


app = FastAPI(
    title="CRM Workspace API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs" if os.getenv("CRM_ENABLE_DOCS") == "true" else None,
    redoc_url=None,
)

if os.getenv("CRM_DEV_CORS", "true").lower() == "true":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1):517[3-9]",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

install_local_security(app)


@app.middleware("http")
async def request_identity(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers.setdefault("X-Request-ID", request_id)
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    labels = {401: "authentication_required", 403: "forbidden", 404: "not_found", 409: "conflict"}
    detail = exc.detail
    message = detail if isinstance(detail, str) else detail.get("message", "The request could not be completed")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": labels.get(exc.status_code, "request_error"),
            "message": message,
            "field_errors": detail.get("field_errors", {}) if isinstance(detail, dict) else {},
            "current_record": detail.get("current_record") if isinstance(detail, dict) else None,
            "current_version": detail.get("current_version") if isinstance(detail, dict) else None,
            "request_id": getattr(request.state, "request_id", ""),
        },
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    fields = {
        ".".join(str(part) for part in error["loc"] if part not in {"body", "query", "path"}): error["msg"]
        for error in exc.errors()
    }
    return JSONResponse(
        status_code=422,
        content={
            "code": "validation_error",
            "message": "Request validation failed",
            "field_errors": fields,
            "request_id": getattr(request.state, "request_id", ""),
        },
    )


@app.exception_handler(v1_service.NotFoundError)
async def not_found_handler(request: Request, exc: v1_service.NotFoundError):
    return JSONResponse(
        status_code=404,
        content={"code": "not_found", "message": str(exc), "field_errors": {}, "request_id": request.headers.get("x-request-id", "")},
    )


@app.exception_handler(v1_service.ConflictError)
async def conflict_handler(request: Request, exc: v1_service.ConflictError):
    current = exc.current_record
    return JSONResponse(
        status_code=409,
        content={
            "code": "conflict",
            "message": str(exc),
            "field_errors": {},
            "current_record": current,
            "current_version": current.get("version") if current else None,
            "request_id": request.headers.get("x-request-id", ""),
        },
    )


@app.exception_handler(v1_service.DomainError)
async def domain_handler(request: Request, exc: v1_service.DomainError):
    return JSONResponse(
        status_code=422,
        content={"code": "domain_error", "message": str(exc), "field_errors": {}, "request_id": request.headers.get("x-request-id", "")},
    )


app.include_router(v1_router, prefix="/api/v1", tags=["v1"])

app.include_router(health.router, prefix="/api", tags=["health"])


def _include_optional_routers() -> None:
    try:
        from app.operations.router import router as operations_router

        app.include_router(operations_router, prefix="/api/v1", tags=["operations"])
    except ImportError:
        pass
    try:
        from app.communications import router as communications_router

        app.include_router(communications_router, prefix="/api/v1", tags=["communications"])
    except ImportError:
        pass
    try:
        from app.integrations_v1.router import router as integrations_router

        app.include_router(integrations_router, prefix="/api/v1", tags=["integrations"])
    except ImportError:
        pass
    try:
        from app.workflows_v1 import router as workflows_router

        app.include_router(workflows_router, prefix="/api/v1", tags=["workflows"])
    except ImportError:
        pass


_include_optional_routers()


FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


if FRONTEND_DIST.exists():
    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        requested = (FRONTEND_DIST / path).resolve()
        if requested.is_file() and FRONTEND_DIST.resolve() in requested.parents:
            return FileResponse(requested)
        return FileResponse(FRONTEND_DIST / "index.html")
