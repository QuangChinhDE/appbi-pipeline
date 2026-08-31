"""Product-owned enums.

These are the public contract. Engine-specific vocabulary is mapped into these
by the adapter and never leaks past it (guardrail 5, section 2.1).
"""

from __future__ import annotations

from enum import Enum


class ResourceStatus(str, Enum):
    """Lifecycle, deliberately separate from health (section 75)."""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    DELETE_PENDING = "DELETE_PENDING"
    DELETED = "DELETED"
    ERROR = "ERROR"


class PipelineStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    DELETE_PENDING = "DELETE_PENDING"
    DELETED = "DELETED"


class HealthLevel(str, Enum):
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


class PipelineHealth(str, Enum):
    HEALTHY = "HEALTHY"
    RUNNING = "RUNNING"
    WARNING = "WARNING"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    FAILED = "FAILED"
    PAUSED = "PAUSED"
    NEVER_RUN = "NEVER_RUN"


class TestResult(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_TESTED = "NOT_TESTED"


class ConnectorType(str, Enum):
    SOURCE = "SOURCE"
    DESTINATION = "DESTINATION"


class ConnectorStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    DEPRECATED = "DEPRECATED"


class BuilderStatus(str, Enum):
    """A builder project is editable until it has been published at least once,
    and stays editable after: DRAFT means "never published"."""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class Certification(str, Enum):
    """Product-owned classification, independent of upstream stage (section 53)."""

    SUPPORTED = "SUPPORTED"
    BETA = "BETA"
    HIDDEN = "HIDDEN"
    BLOCKED = "BLOCKED"


class RunStatus(str, Enum):
    QUEUED = "QUEUED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    FAILED_TO_START = "FAILED_TO_START"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_RUN_STATUSES

    @property
    def is_active(self) -> bool:
        return self in _ACTIVE_RUN_STATUSES


_TERMINAL_RUN_STATUSES = {
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.FAILED_TO_START,
    RunStatus.CANCELLED,
    RunStatus.TIMED_OUT,
}
_ACTIVE_RUN_STATUSES = {
    RunStatus.QUEUED,
    RunStatus.STARTING,
    RunStatus.RUNNING,
    RunStatus.CANCEL_REQUESTED,
}

TERMINAL_RUN_STATUSES = _TERMINAL_RUN_STATUSES
ACTIVE_RUN_STATUSES = _ACTIVE_RUN_STATUSES


class TriggerType(str, Enum):
    MANUAL = "MANUAL"
    SCHEDULE = "SCHEDULE"
    AFTER_UPSTREAM = "AFTER_UPSTREAM"
    RETRY = "RETRY"
    SYSTEM = "SYSTEM"


class ScheduleType(str, Enum):
    MANUAL = "MANUAL"
    INTERVAL = "INTERVAL"
    DAILY = "DAILY"
    CRON = "CRON"


class OverlapPolicy(str, Enum):
    SKIP_IF_RUNNING = "SKIP_IF_RUNNING"
    QUEUE = "QUEUE"


class SyncMode(str, Enum):
    FULL_REFRESH = "full_refresh"
    INCREMENTAL = "incremental"


class DestinationSyncMode(str, Enum):
    OVERWRITE = "overwrite"
    APPEND = "append"
    APPEND_DEDUP = "append_dedup"


class SchemaChangeSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    BREAKING = "BREAKING"


class OperationStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class OperationKind(str, Enum):
    SOURCE_CHECK = "SOURCE_CHECK"
    DESTINATION_CHECK = "DESTINATION_CHECK"
    DISCOVER = "DISCOVER"
    CATALOG_REFRESH = "CATALOG_REFRESH"
    CONNECTOR_PULL = "CONNECTOR_PULL"


class AlertEventType(str, Enum):
    RUN_FAILED = "RUN_FAILED"
    CONSECUTIVE_FAILURES = "CONSECUTIVE_FAILURES"
    SOURCE_AUTH_ERROR = "SOURCE_AUTH_ERROR"
    DESTINATION_ERROR = "DESTINATION_ERROR"
    SCHEMA_BREAKING_CHANGE = "SCHEMA_BREAKING_CHANGE"
    FRESHNESS_BREACH = "FRESHNESS_BREACH"
    ENGINE_DEGRADED = "ENGINE_DEGRADED"


class AlertChannel(str, Enum):
    IN_APP = "IN_APP"
    EMAIL = "EMAIL"
    WEBHOOK = "WEBHOOK"


class NotificationStatus(str, Enum):
    NEW = "NEW"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ActorType(str, Enum):
    USER = "USER"
    SYSTEM = "SYSTEM"
    API = "API"


class AuditResult(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class EngineType(str, Enum):
    AIRBYTE_EMBEDDED = "AIRBYTE_EMBEDDED"
    AIRBYTE_API = "AIRBYTE_API"
    # Not Airbyte at all: plain SQL, no connector images, no protocol. It exists
    # so the adapter interface has an implementation that cannot quietly assume
    # Airbyte's shape. See app/adapters/sql_direct/.
    SQL_DIRECT = "SQL_DIRECT"


class EngineStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"


class EngineResourceType(str, Enum):
    SOURCE = "SOURCE"
    DESTINATION = "DESTINATION"
    CONNECTION = "CONNECTION"
    JOB = "JOB"


class ProductResourceType(str, Enum):
    SOURCE = "SOURCE"
    DESTINATION = "DESTINATION"
    PIPELINE = "PIPELINE"
    RUN = "RUN"


class WorkspaceStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DELETED = "DELETED"
