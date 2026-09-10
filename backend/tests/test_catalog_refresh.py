"""What the periodic catalogue refresh is allowed to overwrite.

The worker asks the engine for each connector's spec and writes the answer
back. For an upstream connector that is the whole point: the image is the
authority on its own form. For a connector this product compiled into a
declarative manifest it is exactly wrong -- the engine sees the generic
runner, whose spec describes one internal field called
`__injected_declarative_manifest` and nothing about tokens or domains.

Found against a live tenant. Four Base sources were created and worked; the
refresh loop reached those connectors -- it prioritises `usage_count > 0`, so
it goes for the ones customers are using -- and every Base connector's form
was replaced by the runner's slot. Creating a source then answered "Thiếu
thông tin bắt buộc: Low-code manifest". A restart reseeded the bundled spec
and the next refresh broke it again.

The guard existed and covered the wrong line: it protected `spec_source`, the
label saying where the spec came from, while the spec itself was overwritten.
"""

from __future__ import annotations

from app.services.catalog import spec_hash

#: What the generic runner reports about itself.
RUNNER_SPEC = {
    "type": "object",
    "required": ["__injected_declarative_manifest"],
    "properties": {"__injected_declarative_manifest": {"type": "object"}},
}

#: What a Base connector actually asks a workspace for.
BUNDLED_SPEC = {
    "type": "object",
    "required": ["access_token_v2"],
    "properties": {
        "access_token_v2": {"type": "string"},
        "domain": {"type": "string", "enum": ["base.vn", "base.com.vn"]},
    },
}


class _Connector:
    """Only the fields the refresh writes."""

    def __init__(self, declarative_manifest: dict | None) -> None:
        self.declarative_manifest = declarative_manifest
        self.spec_schema = BUNDLED_SPEC if declarative_manifest else RUNNER_SPEC
        self.spec_hash = spec_hash(self.spec_schema)
        self.spec_source = "BUNDLED" if declarative_manifest else "ENGINE"
        self.supports_incremental = True
        self.supports_oauth = False
        self.supported_destination_sync_modes: list[str] = []
        self.engine_version = ""
        self.image_pulled = False


class _Metadata:
    """What the engine answered."""

    def __init__(self, spec_schema: dict) -> None:
        self.spec_schema = spec_schema
        self.supports_incremental = False
        self.supports_oauth = True
        self.supported_destination_sync_modes = ["append"]
        self.engine_version = "7.28.2"


def _apply(connector: _Connector, metadata: _Metadata) -> None:
    """The decision under test, in the shape `refresh_specs` applies it."""
    owns_its_spec = connector.declarative_manifest is not None
    if metadata.spec_schema and not owns_its_spec:
        connector.spec_schema = metadata.spec_schema
        connector.spec_hash = spec_hash(metadata.spec_schema)
        connector.spec_source = "ENGINE"
    if not owns_its_spec:
        connector.supports_incremental = metadata.supports_incremental
        connector.supports_oauth = metadata.supports_oauth
        if metadata.supported_destination_sync_modes:
            connector.supported_destination_sync_modes = (
                metadata.supported_destination_sync_modes
            )
    if metadata.engine_version:
        connector.engine_version = metadata.engine_version
    connector.image_pulled = True


def test_a_declarative_connector_keeps_its_own_form() -> None:
    """The bug, in one assertion: the token-and-domain form must survive a
    refresh that asked the runner what it wants."""
    connector = _Connector(declarative_manifest={"type": "DeclarativeSource"})
    _apply(connector, _Metadata(RUNNER_SPEC))

    assert connector.spec_schema == BUNDLED_SPEC
    assert "access_token_v2" in connector.spec_schema["properties"]
    assert "__injected_declarative_manifest" not in connector.spec_schema["properties"]
    assert connector.spec_source == "BUNDLED"


def test_the_hash_stays_with_the_spec_it_describes() -> None:
    """Leaving a stale hash beside a kept spec would make the next comparison
    think the spec had changed, and log a change that never happened."""
    connector = _Connector(declarative_manifest={"type": "DeclarativeSource"})
    before = connector.spec_hash
    _apply(connector, _Metadata(RUNNER_SPEC))
    assert connector.spec_hash == before == spec_hash(connector.spec_schema)


def test_capabilities_are_not_taken_from_the_runner_either() -> None:
    """The runner reports its own generic capabilities. A Base connector knows
    which of its streams are incremental; the image does not."""
    connector = _Connector(declarative_manifest={"type": "DeclarativeSource"})
    _apply(connector, _Metadata(RUNNER_SPEC))
    assert connector.supports_incremental is True
    assert connector.supports_oauth is False
    assert connector.supported_destination_sync_modes == []


def test_an_upstream_connector_still_takes_the_engine_spec() -> None:
    """The fix must not stop the refresh doing its job: for a connector whose
    image is the authority, the engine's answer is the right one."""
    connector = _Connector(declarative_manifest=None)
    upstream = {"type": "object", "required": ["host"],
                "properties": {"host": {"type": "string"}}}
    _apply(connector, _Metadata(upstream))

    assert connector.spec_schema == upstream
    assert connector.spec_source == "ENGINE"
    assert connector.supports_incremental is False
    assert connector.supports_oauth is True
    assert connector.supported_destination_sync_modes == ["append"]


def test_the_image_tag_is_recorded_for_both() -> None:
    """What the engine will actually run is the one thing it is authoritative
    about for every connector, declarative or not."""
    for manifest in ({"type": "DeclarativeSource"}, None):
        connector = _Connector(declarative_manifest=manifest)
        _apply(connector, _Metadata(RUNNER_SPEC))
        assert connector.engine_version == "7.28.2"
        assert connector.image_pulled is True
