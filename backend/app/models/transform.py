"""Product-owned transformation domain.

dbt is deliberately absent from most table names.  These rows describe what
AppBI owns: warehouse assets, authored models, tests and executions.  dbt
artifacts are indexed at the adapter boundary and remain portable.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum as SAEnum, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin
from app.core.errors import ErrorCategory
from app.models.enums import (
    HealthLevel, OverlapPolicy, RunStatus, ScheduleType, TriggerType,
)


class Transform(Base, TimestampMixin):
    __tablename__ = "transforms"
    __table_args__ = (
        Index(
            "uq_transform_ws_name_live", "workspace_id", "name", unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_transforms_ws_health", "workspace_id", "health_status"),
        Index("ix_transforms_destination", "destination_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
    )
    destination_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("destinations.id"), nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_schema: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", nullable=False)
    health_status: Mapped[HealthLevel] = mapped_column(
        SAEnum(HealthLevel, name="health_level"), default=HealthLevel.UNKNOWN, nullable=False,
    )
    health_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_trigger: Mapped[str] = mapped_column(String(40), default="MANUAL", nullable=False)
    trigger_config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    #: Where this Transform's models are read from, when they come from Git:
    #: repo_url, ref, subdirectory, secret_ref, auto_pull, interval_minutes,
    #: auto_publish, last_commit, last_pulled_at, last_status, last_message,
    #: and `managed` -- the model names the repository owns, so a model a person
    #: wrote here is never removed by a pull.
    #:
    #: Read only, in one direction. Nothing in this product writes to a
    #: repository, and no field here is a staging area for doing so.
    git_source: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    git_next_pull_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    dbt_core_version: Mapped[str] = mapped_column(String(32), nullable=False)
    dbt_adapter_name: Mapped[str] = mapped_column(String(80), nullable=False)
    dbt_adapter_version: Mapped[str] = mapped_column(String(32), nullable=False)
    last_run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # What an unattended run executes. NULL means nothing has been published, so
    # a schedule has nothing to run -- deliberately, rather than falling back to
    # whatever half-finished SQL the editor happens to hold.
    active_release_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_releases.id", use_alter=True,
                                         name="fk_transform_active_release"), nullable=True,
    )
    # Mirrors Pipeline's scheduling columns so both share one scheduler shape.
    schedule_type: Mapped[ScheduleType] = mapped_column(
        SAEnum(ScheduleType, name="schedule_type"), default=ScheduleType.MANUAL, nullable=False,
    )
    schedule_config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Bangkok", nullable=False)
    overlap_policy: Mapped[OverlapPolicy] = mapped_column(
        SAEnum(OverlapPolicy, name="overlap_policy"),
        default=OverlapPolicy.SKIP_IF_RUNNING, nullable=False,
    )
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True,
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True,
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    models: Mapped[list["TransformModel"]] = relationship(
        back_populates="transform", cascade="all, delete-orphan", lazy="selectin",
    )
    inputs: Mapped[list["TransformInput"]] = relationship(
        back_populates="transform", cascade="all, delete-orphan", lazy="selectin",
    )


class TransformModel(Base, TimestampMixin):
    __tablename__ = "transform_models"
    __table_args__ = (
        Index(
            "uq_transform_model_name_live", "transform_id", "name", unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_transform_models_transform_layer", "transform_id", "layer"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transform_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transforms.id", ondelete="CASCADE"), nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    layer: Mapped[str] = mapped_column(String(24), default="STAGING", nullable=False)
    materialization: Mapped[str] = mapped_column(String(24), default="VIEW", nullable=False)
    sql: Mapped[str] = mapped_column(Text, nullable=False)
    output_schema: Mapped[str | None] = mapped_column(String(200), nullable=True)
    relation_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    transform: Mapped[Transform] = relationship(back_populates="models")
    tests: Mapped[list["TransformTest"]] = relationship(
        back_populates="model", cascade="all, delete-orphan", lazy="selectin",
    )


class TransformTest(Base, TimestampMixin):
    __tablename__ = "transform_tests"
    __table_args__ = (Index("ix_transform_tests_model", "model_id"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_models.id", ondelete="CASCADE"), nullable=False,
    )
    column_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    rule: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="ERROR", nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    last_status: Mapped[str] = mapped_column(String(24), default="NOT_RUN", nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    model: Mapped[TransformModel] = relationship(back_populates="tests")


class DataAsset(Base, TimestampMixin):
    """A verified physical relation inside an AppBI Destination."""

    __tablename__ = "data_assets"
    __table_args__ = (
        Index(
            "uq_data_asset_physical_live", "destination_id", "physical_identity", unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_data_assets_ws_destination", "workspace_id", "destination_id"),
        Index("ix_data_assets_owner", "owner_type", "owner_resource_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
    )
    destination_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("destinations.id"), nullable=False,
    )
    catalog_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    schema_name: Mapped[str] = mapped_column(String(200), nullable=False)
    relation_name: Mapped[str] = mapped_column(String(300), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(24), default="TABLE", nullable=False)
    asset_type: Mapped[str] = mapped_column(String(24), default="RAW", nullable=False)
    owner_type: Mapped[str] = mapped_column(String(24), nullable=False)
    owner_resource_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    pipeline_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("pipelines.id", ondelete="SET NULL"), nullable=True,
    )
    pipeline_stream_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("pipeline_streams.id", ondelete="SET NULL"), nullable=True,
    )
    transform_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transforms.id", ondelete="SET NULL"), nullable=True,
    )
    transform_model_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_models.id", ondelete="SET NULL"), nullable=True,
    )
    physical_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    resolution_status: Mapped[str] = mapped_column(String(24), default="UNRESOLVED", nullable=False)
    schema_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    last_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TransformInput(Base, TimestampMixin):
    __tablename__ = "transform_inputs"
    __table_args__ = (
        UniqueConstraint("transform_id", "data_asset_id", name="uq_transform_input_asset"),
        # Deliberately not unique on source_name: a dbt source is a schema, and
        # every relation inside that schema shares its alias.
        Index("ix_transform_inputs_source_name", "transform_id", "source_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transform_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transforms.id", ondelete="CASCADE"), nullable=False,
    )
    data_asset_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("data_assets.id"), nullable=False,
    )
    source_name: Mapped[str] = mapped_column(String(200), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    transform: Mapped[Transform] = relationship(back_populates="inputs")
    asset: Mapped[DataAsset] = relationship(lazy="joined")


class TransformDependency(Base):
    __tablename__ = "transform_dependencies"
    __table_args__ = (
        Index("ix_transform_dependencies_transform", "transform_id"),
        Index("ix_transform_dependencies_downstream", "downstream_model_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transform_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transforms.id", ondelete="CASCADE"), nullable=False,
    )
    upstream_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("data_assets.id", ondelete="CASCADE"), nullable=True,
    )
    upstream_model_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_models.id", ondelete="CASCADE"), nullable=True,
    )
    downstream_model_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_models.id", ondelete="CASCADE"), nullable=False,
    )
    dbt_unique_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TransformRun(Base, TimestampMixin):
    __tablename__ = "transform_runs"
    __table_args__ = (
        Index("ix_transform_runs_ws_created", "workspace_id", "created_at"),
        Index("ix_transform_runs_transform_created", "transform_id", "created_at"),
        Index("ix_transform_runs_status_started", "status", "started_at"),
        Index(
            "uq_transform_active_build", "transform_id", unique=True,
            postgresql_where=text(
                "status IN ('QUEUED', 'STARTING', 'RUNNING', 'CANCEL_REQUESTED') "
                "AND operation IN ('RUN_MODEL', 'BUILD')"
            ),
        ),
        Index(
            "uq_transform_run_idempotency", "workspace_id", "idempotency_key", unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    transform_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transforms.id", ondelete="CASCADE"), nullable=False,
    )
    operation: Mapped[str] = mapped_column(String(24), nullable=False)
    selected_model_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_models.id", ondelete="SET NULL"), nullable=True,
    )
    # Which code this run executed: a release, or NULL for the live draft.
    release_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_releases.id", ondelete="SET NULL"), nullable=True,
    )
    trigger_type: Mapped[TriggerType] = mapped_column(
        SAEnum(TriggerType, name="trigger_type"), nullable=False,
    )
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True,
    )
    retry_of_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_runs.id"), nullable=True,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[RunStatus] = mapped_column(
        SAEnum(RunStatus, name="run_status"), default=RunStatus.QUEUED, nullable=False,
    )
    queue_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_category: Mapped[ErrorCategory | None] = mapped_column(
        SAEnum(ErrorCategory, name="error_category"), nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    remediation_action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    models_built: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tests_passed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tests_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tests_warned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_affected: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    technical_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    claimed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    attempts: Mapped[list["TransformRunAttempt"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin",
        order_by="TransformRunAttempt.attempt_number",
    )
    nodes: Mapped[list["TransformRunNode"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin",
    )


class TransformRunAttempt(Base):
    __tablename__ = "transform_run_attempts"
    __table_args__ = (
        UniqueConstraint("run_id", "attempt_number", name="uq_transform_run_attempt"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_runs.id", ondelete="CASCADE"), nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[RunStatus] = mapped_column(SAEnum(RunStatus, name="run_status"), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    run: Mapped[TransformRun] = relationship(back_populates="attempts")


class TransformRunNode(Base):
    __tablename__ = "transform_run_nodes"
    __table_args__ = (
        Index("ix_transform_run_nodes_run", "run_id"),
        UniqueConstraint("run_id", "dbt_unique_id", name="uq_transform_run_node"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_runs.id", ondelete="CASCADE"), nullable=False,
    )
    model_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_models.id", ondelete="SET NULL"), nullable=True,
    )
    dbt_unique_id: Mapped[str] = mapped_column(String(500), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_time: Mapped[float | None] = mapped_column(nullable=True)
    relation_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    adapter_response: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    run: Mapped[TransformRun] = relationship(back_populates="nodes")


class TransformRelease(Base, TimestampMixin):
    """An immutable snapshot of a Transform's generated dbt project.

    Editing a model changes what the *next draft run* compiles. A release is
    taken once and never rewritten, so a schedule keeps executing the code that
    was published even while somebody is midway through editing -- the property
    Dataform gets from compiling a Git commit into a compilation result.
    """

    __tablename__ = "transform_releases"
    __table_args__ = (
        UniqueConstraint("transform_id", "release_number", name="uq_transform_release_number"),
        Index("ix_transform_releases_transform", "transform_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transform_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transforms.id", ondelete="CASCADE"), nullable=False,
    )
    release_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # The generated dbt project, exactly as it will be written to disk.
    project_files: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict, nullable=False)
    # The models as authored, so a later diff can show what changed.
    model_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    default_schema: Mapped[str] = mapped_column(String(200), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True,
    )


class TransformArtifact(Base):
    __tablename__ = "transform_artifacts"

    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_runs.id", ondelete="CASCADE"), primary_key=True,
    )
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    run_results: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    compiled_sql: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict, nullable=False)
    preview: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    log_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
