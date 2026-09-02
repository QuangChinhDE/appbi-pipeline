"""Warehouse connections, and their lifecycle.

A connection is a named warehouse key.  The credential lives in the encrypted
secret store; this layer decides which key a given environment runs as, keeps
the record of whether it still works, and never lets a credential reach the
browser or an exported project.

The lifecycle operations are the part V1 lacked and the blueprint asks for:
re-test, update config, rotate the secret, reconnect an OAuth grant, and a
recorded verdict from the last check.  A key that worked in March and does not
work in September should say so on the settings page, rather than being
discovered by a schedule at 03:00.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.db import utcnow
from app.core.errors import NotFoundError, ValidationError
from app.core.permissions import Action, Module
from app.core.secrets import secret_store
from app.models.engine import ConnectorDefinition
from app.models.integration import Destination
from app.services import actors as actor_service, audit
from app.transforms.compatibility import capability
from app.transforms.warehouse import browse_catalogs
from app.transforms.models import (
    TransformConnection, TransformEnvironment, TransformProject,
)


async def get(
    session: AsyncSession, workspace_id: uuid.UUID, connection_id: uuid.UUID,
) -> TransformConnection:
    row = await session.scalar(select(TransformConnection).where(
        TransformConnection.id == connection_id,
        TransformConnection.workspace_id == workspace_id,
        TransformConnection.deleted_at.is_(None),
    ))
    if row is None:
        raise NotFoundError("That warehouse connection was not found.")
    return row


def connector_key(connection: TransformConnection) -> str:
    if connection.connector_key:
        return connection.connector_key
    raise ValidationError(
        "This connection does not record which kind of warehouse it reaches.",
        code="TRANSFORM_CONNECTION_NO_SYSTEM",
    )


async def resolve_configuration(
    session: AsyncSession, connection: TransformConnection,
) -> dict[str, Any]:
    """The complete connector configuration this connection runs as.

    A connection made from a Destination inherits that Destination's
    configuration, because it *is* that warehouse and re-entering its address
    would only be a chance to get it wrong.  One made by pasting a key carries
    its own, because there is nothing to inherit from.

    dbt uses one connection for reading sources and writing models, so whatever
    comes back here has to do both.
    """
    base: dict[str, Any] = {}
    if connection.destination_id is not None:
        destination = await session.get(Destination, connection.destination_id)
        if destination is not None:
            base = await actor_service.resolve_configuration(session, destination)
    own = dict(connection.configuration_json or {})
    if connection.secret_ref:
        own.update(await secret_store.read(session, connection.secret_ref))
    return {**base, **{key: value for key, value in own.items() if value not in (None, "")}}


def supported_systems() -> list[dict[str, Any]]:
    """The warehouse kinds Transform runs on, and how each authenticates.

    Read from the engine lock rather than from the Destinations that happen to
    exist: step one of creating a project is choosing a *kind* of system, and
    there may be no Destination for it yet -- which is the case the whole flow
    exists for.
    """
    from app.services import oauth as oauth_service

    systems: list[dict[str, Any]] = []
    for key, label in (
        ("destination-bigquery", "BigQuery"),
        ("destination-postgres", "PostgreSQL"),
        ("destination-mssql", "SQL Server"),
    ):
        cap = capability(key)
        if not cap or cap.get("certification") != "SUPPORTED":
            continue
        methods = ["service_account"] if key == "destination-bigquery" else ["password"]
        # OAuth is offered only where this deployment has registered an
        # application. Half-offering it means a consent screen that 404s.
        if key == "destination-bigquery":
            provider = oauth_service.PROVIDERS.get("google")
            if provider is not None and oauth_service.configured(provider):
                methods.append("oauth")
        systems.append({
            "connector_key": key,
            "label": label,
            "auth_methods": methods,
            "adapter": cap.get("package"),
            "adapter_version": cap.get("version"),
            "dbt_core": cap.get("dbt_core"),
        })
    return systems


def view(
    connection: TransformConnection, destination_name: str | None = None,
) -> dict[str, Any]:
    return {
        "id": connection.id,
        "name": connection.name,
        "connector_key": connection.connector_key or "",
        "auth_method": connection.auth_method or "inherited",
        "destination_id": connection.destination_id,
        "destination_name": destination_name,
        "account": connection.account,
        "catalogs": connection.catalogs or [],
        "is_default": connection.is_default,
        "verification_status": connection.verification_status,
        "verification_message": connection.verification_message,
        "last_verified_at": connection.last_verified_at,
    }


async def list_all(
    session: AsyncSession, ctx: RequestContext, connector_key: str | None = None,
) -> list[dict[str, Any]]:
    """Connections a project could run on, the Destinations' own keys first.

    Those need nothing entered and are what somebody who has been using
    Pipelines expects to find already there.
    """
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    rows = list((await session.scalars(select(TransformConnection).where(
        TransformConnection.workspace_id == ctx.workspace_id,
        TransformConnection.deleted_at.is_(None),
    ).order_by(TransformConnection.is_default.desc(), TransformConnection.name))).all())

    out: list[dict[str, Any]] = []
    for row in rows:
        if connector_key and row.connector_key != connector_key:
            continue
        name = None
        if row.destination_id is not None:
            destination = await session.get(Destination, row.destination_id)
            name = destination.name if destination else None
            # An inherited connection has no account of its own -- it is the
            # Destination's. Reading it here rather than storing a copy means a
            # rotated credential shows the new account without a re-save.
            if destination is not None and not row.account:
                try:
                    configuration = await actor_service.resolve_configuration(
                        session, destination,
                    )
                    row.account = account_label(
                        destination.connector_key, "inherited", configuration, configuration,
                    )
                except Exception:  # noqa: BLE001 - a listing must not fail on one row
                    pass
        out.append(view(row, name))
    return out


async def create(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    connector_key: str,
    name: str,
    auth_method: str,
    configuration: dict[str, Any],
    credentials: dict[str, Any],
) -> dict[str, Any]:
    """Check a key against the warehouse, then keep it under a name."""
    ctx.require(Module.TRANSFORMS, Action.CREATE)
    cap = capability(connector_key)
    if not cap or cap.get("certification") != "SUPPORTED":
        raise ValidationError(
            "Transform does not support this kind of warehouse yet.",
            code="TRANSFORM_SYSTEM_UNSUPPORTED",
        )
    label = (name or "").strip()
    if not label:
        raise ValidationError(
            "Give this connection a name.", code="TRANSFORM_CONNECTION_NO_NAME",
        )
    await _assert_name_free(session, ctx.workspace_id, label)

    config = {key: value for key, value in configuration.items() if value not in (None, "")}
    secrets = {key: value for key, value in credentials.items() if value not in (None, "")}
    if not secrets:
        raise ValidationError(
            "No credentials were entered.", code="TRANSFORM_CONNECTION_EMPTY",
        )
    # Verified before it is kept: a connection nobody can use is worse in a
    # list than absent from one, because it looks like a working choice.
    catalogs = await browse_catalogs(connector_key, {**config, **secrets})
    ref = await secret_store.write(session, ctx.workspace_id, secrets)

    row = TransformConnection(
        id=uuid.uuid4(),
        workspace_id=ctx.workspace_id,
        destination_id=None,
        name=label,
        connector_key=connector_key,
        auth_method=auth_method,
        configuration_json=config,
        is_default=False,
        secret_ref=ref,
        account=account_label(connector_key, auth_method, config, secrets),
        catalogs=catalogs,
        verification_status="OK",
        verification_message=None,
        last_verified_at=utcnow(),
        created_by=ctx.user_id,
    )
    session.add(row)
    await session.flush()
    await audit.record(
        session, ctx, "transform.connection.created", resource_type="TRANSFORM",
        resource_id=row.id, resource_name=label,
        after={"connector_key": connector_key, "auth_method": auth_method,
               "catalogs": len(catalogs)},
    )
    return view(row)


async def verify(
    session: AsyncSession, ctx: RequestContext, connection_id: uuid.UUID,
) -> dict[str, Any]:
    """Re-check a stored key and record the verdict.

    Failure is recorded, not raised.  The caller asked whether the key still
    works; "no, because the service account was deleted" is the answer, and
    turning it into a 400 loses it.
    """
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    row = await get(session, ctx.workspace_id, connection_id)
    try:
        configuration = await resolve_configuration(session, row)
        catalogs = await browse_catalogs(connector_key(row), configuration)
    except Exception as exc:  # noqa: BLE001 - the verdict is the return value
        row.verification_status = "FAILED"
        row.verification_message = f"{type(exc).__name__}: {exc}"[:1000]
        row.last_verified_at = utcnow()
        await audit.record(
            session, ctx, "transform.connection.verify_failed",
            resource_type="TRANSFORM", resource_id=row.id, resource_name=row.name,
        )
        return view(row)

    row.catalogs = catalogs
    row.verification_status = "OK"
    row.verification_message = None
    row.last_verified_at = utcnow()
    await audit.record(
        session, ctx, "transform.connection.verified", resource_type="TRANSFORM",
        resource_id=row.id, resource_name=row.name,
    )
    return view(row)


async def update(
    session: AsyncSession,
    ctx: RequestContext,
    connection_id: uuid.UUID,
    *,
    name: str | None = None,
    configuration: dict[str, Any] | None = None,
    credentials: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rename, re-point, or rotate the secret behind a connection.

    Rotation replaces the secret in place, so every project and environment
    using this connection picks up the new key without being re-saved.  The old
    secret is deleted only after the new one verifies -- a rotation that fails
    half way must leave a working connection behind.
    """
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    row = await get(session, ctx.workspace_id, connection_id)
    if row.is_default and (configuration or credentials):
        raise ValidationError(
            "This connection belongs to a Destination. Change it on the "
            "Destination so Pipelines and Transform stay in step.",
            code="TRANSFORM_CONNECTION_IS_DEFAULT",
        )

    if name is not None:
        label = name.strip()
        if not label:
            raise ValidationError(
                "Give this connection a name.", code="TRANSFORM_CONNECTION_NO_NAME",
            )
        if label != row.name:
            await _assert_name_free(session, ctx.workspace_id, label)
            row.name = label

    if configuration is not None or credentials is not None:
        config = dict(row.configuration_json or {})
        if configuration is not None:
            config.update({
                key: value for key, value in configuration.items() if value not in (None, "")
            })
        existing = await secret_store.read(session, row.secret_ref) if row.secret_ref else {}
        secrets = dict(existing)
        if credentials:
            secrets.update({
                key: value for key, value in credentials.items() if value not in (None, "")
            })
        # Verify against the *new* values before anything is persisted.
        catalogs = await browse_catalogs(connector_key(row), {**config, **secrets})

        old_ref = row.secret_ref
        row.secret_ref = await secret_store.write(session, ctx.workspace_id, secrets)
        row.configuration_json = config
        row.catalogs = catalogs
        row.account = account_label(
            connector_key(row), row.auth_method or "", config, secrets,
        )
        row.verification_status = "OK"
        row.verification_message = None
        row.last_verified_at = utcnow()
        if old_ref and old_ref != row.secret_ref:
            await secret_store.delete(session, old_ref)

    await audit.record(
        session, ctx, "transform.connection.updated", resource_type="TRANSFORM",
        resource_id=row.id, resource_name=row.name,
        after={"rotated": bool(credentials)},
    )
    return view(row)


async def delete(
    session: AsyncSession, ctx: RequestContext, connection_id: uuid.UUID,
) -> None:
    """Retire a connection, unless an environment still runs on it."""
    ctx.require(Module.TRANSFORMS, Action.DELETE)
    row = await get(session, ctx.workspace_id, connection_id)
    if row.is_default:
        raise ValidationError(
            "This is a Destination's own key and cannot be deleted on its own.",
            code="TRANSFORM_CONNECTION_IS_DEFAULT",
        )
    users = list((await session.scalars(
        select(TransformProject.name)
        .join(TransformEnvironment, TransformEnvironment.project_id == TransformProject.id)
        .where(
            TransformEnvironment.connection_id == row.id,
            TransformEnvironment.deleted_at.is_(None),
            TransformProject.deleted_at.is_(None),
        )
        .distinct()
    )).all())
    if users:
        raise ValidationError(
            "This connection is in use by: " + ", ".join(users[:5])
            + (" …" if len(users) > 5 else "")
            + ". Point those environments at another connection first.",
            code="TRANSFORM_CONNECTION_IN_USE",
        )
    row.deleted_at = utcnow()
    if row.secret_ref:
        await secret_store.delete(session, row.secret_ref)
    await audit.record(
        session, ctx, "transform.connection.deleted", resource_type="TRANSFORM",
        resource_id=row.id, resource_name=row.name,
    )


async def ensure_destination_connections(
    session: AsyncSession, ctx: RequestContext,
) -> int:
    """Make sure every supported Destination has a selectable connection.

    Runs on demand rather than as a migration so a Destination created after
    the rework also becomes available to Transform without anybody noticing the
    difference.  Idempotent.
    """
    destinations = list((await session.scalars(select(Destination).where(
        Destination.workspace_id == ctx.workspace_id,
        Destination.deleted_at.is_(None),
    ))).all())
    existing = {
        row.destination_id for row in (await session.scalars(select(TransformConnection).where(
            TransformConnection.workspace_id == ctx.workspace_id,
            TransformConnection.destination_id.is_not(None),
            TransformConnection.deleted_at.is_(None),
        ))).all()
    }
    created = 0
    for destination in destinations:
        if destination.id in existing:
            continue
        cap = capability(destination.connector_key)
        if not cap or cap.get("certification") != "SUPPORTED":
            continue
        session.add(TransformConnection(
            id=uuid.uuid4(),
            workspace_id=ctx.workspace_id,
            destination_id=destination.id,
            name=destination.name,
            connector_key=destination.connector_key,
            auth_method="inherited",
            configuration_json={},
            is_default=True,
            secret_ref=None,
            verification_status="UNVERIFIED",
            created_by=ctx.user_id,
        ))
        created += 1
    if created:
        await session.flush()
    return created


async def connector_display(
    session: AsyncSession, connection: TransformConnection,
) -> dict[str, Any]:
    definition = await session.scalar(select(ConnectorDefinition).where(
        ConnectorDefinition.connector_key == (connection.connector_key or ""),
    ))
    return {
        "connection_id": connection.id,
        "name": connection.name,
        "connector_key": connection.connector_key or "",
        "connector_display_name": definition.display_name if definition else None,
        "icon": definition.icon if definition else None,
        "destination_id": connection.destination_id,
    }


async def _assert_name_free(
    session: AsyncSession, workspace_id: uuid.UUID, name: str,
) -> None:
    clash = await session.scalar(select(TransformConnection).where(
        TransformConnection.workspace_id == workspace_id,
        TransformConnection.name == name,
        TransformConnection.deleted_at.is_(None),
    ))
    if clash is not None:
        raise ValidationError(
            f"There is already a connection called `{name}`.",
            code="TRANSFORM_CONNECTION_NAME_TAKEN",
        )


def account_label(
    connector_key: str,
    auth_method: str,
    configuration: dict[str, Any],
    secrets: dict[str, Any],
) -> str | None:
    """Who the connection turned out to be, so a wrong one is obvious in a list."""
    if connector_key == "destination-bigquery":
        if auth_method == "oauth":
            return secrets.get("oauth_account") or configuration.get("project_id")
        raw = secrets.get("credentials_json")
        try:
            info = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (TypeError, ValueError):
            return None
        if isinstance(info, dict):
            return info.get("client_email") or info.get("project_id")
        return None
    if connector_key == "destination-postgres":
        user = secrets.get("username") or configuration.get("username")
        host = configuration.get("host")
        if user and host:
            return f"{user}@{host}"
        return user or host
    return None
