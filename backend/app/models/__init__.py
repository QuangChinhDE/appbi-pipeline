"""SQLAlchemy models. Importing this package registers every table."""

from app.models.builder import (
    BuilderAIChangeSet, BuilderAIMessage, BuilderAIPlan, BuilderAISession,
    BuilderAISource, BuilderAIToolEvent, BuilderProject, BuilderTestRun,
    BuilderTestSession,
)
from app.models.engine import ConnectorDefinition, EngineInstance, EngineMapping
from app.models.enums import *  # noqa: F401,F403
from app.models.identity import Membership, User, Workspace
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
    "Source", "User", "Workspace", "register_transform_tables",
]


def register_transform_tables() -> None:
    """Import Transform's models so their tables join the shared metadata.

    Called at import time by `app.bootstrap` and by Alembic's env, which are the
    two places that need the complete table set. Kept as a function rather than
    a top-level import because of the cycle described above.
    """
    from app.transforms import models as _transform_models  # noqa: F401
