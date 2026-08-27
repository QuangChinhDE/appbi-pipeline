"""Connector catalog service (section 11).

The DB copy is authoritative for the UI. A refresh asks the engine for real
connector specs, but a failure there degrades to "stale catalog", never to a
broken page.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
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
from app.models.integration import Destination, Source
from app.models.enums import Certification, ConnectorStatus, ConnectorType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SeedOutcome:
    """What re-seeding actually changed, so the caller can finish the job.

    `manifests_changed` exists because overwriting the catalogue row is only
    half of "fix the Base API logic once and every workspace has it". A
    declarative connector's behaviour is injected into each source at the time
    that source is created, and the engine keeps its own copy from then on. A
    deploy that edits the manifest therefore reaches the catalogue and no
    running resource: `deal_activity` was fixed, re-seeded, and re-synced three
    times still emitting the unfiltered 3,970 rows, because the source in the
    engine was the one built the hour before.
    """

    created: int
    manifests_changed: frozenset[str] = frozenset()


async def seed_catalog(session: AsyncSession) -> SeedOutcome:
    """Idempotent: insert bundled connectors, refresh pin/version metadata.

    The catalogue is the full upstream registry (650+ connectors), so existing
    rows are read in one query rather than one per connector.
    """
    certifications = bundled_certifications()
    existing_rows = (await session.scalars(select(ConnectorDefinition))).all()
    by_key = {row.connector_key: row for row in existing_rows}
    created = 0
    manifests_changed: set[str] = set()

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
                    declarative_manifest=metadata.declarative_manifest,
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
            # `version` is a pin, not part of the spec. It is the image tag
            # the adapter pushes into the engine when it creates a resource,
            # so it has to stay bundled-authoritative no matter where the spec
            # came from. Refreshing it only in the BUNDLED branch meant that
            # once a row's spec had been read back from the engine, the
            # bundled pin could never win again: the engine bootloader's drift
            # became permanent product state, and `_ensure_definition_version`
            # then faithfully pushed that drift back into the engine on every
            # resource creation -- undoing `airbyte-connector-pin` minutes
            # after it ran. What the engine actually offers is a separate
            # observation and lives in `engine_version`, which
            # `refresh_from_engine` owns; this column is the product's.
            existing.version = metadata.version
            if existing.spec_source == "BUNDLED" or metadata.declarative_manifest is not None:
                existing.spec_schema = metadata.spec_schema
                existing.spec_hash = spec_hash(metadata.spec_schema)
                # The manifest *is* the connector, for the ones this product
                # defines itself. Overwriting it here is what makes "fix the
                # Base API logic once and every workspace has it" true: the
                # code in `app/connectors` is the source of truth and a deploy
                # re-seeds it. A Connector Builder project has
                # `spec_source != BUNDLED` and is never touched.
                if existing.declarative_manifest != metadata.declarative_manifest:
                    manifests_changed.add(metadata.connector_key)
                existing.declarative_manifest = metadata.declarative_manifest
                existing.spec_source = "BUNDLED"

    # Remove connectors this build no longer ships.
    #
    # `seed_catalog` only ever inserted and updated, so trimming the bundled
    # catalogue left every dropped connector sitting in the database: still
    # listed, still rendering as a locked card, still claiming a version
    # nothing pins any more. An upsert-only sync quietly turns the catalogue
    # into the union of every version ever deployed.
    #
    # Two things are never pruned. A connector built in the product has
    # `spec_source != "BUNDLED"` and was never ours to remove. And a connector
    # some source or destination still uses stays, because deleting it would
    # orphan a working resource -- it is disabled instead, which is visible and
    # reversible.
    shipped = {metadata.connector_key for metadata in bundled_connectors()}
    in_use = set((await session.scalars(select(Source.connector_key))).all())
    in_use |= set((await session.scalars(select(Destination.connector_key))).all())

    for key, row in by_key.items():
        if key in shipped or row.spec_source != "BUNDLED":
            continue
        if key in in_use:
            row.certification = Certification.BLOCKED
            row.status = ConnectorStatus.DISABLED
            row.disabled_reason = (
                "Connector này không còn nằm trong phạm vi phát hành, nhưng "
                "vẫn có kết nối đang dùng nó.")
            continue
        await session.delete(row)

    await session.flush()
    return SeedOutcome(created=created,
                       manifests_changed=frozenset(manifests_changed))


async def list_connectors(
    session: AsyncSession,
    *,
    connector_type: str | None = None,
    query: str | None = None,
    category: str | None = None,
    include_hidden: bool = False,
    selectable_only: bool = False,
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
    if selectable_only:
        # The create wizard's list. Filtered here rather than in the browser,
        # because the browser was downloading 598 connectors (~515 KB) to show
        # five: the launch scope is a server-side policy, so the client should
        # never have to receive the rest to discover it cannot use them.
        #
        # Certification alone is not the answer -- `connector_is_offered` also
        # honours the per-deployment beta allowlist -- so the decision runs
        # through the same function the create path enforces.
        rows = [row for row in rows
                if settings.connector_is_offered(row.connector_key,
                                                 row.certification.value,
                                                 row.spec_source)]
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
                                         connector.certification.value,
                                         connector.spec_source):
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
            # A bundled declarative manifest owns its customer-facing spec.
            # Airbyte only sees the generic runner's internal manifest slot.
            if connector.declarative_manifest is None:
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


def _spec_branches(node: dict) -> list[dict]:
    """A spec node together with every alternative it can take.

    `oneOf` is how Airbyte models "pick an auth method" or "pick a loading
    method", and the branches nest: BigQuery's `loading_method` is a `oneOf`
    whose GCS branch has a `credential` which is itself a `oneOf` holding the
    HMAC secret. Flattening only the first level is what let that secret
    through.
    """
    out = [node]
    for keyword in ("oneOf", "anyOf", "allOf"):
        for branch in node.get(keyword) or []:
            if isinstance(branch, dict):
                out.extend(_spec_branches(branch))
    return out


def _child_specs(node: dict, key: str) -> list[dict]:
    """Every sub-spec `key` could match, across all branches."""
    found = []
    for branch in _spec_branches(node or {}):
        properties = branch.get("properties") or {}
        if key in properties and isinstance(properties[key], dict):
            found.append(properties[key])
    return found


def _item_spec(node: dict) -> dict:
    for branch in _spec_branches(node or {}):
        items = branch.get("items")
        if isinstance(items, dict):
            return items
    return {}


def _split_node(spec: dict, value):
    """Split one value into (non-secret, secret, whether any secret was found).

    Shape is preserved on both sides so `merge_configuration` can rebuild the
    original exactly: a dict stays a dict at the same key, a list stays a list
    of the same length with `None` where an element held nothing secret.
    """
    if isinstance(value, dict):
        config: dict = {}
        secrets: dict = {}
        for key, child in value.items():
            candidates = _child_specs(spec, key)
            # If *any* branch calls this secret, it is secret. Branches
            # disagree in real specs, and the safe reading is the strict one.
            if any(_marked_secret(candidate) for candidate in candidates):
                secrets[key] = child
                continue
            # Recurse using the richest matching branch -- the one that
            # actually describes children.
            deeper = next(
                (c for c in candidates
                 if c.get("properties") or c.get("oneOf") or c.get("anyOf")
                 or c.get("allOf") or c.get("items")),
                candidates[0] if candidates else {})
            child_config, child_secret, found = _split_node(deeper, child)
            config[key] = child_config
            if found:
                secrets[key] = child_secret
        return config, secrets, bool(secrets)

    if isinstance(value, list):
        item_spec = _item_spec(spec)
        configs = []
        secrets_list = []
        found_any = False
        for element in value:
            element_config, element_secret, found = _split_node(item_spec, element)
            configs.append(element_config)
            secrets_list.append(element_secret if found else None)
            found_any = found_any or found
        return configs, (secrets_list if found_any else None), found_any

    return value, None, False


def split_configuration(spec: dict, payload: dict) -> tuple[dict, dict]:
    """Split a submitted form into (non-secret config, secret payload).

    Secrecy is decided by the connector spec, walked to any depth through
    `properties`, `oneOf`/`anyOf`/`allOf` branches and array `items`.

    This used to descend exactly one level and through exactly one `oneOf`,
    while its docstring said recursive. `destination-bigquery` puts its HMAC
    secret at `loading_method.credential.hmac_key_secret` -- two levels down,
    two `oneOf`s in -- so that secret was written into plain configuration,
    stored unencrypted, and returned by an endpoint a VIEW-only role can call.

    See SECRET_MARKERS for what counts as a marker; the error is deliberately
    biased toward encrypting a field that did not need it.
    """
    config, secrets, _ = _split_node(spec or {}, payload or {})
    return config, secrets


def _merge_node(config, secrets):
    if secrets is None:
        return config
    if isinstance(secrets, dict) and isinstance(config, dict):
        merged = dict(config)
        for key, value in secrets.items():
            merged[key] = _merge_node(config.get(key), value)
        return merged
    if isinstance(secrets, list) and isinstance(config, list):
        if len(secrets) == len(config):
            return [_merge_node(item, secret)
                    for item, secret in zip(config, secrets)]
    return secrets


def merge_configuration(config: dict, secrets: dict) -> dict:
    """Rebuild the full connector configuration just before an engine call.

    The exact inverse of `split_configuration`, to the same depth. A merge that
    stopped a level short of the split would hand the connector a config with a
    hole in it, and the failure would look like a bad credential.
    """
    merged = _merge_node(dict(config or {}), secrets or {})
    return merged if isinstance(merged, dict) else dict(config or {})


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
