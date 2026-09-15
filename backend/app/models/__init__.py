"""SQLAlchemy models. Importing this package registers every table."""

import importlib

from app.models.builder import (
    BuilderAIChangeSet, BuilderAIMessage, BuilderAIPlan, BuilderAISession,
    BuilderAISource, BuilderAIToolEvent, BuilderProject, BuilderTestRun,
    BuilderTestSession,
)
from app.models.engine import ConnectorDefinition, EngineInstance, EngineMapping
# Explicit rather than `import *`. A star import tells pyflakes it can no longer
# tell an undefined name from a star-imported one, which switches off -- for this
# module -- the exact check CI runs the whole package through. The re-export
# itself is kept: nothing in the tree imports these through `app.models` today,
# but they have been part of this package's surface and narrowing it is not what
# this change is for.
from app.models.enums import (
    ACTIVE_RUN_STATUSES, TERMINAL_RUN_STATUSES, ActorType, AlertChannel, AlertEventType,
    AuditResult, BuilderStatus, Certification, ConnectorStatus, ConnectorType,
    DestinationSyncMode, EngineResourceType, EngineStatus, EngineType, HealthLevel,
    NotificationStatus, OperationKind, OperationStatus, OverlapPolicy, PipelineHealth,
    PipelineStatus, ProductResourceType, ResourceStatus, RunStatus, ScheduleType,
    SchemaChangeSeverity, Severity, SyncMode, TestResult, TriggerType, WorkspaceStatus,
)
from app.models.identity import (
    Membership, Organization, OrganizationMembership, User, Workspace,
)
from app.models.integration import (
    Destination, Pipeline, PipelineStream, PipelineStreamStat, SchemaSnapshot, Source,
)
from app.models.oauth import OAuthGrant
from app.models.outbox import EngineOperation, EngineOperationState
from app.models.ops import AlertRule, AuditEvent, Notification, Operation, SecretRecord
from app.models.run import PipelineRun, RunAttempt
# Transform's own tables live in `app.transforms.models`, next to the code that
# owns them -- the module was rebuilt around dbt project files and its schema
# changes on its own cadence now.
#
# Deliberately NOT imported at module level. `app.transforms.models` imports
# `app.models.enums`, which runs this package's __init__, so a top-level import
# here is a cycle: importing `app.transforms.models` first leaves this file
# reading a half-initialised module. `register_transform_tables()` is called by
# `app.bootstrap`, which is the one place that needs every table on the
# metadata; nothing else should reach for these through this package.

__all__ = [
    "AlertRule", "AuditEvent", "BuilderAIChangeSet", "BuilderAIMessage", "BuilderAIPlan",
    "BuilderAISession", "BuilderAISource", "BuilderAIToolEvent", "BuilderProject",
    "BuilderTestRun", "BuilderTestSession", "ConnectorDefinition", "Destination", "EngineInstance",
    "EngineMapping", "Membership", "Notification", "Operation", "Pipeline", "PipelineRun",
    "PipelineStream", "PipelineStreamStat", "RunAttempt", "SchemaSnapshot", "SecretRecord",
    "Source", "User", "Workspace",
    "Organization",
    "OrganizationMembership", "register_transform_tables",
    # Re-exported models that were absent from __all__ and so looked unused.
    "OAuthGrant", "EngineOperation", "EngineOperationState",
    # The enum surface, previously carried in by a star import.
    "ACTIVE_RUN_STATUSES", "TERMINAL_RUN_STATUSES", "ActorType", "AlertChannel",
    "AlertEventType", "AuditResult", "BuilderStatus", "Certification", "ConnectorStatus",
    "ConnectorType", "DestinationSyncMode", "EngineResourceType", "EngineStatus",
    "EngineType", "HealthLevel", "NotificationStatus", "OperationKind", "OperationStatus",
    "OverlapPolicy", "PipelineHealth", "PipelineStatus", "ProductResourceType",
    "ResourceStatus", "RunStatus", "ScheduleType", "SchemaChangeSeverity", "Severity",
    "SyncMode", "TestResult", "TriggerType", "WorkspaceStatus",
]


def register_transform_tables() -> None:
    """Import Transform's models so their tables join the shared metadata.

    Called at import time by `app.bootstrap` and by Alembic's env, which are the
    two places that need the complete table set. Kept as a function rather than
    a top-level import because of the cycle described above.
    """
    # An import purely for its side effect: binding a name that is never read
    # is what makes a checker call load-bearing code dead, and `# noqa` does not
    # help because pyflakes does not read it. Importing by module path says the
    # same thing with nothing to flag.
    importlib.import_module("app.transforms.models")
