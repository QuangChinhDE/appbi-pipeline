"""Connector catalog service (section 11).

The DB copy is authoritative for the UI. A refresh asks the engine for real
connector specs, but a failure there degrades to "stale catalog", never to a
broken page.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.dto import ConnectorDescriptor
from app.adapters.registry import (
    bundled_by_key, bundled_certifications, bundled_connectors, get_adapter, spec_hash,
)
from app.core.config import settings
from app.core.db import utcnow
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import log_event
from app.core.params import as_enum
from app.models.engine import ConnectorDefinition
from app.models.enums import Certification, ConnectorStatus, ConnectorType

logger = logging.getLogger(__name__)


async def seed_catalog(session: AsyncSession) -> int:
    """Idempotent: insert bundled connectors, refresh pin/version metadata.

    The catalogue is the full upstream registry (650+ connectors), so existing
    rows are read in one query rather than one per connector.
    """
    certifications = bundled_certifications()
    existing_rows = (await session.scalars(select(ConnectorDefinition))).all()
    by_key = {row.connector_key: row for row in existing_rows}
    created = 0

    for metadata in bundled_connectors():
        existing = by_key.get(metadata.connector_key)
        if existing is None:
            session.add(
                ConnectorDefinition(
                    connector_key=metadata.connector_key,
                    display_name=metadata.display_name,
                    connector_type=ConnectorType(metadata.connector_type),
                    category=metadata.category,
                    description=metadata.description,
                    icon=metadata.icon,
                    icon_url=metadata.icon_url,
                    documentation_url=metadata.documentation_url,
                    docker_repository=metadata.docker_repository,
                    version=metadata.version,
                    latest_version=metadata.version,
                    release_stage=metadata.release_stage,
                    support_level=metadata.support_level,
                    engine_definition_id=metadata.engine_definition_id,
                    supports_oauth=metadata.supports_oauth,
                    supports_incremental=metadata.supports_incremental,
                    supports_cdc=metadata.supports_cdc,
                    supports_namespaces=metadata.supports_namespaces,
                    supported_destination_sync_modes=metadata.supported_destination_sync_modes,
                    spec_schema=metadata.spec_schema,
                    spec_hash=spec_hash(metadata.spec_schema),
                    spec_source="BUNDLED",
                    certification=Certification(
                        certifications.get(metadata.connector_key, "BETA")
                    ),
                )
            )
            created += 1
        else:
            # Keep the bundled pin authoritative for version/certification so a
            # redeploy of the product cannot silently drift from the tested set.
            existing.docker_repository = metadata.docker_repository
            existing.display_name = metadata.display_name
            existing.category = metadata.category
            existing.description = metadata.description
            existing.icon = metadata.icon
            existing.icon_url = metadata.icon_url
            existing.documentation_url = metadata.documentation_url
            existing.support_level = metadata.support_level
            existing.release_stage = metadata.release_stage
            existing.certification = Certification(
                certifications.get(metadata.connector_key, existing.certification.value)
            )
            if existing.spec_source == "BUNDLED":
                existing.spec_schema = metadata.spec_schema
                existing.spec_hash = spec_hash(metadata.spec_schema)
                existing.version = metadata.version

    await session.flush()
    return created


async def list_connectors(
    session: AsyncSession,
    *,
    connector_type: str | None = None,
    query: str | None = None,
    category: str | None = None,
    include_hidden: bool = False,
    limit: int | None = None,
) -> list[ConnectorDefinition]:
    stmt = select(ConnectorDefinition)
    if connector_type:
        stmt = stmt.where(
            ConnectorDefinition.connector_type
            == as_enum(connector_type, ConnectorType, field="type")
        )
    if category:
        stmt = stmt.where(ConnectorDefinition.category == category)
    if not include_hidden:
        stmt = stmt.where(ConnectorDefinition.certification != Certification.HIDDEN)
    if query:
        # Matched in SQL: the catalogue is the full upstream registry, so loading
        # every row to filter it in Python is wasted work on every keystroke.
        needle = f"%{query.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(ConnectorDefinition.display_name).like(needle),
                func.lower(ConnectorDefinition.connector_key).like(needle),
            )
        )
    rows = list((await session.scalars(stmt)).all())
    order = {Certification.SUPPORTED: 0, Certification.BETA: 1,
             Certification.BLOCKED: 2, Certification.HIDDEN: 3}
    rows.sort(key=lambda r: (order.get(r.certification, 9), r.display_name.lower()))
    return rows[:limit] if limit else rows


async def get_connector(session: AsyncSession, connector_key: str) -> ConnectorDefinition:
    row = await session.scalar(
        select(ConnectorDefinition).where(ConnectorDefinition.connector_key == connector_key)
    )
    if row is None:
        raise NotFoundError(f"Không tìm thấy connector '{connector_key}'.")
    return row


async def require_usable(session: AsyncSession, connector_key: str, expected_type: ConnectorType
                         ) -> ConnectorDefinition:
    connector = await get_connector(session, connector_key)
    if connector.connector_type is not expected_type:
        raise ValidationError(
            f"Connector '{connector_key}' không phải loại {expected_type.value.lower()}."
        )
    if connector.status is ConnectorStatus.DISABLED or connector.certification is Certification.BLOCKED:
        raise ValidationError(
            connector.disabled_reason or "Connector này đang bị tạm khóa cho workspace của bạn.",
            code="CONNECTOR_DISABLED",
        )
    # The launch scope, enforced where it cannot be bypassed. The presenter
    # applies the same rule so the catalogue matches, but the presenter is a
    # rendering decision and this is the one that holds against a direct API
    # call.
    if not settings.connector_is_offered(connector.connector_key,
                                         connector.certification.value):
        raise ValidationError(
            "Connector này chưa nằm trong phạm vi hỗ trợ của bản phát hành "
            "hiện tại. Liên hệ quản trị viên nếu bạn cần bật nó.",
            code="CONNECTOR_NOT_IN_LAUNCH_SCOPE",
        )
    return connector


def descriptor(connector: ConnectorDefinition) -> ConnectorDescriptor:
    return ConnectorDescriptor(
        connector_key=connector.connector_key,
        docker_repository=connector.docker_repository,
        version=connector.version,
        engine_definition_id=connector.engine_definition_id,
        declarative_manifest=connector.declarative_manifest,
    )


async def refresh_specs(
    session: AsyncSession,
    *,
    only_key: str | None = None,
    limit: int = 12,
) -> dict[str, str]:
    """Ask the engine for real specs, for a bounded set of connectors.

    Two things make a blanket refresh wrong now that the catalogue is the whole
    upstream registry:

    * Every connector refreshed means one `docker run <image> spec`, which pulls
      the image if it is absent. Across 650+ connectors that is hundreds of
      gigabytes of pulls for specs the bundled registry already ships.
    * Holding one transaction open across those calls blocks DDL behind it, and
      anything queued behind that DDL — a table-wide stall from a background job.

    So this refreshes only connectors the workspace actually uses (or one named
    connector), oldest first, a few per cycle, and never keeps a transaction open
    across an engine call.
    """
    adapter = get_adapter()
    outcome: dict[str, str] = {}

    stmt = select(ConnectorDefinition)
    if only_key:
        stmt = stmt.where(ConnectorDefinition.connector_key == only_key)
    else:
        # An unused connector's spec costs an image pull to learn nothing: the
        # bundled registry spec is what the wizard already renders.
        stmt = (
            stmt.where(ConnectorDefinition.usage_count > 0)
            .order_by(ConnectorDefinition.last_refreshed_at.asc().nullsfirst())
            .limit(limit)
        )

    candidates = [
        (row.id, row.connector_key, descriptor(row), row.spec_hash)
        for row in (await session.scalars(stmt)).all()
    ]
    # Release the read snapshot before touching the engine; the calls below can
    # take minutes and must not pin a transaction open for that long.
    await session.commit()

    for connector_id, connector_key, engine_descriptor, previous_hash in candidates:
        try:
            metadata = await adapter.get_connector_spec(engine_descriptor)
        except Exception as exc:  # noqa: BLE001 - degraded refresh is acceptable
            outcome[connector_key] = f"failed: {type(exc).__name__}"
            log_event(logger, logging.WARNING, "catalog.refresh_failed",
                      connector=connector_key, error=str(exc)[:300])
            continue

        connector = await session.get(ConnectorDefinition, connector_id)
        if connector is None:          # deleted while we were talking to the engine
            continue

        new_hash = spec_hash(metadata.spec_schema)
        if metadata.spec_schema:
            connector.spec_schema = metadata.spec_schema
            connector.spec_hash = new_hash
            connector.spec_source = "ENGINE"
        connector.supports_incremental = metadata.supports_incremental
        connector.supports_oauth = metadata.supports_oauth
        if metadata.supported_destination_sync_modes:
            connector.supported_destination_sync_modes = metadata.supported_destination_sync_modes
        # What the engine says it will run. In embedded mode this equals the
        # product's pinned version; in API mode it is Airbyte's own choice, and
        # the difference is exactly what an operator needs to see.
        if metadata.engine_version:
            connector.engine_version = metadata.engine_version
        connector.image_pulled = True
        connector.last_refreshed_at = utcnow()
        # One short transaction per connector, so a slow neighbour cannot hold
        # this one's row lock.
        await session.commit()
        outcome[connector_key] = "changed" if new_hash != previous_hash else "unchanged"

    return outcome


def catalog_is_stale(connector: ConnectorDefinition) -> bool:
    if connector.last_refreshed_at is None:
        return True
    return utcnow() - connector.last_refreshed_at > timedelta(seconds=settings.catalog_refresh_seconds)


# How a connector spec says "this field is a credential". `airbyte_secret` is
# Airbyte's JSON Schema extension; the other two are standard JSON Schema and
# are what a non-Airbyte engine is likely to use. Recognising all of them keeps
# this layer engine-agnostic without changing what happens to an Airbyte spec.
#
# The direction of the error matters here. Recognising a marker that turns out
# not to mean "secret" encrypts and masks a field that did not need it —
# irritating. Failing to recognise one writes a password into plain
# configuration. So this list errs toward more markers, never fewer.
SECRET_MARKERS = ("airbyte_secret", "writeOnly", "secret")


def _marked_secret(prop: dict) -> bool:
    if any(prop.get(marker) for marker in SECRET_MARKERS):
        return True
    # JSON Schema's own convention for a credential input.
    return prop.get("format") == "password"


def split_configuration(spec: dict, payload: dict) -> tuple[dict, dict]:
    """Split a submitted form into (non-secret config, secret payload).

    Secrecy is driven by the connector spec, walked recursively so nested and
    oneOf credentials are caught too. See SECRET_MARKERS for what counts.
    """
    secrets: dict = {}
    config: dict = {}
    properties = (spec or {}).get("properties") or {}

    def is_secret(key: str) -> bool:
        prop = properties.get(key) or {}
        if _marked_secret(prop):
            return True
        for branch in prop.get("oneOf") or []:
            for sub_key, sub in (branch.get("properties") or {}).items():
                if _marked_secret(sub) and sub_key == key:
                    return True
        return False

    for key, value in (payload or {}).items():
        if is_secret(key):
            secrets[key] = value
        elif isinstance(value, dict):
            nested_spec = properties.get(key) or {}
            branches = nested_spec.get("oneOf") or [nested_spec]
            nested_secret_keys = {
                sub_key
                for branch in branches
                for sub_key, sub in (branch.get("properties") or {}).items()
                if _marked_secret(sub)
            }
            if nested_secret_keys & set(value.keys()):
                config[key] = {k: v for k, v in value.items() if k not in nested_secret_keys}
                secrets[key] = {k: v for k, v in value.items() if k in nested_secret_keys}
            else:
                config[key] = value
        else:
            config[key] = value
    return config, secrets


def merge_configuration(config: dict, secrets: dict) -> dict:
    """Rebuild the full connector configuration just before an engine call."""
    merged = dict(config or {})
    for key, value in (secrets or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def apply_spec_defaults(spec: dict, configuration: dict) -> dict:
    """Fill in spec defaults the form did not send, so connectors that require a
    field (postgres `replication_method`, `ssl_mode`, ...) still get one."""
    out = dict(configuration or {})
    for key, prop in ((spec or {}).get("properties") or {}).items():
        if key in out and out[key] not in (None, ""):
            continue
        if "default" in prop:
            out[key] = prop["default"]
    return out


def validate_against_spec(spec: dict, configuration: dict) -> None:
    """Light client-parity validation. The connector's own `check` is the real
    gate; this only catches obvious omissions before we pay for a container."""
    missing = [
        key for key in (spec or {}).get("required") or []
        if configuration.get(key) in (None, "", [], {})
    ]
    if missing:
        properties = (spec or {}).get("properties") or {}
        labels = [properties.get(key, {}).get("title", key) for key in missing]
        raise ValidationError(
            "Thiếu thông tin bắt buộc: " + ", ".join(labels),
            details={"missing_fields": missing},
        )
