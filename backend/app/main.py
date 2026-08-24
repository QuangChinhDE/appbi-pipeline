"""Product API / BFF.

The browser talks only to this service. It owns auth, RBAC, tenancy, business
rules, audit and the normalized error envelope; the engine sits behind the
adapter and is never addressable from the outside (guardrail 1, section 2.1).
"""

from __future__ import annotations

import logging
import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.adapters.registry import close_adapter
from app.api.v1 import actors, auth, builder, ops, pipelines, runs, schema
from app.core.config import settings
from app.core.readiness import enforce_at_startup, probe_engine_at_startup
from app.core.errors import AppError, ErrorCategory
from app.core.logging import configure_logging, log_event, new_trace_id, trace_id_var

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log_event(logger, logging.INFO, "api.startup", engine_type=settings.engine_type,
              product_version=settings.product_version)

    # One configuration check, at boot, covering whichever engine this
    # deployment claims to be. A misconfigured control plane otherwise looks
    # healthy until the first user action fails three layers away from the
    # cause. Production refuses to start; elsewhere it logs and continues.
    await enforce_at_startup()

    # Configuration is right; is the engine actually answering? Reported, not
    # enforced, unless STARTUP_REQUIRE_ENGINE says otherwise.
    await probe_engine_at_startup()

    yield
    await close_adapter()


app = FastAPI(
    title="AppBI Data Integration Platform API",
    version=settings.product_version,
    description=(
        "Product-owned control plane. Airbyte runs behind the IntegrationEngineAdapter; "
        "no engine identifier is ever part of this contract."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8080", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-Id") or new_trace_id()
    request.state.trace_id = trace_id
    trace_id_var.set(trace_id)
    response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id
    return response


def _envelope(request: Request, error: AppError) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", "")
    return JSONResponse(status_code=error.status_code, content=error.to_envelope(trace_id))


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    if exc.status_code >= 500:
        log_event(logger, logging.ERROR, "api.error", code=exc.code,
                  path=request.url.path, technical=exc.technical_message)
    return _envelope(request, exc)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"field": ".".join(str(p) for p in err.get("loc", [])[1:]), "message": err.get("msg")}
        for err in exc.errors()
    ]
    return _envelope(request, AppError(
        "Dữ liệu gửi lên không hợp lệ.",
        code="VALIDATION_FAILED", category=ErrorCategory.VALIDATION, status_code=422,
        details={"fields": details},
    ))


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    mapping = {401: "UNAUTHENTICATED", 403: "PERMISSION_DENIED", 404: "RESOURCE_NOT_FOUND",
               405: "METHOD_NOT_ALLOWED"}
    return _envelope(request, AppError(
        str(exc.detail), code=mapping.get(exc.status_code, "HTTP_ERROR"),
        category=ErrorCategory.UNKNOWN, status_code=exc.status_code,
    ))


@app.exception_handler(IntegrityError)
async def integrity_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    """A constraint the service layer should have caught first.

    Two requests can pass the same uniqueness pre-check concurrently; the
    database is the real arbiter. That is a conflict the caller can act on, not
    a server fault, so it must not surface as a 500.
    """
    detail = str(getattr(exc, "orig", exc))
    duplicate = "duplicate key" in detail or "UniqueViolation" in detail
    log_event(logger, logging.WARNING, "api.integrity_error",
              path=request.url.path, duplicate=duplicate, technical=detail[:300])
    return _envelope(request, AppError(
        "Tên này đã tồn tại trong workspace." if duplicate
        else "Dữ liệu vi phạm ràng buộc toàn vẹn.",
        code="RESOURCE_CONFLICT" if duplicate else "INTEGRITY_ERROR",
        category=ErrorCategory.CONFLICT, status_code=409,
        technical_message=detail[:1000] if settings.app_env != "production" else None,
    ))


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    log_event(logger, logging.ERROR, "api.unhandled", path=request.url.path,
              error=f"{type(exc).__name__}: {exc}")
    return _envelope(request, AppError(
        "Đã xảy ra lỗi không mong muốn. Vui lòng thử lại.",
        code="INTERNAL_ERROR", category=ErrorCategory.UNKNOWN, status_code=500,
        technical_message=f"{type(exc).__name__}: {exc}"
        if settings.app_env != "production" else None,
    ))


@app.get("/healthz", tags=["system"])
async def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name,
            "version": settings.product_version}


@app.get("/readyz", tags=["system"])
async def readyz(response: Response, deep: bool = False) -> dict:
    """Whether this instance should be sent traffic.

    `deep=1` also requires the engine and is the one to point a deploy gate at.
    Plain `/readyz` is for the load balancer and does not fail on an engine
    outage — see app/core/readiness.py. Either way the engine's state is in the
    body, so an operator can see it without needing a second endpoint.
    """
    from app.core.readiness import probe

    ok, report = await probe(deep=deep)
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report


# Not under /api/v1: metrics describe the deployment, not a tenant, and they
# are not part of the product's versioned contract.
from app.api import metrics as metrics_module  # noqa: E402

app.include_router(metrics_module.router)

API_PREFIX = "/api/v1"
for router in (
    auth.router, actors.sources_router, actors.destinations_router, schema.router,
    pipelines.router, runs.router, ops.router, ops.admin_router, builder.router,
):
    app.include_router(router, prefix=API_PREFIX)
