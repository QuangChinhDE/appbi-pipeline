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
from app.models.transform import (
    DataAsset, Transform, TransformArtifact, TransformDependency, TransformInput,
    TransformRelease,
    TransformModel, TransformRun, TransformRunAttempt, TransformRunNode, TransformTest,
)

__all__ = [
    "AlertRule", "AuditEvent", "BuilderAIChangeSet", "BuilderAIMessage", "BuilderAIPlan",
    "BuilderAISession", "BuilderAISource", "BuilderAIToolEvent", "BuilderProject",
    "BuilderTestRun", "BuilderTestSession", "ConnectorDefinition", "Destination", "EngineInstance",
    "EngineMapping", "Membership", "Notification", "Operation", "Pipeline", "PipelineRun",
    "PipelineStream", "PipelineStreamStat", "RunAttempt", "SchemaSnapshot", "SecretRecord",
    "Source", "User", "Workspace", "DataAsset", "Transform", "TransformArtifact",
    "TransformRelease",
    "TransformDependency", "TransformInput", "TransformModel", "TransformRun",
    "TransformRunAttempt", "TransformRunNode", "TransformTest",
]
