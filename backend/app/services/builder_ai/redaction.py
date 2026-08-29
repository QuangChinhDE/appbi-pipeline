"""One redaction boundary for prompts, evidence, messages, and tool events."""

from __future__ import annotations

import copy
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.core.logging import redact as redact_fields

MASK = "********"
_SENSITIVE = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|credential|private[_-]?key)"
)
_AUTH = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)\s*[:=]\s*([^\s,;&]+)"
)
_JSON_SECRET = re.compile(
    r'(?i)(["\'](?:password|passwd|secret|token|api[_-]?key|authorization)["\']\s*:\s*)["\'][^"\']*["\']'
)


def _redact_url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if not parts.scheme or not parts.netloc:
        return value
    query = [
        (key, MASK if _SENSITIVE.search(key) else item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
    ]
    userinfo_free = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, userinfo_free, parts.path, urlencode(query), parts.fragment))


def redact_text(value: str, *, limit: int = 40_000) -> str:
    cleaned = _redact_url(value.strip())
    cleaned = _AUTH.sub(lambda match: f"{match.group(1)} {MASK}", cleaned)
    cleaned = _JWT.sub(MASK, cleaned)
    cleaned = _ASSIGNMENT.sub(lambda match: f"{match.group(1)}={MASK}", cleaned)
    cleaned = _JSON_SECRET.sub(lambda match: f'{match.group(1)}"{MASK}"', cleaned)
    return cleaned[:limit]


def sanitize(value: Any, _depth: int = 0) -> Any:
    if _depth > 10:
        return "<truncated>"
    value = redact_fields(value)
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {str(key): sanitize(item, _depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item, _depth + 1) for item in value[:200]]
    return value


def sanitize_definition(definition: dict[str, Any]) -> dict[str, Any]:
    cleaned = copy.deepcopy(definition)
    for field in cleaned.get("user_inputs") or []:
        if field.get("secret"):
            field.pop("default", None)
    for stream in cleaned.get("streams") or []:
        rows = [*(stream.get("headers") or []), *(stream.get("query_params") or [])]
        rows.extend(((stream.get("request_body") or {}).get("entries") or []))
        for row in rows:
            if _SENSITIVE.search(str(row.get("key") or "")):
                row["value"] = MASK
    return sanitize(cleaned)
