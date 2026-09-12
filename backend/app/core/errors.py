"""Normalized error envelope (section 23.2) and product error taxonomy (section 32).

Nothing raw from the engine ever reaches the browser: every failure is
translated into one of these codes, each carrying a human message and a next
action the UI can turn into a button.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCategory(str, Enum):
    AUTHENTICATION = "AUTHENTICATION"
    NETWORK = "NETWORK"
    PERMISSION = "PERMISSION"
    CONFIGURATION = "CONFIGURATION"
    SCHEMA = "SCHEMA"
    RATE_LIMIT = "RATE_LIMIT"
    DESTINATION_WRITE = "DESTINATION_WRITE"
    SOURCE_READ = "SOURCE_READ"
    ENGINE = "ENGINE"
    CANCELLED = "CANCELLED"
    VALIDATION = "VALIDATION"
    CONFLICT = "CONFLICT"
    NOT_FOUND = "NOT_FOUND"
    QUOTA = "QUOTA"
    TIMEOUT = "TIMEOUT"
    UNKNOWN = "UNKNOWN"


class AppError(Exception):
    """Every failure the FE can see travels as one of these."""

    status_code = 400
    code = "BAD_REQUEST"
    category = ErrorCategory.VALIDATION
    message = "The request is not valid."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        category: ErrorCategory | None = None,
        status_code: int | None = None,
        remediation: dict[str, Any] | None = None,
        technical_message: str | None = None,
        constraints: list[dict[str, Any]] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.category = category or self.category
        self.status_code = status_code or self.status_code
        self.remediation = remediation
        self.technical_message = technical_message
        self.constraints = constraints
        self.details = details
        super().__init__(self.message)

    def to_envelope(self, trace_id: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "category": self.category.value,
            "trace_id": trace_id,
        }
        if self.remediation:
            body["remediation"] = self.remediation
        if self.technical_message:
            body["technical_message"] = self.technical_message
        if self.constraints:
            body["constraints"] = self.constraints
        if self.details:
            body["details"] = self.details
        return {"error": body}


class ValidationError(AppError):
    status_code = 422
    code = "VALIDATION_FAILED"
    category = ErrorCategory.VALIDATION
    message = "The information given is not valid."


class NotFoundError(AppError):
    status_code = 404
    code = "RESOURCE_NOT_FOUND"
    category = ErrorCategory.NOT_FOUND
    message = "That was not found."


class UnauthorizedError(AppError):
    status_code = 401
    code = "UNAUTHENTICATED"
    category = ErrorCategory.AUTHENTICATION
    message = "The session is not valid, or it has expired."


class ForbiddenError(AppError):
    status_code = 403
    code = "PERMISSION_DENIED"
    category = ErrorCategory.PERMISSION
    message = "Your account is not allowed to do that."


class ConflictError(AppError):
    status_code = 409
    code = "RESOURCE_CONFLICT"
    category = ErrorCategory.CONFLICT
    message = "Something changed while you were working on it."


class ResourceInUseError(ConflictError):
    code = "RESOURCE_IN_USE"
    message = "Something else is using it."


class ResourceModifiedError(ConflictError):
    code = "RESOURCE_MODIFIED"
    message = "Somebody else changed this. Load it again."


class QuotaExceededError(AppError):
    status_code = 429
    code = "QUOTA_EXCEEDED"
    category = ErrorCategory.QUOTA
    message = "As many syncs are running as are allowed at once."


class RateLimitedError(AppError):
    status_code = 429
    code = "RATE_LIMITED"
    category = ErrorCategory.RATE_LIMIT
    message = "That was too quick. Try again in a moment."


class EngineUnavailableError(AppError):
    status_code = 503
    code = "ENGINE_UNAVAILABLE"
    category = ErrorCategory.ENGINE
    message = "The sync service is unavailable at the moment."


class EngineOperationError(AppError):
    status_code = 502
    code = "ENGINE_OPERATION_FAILED"
    category = ErrorCategory.ENGINE
    message = "The engine could not carry that out."


class EngineResourceGoneError(EngineOperationError):
    """The engine positively confirms the resource does not exist.

    Distinct from its parent on purpose. Every 4xx used to collapse into
    `EngineOperationError`, and worker recovery read that as "the job is gone"
    -- so a 401 from a rotated credential, a 403, or a 429 marked a run FAILED
    while Airbyte carried on writing. The user then retried into a second job
    against the same destination.

    Only a confirmed absence may be treated as absence. Anything else means the
    engine did not answer the question that was asked.
    """

    code = "ENGINE_RESOURCE_GONE"


# Section 32 error-handling UX matrix: code -> (http, category, message, action).
# The FE renders `remediation.action` as the primary CTA on the error card.
ERROR_UX_MATRIX: dict[str, tuple[int, ErrorCategory, str, str | None]] = {
    "SOURCE_AUTHENTICATION_FAILED": (
        400, ErrorCategory.AUTHENTICATION,
        "The source would not accept the credentials.", "UPDATE_CREDENTIALS"),
    "SOURCE_NETWORK_UNREACHABLE": (
        400, ErrorCategory.NETWORK,
        "The source server could not be reached.", "CHECK_NETWORK"),
    "SOURCE_PERMISSION_DENIED": (
        400, ErrorCategory.PERMISSION,
        "The account does not have enough permission on the source.",
        "GRANT_PERMISSION"),
    "SOURCE_CONFIGURATION_INVALID": (
        400, ErrorCategory.CONFIGURATION,
        "The source configuration is not valid.", "OPEN_CONFIGURATION"),
    "DESTINATION_AUTHENTICATION_FAILED": (
        400, ErrorCategory.AUTHENTICATION,
        "The destination would not accept the credentials.",
        "UPDATE_CREDENTIALS"),
    "DESTINATION_PERMISSION_DENIED": (
        400, ErrorCategory.PERMISSION,
        "Nothing could be written to the destination.", "UPDATE_DESTINATION"),
    "SCHEMA_DISCOVERY_TIMEOUT": (
        504, ErrorCategory.TIMEOUT,
        "Reading the data structure took too long.", "RETRY_DISCOVERY"),
    "PIPELINE_NO_STREAM_SELECTED": (
        422, ErrorCategory.VALIDATION,
        "No data has been chosen to sync.", "SELECT_DATA"),
    "PIPELINE_CURSOR_INVALID": (
        422, ErrorCategory.VALIDATION,
        "That cursor does not work for incremental mode.",
        "EDIT_SYNC_SETTINGS"),
    "PIPELINE_PRIMARY_KEY_REQUIRED": (
        422, ErrorCategory.VALIDATION,
        "Dedupe mode needs a primary key.", "EDIT_SYNC_SETTINGS"),
    "PIPELINE_ALREADY_RUNNING": (
        409, ErrorCategory.CONFLICT,
        "The pipeline is already running.", "VIEW_ACTIVE_RUN"),
    "PIPELINE_PAUSED": (
        409, ErrorCategory.CONFLICT,
        "The pipeline is paused. Resume its schedule before syncing.",
        "RESUME_PIPELINE"),
    "PIPELINE_NEEDS_REVIEW": (
        409, ErrorCategory.SCHEMA,
        "The source structure changed and needs your confirmation.",
        "REVIEW_SCHEMA"),
    "RESOURCE_IN_USE": (
        409, ErrorCategory.CONFLICT,
        "Something else is using it.", "VIEW_DEPENDENCIES"),
    "CONNECTOR_IMAGE_UNAVAILABLE": (
        503, ErrorCategory.ENGINE,
        "The connector could not be fetched from the registry.", "CONTACT_ADMIN"),
    "ENGINE_UNAVAILABLE": (
        503, ErrorCategory.ENGINE,
        "The sync service is unavailable at the moment.", "RETRY_LATER"),
}


def error_from_matrix(code: str, **kwargs: Any) -> AppError:
    """Build an AppError straight from the UX matrix so wording stays uniform."""
    status, category, message, action = ERROR_UX_MATRIX.get(
        code, (400, ErrorCategory.UNKNOWN, "Something went wrong.", None)
    )
    remediation = kwargs.pop("remediation", None)
    resource_id = kwargs.pop("resource_id", None)
    if remediation is None and action:
        remediation = {"action": action}
        if resource_id:
            remediation["resource_id"] = str(resource_id)
    return AppError(
        kwargs.pop("message", None) or message,
        code=code,
        category=category,
        status_code=status,
        remediation=remediation,
        **kwargs,
    )
