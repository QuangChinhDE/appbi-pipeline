"""Structured JSON logging with trace correlation (SRS section 27)."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import uuid
from typing import Any

from app.core.config import settings

trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
workspace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("workspace_id", default="")
actor_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("actor_id", default="")

_SECRET_HINTS = (
    "password", "secret", "token", "credential", "api_key", "apikey",
    "private_key", "client_secret", "access_key", "passphrase", "authorization",
    "service_account", "keyfile", "sasl",
)


def new_trace_id() -> str:
    return "trc_" + uuid.uuid4().hex[:24]


def redact(value: Any, _depth: int = 0) -> Any:
    """Recursively mask anything that smells like a credential (section 27.2)."""
    if _depth > 8:
        return "<truncated>"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if any(hint in str(key).lower() for hint in _SECRET_HINTS):
                out[key] = "********"
            else:
                out[key] = redact(item, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(item, _depth + 1) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "service": settings.service_name,
            "environment": settings.app_env,
            "message": record.getMessage(),
        }
        if trace_id_var.get():
            payload["trace_id"] = trace_id_var.get()
        if workspace_id_var.get():
            payload["workspace_id"] = workspace_id_var.get()
        if actor_id_var.get():
            payload["actor_id"] = actor_id_var.get()
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(redact(extra))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_event(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    logger.log(level, message, extra={"extra_fields": fields})
