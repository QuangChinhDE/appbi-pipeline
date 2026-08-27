"""The Base.vn connectors, as definitions and as compiled manifests.

These are the product's own connectors, so nothing upstream will catch a
mistake in them. The checks that matter fall into three groups:

* **the promises** — a token field spelled one way, a host that is not a
  config field, a primary key on everything, and manifests that compile
* **the fixes** — each defect found in the old YAML, asserted so it cannot
  come back
* **the shape** — pagination, cursors and parent wiring reaching the manifest
  in the form the Airbyte runner expects

Live calls against Base need a token and live in `qa/e2e/base-connectors.py`.
"""

from __future__ import annotations

import json
import pathlib
import sys
from unittest.mock import AsyncMock

import pytest

from app.connectors.base_vn import (
    BY_KEY, CONNECTORS, TOKEN_FIELD, compile_manifest, stream_inventory,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

ALL_STREAMS = [(c, s) for c in CONNECTORS for s in c.streams]
IDS = [f"{c.app}.{s.name}" for c, s in ALL_STREAMS]


# ── the promises ─────────────────────────────────────────────────────────────

def test_every_required_connector_ships() -> None:
    """The applications asked for, by key. Widening this is a deliberate edit."""
    assert set(BY_KEY) == {
        # The ten originally asked for.
        "source-base-account", "source-base-hrm", "source-base-hiring",
        "source-base-workflow", "source-base-request", "source-base-service",
        "source-base-wework", "source-base-timeoff", "source-base-payroll",
        "source-base-income",
        # Added later, and on a different API generation -- see `crm.py`.
        "source-base-crm",
        # The other half of Base CRM, and a separate application: its own
        # token, its own password, its own path root -- see `crm_leads.py`.
        "source-base-crm-leads",
    }, sorted(BY_KEY)


#: Applications that do not use `access_token_v2`, and the field they use.
#:
#: An allowlist rather than a free-for-all. The reason the spelling matters is
#: that Base distinguishes the two names in its own error taxonomy --
#: `access_token_invalid_2` for the old field, `access_token_v2_invalid_*` for
#: the current one -- and the very first Base manifests all sent
#: `access_token`, so none of them could authenticate. A connector that picks
#: the wrong name fails the same way, so a new entry here has to be a measured
#: fact about that API, not a guess.
TOKEN_FIELD_EXCEPTIONS = {
    # Measured: `apis.base.vn/sales/v1` refuses `access_token_v2` and accepts
    # `access_token`. It is a different API generation from the rest.
    "crm": "access_token",
    # Same generation, same spelling. Measured separately because it is a
    # separate application: `apis.basecrm.vn/leads/` refuses the Sales token
    # outright with `INVALID TOKEN APPKEY`, and answers `access_token_v2` with
    # `access_token_invalid_1` -- two different refusals, so it reads both
    # fields and accepts neither from the wrong app.
    "crm-leads": "access_token",
}


def test_each_connector_sends_the_credential_its_api_actually_reads() -> None:
    """One spelling per application, and the same one in the spec and the body.

    The failure this prevents is subtle: a spec that asks for one field name
    and a request that sends another authenticates against nothing, and Base
    answers with a refusal that reads like an expired token.
    """
    for connector in CONNECTORS:
        expected = TOKEN_FIELD_EXCEPTIONS.get(connector.app, TOKEN_FIELD)
        assert connector.token_field == expected, (
            f"{connector.app} sends {connector.token_field!r}; if that is "
            f"deliberate, record it in TOKEN_FIELD_EXCEPTIONS with what was "
            f"measured")

        manifest = compile_manifest(connector)
        spec = manifest["spec"]["connection_specification"]
        assert expected in spec["required"], connector.app
        assert spec["properties"][expected]["airbyte_secret"] is True

        for stream in manifest["definitions"]["streams"].values():
            body = stream["retriever"]["requester"]["request_body_data"]
            assert body[expected] == "{{ config['" + expected + "'] }}", (
                f"{connector.app}.{stream['name']} sends a different field "
                f"from the one its spec asks for")


def test_a_second_credential_is_declared_secret_and_actually_sent() -> None:
    """Base CRM needs a password on every call as well as a token.

    Two ways to get this wrong, both silent. Collect it and never send it, and
    every request is refused while the form looks complete. Send it without
    `airbyte_secret`, and an account password is stored and logged in clear.
    """
    for connector in CONNECTORS:
        manifest = compile_manifest(connector)
        spec = manifest["spec"]["connection_specification"]
        for extra in connector.config:
            if not extra.send_in_body:
                continue
            assert extra.secret, (
                f"{connector.app}.{extra.name} rides on every request but is "
                "not marked secret")
            assert spec["properties"][extra.name]["airbyte_secret"] is True
            for stream in manifest["definitions"]["streams"].values():
                body = stream["retriever"]["requester"]["request_body_data"]
                assert body.get(extra.name) == "{{ config['" + extra.name + "'] }}", (
                    f"{connector.app}.{stream['name']} does not send "
                    f"{extra.name}, so the request is unauthenticated")


def test_no_token_is_baked_into_a_connector() -> None:
    """Credentials are per workspace; the definition is shared.

    The whole multi-workspace story rests on this: one connector row, one
    manifest, and each workspace's token supplied at sync time.
    """
    blob = json.dumps([compile_manifest(c) for c in CONNECTORS])
    assert "2329~" not in blob, "a real Base token is embedded in a manifest"


@pytest.mark.parametrize("connector,stream", ALL_STREAMS, ids=IDS)
def test_every_stream_can_be_deduplicated(connector, stream) -> None:
    """A stream with no primary key cannot be re-synced without duplicating.

    Eight streams had none: both Account streams, the Timeoff group, WeWork
    projects and milestones, and — worst — Service tickets, the largest table
    in that application.
    """
    assert stream.primary_key, f"{connector.app}.{stream.name}"


def test_every_manifest_compiles_and_declares_its_streams() -> None:
    for connector in CONNECTORS:
        manifest = compile_manifest(connector)
        assert manifest["type"] == "DeclarativeSource"
        assert len(manifest["streams"]) == len(connector.streams)
        checked = manifest["check"]["stream_names"][0]
        assert not connector.stream(checked).parent, (
            f"{connector.app}: `check` uses {checked}, a substream — it would "
            "crawl a parent stream just to verify a token")


def test_runtime_schemas_keep_the_reviewed_field_contracts() -> None:
    """Discover must expose real fields, not only an id and a display name."""
    account = compile_manifest(BY_KEY["source-base-account"])
    user_schema = account["definitions"]["streams"]["user"]["schema_loader"]["schema"]
    assert len(user_schema["properties"]) > 30
    assert {"id", "email", "username", "first_name", "last_name", "manager"} <= set(
        user_schema["properties"]
    )
    assert user_schema["additionalProperties"] is True

    # The generated resource must stay reproducible from the reviewed YAML.
    import subprocess

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_base_schemas.py"), "--check"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# ── the fixes ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("connector", CONNECTORS, ids=[c.app for c in CONNECTORS])
def test_a_rejected_token_fails_the_sync(connector) -> None:
    """Base answers `HTTP 200` with `{"code": 0}` when it refuses a request.

    Nothing in the old manifests looked at `code`, so a rejected token produced
    an empty collection, a sync that completed, and a full-refresh destination
    that replaced the customer's table with nothing — reported as success.

    This is the single most important behaviour in the package, so it is
    asserted for every stream of every connector rather than spot-checked.
    """
    for stream in compile_manifest(connector)["definitions"]["streams"].values():
        handler = stream["retriever"]["requester"]["error_handler"]
        failing = [f for f in handler["response_filters"] if f["action"] == "FAIL"]
        assert failing, stream["name"]
        assert "response.get('code') == 0" in failing[0]["predicate"]


def test_wework_no_longer_reads_one_hardcoded_project() -> None:
    """The old manifest had `"id": "131471"` in the request body.

    Every WeWork sync, for every customer, returned that one project and the
    tasks inside it. `project/list` exists and is now the parent.
    """
    wework = BY_KEY["source-base-wework"]
    assert wework.stream("project").path == "project/list"

    blob = json.dumps(compile_manifest(wework))
    assert "131471" not in blob

    for name in ("task", "topic", "tasklist", "milestone"):
        assert wework.stream(name).parent.stream == "project", name


def test_the_duplicated_and_placeholder_streams_are_gone() -> None:
    """Two applications shipped streams that did nothing but cost requests.

    Service had `ticket` and `test` — identical path, extractor, parent and
    cursor — so every sync crawled every ticket twice. Payroll had a `test`
    stream doing `GET /test` with an empty extractor.
    """
    for key in ("source-base-service", "source-base-payroll"):
        names = {s.name for s in BY_KEY[key].streams}
        assert "test" not in names, key

    # And no two streams read the same endpoint into the same collection.
    for connector in CONNECTORS:
        seen: dict[tuple, str] = {}
        for stream in connector.streams:
            signature = (stream.path, stream.collection)
            assert signature not in seen, (
                f"{connector.app}: {stream.name} and {seen[signature]} both read "
                f"{stream.path} -> {'.'.join(stream.collection)}")
            seen[signature] = stream.name


def test_income_no_longer_reads_the_same_entity_from_the_wrong_endpoint() -> None:
    """`income_inflow` and `inflow_income` were duplicates by another route.

    Base returns inflows nested inside income documents and vice versa, so the
    old manifest had four streams for two entities — two of them reading from
    the endpoint that does not own the entity, each costing a full paginated
    crawl of the customer's revenue history.
    """
    names = {s.name for s in BY_KEY["source-base-income"].streams}
    assert "income_inflow" not in names
    assert "inflow_income" not in names
    # The entities themselves are still there, from the endpoints that own them.
    assert {"income", "inflow"} <= names


def test_timeoff_filters_on_the_field_it_tracks() -> None:
    """It tracked `last_update` and filtered on `start_date_from`.

    A leave request booked last year and approved today changes its
    `last_update` but not its start date, so incremental syncs skipped it.
    """
    timeoff = BY_KEY["source-base-timeoff"].stream("timeoff")
    assert timeoff.incremental is not None
    assert timeoff.incremental.field == "last_update"
    assert timeoff.incremental.param == "updated_from"


def test_the_large_list_endpoints_paginate() -> None:
    """Unpaginated list endpoints truncate silently.

    HRM paginated 8 of 25 streams; `employee/list` was not one of them, so any
    account past Base's default page size lost people with no error at all.
    """
    must_paginate = {
        ("hrm", "employee"), ("hrm", "contract"), ("hrm", "insurance"),
        ("hrm", "timesheet"), ("hrm", "position"), ("hrm", "tax"),
        ("income", "income_payment"), ("income", "income_customer"),
        ("service", "ticket"), ("wework", "task"), ("hiring", "candidate"),
    }
    for app, name in must_paginate:
        stream = BY_KEY[f"source-base-{app}"].stream(name)
        assert stream.paginate, f"{app}.{name} would truncate"


def test_each_app_uses_its_real_page_parameter() -> None:
    """A wrong page field is invisible until an endpoint exceeds one page.

    WeWork task exposed this by returning page zero for ten minutes and more
    than 120 MB. Workflow uses `page_id`; most other endpoints use `page`, with
    one documented WeWork exception.
    """
    for connector in CONNECTORS:
        for stream in connector.streams:
            if not stream.paginate:
                continue
            expected = "page_id" if connector.app == "workflow" else "page"
            assert stream.page_field == expected, f"{connector.app}.{stream.name}"
            # Where page numbering starts is part of the same contract, and
            # getting it wrong reads page one twice rather than erroring.
            first = 1 if connector.app.startswith("crm") else 0
            assert stream.first_page == first, f"{connector.app}.{stream.name}"


def test_each_stream_uses_the_documented_page_size_parameter() -> None:
    """A correct page number is insufficient when the size field is ignored."""
    expected = {
        ("wework", "project"): "items_per_page",
        ("wework", "topic"): "per_page",
        ("wework", "task"): "limit",
        ("hiring", "opening"): "num_per_page",
        ("hiring", "candidate"): "num_per_page",
        ("hiring", "stage"): "num_per_page",
        ("hiring", "contact"): "num_per_page",
        ("hiring", "interview"): "num_per_page",
    }
    for (app, name), field in expected.items():
        stream = BY_KEY[f"source-base-{app}"].stream(name)
        assert stream.page_size_field == field
        compiled = compile_manifest(BY_KEY[f"source-base-{app}"])["definitions"]["streams"][name]
        assert compiled["retriever"]["paginator"]["page_size_option"]["field_name"] == field


# ── the shape the runner expects ─────────────────────────────────────────────

@pytest.mark.parametrize("connector,stream", ALL_STREAMS, ids=IDS)
def test_stream_wiring_reaches_the_manifest(connector, stream) -> None:
    compiled = compile_manifest(connector)["definitions"]["streams"][stream.name]
    retriever = compiled["retriever"]

    assert retriever["requester"]["http_method"] == "POST"
    assert retriever["record_selector"]["extractor"]["field_path"] == list(stream.collection)
    assert compiled["primary_key"] == list(stream.primary_key)

    if stream.paginate:
        paginator = retriever["paginator"]
        assert paginator["type"] == "DefaultPaginator"
        assert paginator["page_token_option"]["field_name"] == stream.page_field
        if stream.page_size_field:
            assert paginator["page_size_option"]["inject_into"] == "body_data"
            assert paginator["pagination_strategy"]["page_size"] == stream.page_size
        else:
            # An endpoint that pages but takes no size. Two things must follow,
            # and the second is the one that bites: no invented `limit` on the
            # request, and no declared `page_size` either -- that number is what
            # the CDK compares a short page against to decide it has finished,
            # so asserting a size the server never agreed to either stops on the
            # server's first default page or pages past the end forever.
            assert "page_size_option" not in paginator, stream.name
            assert "page_size" not in paginator["pagination_strategy"], stream.name
    else:
        assert retriever["paginator"]["type"] == "NoPagination"

    if stream.incremental:
        cursor = compiled["incremental_sync"]
        assert cursor["cursor_field"] == stream.incremental.field
        if stream.incremental.send_request_options:
            assert cursor["start_time_option"]["field_name"] == stream.incremental.param
        else:
            # A cursor that filters on the way out and sends nothing. Correct
            # only where the endpoint documents no time filter at all --
            # `deal/get.activities` takes an id and a viewer, full stop. The
            # assertion is that we send *neither* bound, because sending an
            # invented parameter name is what this shape exists to avoid.
            assert "start_time_option" not in cursor, stream.name
            assert "end_time_option" not in cursor, stream.name
            # ...and then it has to actually filter. Declaring a cursor is not
            # enough: the CDK only compares records against the high-water mark
            # when `is_client_side_incremental` is set, so without it the stream
            # advertises `incremental` in the UI, saves state every sync, and
            # re-emits every record forever. `deal_activity` did exactly that --
            # 5,583 partitions of state and the identical 3,970 rows twice.
            assert cursor.get("is_client_side_incremental"), (
                f"{stream.name}: cursor sends nothing and filters nothing")
        if stream.incremental.client_side:
            assert not stream.incremental.send_request_options, (
                f"{stream.name}: the server already filters; a second, "
                f"client-side pass can only disagree with it and drop rows")
        # One slice: Base takes a "changed since" value, not a range, so
        # stepping the window would re-read everything once per step.
        assert cursor["step"] == "P1000Y"
    else:
        assert "incremental_sync" not in compiled

    if stream.parent:
        router = retriever["partition_router"]
        assert router["type"] == "SubstreamPartitionRouter"
        parent = router["parent_stream_configs"][0]
        assert parent["stream"]["$ref"].endswith(f"/{stream.parent.stream}")
        body = retriever["requester"]["request_body_data"]
        assert body[stream.parent.inject] == "{{ stream_partition.parent_id }}"
    else:
        assert "partition_router" not in retriever


def test_the_inventory_matches_the_definitions() -> None:
    """The handover table is generated, so it cannot drift from the code."""
    rows = stream_inventory()
    assert len(rows) == sum(len(c.streams) for c in CONNECTORS)
    for row in rows:
        stream = BY_KEY[row["connector"]].stream(row["stream"])
        assert row["endpoint"].endswith(stream.path)
        assert row["primary_key"] == list(stream.primary_key)


def test_nothing_regressed_against_the_old_yaml_without_a_reason() -> None:
    """Every stream the old manifests had still exists, or is explained.

    The point of the rewrite was to fix defects, not to quietly drop data. Any
    stream that went away has to be on this list with a reason, so a future
    reader can tell a decision from an accident.
    """
    removed = {
        # duplicate of `ticket`: identical path, extractor, parent and cursor
        ("service", "test"),
        # `GET /test` with an empty extractor; produced no records
        ("payroll", "test"),
        # `inflows` read from the incomes endpoint; `inflow` reads the endpoint
        # that owns them
        ("income", "income_inflow"),
        # `incomes` read from the inflows endpoint; see `income`
        ("income", "inflow_income"),
    }
    renamed = {("wework", "project")}       # get.full+hardcoded id -> project/list

    import pathlib

    import yaml

    root = pathlib.Path(__file__).resolve().parent.parent.parent
    yaml_dir = root / "docs" / "base-api"
    if not yaml_dir.exists():                      # pragma: no cover
        pytest.skip("the original manifests are not in this checkout")

    for path in sorted(yaml_dir.glob("base_*.yaml")):
        app = path.stem.replace("base_", "")
        key = f"source-base-{app}"
        if key not in BY_KEY:                      # expense: not in launch scope
            continue
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        old = set((document.get("definitions") or {}).get("streams") or {})
        new = {s.name for s in BY_KEY[key].streams}
        missing = {n for n in old - new
                   if (app, n) not in removed and (app, n) not in renamed}
        assert not missing, f"{app}: dropped {sorted(missing)} with no reason recorded"


# ── the domain, and why it is a field again ──────────────────────────────────

def test_the_domain_is_a_workspace_setting() -> None:
    """`base.vn` and `base.com.vn` are separate installations.

    This was briefly removed on the argument that the host belongs to the
    product. It does not: a token issued on one installation is refused by the
    other with `access_token_v2_invalid_3`, which is the same message an
    expired token produces. Ten working tokens looked dead for an afternoon
    because of it.

    What did stay removed is `version`. It was the other half of the old HRM
    config, and letting a workspace change `extapi/v1` only lets them point at
    an API these streams are not written against.
    """
    # Per connector, because they are not all on the same pair of hosts. The
    # invariant is that the choice is offered, is a closed set, and defaults to
    # the first member -- not that every application lives at `base.com.vn`.
    expected = {
        "crm": ["basecrm.vn", "base.vn"],        # apis.basecrm.vn / apis.base.vn
        "crm-leads": ["basecrm.vn", "base.vn"],  # same two hosts, other root
    }
    for connector in CONNECTORS:
        spec = compile_manifest(connector)["spec"]["connection_specification"]
        assert "domain" in spec["properties"], connector.app
        hosts = expected.get(connector.app, ["base.com.vn", "base.vn"])
        # An enum, so the form renders a dropdown: a fixed list, no typos.
        assert spec["properties"]["domain"]["enum"] == hosts, connector.app
        assert spec["properties"]["domain"]["default"] == hosts[0], connector.app
        assert "version" not in spec["properties"], connector.app

        # And it actually reaches the request.
        for stream in compile_manifest(connector)["definitions"]["streams"].values():
            url = stream["retriever"]["requester"]["url_base"]
            assert "config['domain']" in url, (connector.app, url)
            assert ".base.vn/" not in url, (
                f"{connector.app}: the host is hardcoded, so the domain field "
                "would do nothing")


def test_help_text_is_vietnamese_and_renders_as_written() -> None:
    """The product is Vietnamese; English help beside Vietnamese labels reads
    as a half-finished screen.

    No markdown either. The form renders help as plain text, so backticks and
    asterisks arrive on screen as punctuation — which is how `base.vn` came to
    be displayed with the quotes visible.
    """
    vietnamese = "àáâãèéêìíòóôõùúýăđĩũơưạảấầẩậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ"
    for connector in CONNECTORS:
        spec = compile_manifest(connector)["spec"]["connection_specification"]
        for name, field in spec["properties"].items():
            description = field.get("description", "")
            assert description, f"{connector.app}.{name} has no help at all"
            assert "`" not in description, (
                f"{connector.app}.{name}: markdown in help text renders literally")
            assert "**" not in description, f"{connector.app}.{name}"
            assert any(ch in vietnamese for ch in description.lower()), (
                f"{connector.app}.{name}: help is not in Vietnamese")


def test_every_connector_ships_an_icon() -> None:
    """A missing icon is a 404 per connector per page load.

    Ten of them, on the first screen of the create wizard, in a product whose
    own audit counts console errors.
    """
    icons = ROOT / "backend" / "app" / "resources" / "connector_icons"
    for connector in CONNECTORS:
        path = icons / f"{connector.connector_key}.svg"
        assert path.exists(), f"no icon for {connector.connector_key}"
        assert path.read_text(encoding="utf-8").lstrip().startswith(("<?xml", "<svg")), (
            f"{path.name} is not an SVG")


@pytest.mark.asyncio
async def test_airbyte_api_keeps_the_base_spec_not_the_runner_spec() -> None:
    """The generic runner's manifest slot is not a customer config field."""
    from app.adapters.airbyte_api.adapter import AirbyteApiAdapter
    from app.adapters.dto import ConnectorDescriptor
    from app.adapters.registry import bundled_by_key

    bundled = bundled_by_key("source-base-workflow")
    assert bundled is not None and bundled.declarative_manifest
    descriptor = ConnectorDescriptor(
        connector_key=bundled.connector_key,
        docker_repository=bundled.docker_repository,
        version=bundled.version,
        declarative_manifest=bundled.declarative_manifest,
    )
    adapter = object.__new__(AirbyteApiAdapter)
    adapter._definition_id = AsyncMock(return_value="runner-id")
    adapter._definitions = AsyncMock(return_value={
        bundled.docker_repository: {"dockerImageTag": bundled.version},
    })
    adapter._post = AsyncMock()

    metadata = await adapter.get_connector_spec(descriptor)

    properties = metadata.spec_schema["properties"]
    assert "access_token_v2" in properties
    assert "domain" in properties
    assert "__injected_declarative_manifest" not in properties
    adapter._post.assert_not_awaited()


@pytest.mark.asyncio
async def test_airbyte_definition_is_moved_to_the_certified_tag() -> None:
    """A Product DB version label cannot substitute for engine execution."""
    from app.adapters.airbyte_api.adapter import AirbyteApiAdapter
    from app.adapters.dto import ConnectorDescriptor

    descriptor = ConnectorDescriptor(
        connector_key="destination-bigquery",
        docker_repository="airbyte/destination-bigquery",
        version="3.0.22",
    )
    entry = {
        "destinationDefinitionId": "definition-id",
        "dockerImageTag": "2.4.19",
    }
    adapter = object.__new__(AirbyteApiAdapter)
    adapter._definition_cache = {}
    adapter._definitions = AsyncMock(return_value={
        descriptor.docker_repository: entry,
    })
    adapter._resolve_workspace = AsyncMock(return_value="workspace-id")
    adapter._post = AsyncMock(return_value={})

    definition_id = await adapter._definition_id(descriptor, "DESTINATION")

    assert definition_id == "definition-id"
    assert entry["dockerImageTag"] == "3.0.22"
    adapter._post.assert_awaited_once_with(
        "/api/v1/destination_definitions/update",
        {
            "destinationDefinitionId": "definition-id",
            "dockerImageTag": "3.0.22",
            "workspaceId": "workspace-id",
        },
    )


@pytest.mark.asyncio
async def test_cached_engine_definition_id_still_pins_the_certified_tag() -> None:
    """An engine id saved in Product DB must not bypass version enforcement."""
    from app.adapters.airbyte_api.adapter import AirbyteApiAdapter
    from app.adapters.dto import ConnectorDescriptor

    descriptor = ConnectorDescriptor(
        connector_key="destination-bigquery",
        docker_repository="airbyte/destination-bigquery",
        version="3.0.22",
        engine_definition_id="stale-definition-id",
    )
    entry = {
        "destinationDefinitionId": "current-definition-id",
        "dockerImageTag": "2.4.19",
    }
    adapter = object.__new__(AirbyteApiAdapter)
    adapter._definitions = AsyncMock(return_value={
        descriptor.docker_repository: entry,
    })
    adapter._resolve_workspace = AsyncMock(return_value="workspace-id")
    adapter._post = AsyncMock(return_value={})

    definition_id = await adapter._definition_id(descriptor, "DESTINATION")

    assert definition_id == "current-definition-id"
    adapter._post.assert_awaited_once_with(
        "/api/v1/destination_definitions/update",
        {
            "destinationDefinitionId": "current-definition-id",
            "dockerImageTag": "3.0.22",
            "workspaceId": "workspace-id",
        },
    )


# ── a refusal about one record is not a refusal of the request ───────────────

def test_a_private_resource_is_skipped_but_a_bad_token_still_fails() -> None:
    """Base says `code: 0` for both, and only the message distinguishes them.

    Hiring's `stage` stream is partitioned by `opening_id`. Opening 386 in the
    test workspace is private, so `stage/list` answers HTTP 200 with
    `{"code": 0, "message": "This opening is private."}`. The error handler
    failed on any `code: 0`, so one unreadable opening failed the stream,
    failed the sync, and failed all three retries the same way -- 579 records
    already written and the run still reported as broken.

    The fix has to stay narrow. The reason `code: 0` fails at all is that
    treating it as an empty collection lets a rejected token overwrite a
    customer's table with nothing and report success. So a new refusal that
    nobody has seen must keep failing loudly; only phrases in
    `PARTITION_REFUSALS` are skipped.
    """
    from app.connectors.base_vn._shared import PARTITION_REFUSALS, _error_handler

    filters = _error_handler()["response_filters"]
    actions = [f["action"] for f in filters]
    assert actions.index("IGNORE") < actions.index("FAIL"), (
        "the catch-all FAIL is evaluated first, so nothing is ever ignored")

    ignore = filters[actions.index("IGNORE")]["predicate"]
    for phrase in PARTITION_REFUSALS:
        assert repr(phrase) in ignore or phrase in ignore, phrase
    assert "|lower" in ignore, "matching is case-sensitive against Base's prose"

    # The narrowness is the point: an unknown refusal must not be ignored.
    assert "access_token_v2_invalid" not in ignore
    assert len(PARTITION_REFUSALS) <= 3, (
        f"{len(PARTITION_REFUSALS)} phrases are now skipped silently; each one "
        "is a case where a sync reports success having read nothing, so add "
        "them only with an observed log line to justify it")


def test_the_ignore_predicate_decides_the_way_base_actually_answers() -> None:
    """Rendered against real response bodies, not read as a string.

    A predicate that looks right and evaluates wrong is the failure mode here:
    it is Jinja inside JSON inside a manifest, and nothing type-checks it.
    """
    from jinja2 import Template

    from app.connectors.base_vn._shared import _error_handler

    filters = _error_handler()["response_filters"]
    predicate = next(f for f in filters if f["action"] == "IGNORE")["predicate"]

    def decides(body: dict) -> bool:
        return Template(predicate).render(response=body).strip() == "True"

    assert decides({"code": 0, "message": "This opening is private."})
    assert decides({"code": 0, "message": "THIS OPENING IS PRIVATE."})
    # A token failure is not a private record, and must still reach FAIL.
    assert not decides({"code": 0, "message": "access_token_v2_invalid_3"})
    assert not decides({"code": 0, "message": "Something new and unexplained"})
    # A success carries no `code: 0` at all.
    assert not decides({"code": 1, "data": [], "message": "ok"})
    assert not decides({"data": []})


def test_income_sends_a_closed_range_in_the_body_not_the_query() -> None:
    """Income is on `extapi/v1`, and that API differs from `publicapi/v2` twice.

    Measured against the live service, not inferred from documentation:

        ?updated_from=0                      -> code 0, "Updated from param is required"
        body updated_from=0                  -> code 0, "Updated to param is required"
        body updated_from=0 & updated_to=now -> code 1, records

    So the bounds belong in the form body, and the range has to be closed. The
    connector sent `updated_from` in the query string and no end bound at all,
    so Income reported the parameter as missing while it was being sent — to a
    place that application does not read — and the sync failed outright.

    The other nine applications must keep the open-ended query-string form;
    that is the second half of this test, and the reason the setting lives on
    `Incremental` rather than being applied everywhere.
    """
    import sys

    from app.connectors.base_vn import BY_KEY, CONNECTORS
    from app.connectors.base_vn._shared import compile_manifest

    def options(connector):
        manifest = compile_manifest(connector)
        node = manifest.get("definitions", {}).get("streams", {})
        for stream in node.values():
            sync = stream.get("incremental_sync")
            if sync:
                return sync.get("start_time_option"), sync.get("end_time_option")
        return None, None

    start, end = options(BY_KEY["source-base-income"])
    assert start["inject_into"] == "body_data", start
    assert start["field_name"] == "updated_from"
    assert end is not None, "Income needs a closing bound or it refuses the call"
    assert end["field_name"] == "updated_to"
    assert end["inject_into"] == "body_data", end

    # Applications whose cursor rides in the request body rather than the query
    # string, each verified against the live API. Everything else must stay on
    # the query string -- see the assertion below for what breaks otherwise.
    body_cursor = {"income", "crm", "crm-leads"}
    for connector in CONNECTORS:
        if connector.app in body_cursor:
            continue
        start, end = options(connector)
        if start is None:
            continue
        assert start["inject_into"] == "request_parameter", (
            f"{connector.app} moved its cursor into the body; only Income's "
            f"older API wants that: {start}")
        assert end is None, (
            f"{connector.app} now sends a closing bound. publicapi/v2 treats "
            f"the filter as open-ended, so this silently truncates every sync "
            f"at the moment it started: {end}")


def test_every_substream_names_a_parent_that_exists() -> None:
    """A substream is only as complete as the partitions it is handed.

    This replaced a test that asserted a partition parent must be full refresh.
    That was a theory -- an incremental parent emits only changed rows, so the
    children would surely lose partitions -- and it is wrong.
    `SubstreamPartitionRouter` reads the parent independently of the parent's
    cursor. Measured on the live API: `workflow.workflow` is incremental and
    emits one record per sync, while `workflow.stage` still receives all twenty
    partitions and reads all 103 stages, every run.

    What is worth asserting is the thing that would actually silently drop
    records: a parent named in a partition router that is not a stream this
    connector ships. `_shared` already refuses that at import; this pins it so
    the check cannot be quietly relaxed.
    """
    from app.connectors.base_vn import CONNECTORS

    for connector in CONNECTORS:
        names = {stream.name for stream in connector.streams}
        for stream in connector.streams:
            if not stream.parent:
                continue
            assert stream.parent.stream in names, (
                f"{connector.app}.{stream.name} is partitioned by "
                f"{stream.parent.stream!r}, which this connector does not ship")
            assert stream.parent.inject, (
                f"{connector.app}.{stream.name} has a parent but no request "
                "field to inject the parent id into, so every partition would "
                "issue the same unfiltered request")


def test_no_substream_asks_the_router_to_follow_the_parent_cursor() -> None:
    """`incremental_dependency` is off, and that is a decision with evidence.

    The complaint it answers is real and was reproduced: a parent filtered by
    `last_update` yields far fewer records than it holds, yet the child still
    issues one request per parent id. On Base CRM, run two emitted 263 deals
    instead of 5,582 and still made all 270 child requests.

    The CDK flag that changes this is `incremental_dependency` on
    `ParentStreamConfig`. It stays off for two measured reasons:

    * Enabling it on CRM changed nothing -- identical 263 records and 270
      requests -- because `pipeline/all` ignores the time filter, so the parent
      read is the full list either way.
    * Where it *would* bite, it loses data. It is only correct when a parent's
      `last_update` moves whenever a child changes. Base does not do that: 234
      of 234 CRM pipelines have a deal newer than the pipeline, and 41 of 42
      WeWork projects have a task newer than the project. Those parents would
      leave the partition list after the first sync and their children would
      stop arriving, with every run still green.

    So this asserts the flag is absent everywhere. Turning it on for a stream
    is allowed -- but only by editing this test with the cascade measurement
    for that specific parent, which is the check that was missing.
    """
    from app.connectors.base_vn import CONNECTORS
    from app.connectors.base_vn._shared import compile_manifest

    for connector in CONNECTORS:
        manifest = compile_manifest(connector)
        for name, stream in manifest["definitions"]["streams"].items():
            router = stream["retriever"].get("partition_router")
            if not router:
                continue
            for parent in router.get("parent_stream_configs", []):
                assert "incremental_dependency" not in parent, (
                    f"{connector.app}.{name} follows its parent's cursor. That "
                    f"only holds if {parent.get('partition_field')}'s parent is "
                    f"touched whenever a child changes -- measure it before "
                    f"enabling, and record the numbers here.")


def test_crm_does_not_ship_endpoints_whose_data_is_already_embedded() -> None:
    """Three documented CRM endpoints are left out on purpose.

    `pipeline/get.stages` returns exactly what `pipeline.cached_stages`
    already carries -- compared field by field across twenty pipelines, the
    only difference is `token`, a per-response value. `pipeline/get.segments`
    is the same against `cached_segments`, identical on all fifteen checked.
    Shipping either means 270 extra requests a sync for rows the reader
    already has, and two tables that must be joined to say the same thing.

    `account/get.activities` and `contact/get.activities` answer with the
    plain string `Function 1 is deprecated` -- not JSON, not an error code.

    This is a list of paths rather than stream names because the tempting
    mistake is to add the endpoint back under a new name.
    """
    from app.connectors.base_vn import BY_KEY

    crm = BY_KEY["source-base-crm"]
    shipped = {stream.path for stream in crm.streams}
    for path, why in (
        ("pipeline/get.stages", "duplicated by pipeline.cached_stages"),
        ("pipeline/get.segments", "duplicated by pipeline.cached_segments"),
        ("account/get.activities", "returns 'Function 1 is deprecated'"),
        ("contact/get.activities", "returns 'Function 1 is deprecated'"),
    ):
        assert path not in shipped, f"{path} is back: {why}"


def test_crm_reaches_accounts_and_contacts_without_a_hardcoded_service() -> None:
    """The ported YAML baked in one tenant's service ids; they fail elsewhere.

    `service_id: '248'` for contacts and `['680', '211']` for accounts are
    rejected for this token with `INVALID_CONTACT_SERVICE` /
    `INVALID_ACCOUNT_SERVICE`. `account/service/all` and `contact/service/all`
    list them from the token alone, so the services are streams and the rest
    hang off them.

    What this pins is that no CRM stream carries a literal id in its request
    body -- the shape the YAML had, and the one that ships somebody else's
    tenant to every workspace.
    """
    from app.connectors.base_vn import BY_KEY

    crm = BY_KEY["source-base-crm"]
    by_name = {s.name: s for s in crm.streams}

    for child, parent in (("account", "account_service"),
                          ("contact", "contact_service"),
                          ("account_segment", "account_service"),
                          ("contact_segment", "contact_service")):
        stream = by_name[child]
        assert stream.parent is not None, f"{child} has no parent service stream"
        assert stream.parent.stream == parent, (child, stream.parent.stream)
        assert stream.parent.inject == "service_id", stream.parent.inject

    for stream in crm.streams:
        for field, value in stream.body.items():
            assert not str(value).isdigit(), (
                f"crm.{stream.name} sends a literal {field}={value!r}; that is "
                "one tenant's id and fails for every other workspace")


def test_a_quota_refusal_is_a_wait_not_a_failure() -> None:
    """Base says "too fast" in the body, with HTTP 400 and no Retry-After.

    Measured: `lead/feed/list` answers
    `{"code": 0, "message": "Quota exceeded: 100 req/min"}` once a token has
    spent its minute. Read by the catch-all FAIL rule that turns `code: 0` into
    an error, that killed the stream 37 seconds into a sync -- and Airbyte then
    turned one failed stream into three unreadable retries, because
    `StreamStatusException.getMessage()` in 0.59.1 calls itself and the
    resulting StackOverflow stacktrace pushed the job result past Temporal's
    2 MB limit ("Complete result exceeds size limit").

    So the ordering matters as much as the rule: RATE_LIMITED has to be matched
    before FAIL.
    """
    from jinja2 import Template

    from app.connectors.base_vn._shared import _error_handler

    filters = _error_handler()["response_filters"]
    actions = [f["action"] for f in filters]
    assert actions.index("RATE_LIMITED") < actions.index("FAIL"), (
        "FAIL is evaluated first, so a quota refusal kills the sync")

    throttle = next(f for f in filters
                    if f["action"] == "RATE_LIMITED" and "predicate" in f)

    def decides(body: dict) -> bool:
        return Template(throttle["predicate"]).render(response=body).strip() == "True"

    assert decides({"code": 0, "message": "Quota exceeded: 100 req/min"})
    assert decides({"code": 0, "message": "TOO MANY REQUESTS"})
    # A token failure is not throttling and must still reach FAIL.
    assert not decides({"code": 0, "message": "access_token_v2_invalid_3"})
    assert not decides({"code": 0, "message": "This opening is private."})
    assert not decides({"code": 1, "data": []})


def test_only_the_application_with_a_measured_cap_declares_one() -> None:
    """A budget slows every request, so it is not a precaution to sprinkle.

    Base CRM Leads refused at 100/minute. The Sales API sustained 5,582 requests
    in one sync at roughly 220/minute without a refusal, so the two do not share
    a limit and inventing one for the other ten would make every sync slower to
    guard against a cap nobody has seen.
    """
    declared = {c.app: c.rate_limit for c in CONNECTORS if c.rate_limit}
    assert declared == {"crm-leads": (100, "PT1M")}, declared

    manifest = compile_manifest(BY_KEY["source-base-crm-leads"])
    budget = manifest["api_budget"]
    assert budget["type"] == "HTTPAPIBudget"
    policy = budget["policies"][0]
    assert policy["type"] == "MovingWindowCallRatePolicy"
    assert policy["rates"] == [{"type": "Rate", "limit": 100, "interval": "PT1M"}]
    # No matchers: the quota is per token, not per endpoint, so it has to cover
    # every request the connector makes rather than one path.
    assert policy["matchers"] == []

    # And nothing is emitted where no cap was measured.
    assert "api_budget" not in compile_manifest(BY_KEY["source-base-crm"])

