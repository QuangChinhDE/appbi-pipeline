"""The Base connectors on the extapis gateway (`base_vn/_extapi.py`), checked as data.

Like `test_base_connectors.py`: no Docker, no network, no tenant. What is
pinned here are the rules every extapis connector has to keep, so a new one
cannot quietly break them.
"""

from __future__ import annotations

import pytest

from app.connectors.base_vn import BY_KEY, EXTAPI_CONNECTORS, catalogue_entries, manifests
from app.connectors.base_vn._extapi import USER_PICKED_CURSOR, compile_manifest

ALL = list(EXTAPI_CONNECTORS)
IDS = [c.app for c in ALL]


@pytest.mark.parametrize("connector", ALL, ids=IDS)
def test_registered_like_every_other_base_connector(connector) -> None:
    """In the group, not wired in on the side: the same lookups that find
    the other Base connectors find these."""
    assert BY_KEY[connector.connector_key] is connector
    assert connector.connector_key in manifests()
    entry = next(e for e in catalogue_entries() if e["connector_key"] == connector.connector_key)
    assert entry["category"] == "Base.vn"
    assert entry["stream_count"] == len(connector.streams)


@pytest.mark.parametrize("connector", ALL, ids=IDS)
def test_the_workspace_picks_the_cursor(connector) -> None:
    """No connector decides the cursor: none is named, and the catalogue's
    choice is honoured."""
    for name, stream in compile_manifest(connector)["definitions"]["streams"].items():
        inc = stream.get("incremental_sync")
        if not inc:
            continue
        assert inc["cursor_field"] == USER_PICKED_CURSOR, f"{connector.app}.{name}"
        assert inc["allow_catalog_defined_cursor_field"] is True, f"{connector.app}.{name}"


@pytest.mark.parametrize("connector", ALL, ids=IDS)
def test_cursor_candidates_are_real_columns(connector) -> None:
    for stream in connector.streams:
        if stream.incremental:
            for column in stream.incremental.candidates:
                assert column in stream.properties, f"{connector.app}.{stream.name}.{column}"


@pytest.mark.parametrize("connector", ALL, ids=IDS)
def test_nothing_is_stamped_as_a_cursor(connector) -> None:
    """The only field added to a record is its parent id; a cursor column is
    never manufactured."""
    for name, stream in compile_manifest(connector)["definitions"]["streams"].items():
        for transformation in stream.get("transformations", []):
            for added in transformation["fields"]:
                assert "stream_partition." in added["value"], f"{connector.app}.{name}"


def _undescribed(properties: dict, prefix: str = "") -> list[str]:
    missing = []
    for name, prop in properties.items():
        path = f"{prefix}{name}"
        if not prop.get("description") or not prop.get("description_vi"):
            missing.append(path)
        missing += _undescribed(prop.get("properties") or {}, f"{path}.")
        items = prop.get("items") or {}
        missing += _undescribed(items.get("properties") or {}, f"{path}[].")
    return missing


@pytest.mark.parametrize("connector", ALL, ids=IDS)
def test_every_field_says_what_it_means(connector) -> None:
    """The Dictionary tab shows the connector's own meaning first; a field
    this product wrote the schema for has no excuse to leave it to a guess."""
    for stream in connector.streams:
        assert not _undescribed(stream.properties), (
            f"{connector.app}.{stream.name}: {_undescribed(stream.properties)}")


def _parents(stream: dict) -> list[dict]:
    router = stream["retriever"].get("partition_router") or []
    routers = router if isinstance(router, list) else [router]
    return [c["stream"] for r in routers for c in r.get("parent_stream_configs", [])]


@pytest.mark.parametrize("connector", ALL, ids=IDS)
def test_a_parent_is_read_without_a_cursor(connector) -> None:
    """A child reads its parent for the ids; a parent carrying the empty
    user-picked cursor warned once per record (86 lines in one run)."""
    def walk(stream: dict, path: str) -> None:
        for parent in _parents(stream):
            assert "$ref" not in parent, f"{path}: parent still referenced, not inlined"
            assert "incremental_sync" not in parent, f"{path} -> {parent['name']}: parent has a cursor"
            walk(parent, f"{path} -> {parent['name']}")

    for name, stream in compile_manifest(connector)["definitions"]["streams"].items():
        walk(stream, f"{connector.app}.{name}")


@pytest.mark.parametrize("connector", ALL, ids=IDS)
def test_a_refusal_in_the_body_fails_the_sync(connector) -> None:
    """`status: 0` on a 200 is a refusal; read as success it would replace a
    table with nothing."""
    for name, stream in compile_manifest(connector)["definitions"]["streams"].items():
        filters = stream["retriever"]["requester"]["error_handler"]["response_filters"]
        assert any(
            f["action"] == "FAIL" and "response.get('status') == 0" in f.get("predicate", "")
            for f in filters
        ), f"{connector.app}.{name}"
