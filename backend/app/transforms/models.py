"""Transform V2 domain.

What is *not* here is the point of the file.  There is no table for a model, a
test, a seed, a macro or a snapshot, and no column holding SQL.  Those live in
project files, and project files are canonical.  Adding a dbt concept to this
module would be the mistake this rework exists to undo: it is what forced a
schema change, an API change, a form change and a generator change every time
dbt grew a capability.

What is here is what AppBI genuinely owns and dbt has no opinion about: which
projects exist, who may run them, which credentials they run with, what was
executed and when, and which exact code production is allowed to execute.

Two tables look like exceptions and are not.  ``TransformResourceIndex`` and
``TransformResourceEdge`` mirror rows out of ``manifest.json`` so the UI can
filter and paginate resources in SQL instead of shipping a manifest to the
browser.  Both are caches: dropping them loses nothing, because rebuilding them
is one read of an artifact that is already stored.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum as SAEnum, Float, ForeignKey, Index, Integer,
    String, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin
from app.core.errors import ErrorCategory
from app.models.enums import (
    HealthLevel, OverlapPolicy, RunStatus, ScheduleType, TriggerType,
)

# ── project ───────────────────────────────────────────────────────────────


class TransformProject(Base, TimestampMixin):
    """A dbt project AppBI hosts.

    ``mode`` decides where canonical code lives.  MANAGED means AppBI's own
    revision chain is canonical.  GIT means the checked-out working tree is,
    and AppBI is an editor over it -- it does not convert the repository into
    anything, and a project cloned out again is the project that was cloned in.
    """

    __tablename__ = "transform_projects"
    __table_args__ = (
        Index(
            "uq_transform_project_ws_name_live", "workspace_id", "name", unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_transform_projects_ws_health", "workspace_id", "health_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: MANAGED | GIT
    mode: Mapped[str] = mapped_column(String(16), default="MANAGED", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", nullable=False)
    #: The dbt project's own name, out of `dbt_project.yml`.  Cached here so a
    #: list page need not open a revision; the file remains authoritative.
    dbt_project_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    #: The revision the editor is writing to.  Advances on every save.
    working_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("transform_project_revisions.id", use_alter=True,
                   name="fk_transform_project_working_revision", ondelete="SET NULL"),
        nullable=True,
    )
    #: What an unattended run executes.  NULL means nothing has been published,
    #: so a schedule has nothing to run -- deliberately, rather than falling back
    #: to whatever half-finished SQL the editor happens to hold.
    active_release_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("transform_releases.id", use_alter=True,
                   name="fk_transform_project_active_release", ondelete="SET NULL"),
        nullable=True,
    )
    default_environment_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("transform_environments.id", use_alter=True,
                   name="fk_transform_project_default_environment", ondelete="SET NULL"),
        nullable=True,
    )
    production_environment_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("transform_environments.id", use_alter=True,
                   name="fk_transform_project_production_environment", ondelete="SET NULL"),
        nullable=True,
    )

    health_status: Mapped[HealthLevel] = mapped_column(
        SAEnum(HealthLevel, name="health_level"), default=HealthLevel.UNKNOWN, nullable=False,
    )
    health_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Result of the most recent parse of the working revision.  A project with
    #: a broken parse still opens in the editor -- that is where it gets fixed.
    parse_status: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True,
    )
    last_parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    last_invocation_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    dbt_core_version: Mapped[str] = mapped_column(String(32), nullable=False)
    dbt_adapter_name: Mapped[str] = mapped_column(String(80), nullable=False)
    dbt_adapter_version: Mapped[str] = mapped_column(String(32), nullable=False)

    # Scheduling mirrors Pipeline's columns so both share one scheduler shape.
    schedule_type: Mapped[ScheduleType] = mapped_column(
        SAEnum(ScheduleType, name="schedule_type"), default=ScheduleType.MANUAL, nullable=False,
    )
    schedule_config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    #: What a schedule fires.  Structured, never a shell string.
    schedule_command: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
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

    environments: Mapped[list["TransformEnvironment"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", lazy="selectin",
        foreign_keys="TransformEnvironment.project_id",
    )
    git_binding: Mapped["TransformGitBinding | None"] = relationship(
        back_populates="project", cascade="all, delete-orphan", uselist=False, lazy="selectin",
    )


class TransformProjectRevision(Base):
    """An exact set of project files.

    A revision is immutable once ``frozen`` -- a release points at one, and a
    production run replays it byte for byte months later.  The working revision
    is the one exception: saves advance it in place until a publish freezes it
    and starts a successor.

    ``manifest_json`` is not stored here.  A revision is code; a manifest is
    what dbt made of that code, which belongs to an invocation.
    """

    __tablename__ = "transform_project_revisions"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "revision_number", name="uq_transform_revision_number",
        ),
        Index("ix_transform_revisions_project", "project_id", "created_at"),
        Index("ix_transform_revisions_hash", "project_id", "content_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    #: sha256 over the whole canonical file set.  Two revisions with the same
    #: hash contain the same project, which is what "Draft matches Live" means
    #: -- not a comparison of SQL text.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: {path: {"key": <blob key>, "size": int, "sha256": str}}.  Paths point at
    #: content-addressed blobs in object storage; nothing here holds file bytes.
    manifest_index: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    git_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    git_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    parent_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_project_revisions.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: True once a release froze it.  A frozen revision is never written again.
    frozen: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ── connection and environment ────────────────────────────────────────────


class TransformConnection(Base, TimestampMixin):
    """A named warehouse key, kept so it can be chosen instead of re-entered.

    The credential itself lives in the encrypted secret store; this row is what
    makes it selectable -- a name a person recognises, the warehouse it reaches,
    the account it turned out to be, and what it could see last time it was
    checked.  Several projects and environments can share one.

    ``destination_id`` is a back-link, set only when the connection was made
    from an AppBI Destination.  That link is what still resolves
    `Source → Pipeline → table` upstream.  A connection made by pasting a
    service account has no Destination and no upstream, and that is the honest
    answer for a table AppBI does not load.
    """

    __tablename__ = "transform_connections"
    __table_args__ = (
        Index(
            "uq_transform_connection_name", "workspace_id", "name", unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_transform_connections_destination", "destination_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
    )
    destination_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("destinations.id", ondelete="CASCADE"), nullable=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    connector_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: inherited | service_account | oauth | password
    auth_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: The non-secret half: project and location for BigQuery, host/port/database
    #: for Postgres.  Empty when inherited from a Destination.
    configuration_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account: Mapped[str | None] = mapped_column(String(320), nullable=True)
    catalogs: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    # Lifecycle: a key that worked in March may not work in September, and the
    # difference should be visible before a 03:00 schedule discovers it.
    #: UNVERIFIED | OK | FAILED
    verification_status: Mapped[str] = mapped_column(
        String(24), default="UNVERIFIED", nullable=False,
    )
    verification_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TransformEnvironment(Base, TimestampMixin):
    """Where a project runs, and as whom.

    Development and production are two real environments, not one schema name
    with a convention applied to it.  Each carries its own connection, so a
    development IDE never holds a production credential -- the permission
    boundary is the point, and it cannot exist if both targets share a key.
    """

    __tablename__ = "transform_environments"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_transform_environment_name"),
        Index("ix_transform_environments_project", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    #: DEVELOPMENT | PRODUCTION
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_connections.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: The dbt target name this environment presents, so `{{ target.name }}` in
    #: a user's own project resolves the way their local dbt would resolve it.
    target_name: Mapped[str] = mapped_column(String(64), default="dev", nullable=False)
    #: STATIC | PER_USER -- per-user appends a suffix so two developers editing
    #: the same project do not build over each other.
    schema_strategy: Mapped[str] = mapped_column(String(24), default="STATIC", nullable=False)
    schema_name: Mapped[str] = mapped_column(String(200), nullable=False)
    schema_prefix: Mapped[str | None] = mapped_column(String(100), nullable=True)
    schema_suffix: Mapped[str | None] = mapped_column(String(100), nullable=True)
    threads: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    #: dbt `vars`, merged into every invocation from this environment.
    vars_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    #: Room for adapter knobs a form has no field for, preserved verbatim.
    env_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    #: Production commands are gated on OPERATE; a developer environment is not.
    protected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[TransformProject] = relationship(
        back_populates="environments", foreign_keys=[project_id],
    )


class TransformGitBinding(Base, TimestampMixin):
    """A repository a GIT-mode project is checked out from.

    Unlike V1's importer this does not convert anything.  The working tree is
    the project; a commit records what the editor already holds.
    """

    __tablename__ = "transform_git_bindings"
    __table_args__ = (
        UniqueConstraint("project_id", name="uq_transform_git_binding_project"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(24), default="github", nullable=False)
    repo_url: Mapped[str] = mapped_column(String(500), nullable=False)
    branch: Mapped[str] = mapped_column(String(255), default="main", nullable=False)
    #: Where `dbt_project.yml` sits inside the repository, "" for the root.
    subdirectory: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: The commit the checked-out revision came from.
    head_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The remote tip last time it was looked at; ahead of head when a pull is due.
    remote_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    auto_pull: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    next_pull_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_pulled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    last_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[TransformProject] = relationship(back_populates="git_binding")


# ── execution ─────────────────────────────────────────────────────────────


class TransformInvocation(Base, TimestampMixin):
    """One dbt command, against one revision, in one environment.

    Replaces V1's ``TransformRun``, which was bound to a ``TransformModel`` row
    and so could only express commands the product had modelled.  This row
    names a dbt command and a selector, which is every command dbt has.

    ``revision_id`` is not nullable.  "The run of the current code" is exactly
    the ambiguity that made a production failure impossible to reproduce; a run
    always names the bytes it executed.
    """

    __tablename__ = "transform_invocations"
    __table_args__ = (
        Index("ix_transform_invocations_ws_created", "workspace_id", "created_at"),
        Index("ix_transform_invocations_project_created", "project_id", "created_at"),
        Index("ix_transform_invocations_status_started", "status", "started_at"),
        # One writing command per project+environment at a time.  Read commands
        # (parse, compile, show, ls) are excluded: they touch no relation, and
        # serialising them would make the editor feel broken.
        Index(
            "uq_transform_active_write", "project_id", "environment_id", unique=True,
            postgresql_where=text(
                "status IN ('QUEUED', 'STARTING', 'RUNNING', 'CANCEL_REQUESTED') "
                "AND command IN ('build', 'run', 'seed', 'snapshot', 'run-operation', "
                "'clone', 'test')"
            ),
        ),
        Index(
            "uq_transform_invocation_idempotency", "workspace_id", "idempotency_key",
            unique=True, postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_environments.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_project_revisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    release_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_releases.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: A dbt subcommand: parse, compile, show, build, run, test, seed, snapshot,
    #: ls, deps, source-freshness, docs-generate, run-operation, clone, debug.
    #: Not a shell string, ever -- the adapter builds an argv array from this.
    command: Mapped[str] = mapped_column(String(32), nullable=False)
    selector: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    exclude: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    #: full_refresh, vars, limit, macro, args, target-path -- validated per
    #: command before it becomes argv.
    args_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    trigger_type: Mapped[TriggerType] = mapped_column(
        SAEnum(TriggerType, name="trigger_type"), nullable=False,
    )
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True,
    )
    retry_of_invocation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_invocations.id"), nullable=True,
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
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    remediation_action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: dbt's own invocation id from the artifact metadata, so a run here can be
    #: matched with a line in a warehouse query log.
    dbt_invocation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Counted from run_results, for a list row that need not open the artifact.
    nodes_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    nodes_succeeded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    nodes_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    nodes_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tests_passed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tests_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tests_warned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_affected: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    technical_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    claimed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    artifact_bundle_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)

    nodes: Mapped[list["TransformInvocationNode"]] = relationship(
        back_populates="invocation", cascade="all, delete-orphan", lazy="selectin",
    )


class TransformInvocationNode(Base):
    """One resource's outcome inside an invocation, out of ``run_results.json``."""

    __tablename__ = "transform_invocation_nodes"
    __table_args__ = (
        Index("ix_transform_invocation_nodes_invocation", "invocation_id"),
        UniqueConstraint("invocation_id", "unique_id", name="uq_transform_invocation_node"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invocation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_invocations.id", ondelete="CASCADE"),
        nullable=False,
    )
    unique_id: Mapped[str] = mapped_column(String(500), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    relation_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    rows_affected: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bytes_processed: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    failures: Mapped[int | None] = mapped_column(Integer, nullable=True)
    adapter_response: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    #: {path, line, column} when dbt said where.  Drives click-error-to-line.
    error_location: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    invocation: Mapped[TransformInvocation] = relationship(back_populates="nodes")


class TransformArtifactBundle(Base):
    """Pointers to the artifacts one invocation produced.

    Storage keys, not documents.  A manifest can exceed what is sensible to put
    in a JSONB column and there is one per parse; the bundle row stays small
    enough to list, and the artifact is fetched only when something reads it.
    """

    __tablename__ = "transform_artifact_bundles"
    __table_args__ = (
        Index("ix_transform_artifact_bundles_project", "project_id", "created_at"),
        Index("ix_transform_artifact_bundles_invocation", "invocation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    invocation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_invocations.id", ondelete="CASCADE"),
        nullable=True,
    )
    revision_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_project_revisions.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: DRAFT for a working-revision parse, RELEASE for a release's own artifacts.
    #: Keeping them apart is what stops a draft parse overwriting the graph that
    #: production is running.
    scope: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)

    manifest_storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    run_results_storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    catalog_storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sources_storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    semantic_manifest_storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    log_storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)

    dbt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dbt_schema_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    adapter_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ── artifact-derived index (cache, never truth) ───────────────────────────


class TransformResourceIndex(Base):
    """Resources as dbt reported them, flattened for querying.

    Rebuildable from the bundle it came from; a row here is never edited and
    never consulted for what a resource *should* be.  It exists so a project
    with 5,000 resources can be filtered and paged in SQL.

    ``config_json`` keeps the node's full config as parsed -- including keys
    AppBI has no form for -- so the inspector can show what is really set
    without pretending the product understands every key.
    """

    __tablename__ = "transform_resource_index"
    __table_args__ = (
        UniqueConstraint("bundle_id", "unique_id", name="uq_transform_resource_unique"),
        Index("ix_transform_resource_project_scope", "project_id", "scope"),
        Index("ix_transform_resource_type", "bundle_id", "resource_type"),
        Index("ix_transform_resource_name", "project_id", "name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    bundle_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_artifact_bundles.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    scope: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)

    unique_id: Mapped[str] = mapped_column(String(500), nullable=False)
    #: Whatever dbt called it.  Deliberately a string and not an enum: dbt adds
    #: resource types, and an enum here would mean a migration each time.
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    package_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    original_file_path: Mapped[str | None] = mapped_column(String(700), nullable=True)
    patch_path: Mapped[str | None] = mapped_column(String(700), nullable=True)

    database_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    schema_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    alias: Mapped[str | None] = mapped_column(String(300), nullable=True)
    relation_name: Mapped[str | None] = mapped_column(String(700), nullable=True)
    materialized: Mapped[str | None] = mapped_column(String(64), nullable=True)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    group_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tags_json: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    columns_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(100), nullable=True)

    #: For a source AppBI's own Pipeline populates.  Enrichment only: it changes
    #: nothing about what the source means to dbt.
    produced_by_pipeline_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True,
    )


class TransformResourceEdge(Base):
    """An edge of dbt's own dependency graph, from ``parent_map``.

    Never inferred from SQL.  dbt has already resolved every `ref` and `source`
    including the ones a regex cannot see -- inside a macro, behind a var, in a
    package.
    """

    __tablename__ = "transform_resource_edges"
    __table_args__ = (
        UniqueConstraint(
            "bundle_id", "parent_unique_id", "child_unique_id",
            name="uq_transform_resource_edge",
        ),
        Index("ix_transform_resource_edges_child", "bundle_id", "child_unique_id"),
        Index("ix_transform_resource_edges_parent", "bundle_id", "parent_unique_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    bundle_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_artifact_bundles.id", ondelete="CASCADE"),
        nullable=False,
    )
    scope: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)
    parent_unique_id: Mapped[str] = mapped_column(String(500), nullable=False)
    child_unique_id: Mapped[str] = mapped_column(String(500), nullable=False)


# ── release ───────────────────────────────────────────────────────────────


class TransformRelease(Base, TimestampMixin):
    """An immutable project revision that production is allowed to execute.

    A release freezes *the project*, not a set of product rows.  That is the
    difference the whole rework turns on: restoring, diffing and re-running a
    release are all operations on files, so a config AppBI never understood is
    still there when the release runs.

    VERIFYING until this exact revision has been proven runnable, READY once it
    has, FAILED if it has not.  Only READY can be activated: publishing froze
    the code, it did not prove the code works, and a schedule firing at 03:00 is
    the wrong place to discover the difference.
    """

    __tablename__ = "transform_releases"
    __table_args__ = (
        UniqueConstraint("project_id", "release_number", name="uq_transform_release_number"),
        Index("ix_transform_releases_project", "project_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    release_number: Mapped[int] = mapped_column(Integer, nullable=False)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_project_revisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: The revision's content hash, copied so "Draft matches Live" is one
    #: comparison and does not need to load either revision.
    project_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    git_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_environments.id", ondelete="SET NULL"),
        nullable=True,
    )
    dbt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    adapter_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    #: VERIFYING | READY | FAILED | ACTIVE | RETIRED
    status: Mapped[str] = mapped_column(String(20), default="VERIFYING", nullable=False)
    verification_invocation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True,
    )
    verification_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activate_on_success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Monotonic per project.  A verification that finishes late must not
    #: activate over a newer release that already went live -- this is the value
    #: that decides, rather than the order two async jobs happen to complete in.
    activation_sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True,
    )


# ── AppBI enrichment ──────────────────────────────────────────────────────


class DataAsset(Base, TimestampMixin):
    """A physical relation AppBI knows about, in a warehouse it can reach.

    Retained from V1 because it is the one thing dbt genuinely cannot tell us:
    that a table was produced by an AppBI Pipeline, from which Source.  That
    link is what lets a dbt source show "Produced by CRM → BigQuery Pipeline",
    and it is enrichment -- it changes nothing about what the source means to
    dbt, and a project works fine with no assets registered at all.
    """

    __tablename__ = "data_assets"
    __table_args__ = (
        Index(
            "uq_data_asset_physical_live", "connection_id", "physical_identity", unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_data_assets_ws_destination", "workspace_id", "destination_id"),
        Index("ix_data_assets_connection", "connection_id"),
        Index("ix_data_assets_owner", "owner_type", "owner_resource_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
    )
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_connections.id"), nullable=True,
    )
    destination_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("destinations.id"), nullable=True,
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
    #: Which Transform project materialised it, when AppBI built it.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transform_projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: dbt's unique_id for the resource that owns this relation, when known.
    dbt_unique_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    physical_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    resolution_status: Mapped[str] = mapped_column(String(24), default="UNRESOLVED", nullable=False)
    schema_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    last_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
