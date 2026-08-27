"""Regressions for defects the adversarial audit found.

Each test names the behaviour that was wrong, so a future change that
reintroduces it fails here rather than in production.
"""

from __future__ import annotations

import json
import pathlib
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

import pytest

from app.core.errors import ValidationError
from app.models.engine import ConnectorDefinition
from app.core.params import as_enum
from app.models.enums import RunStatus, ScheduleType, TriggerType
from app.services import scheduling


# ── an invalid filter value is bad input, not a server fault ───────────────
# Before: `RunStatus(value.upper())` raised ValueError inside the request and
# surfaced as a 500 on ?status=NOT_A_STATUS.

def test_invalid_enum_filter_raises_validation_not_valueerror() -> None:
    with pytest.raises(ValidationError) as caught:
        as_enum("NOT_A_STATUS", RunStatus, field="status")
    assert caught.value.status_code == 422
    assert caught.value.code == "INVALID_FILTER_VALUE"
    # The response must tell the caller what is accepted.
    assert "SUCCEEDED" in caught.value.details["allowed"]


def test_valid_enum_filter_is_case_insensitive() -> None:
    assert as_enum("succeeded", RunStatus, field="status") is RunStatus.SUCCEEDED
    assert as_enum("  RETRY  ", TriggerType, field="trigger") is TriggerType.RETRY


def test_empty_filter_is_not_an_error() -> None:
    assert as_enum(None, RunStatus, field="status") is None
    assert as_enum("", RunStatus, field="status") is None


# ── an unknown timezone must be rejected, not silently treated as UTC ──────
# Before: `resolve_zone` swallowed the error, so the API accepted
# "Mars/Olympus", echoed it back, and computed the schedule in UTC.

def test_unknown_timezone_is_rejected() -> None:
    with pytest.raises(ValidationError) as caught:
        scheduling.require_zone("Mars/Olympus")
    assert caught.value.code == "INVALID_TIMEZONE"


def test_validate_rejects_unknown_timezone() -> None:
    with pytest.raises(ValidationError):
        scheduling.validate({"type": "DAILY", "time_of_day": "02:00",
                             "timezone": "Mars/Olympus"})


def test_known_timezone_is_preserved_exactly() -> None:
    normalized = scheduling.validate(
        {"type": "DAILY", "time_of_day": "02:00", "timezone": "Asia/Ho_Chi_Minh"}
    )
    assert normalized["timezone"] == "Asia/Ho_Chi_Minh"


def test_reading_a_stored_bad_timezone_still_works() -> None:
    """The read path stays lenient: a value that became invalid after an OS
    tzdata update must not take the page down."""
    zone = scheduling.resolve_zone("Mars/Olympus")
    assert str(zone) == "UTC"


def test_unknown_schedule_type_is_rejected() -> None:
    with pytest.raises(ValidationError) as caught:
        scheduling.validate({"type": "HOURLY_ISH", "timezone": "UTC"})
    assert caught.value.code == "INVALID_SCHEDULE_TYPE"


def test_manual_schedule_is_valid_with_no_other_fields() -> None:
    """An empty schedule body means MANUAL; that is a legitimate request."""
    normalized = scheduling.validate({})
    assert normalized["type"] == ScheduleType.MANUAL.value
    assert scheduling.preview(ScheduleType.MANUAL, normalized, "UTC") == []


# ── name uniqueness applies to live rows only ──────────────────────────────
# Before: the unique constraint ignored deleted_at, so deleting a source
# reserved its name forever and re-creating it produced a 500.

def test_live_name_uniqueness_is_a_partial_index() -> None:
    from app.models.integration import Destination, Pipeline, Source

    for model, expected in (
        (Source, "uq_source_ws_name_live"),
        (Destination, "uq_destination_ws_name_live"),
        (Pipeline, "uq_pipeline_ws_name_live"),
    ):
        index = next(
            (i for i in model.__table__.indexes if i.name == expected), None
        )
        assert index is not None, f"{model.__name__} lost its live-name index"
        assert index.unique, f"{expected} must be unique"
        where = index.dialect_options["postgresql"].get("where")
        assert where is not None and "deleted_at IS NULL" in str(where), (
            f"{expected} must exclude soft-deleted rows"
        )
        # The old unconditional constraint must not come back.
        assert not any(
            getattr(c, "name", "") in {
                "uq_source_ws_name", "uq_destination_ws_name", "uq_pipeline_ws_name",
            }
            for c in model.__table__.constraints
        )


def test_bootstrap_ships_the_matching_ddl() -> None:
    """create_all cannot alter an existing table, so the fixups must exist."""
    from app.bootstrap import SCHEMA_FIXUPS

    joined = " ".join(SCHEMA_FIXUPS)
    for name in ("uq_source_ws_name_live", "uq_destination_ws_name_live",
                 "uq_pipeline_ws_name_live"):
        assert f'CREATE UNIQUE INDEX IF NOT EXISTS "{name}"' in joined
    for old in ("uq_source_ws_name", "uq_destination_ws_name", "uq_pipeline_ws_name"):
        assert f'DROP CONSTRAINT IF EXISTS "{old}"' in joined


# ── a successful check is reusable, and only for the same config ───────────

def test_check_token_round_trips_for_the_same_configuration() -> None:
    from app.services.actors import issue_check_token, verify_check_token

    config = {"host": "db", "port": 5432, "password": "s3cret"}
    token = issue_check_token("SOURCE", "source-postgres", config)
    assert verify_check_token(token, "SOURCE", "source-postgres", config)


def test_check_token_is_bound_to_the_exact_configuration() -> None:
    from app.services.actors import issue_check_token, verify_check_token

    token = issue_check_token("SOURCE", "source-postgres",
                              {"host": "db", "password": "s3cret"})
    # A changed credential must invalidate the proof.
    assert not verify_check_token(token, "SOURCE", "source-postgres",
                                  {"host": "db", "password": "different"})
    # So must a different connector or side.
    assert not verify_check_token(token, "DESTINATION", "source-postgres",
                                  {"host": "db", "password": "s3cret"})
    assert not verify_check_token(token, "SOURCE", "source-mysql",
                                  {"host": "db", "password": "s3cret"})


def test_check_token_rejects_garbage() -> None:
    from app.services.actors import verify_check_token

    for bad in (None, "", "nonsense", "abc.def", "0.deadbeef"):
        assert not verify_check_token(bad, "SOURCE", "source-postgres", {})


# ── a read-only role is offered no mutating action ─────────────────────────
# The UI hides buttons that `available_actions` omits, so this list is the
# real permission boundary as far as the browser is concerned.

def test_analyst_is_offered_no_mutating_pipeline_action() -> None:
    from app.core.permissions import Role
    from app.models.enums import PipelineHealth, PipelineStatus
    from app.services import pipelines as pipeline_service

    class _Ctx:
        def __init__(self, role: Role) -> None:
            self.role = role

        def can(self, module, action) -> bool:
            from app.core.permissions import MATRIX

            return action in MATRIX[self.role].get(module, set())

    class _Pipeline:
        status = PipelineStatus.ACTIVE

    actions = pipeline_service.available_actions(
        _Ctx(Role.ANALYST), _Pipeline(), PipelineHealth.HEALTHY
    )
    assert actions == [], f"read-only role was offered {actions}"

    # And an operator must still get the operational ones, or the check above
    # would pass simply because the function always returns nothing.
    operator = pipeline_service.available_actions(
        _Ctx(Role.OPERATOR), _Pipeline(), PipelineHealth.HEALTHY
    )
    assert "RUN_NOW" in operator and "PAUSE" in operator


# ── the catalogue is the whole upstream registry, and that changes the rules ──
# Before: the worker refreshed every connector by running its image, holding one
# transaction across all of them. At 4 connectors that was slow; at 650 it pulled
# hundreds of gigabytes and blocked DDL behind an hours-long transaction, which
# stalled every query on the table.

def test_blanket_refresh_is_bounded_to_connectors_in_use() -> None:
    import inspect

    from app.services import catalog

    source = inspect.getsource(catalog.refresh_specs)
    assert "usage_count > 0" in source, (
        "an unused connector's spec costs an image pull to learn nothing"
    )
    assert ".limit(" in source, "a refresh cycle must be bounded"
    # The read snapshot must be released before the engine is called.
    assert source.index("await session.commit()") < source.index("get_connector_spec"), (
        "the transaction must close before the first engine call"
    )


def test_refresh_specs_defaults_to_a_small_batch() -> None:
    import inspect

    from app.services import catalog

    limit = inspect.signature(catalog.refresh_specs).parameters["limit"]
    assert isinstance(limit.default, int) and 0 < limit.default <= 50


# ── a blocked schema fixup must not freeze the table for everyone ─────────────

def test_schema_fixups_run_with_a_lock_timeout() -> None:
    import inspect

    from app import bootstrap

    source = inspect.getsource(bootstrap.apply_schema_fixups)
    assert "lock_timeout" in source, "DDL that waits forever queues every reader behind it"
    # One transaction per statement, opened inside the loop: a fixup that blocks
    # must not discard the ones that already succeeded.
    assert "for statement in" in source
    opened_at = source.index("engine.begin()")
    assert source.index("for statement in") < opened_at


def test_lock_timeout_is_detected_by_sqlstate() -> None:
    from app.bootstrap import _is_lock_timeout

    class _Orig:
        sqlstate = "55P03"

    class _Other:
        sqlstate = "42P01"

    class _Exc:
        def __init__(self, orig) -> None:
            self.orig = orig

    assert _is_lock_timeout(_Exc(_Orig()))
    assert not _is_lock_timeout(_Exc(_Other()))
    assert not _is_lock_timeout(_Exc(None))


# ── the registry is generated, and the pins we tested must survive that ───────

def test_curated_connectors_keep_their_tested_pins() -> None:
    from app.adapters.registry import bundled_by_key

    for key, version in (
        ("source-postgres", "3.8.5"),
        ("source-faker", "7.2.1"),
        # Destinations are held below upstream on purpose: anything declaring
        # `supportsRefreshes` needs the `generationId` that platform 0.59.1 --
        # the last Airbyte that runs a sync in Compose -- does not send.
        # Raising these means raising the platform, which means Kubernetes.
        ("destination-postgres", "2.0.10"),
        ("source-bigquery", "0.4.5"),
        ("destination-bigquery", "2.4.19"),
        ("source-google-sheets", "0.12.35"),
        ("destination-google-sheets", "0.3.5"),
    ):
        metadata = bundled_by_key(key)
        assert metadata is not None, f"{key} vanished from the generated registry"
        assert metadata.version == version, (
            f"{key} drifted to {metadata.version}; the contract suite tested {version}"
        )


def test_the_catalogue_is_exactly_the_launch_scope() -> None:
    """The product ships what it can support, and nothing else.

    This used to assert the opposite -- more than 300 sources -- because for a
    while the gap was a four-connector catalogue and breadth was the fix. The
    breadth turned out to be the problem: 654 connectors meant a 2.1 MB
    resource file, 654 rows in every deployment's database, and 649 locked
    cards in the create wizard saying "not certified for this release".

    Three systems, both directions where the connector exists, plus the sample
    source the demo and the end-to-end suite run against. Widening this is a
    deliberate edit here and in `CURATED`, not something that drifts.
    """
    from app.adapters.registry import bundled_connectors

    connectors = bundled_connectors()
    assert {c.connector_key for c in connectors} == {
        # Warehouses and files, from Airbyte.
        "source-postgres", "destination-postgres",
        "source-bigquery", "destination-bigquery",
        "source-google-sheets", "destination-google-sheets",
        "source-mssql", "destination-mssql",
        "source-faker",
        # Base.vn, written here. Not pulled from anywhere: these are Python in
        # `app/connectors/base_vn`, compiled to a declarative manifest at
        # import, and seeded through the same path as the rest.
        "source-base-account", "source-base-hrm", "source-base-hiring",
        "source-base-workflow", "source-base-request", "source-base-service",
        "source-base-wework", "source-base-timeoff", "source-base-payroll",
        "source-base-income", "source-base-crm", "source-base-crm-leads",
    }, sorted(c.connector_key for c in connectors)

    # Every offered connector must be renderable, or the wizard dead-ends.
    assert all(c.spec_schema for c in connectors)

    # And the resource file stays small enough to ship and to read.
    registry = (ROOT / "backend" / "app" / "resources"
                / "connector_registry.json").stat().st_size
    assert registry < 400_000, f"the registry is back to {registry / 1024:.0f} KB"


def test_only_verified_connectors_claim_certification() -> None:
    from app.adapters.registry import bundled_certifications

    supported = {k for k, v in bundled_certifications().items() if v == "SUPPORTED"}
    # Deliberately a literal list rather than a re-read of compatibility.yaml.
    # Widening what the product claims to support should require editing a test
    # that says "this product tested it", not just a line in a config file.
    #
    # Each entry below was run by scripts/certify-connector.py against a real
    # system, and then again through the product's own /test endpoint:
    #
    #   source-postgres        full e2e, incremental cursor, dedup
    #   destination-postgres   full e2e, overwrite/append/dedup
    #   source-faker           full e2e
    #   source-bigquery        check, discover 164 streams, read 50 records
    #   destination-bigquery   check, 50 records written and read back
    #   source-google-sheets   check, discover, read 30 records
    #   source-mssql           check, discover, read 3 records from SQL Server 2022
    #
    # source-file and source-microsoft-onedrive are deliberately absent.
    # source-file has no e2e driver; OneDrive has only ever run `spec`, because
    # no Microsoft tenant was available to check against.
    #
    # `destination-mssql` is absent from this set on purpose. Its sync
    # completes and the rows arrive, but only as JSON in
    # `airbyte_internal.<schema>_raw__stream_<name>` -- no typed table, on any
    # of 1.0.0 / 2.0.0 / 2.2.20. Calling it SUPPORTED would promise a SQL
    # Server table nobody gets.
    #
    # The Base.vn connectors are SUPPORTED on different evidence from the
    # Airbyte ones, and it is worth being precise about which. Nobody upstream
    # tests them, so "supported" here means: this product wrote them, 160
    # structural tests cover them, and all ten now run end to end into BigQuery
    # -- 9,049 records across 69 tables, `qa/e2e/base-to-bigquery.py`.
    #
    #   source-base-crm-leads  built from the published request contract, then
    #     corrected against a live token by `qa/probe/base_crm_leads.py` (the
    #     collection is `services`, not `lead_services`, which the convention
    #     would have got wrong), and synced twice into BigQuery: 1,833 records
    #     over 3 streams and a two-level parent chain, then 707 on the second
    #     run as the cursors reduced. It shipped BETA until those runs existed.
    assert supported == {
        "source-postgres", "source-faker", "destination-postgres",
        "source-bigquery", "destination-bigquery", "source-google-sheets",
        "source-mssql",
        "source-base-account", "source-base-hrm", "source-base-hiring",
        "source-base-workflow", "source-base-request", "source-base-service",
        "source-base-wework", "source-base-timeoff", "source-base-payroll",
        "source-base-income", "source-base-crm", "source-base-crm-leads",
    }, f"certification must mean this product tested it, got {sorted(supported)}"


# ── a spec read from the engine must not capture the version pin ─────────────
# Found while a sync kept dying on `getGenerationId(...) must not be null` even
# though `airbyte-connector-pin` reported success minutes earlier.
#
# `connector_definitions` carries two version columns on purpose: `version` is
# what the product pins and pushes into the engine, `engine_version` is what
# the engine was last observed offering. `seed_catalog` wrote the pin only
# inside its `spec_source == "BUNDLED"` branch, so the moment a row's spec was
# refreshed from the engine -- which flips `spec_source` to "ENGINE" -- the
# bundled pin stopped being applied to that row for good. The engine
# bootloader re-seeds definitions from Airbyte's *current* catalogue on every
# start, so the drift it introduced was copied into the product database once
# and then became the product's own answer. `_ensure_definition_version` then
# pushed that answer back into the engine on every resource creation, undoing
# the pin service a few minutes after it ran, and the sync failed with a
# storage-shaped error that had nothing to do with storage.

async def _seed_into(name: str):
    """A scratch database with the schema created and `app.core.db` pointed at it."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.core.db as db
    import app.models  # noqa: F401  -- register the tables
    from app.core.db import Base
    from scratchdb import fresh_database

    engine = create_async_engine(await fresh_database(name))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    saved = (db._engine, db._session_factory)
    db._engine, db._session_factory = engine, maker
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def release() -> None:
        db._engine, db._session_factory = saved
        await engine.dispose()

    return maker, release


@pytest.mark.asyncio
async def test_an_engine_sourced_spec_does_not_freeze_the_version_pin() -> None:
    from sqlalchemy import select

    from app.adapters.registry import bundled_by_key
    from app.models.engine import ConnectorDefinition
    from app.services.catalog import seed_catalog

    bundled = bundled_by_key("destination-postgres")
    assert bundled is not None and bundled.version == "2.0.10"

    maker, release = await _seed_into("catalog_pin_regression")
    try:
        async with maker() as session:
            await seed_catalog(session)
            await session.commit()

        # The engine is asked for a spec, and answers as a 2026 Airbyte
        # bootloader does: with a connector far newer than this platform can
        # drive. `refresh_from_engine` records that as an observation.
        async with maker() as session:
            row = await session.scalar(
                select(ConnectorDefinition).where(
                    ConnectorDefinition.connector_key == "destination-postgres"))
            assert row.version == "2.0.10", "the first seed did not apply the pin"
            # This is the state a real deployment reached: the row was written
            # while the engine was offering 3.0.17, so both columns hold it,
            # and the spec has since been refreshed from the engine.
            row.spec_source = "ENGINE"
            row.version = "3.0.17"
            row.engine_version = "3.0.17"
            await session.commit()

        # A redeploy. The bundled pin must win again.
        async with maker() as session:
            await seed_catalog(session)
            await session.commit()

        async with maker() as session:
            row = await session.scalar(
                select(ConnectorDefinition).where(
                    ConnectorDefinition.connector_key == "destination-postgres"))
        assert row.version == "2.0.10", (
            f"the pin froze at {row.version}: a spec read from the engine "
            "captured the version column, so the product would push the "
            "engine's own drift back into the engine")
        # And the observation is left alone -- operators need to see the gap.
        assert row.engine_version == "3.0.17"
    finally:
        await release()


# ── the Status view needs per-stream truth, not a pipeline total ─────────────

def test_a_stream_view_can_report_its_own_last_sync() -> None:
    """A pipeline that reports SUCCEEDED can still have a stream that read zero.

    The connection page lists streams, and for each one: did it sync, how much
    landed, how stale is it. A pipeline-level record count cannot answer any of
    those per stream -- and `pipeline_stream_stats` has been recording exactly
    that on every run since the beginning, read by nothing.
    """
    from app.schemas.domain import PipelineStreamView, StreamSyncState

    view = PipelineStreamView(
        id=__import__("uuid").uuid4(), name="transaction", selected=True,
        sync_mode="incremental", destination_sync_mode="append",
        last_sync=StreamSyncState(status="COMPLETED", records_loaded=33,
                                  bytes_loaded=51_312),
    )
    assert view.last_sync.records_loaded == 33
    # A stream that has never been in a completed run says so by absence, which
    # renders as "never synced" rather than as zero records.
    assert PipelineStreamView(
        id=__import__("uuid").uuid4(), name="new_stream", selected=True,
        sync_mode="full_refresh", destination_sync_mode="overwrite",
    ).last_sync is None


def test_replication_state_asks_whoever_owns_the_cursor() -> None:
    """The owner is not the same in both engine modes, and both must work.

    In `AIRBYTE_API` Airbyte keeps the cursor and never returns it on job
    completion, so `pipelines.sync_state` stays null no matter how many syncs
    run — reading only the column would show an empty panel forever on the
    topology this product actually ships. In `AIRBYTE_EMBEDDED` the
    destination commits state back and `runs.py` writes that column, while the
    adapter has no connection-scoped store to ask — so asking only the engine
    would show nothing there.

    Hence both, in that order. This test exists because the first version of
    this function asserted in its own docstring that the column was never
    written, which was wrong: `runs.py` writes it.
    """
    import inspect

    from app.services import pipelines as service

    source = inspect.getsource(service.replication_state)
    assert "connection_ref" in source, "the engine is not asked"
    assert "pipeline.sync_state" in source, "the stored cursor is not the fallback"
    # The failure path returns a reason rather than propagating an exception:
    # this backs one collapsed panel, not the page.
    assert "except Exception" in source
    assert 'return True, [], "Engine' in source

    # And `runs.py` really is the writer, so the fallback is not dead code.
    from app.services import runs as run_service

    assert "pipeline.sync_state = state" in inspect.getsource(run_service)


def test_the_engine_protocol_distinguishes_no_state_from_empty_state() -> None:
    """None means "this engine has no such concept"; [] means "none yet".

    They render differently -- hide the panel versus say the pipeline has not
    checkpointed -- so collapsing them into one value loses the distinction
    that decides what the operator is told. `sql_direct` is the None case.
    """
    import inspect

    from app.adapters.base import IntegrationEngineAdapter

    default = inspect.getsource(IntegrationEngineAdapter.connection_state)
    assert "return None" in default


def test_turning_a_stream_off_keeps_it_instead_of_deleting_it() -> None:
    """The schema screen lists streams and lets you switch them on and off.

    That was not possible: `_update` deleted every stream not in the selected
    set, so the only rows the screen could ever show were the enabled ones.
    "Hide disabled streams" could never hide anything, switching a stream off
    made it disappear from the list, and switching it back on meant re-running
    source discovery to get the row back.

    The distinction that has to survive is between a stream the caller turned
    off — keep it, deselected — and one that is no longer in the source
    catalogue at all, which is a real deletion.
    """
    import inspect

    from app.services import pipelines as service

    source = inspect.getsource(service.update)
    assert "offered = {" in source, "the payload's full stream list is not consulted"
    assert "stream.selected = False" in source, "a deselected stream is not kept"
    # The delete must still exist, and must be the branch for streams that are
    # absent from the payload entirely.
    body = source[source.index("for key, stream in existing.items():"):]
    assert "session.delete(stream)" in body
    assert body.index("if key in offered:") < body.index("session.delete(stream)"), (
        "the delete is reached before the deselect check, so switching a "
        "stream off still removes it")


def test_editing_the_cursor_is_refused_while_a_run_is_active() -> None:
    """The running sync commits its own cursor when it finishes.

    So an edit made mid-run is overwritten minutes later, with no error
    anywhere. The operator concludes the feature is broken -- or, worse, edits
    again and now two people's idea of the mark are in play. Refusing up front
    is the only honest answer.
    """
    import inspect

    from app.services import pipelines as service

    source = inspect.getsource(service.set_replication_state)
    guard = source.index("active_run")
    write = source.index("set_connection_state")
    assert guard < write, "the engine is written before the active-run check"
    assert "ValidationError" in source[guard:write], "an active run does not stop the write"


def test_editing_the_cursor_is_audited_with_the_old_value() -> None:
    """"What was it before" is the first question asked after a bad edit.

    A sync that duplicates or skips rows days later is traced back through this
    record, and an audit entry saying only "someone changed the state" cannot
    answer it. Both sides are stored.
    """
    import inspect

    from app.services import pipelines as service

    source = inspect.getsource(service.set_replication_state)
    assert 'before={"state": before}' in source
    assert '"written_to"' in source, (
        "the trail does not say whether the engine or the product took it, so "
        "there is no way to know where to look")


def test_a_cursor_of_the_wrong_shape_is_refused_by_the_schema() -> None:
    """The panel is a free-text JSON editor, so anything can arrive.

    A list of strings parses, passes any `is it JSON` check, and means nothing
    to any engine -- it would be accepted here and fail hours later inside a
    job log, which is the worst place to learn about it.
    """
    import pytest as _pytest

    from app.schemas.domain import ConnectionStateUpdate

    ok = ConnectionStateUpdate(state=[{"streamDescriptor": {"name": "a"}}])
    assert len(ok.state) == 1
    # Empty is a real instruction -- "forget the cursor" -- not an error.
    assert ConnectionStateUpdate(state=[]).state == []

    with _pytest.raises(Exception):
        ConnectionStateUpdate(state=["not-an-object"])
    with _pytest.raises(Exception):
        ConnectionStateUpdate(state=[["nested-list"]])


# A declarative connector carries its logic inside each source's configuration,
# injected once when that source is created; the engine keeps its own copy from
# then on. `seed_catalog` overwrites the catalogue row on every deploy and the
# module docstring promises that this is how "fix the Base API logic once and
# every workspace has it" works -- but nothing carried the new manifest to a
# source that already existed. `deal_activity` was fixed, re-seeded, redeployed
# and re-synced three times, still emitting the same unfiltered 3,970 rows,
# because the pipeline pointed at a source built an hour earlier. Editing the
# source by hand was the only thing that moved it.

@pytest.mark.asyncio
async def test_a_rebuilt_manifest_reaches_the_sources_already_built_on_it() -> None:
    import uuid as _uuid

    from sqlalchemy import select

    from app.models.engine import EngineMapping
    from app.models.enums import EngineResourceType, ProductResourceType
    from app.models.integration import Source
    from app.models.identity import Workspace
    from app.services import actors as actor_service
    from app.services.catalog import seed_catalog

    KEY = "source-base-crm"
    pushed: list[dict] = []

    class _Ref:
        engine_type = "AIRBYTE_EMBEDDED"
        ref = "engine-source-1"

    class _Adapter:
        async def update_source(self, ref, request):
            # The manifest is injected by the adapter itself, from the
            # descriptor -- `_config_for` is what turns it into
            # `__injected_declarative_manifest` on the wire.
            pushed.append({"ref": ref,
                           "manifest": request.connector.declarative_manifest})
            return _Ref()

        async def update_destination(self, ref, request):  # pragma: no cover
            raise AssertionError("no destination in this fixture")

    maker, release = await _seed_into("catalog_manifest_propagation")
    try:
        async with maker() as session:
            await seed_catalog(session)
            workspace_id = _uuid.uuid4()
            session.add(Workspace(id=workspace_id, name="QA", slug="qa",
                                  timezone="Asia/Ho_Chi_Minh"))
            await session.flush()
            source = Source(
                id=_uuid.uuid4(), workspace_id=workspace_id, name="CRM",
                connector_key=KEY, configuration_json={"domain": "basecrm.vn"},
            )
            session.add(source)
            await session.flush()
            session.add(EngineMapping(
                workspace_id=workspace_id,
                product_resource_type=ProductResourceType.SOURCE,
                product_resource_id=source.id,
                engine_type="AIRBYTE_EMBEDDED",
                engine_resource_type=EngineResourceType.SOURCE,
                engine_resource_ref="engine-source-1",
            ))
            await session.commit()
            source_id = source.id

        # A deploy that changes nothing must not disturb a running resource.
        async with maker() as session:
            outcome = await seed_catalog(session)
            await session.commit()
        assert KEY not in outcome.manifests_changed
        assert not pushed, "an unchanged manifest was pushed to the engine anyway"

        # Now the manifest really changes, as a connector fix does.
        async with maker() as session:
            row = await session.scalar(
                select(ConnectorDefinition).where(
                    ConnectorDefinition.connector_key == KEY))
            stale = json.loads(json.dumps(row.declarative_manifest))
            stale["definitions"]["streams"]["deal_activity"]["incremental_sync"].pop(
                "is_client_side_incremental", None)
            row.declarative_manifest = stale
            await session.commit()

        async with maker() as session:
            outcome = await seed_catalog(session)
            assert KEY in outcome.manifests_changed, (
                "seeding overwrote the manifest without noticing it had changed")
            with mock.patch("app.services.actors.get_adapter", return_value=_Adapter()):
                count = await actor_service.republish_manifests(
                    session, outcome.manifests_changed)
            await session.commit()

        assert count == 1 and len(pushed) == 1, (
            "the fixed connector never reached the source already built on it")
        manifest = pushed[0]["manifest"]
        cursor = manifest["definitions"]["streams"]["deal_activity"]["incremental_sync"]
        assert cursor.get("is_client_side_incremental") is True
        assert pushed[0]["ref"] == "engine-source-1", "pushed to the wrong resource"

        async with maker() as session:
            mapping = await session.scalar(
                select(EngineMapping).where(
                    EngineMapping.product_resource_id == source_id))
        assert mapping.engine_resource_ref == "engine-source-1"
    finally:
        await release()


# `alembic.ini` describes logging for the standalone CLI -- root at WARNING with
# a plain stderr handler -- and `migrations/env.py` applied it unconditionally.
# Inside `python -m app.bootstrap` that runs *between* migrating and seeding, so
# the deploy container migrated, seeded the catalogue, republished manifests and
# exited 0 having printed nothing after its Alembic lines. Root had been reset
# and the JSON handler dropped. A bootstrap that works silently cannot be told
# apart from one that skipped, and any warning raised in that window is lost --
# including `catalog.manifest_republish_failed`, which is precisely the warning
# an operator needs to see.

def test_running_migrations_does_not_silence_the_application_log() -> None:
    import io
    import logging as _logging
    from logging.config import fileConfig

    from app.core.logging import JsonFormatter, log_event

    ini = str(ROOT / "backend" / "alembic.ini")
    env = (ROOT / "backend" / "migrations" / "env.py").read_text(encoding="utf-8")

    def _emits_after(name, apply_ini) -> str:
        """Install the app's handler, let `apply_ini` run, then log at INFO."""
        stream = io.StringIO()
        handler = _logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        root = _logging.getLogger()
        saved = (root.handlers, root.level, root.manager.disable)
        root.handlers, root.level = [handler], _logging.INFO
        try:
            apply_ini()
            log_event(_logging.getLogger(name), _logging.INFO, "seed.done")
            for installed in _logging.getLogger().handlers:
                installed.flush()
            return stream.getvalue()
        finally:
            root.handlers, root.level, root.manager.disable = saved
            _logging.getLogger(name).disabled = False

    # The hazard is real: applying the ini the way env.py used to swallows the
    # line the deploy container exists to print.
    assert "seed.done" not in _emits_after("qa.unguarded", lambda: fileConfig(ini)), (
        "alembic.ini no longer replaces the host logging, so this guard is "
        "protecting nothing -- check what changed before deleting it")

    # env.py must therefore not apply it when a host already configured logging.
    assert "not logging.getLogger().handlers" in env, (
        "migrations/env.py applies alembic.ini's logging unconditionally, which "
        "replaces the host process's handlers")
    guarded = lambda: (fileConfig(ini)                      # noqa: E731
                       if not _logging.getLogger().handlers else None)
    assert "seed.done" in _emits_after("qa.guarded", guarded)
