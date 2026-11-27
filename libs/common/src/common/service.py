"""FastAPI bootstrap shared by every service: health, readiness, metrics, logging,
correlation ids and the error envelope (GEN-8, architecture §21).
"""

import asyncio
import inspect
import json
import logging
import sys
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from starlette.exceptions import HTTPException as StarletteHTTPException

from common import context
from common.contracts.core import ErrorBody, ErrorEnvelope, HealthStatus
from common.settings import ServiceSettings

ReadyCheck = Callable[[], bool | Awaitable[bool]]

HTTP_REQUESTS = Counter(
    "ideaval_http_requests_total", "HTTP requests", ["service", "route", "code"]
)
log = logging.getLogger("common.service")

_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    402: "BUDGET_EXCEEDED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    503: "NOT_READY",
}


class ApiError(Exception):
    """Raise from services or routes; becomes the error envelope (§21.3)."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorEnvelope(
        error=ErrorBody(
            code=code,
            message=message,
            details=details or {},
            request_id=context.request_id_var.get(),
        )
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=status_code, headers=headers)


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": context.request_id_var.get(),
            "run_id": getattr(record, "run_id", None) or context.run_id_var.get(),
            "step_id": getattr(record, "step_id", None) or context.parent_step_var.get(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps({k: v for k, v in entry.items() if v is not None}, default=str)


class _JsonHandler(logging.StreamHandler[Any]):
    """Marker class so reconfiguring replaces only our handler."""


def configure_logging(service: str, level: str = "INFO") -> None:
    root = logging.getLogger()
    for handler in [h for h in root.handlers if isinstance(h, _JsonHandler)]:
        root.removeHandler(handler)
    handler = _JsonHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root.addHandler(handler)
    root.setLevel(level.upper() or "INFO")  # LOG_LEVEL= means the default
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True


async def _is_ready(check: ReadyCheck, limit_s: float) -> bool:
    async def run() -> bool:
        if inspect.iscoroutinefunction(check):
            return bool(await check())
        result = await asyncio.to_thread(check)
        return bool(await result if inspect.isawaitable(result) else result)

    try:
        return await asyncio.wait_for(run(), limit_s)
    except Exception:
        log.warning("readiness check failed", exc_info=True)
        return False


def _route_label(request: Request) -> str:
    # the route template, never the raw path: ids would explode metric cardinality
    return getattr(request.scope.get("route"), "path", "unmatched")


def create_app(
    name: str,
    ready_check: ReadyCheck | None = None,
    *,
    ready_timeout_s: float = 3.0,
    settings: ServiceSettings | None = None,
    **fastapi_kwargs: Any,
) -> FastAPI:
    """A FastAPI app with ``/health``, ``/ready`` and ``/metrics`` and the shared behaviour."""
    configure_logging(name, (settings or ServiceSettings()).log_level)
    app = FastAPI(title=name, **fastapi_kwargs)

    @app.middleware("http")
    async def correlate(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        headers = request.headers
        request_id = context.safe_id(headers.get(context.REQUEST_ID_HEADER)) or uuid.uuid4().hex
        run_id = context.safe_id(headers.get(context.RUN_ID_HEADER))
        parent = headers.get(context.PARENT_STEP_HEADER, "")
        parent_step = (
            int(parent) if parent.isascii() and parent.isdigit() and len(parent) < 20 else None
        )
        request_token = context.request_id_var.set(request_id)
        run_token = context.run_id_var.set(run_id)
        parent_token = context.parent_step_var.set(parent_step)
        user_token = context.user_id_var.set(context.safe_id(headers.get(context.USER_ID_HEADER)))
        try:
            try:
                response = await call_next(request)
            except Exception:
                log.exception("unhandled error")
                response = error_response(500, "INTERNAL_ERROR", "Internal server error")
            response.headers[context.REQUEST_ID_HEADER] = request_id
            if run_id:
                response.headers[context.RUN_ID_HEADER] = run_id
            HTTP_REQUESTS.labels(name, _route_label(request), str(response.status_code)).inc()
            return response
        finally:
            context.request_id_var.reset(request_token)
            context.run_id_var.reset(run_token)
            context.parent_step_var.reset(parent_token)
            context.user_id_var.reset(user_token)

    @app.exception_handler(ApiError)
    async def on_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # location, type and message only: never echo submitted values (passwords)
        errors = [
            {"loc": [str(p) for p in e["loc"]], "type": e["type"], "msg": e["msg"]}
            for e in exc.errors()
        ]
        return error_response(
            422, "VALIDATION_ERROR", "Request validation failed", {"errors": errors}
        )

    @app.exception_handler(StarletteHTTPException)
    async def on_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return error_response(
            exc.status_code,
            _HTTP_CODES.get(exc.status_code, "HTTP_ERROR"),
            str(exc.detail),
            headers=dict(exc.headers) if exc.headers else None,
        )

    @app.get("/health", tags=["ops"])
    async def health() -> HealthStatus:
        return HealthStatus(status="ok")

    @app.get("/ready", tags=["ops"], response_model=HealthStatus)
    async def ready() -> HealthStatus | JSONResponse:
        if ready_check is not None and not await _is_ready(ready_check, ready_timeout_s):
            return error_response(503, "NOT_READY", "Service is not ready")
        return HealthStatus(status="ready")

    @app.get("/metrics", tags=["ops"], include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
