"""Engine failure -> product failure category (section 16.7, section 24.2).

Connector error text is unstructured and version-dependent, so classification is
pattern-based with an explicit UNKNOWN fallback -- a wrong-but-confident category
is worse for the user than "unknown + technical details".
"""

from __future__ import annotations

import hashlib
import re

from app.adapters.dto import EngineFailure
from app.core.errors import ErrorCategory

# Ordered: first match wins, so put the specific patterns above the generic ones.
_PATTERNS: list[tuple[str, ErrorCategory, str, str]] = [
    # (regex, category, product code, remediation action)
    (r"password authentication failed|authentication failed|invalid credentials"
     r"|access denied for user|401 unauthorized|invalid[_ ]?grant|bad credentials"
     r"|token (has )?expired|invalid api key|authenticationexception",
     ErrorCategory.AUTHENTICATION, "SOURCE_AUTHENTICATION_FAILED", "UPDATE_CREDENTIALS"),

    (r"permission denied|insufficient privile|not authorized|403 forbidden"
     r"|must be owner of|access to table .* denied",
     ErrorCategory.PERMISSION, "SOURCE_PERMISSION_DENIED", "GRANT_PERMISSION"),

    (r"could not connect|connection refused|connection timed out|no route to host"
     r"|name or service not known|temporary failure in name resolution|unknownhostexception"
     r"|econnrefused|network is unreachable|ssl.*handshake|connect timed out",
     ErrorCategory.NETWORK, "SOURCE_NETWORK_UNREACHABLE", "CHECK_NETWORK"),

    (r"rate limit|too many requests|429|quota exceeded|throttl",
     ErrorCategory.RATE_LIMIT, "SOURCE_RATE_LIMITED", "RETRY_LATER"),

    (r"relation .* does not exist|column .* does not exist|table .* not found"
     r"|no such table|schema .* does not exist|cursor field .* not found"
     r"|invalid cursor|field .* is not present",
     ErrorCategory.SCHEMA, "SCHEMA_CHANGED", "REDISCOVER_SCHEMA"),

    (r"could not write|failed to write|disk (is )?full|no space left"
     r"|destination .* failed|write to destination",
     ErrorCategory.DESTINATION_WRITE, "DESTINATION_WRITE_FAILED", "UPDATE_DESTINATION"),

    (r"config( is)? (not )?valid|invalid configuration|missing required (field|property)"
     r"|jsonschema validation|configuration check failed",
     ErrorCategory.CONFIGURATION, "SOURCE_CONFIGURATION_INVALID", "OPEN_CONFIGURATION"),

    (r"failed to read|read error|query (failed|cancell?ed)|source process exited",
     ErrorCategory.SOURCE_READ, "SOURCE_READ_FAILED", "OPEN_CONFIGURATION"),

    (r"timed? ?out|deadline exceeded",
     ErrorCategory.TIMEOUT, "ENGINE_TIMEOUT", "RETRY_LATER"),

    (r"cancell?ed",
     ErrorCategory.CANCELLED, "RUN_CANCELLED", None),

    (r"image .* not found|manifest unknown|pull access denied|no such image",
     ErrorCategory.ENGINE, "CONNECTOR_IMAGE_UNAVAILABLE", "CONTACT_ADMIN"),
]

_COMPILED = [(re.compile(pat, re.IGNORECASE), cat, code, action) for pat, cat, code, action in _PATTERNS]

_VI_SUMMARY: dict[ErrorCategory, str] = {
    ErrorCategory.AUTHENTICATION: "Thông tin đăng nhập không còn hợp lệ.",
    ErrorCategory.PERMISSION: "Tài khoản không có đủ quyền.",
    ErrorCategory.NETWORK: "Không thể kết nối tới máy chủ.",
    ErrorCategory.RATE_LIMIT: "Nguồn dữ liệu đang giới hạn tần suất truy cập.",
    ErrorCategory.SCHEMA: "Cấu trúc dữ liệu nguồn đã thay đổi.",
    ErrorCategory.DESTINATION_WRITE: "Không ghi được dữ liệu vào đích.",
    ErrorCategory.CONFIGURATION: "Cấu hình kết nối không hợp lệ.",
    ErrorCategory.SOURCE_READ: "Không đọc được dữ liệu từ nguồn.",
    ErrorCategory.TIMEOUT: "Thao tác vượt quá thời gian cho phép.",
    ErrorCategory.CANCELLED: "Lần chạy đã bị hủy.",
    ErrorCategory.ENGINE: "Engine đồng bộ gặp sự cố nội bộ.",
    ErrorCategory.UNKNOWN: "Đồng bộ thất bại vì lỗi chưa phân loại.",
}


def fingerprint(text: str) -> str:
    """Stable-ish hash used for alert dedup: strip digits/uuids/timestamps first."""
    normalized = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<uuid>", text.lower())
    normalized = re.sub(r"\d+", "<n>", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()[:400]
    return hashlib.sha256(normalized.encode()).hexdigest()[:32]


def classify(
    raw_message: str | None,
    *,
    default_category: ErrorCategory = ErrorCategory.UNKNOWN,
    side: str = "SOURCE",
) -> EngineFailure:
    text = (raw_message or "").strip()
    for pattern, category, code, action in _COMPILED:
        if pattern.search(text):
            if side == "DESTINATION" and code.startswith("SOURCE_"):
                code = code.replace("SOURCE_", "DESTINATION_", 1)
            return EngineFailure(
                code=code,
                category=category,
                summary=_VI_SUMMARY.get(category, _VI_SUMMARY[ErrorCategory.UNKNOWN]),
                technical_message=text[:4000] or None,
                remediation_action=action,
                fingerprint=fingerprint(text or code),
            )
    return EngineFailure(
        code="UNKNOWN_ENGINE_FAILURE",
        category=default_category,
        summary=_VI_SUMMARY.get(default_category, _VI_SUMMARY[ErrorCategory.UNKNOWN]),
        technical_message=text[:4000] or None,
        remediation_action="VIEW_TECHNICAL_DETAILS",
        fingerprint=fingerprint(text or "unknown"),
    )
