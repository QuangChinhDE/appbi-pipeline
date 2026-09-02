"""Development and production environments.

Two real environments, not one schema name with a naming convention applied to
it.  The distinction has to be structural because the property that matters is a
permission boundary: a development IDE must not hold a credential that can write
the production schema, and no amount of care in the UI can provide that if both
targets resolve to the same key.

Every project gets both at creation.  A person who only ever presses Preview
never thinks about it; a person who publishes a release depends on it entirely.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.db import utcnow
from app.core.errors import NotFoundError, ValidationError
from app.core.permissions import Action, Module
from app.transforms import connections as connection_service
from app.transforms.models import TransformEnvironment, TransformProject
from app.transforms.runtime.profiles import resolve_schema, user_token
from app.transforms.scaffold import validate_schema_name

DEVELOPMENT = "DEVELOPMENT"
PRODUCTION = "PRODUCTION"


async def get(
    session: AsyncSession, project: TransformProject, environment_id: uuid.UUID,
) -> TransformEnvironment:
    row = await session.scalar(select(TransformEnvironment).where(
        TransformEnvironment.id == environment_id,
        TransformEnvironment.project_id == project.id,
        TransformEnvironment.deleted_at.is_(None),
    ))
    if row is None:
        raise NotFoundError("That environment was not found in this project.")
    return row


async def list_all(
    session: AsyncSession, project: TransformProject,
) -> list[TransformEnvironment]:
    return list((await session.scalars(select(TransformEnvironment).where(
        TransformEnvironment.project_id == project.id,
        TransformEnvironment.deleted_at.is_(None),
    ).order_by(TransformEnvironment.type.desc(), TransformEnvironment.name))).all())


async def resolve(
    session: AsyncSession,
    project: TransformProject,
    environment_id: uuid.UUID | None,
    *,
    default_to: str = DEVELOPMENT,
) -> TransformEnvironment:
    """Which environment a request means.

    An unspecified environment resolves to development, never production.  The
    asymmetry is deliberate: a missing field should not be able to run something
    against production tables.
    """
    if environment_id is not None:
        return await get(session, project, environment_id)
    preferred = (
        project.default_environment_id if default_to == DEVELOPMENT
        else project.production_environment_id
    )
    if preferred is not None:
        return await get(session, project, preferred)
    rows = await list_all(session, project)
    match = next((row for row in rows if row.type == default_to), None)
    if match is None:
        raise ValidationError(
            "This project has no environment to run in.",
            code="TRANSFORM_ENVIRONMENT_MISSING",
        )
    return match


async def create_defaults(
    session: AsyncSession,
    project: TransformProject,
    *,
    connection_id: uuid.UUID,
    development_schema: str,
    production_schema: str,
    per_user_schemas: bool = False,
    threads: int = 4,
) -> tuple[TransformEnvironment, TransformEnvironment]:
    """The development and production pair a new project starts with.

    Both point at the same connection initially, because that is the only key
    the person creating the project has given us.  The settings page is where a
    separate production credential gets attached, and the model supports it from
    the first day rather than requiring a migration later.
    """
    development = TransformEnvironment(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Development",
        type=DEVELOPMENT,
        connection_id=connection_id,
        target_name="dev",
        schema_strategy="PER_USER" if per_user_schemas else "STATIC",
        schema_name=validate_schema_name(development_schema, "Development schema"),
        threads=threads,
        vars_json={},
        env_metadata={},
        protected=False,
    )
    production = TransformEnvironment(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Production",
        type=PRODUCTION,
        connection_id=connection_id,
        target_name="prod",
        schema_strategy="STATIC",
        schema_name=validate_schema_name(production_schema, "Production schema"),
        threads=threads,
        vars_json={},
        env_metadata={},
        protected=True,
    )
    session.add_all([development, production])
    await session.flush()
    project.default_environment_id = development.id
    project.production_environment_id = production.id
    return development, production


async def update(
    session: AsyncSession,
    ctx: RequestContext,
    project: TransformProject,
    environment_id: uuid.UUID,
    *,
    name: str | None = None,
    connection_id: uuid.UUID | None = None,
    target_name: str | None = None,
    schema_name: str | None = None,
    schema_strategy: str | None = None,
    schema_prefix: str | None = None,
    schema_suffix: str | None = None,
    threads: int | None = None,
    vars_json: dict[str, Any] | None = None,
) -> TransformEnvironment:
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    row = await get(session, project, environment_id)

    if name is not None:
        label = name.strip()
        if not label:
            raise ValidationError(
                "An environment needs a name.", code="TRANSFORM_ENVIRONMENT_NO_NAME",
            )
        row.name = label
    if connection_id is not None:
        connection = await connection_service.get(
            session, project.workspace_id, connection_id,
        )
        # Changing to a different warehouse kind would leave the project's own
        # adapter-specific configs meaningless -- BigQuery partitioning in a
        # Postgres target compiles and then fails at run time.
        current = row.connection_id
        if current is not None:
            existing = await connection_service.get(
                session, project.workspace_id, current,
            )
            if existing.connector_key != connection.connector_key:
                raise ValidationError(
                    "That connection is a different kind of warehouse. Create a "
                    "new project rather than repointing this one.",
                    code="TRANSFORM_ENVIRONMENT_ADAPTER_MISMATCH",
                )
        row.connection_id = connection.id
    if target_name is not None:
        import re

        if not re.match(r"^[A-Za-z0-9_-]{1,64}$", target_name):
            raise ValidationError(
                "A target name may contain letters, numbers, dashes and underscores.",
                code="TRANSFORM_ENVIRONMENT_TARGET_INVALID",
            )
        row.target_name = target_name
    if schema_name is not None:
        row.schema_name = validate_schema_name(schema_name, "Schema")
    if schema_strategy is not None:
        if schema_strategy.upper() not in ("STATIC", "PER_USER"):
            raise ValidationError(
                "Schema strategy must be STATIC or PER_USER.",
                code="TRANSFORM_ENVIRONMENT_STRATEGY_INVALID",
            )
        if schema_strategy.upper() == "PER_USER" and row.type == PRODUCTION:
            # Per-user production schemas would mean the tables a dashboard
            # reads depend on who last pressed Run.
            raise ValidationError(
                "A production environment writes to one schema, not one per person.",
                code="TRANSFORM_ENVIRONMENT_STRATEGY_INVALID",
            )
        row.schema_strategy = schema_strategy.upper()
    if schema_prefix is not None:
        row.schema_prefix = schema_prefix.strip() or None
    if schema_suffix is not None:
        row.schema_suffix = schema_suffix.strip() or None
    if threads is not None:
        row.threads = max(1, min(int(threads), 32))
    if vars_json is not None:
        row.vars_json = vars_json

    project.updated_by = ctx.user_id
    await session.flush()
    return row


def effective_schema(
    environment: TransformEnvironment, *, user_id: uuid.UUID | None,
) -> str:
    """The schema this environment writes into for this person."""
    return resolve_schema(
        strategy=environment.schema_strategy,
        base=environment.schema_name,
        prefix=environment.schema_prefix,
        suffix=environment.schema_suffix,
        user_token=user_token(user_id) if user_id else None,
    )


def view(
    environment: TransformEnvironment,
    *,
    connection: dict[str, Any] | None = None,
    user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return {
        "id": environment.id,
        "name": environment.name,
        "type": environment.type,
        "connection": connection,
        "target_name": environment.target_name,
        "schema_strategy": environment.schema_strategy,
        "schema_name": environment.schema_name,
        "effective_schema": effective_schema(environment, user_id=user_id),
        "threads": environment.threads,
        "vars": environment.vars_json or {},
        "protected": environment.protected,
    }


def require_operate(ctx: RequestContext, environment: TransformEnvironment) -> None:
    """Gate a protected environment behind OPERATE.

    A developer with EDIT can build in development all day.  Touching the
    production schema is a different act and needs the permission that says so.
    """
    if environment.protected:
        ctx.require(Module.TRANSFORMS, Action.OPERATE)
    else:
        ctx.require(Module.TRANSFORMS, Action.EDIT)


async def touch(session: AsyncSession, environment: TransformEnvironment) -> None:
    environment.updated_at = utcnow()
    await session.flush()
