"""Refusing an unsupported rename must not lock the pipeline.

The embedded runner cannot name a destination stream anything other than what
the source emitted, so `stream_prefix` and `namespace_format` are refused
rather than stored and ignored -- storing them let two pipelines write into one
table and corrupt each other's staging.

The first version of that guard read the pipeline's *stored* value on update as
well as the payload's. Every pipeline created before the guard existed carried
a prefix, so editing any of them -- renaming, or setting a schedule -- was
refused with a message about `stream_prefix`, which is not what the person was
doing and not something they could act on. Reported from the settings screen:
"why can't this pipeline have a schedule?"

Reading only the payload was not enough either, and driving the real screen is
what showed it: the settings form submits every field it holds, so the stored
prefix comes back in the payload on a save that only changed the schedule. The
guard saw a prefix, refused, and the pipeline was still stranded.

So what is refused is a *change*. A resubmitted value is not a request for
anything -- the stored prefix is already inert at sync time, and refusing to
save around it protects nothing.
"""

from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.services.pipelines import _reject_unsupported_naming


@pytest.fixture(autouse=True)
def embedded_engine(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "engine_type", "AIRBYTE_EMBEDDED", raising=False)


def test_asking_for_a_prefix_is_refused() -> None:
    with pytest.raises(ValidationError) as caught:
        _reject_unsupported_naming(None, "service_")
    assert "stream_prefix" in str(caught.value.details["unsupported_fields"])


def test_asking_for_a_namespace_format_is_refused() -> None:
    with pytest.raises(ValidationError):
        _reject_unsupported_naming("${SOURCE_NAMESPACE}_raw", None)


def test_asking_for_neither_is_allowed() -> None:
    """The shape every ordinary edit takes: a schedule change mentions neither
    field, and must not be refused because of a value stored months ago."""
    _reject_unsupported_naming(None, None)


def test_clearing_the_field_is_allowed() -> None:
    """An empty string is how the form clears it. Refusing that would leave
    the one pipeline that wants to stop using a prefix unable to say so."""
    _reject_unsupported_naming("", "")


def test_the_message_names_the_way_out() -> None:
    """A refusal that does not say what to do instead is a dead end. Two
    pipelines on one warehouse are separated by a schema each."""
    with pytest.raises(ValidationError) as caught:
        _reject_unsupported_naming(None, "svc_")
    message = str(caught.value)
    assert "schema" in message
    assert "AIRBYTE_EMBEDDED" in message


def test_the_api_engine_accepts_both(monkeypatch) -> None:
    """The refusal is about this engine, not about the feature. Airbyte's API
    adapter honours both, and sql_direct does too."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "engine_type", "AIRBYTE_API", raising=False)
    _reject_unsupported_naming("${SOURCE_NAMESPACE}", "service_")


# -- what the settings form actually posts ----------------------------------

def test_resubmitting_the_stored_prefix_is_allowed() -> None:
    """The bug the user hit. Choosing a schedule on a pipeline created before
    the guard posted `stream_prefix: "service_"` back untouched, and the save
    failed with a message about a field the person had not touched."""
    _reject_unsupported_naming(None, "service_", current_stream_prefix="service_")


def test_resubmitting_the_stored_namespace_format_is_allowed() -> None:
    _reject_unsupported_naming(
        "${SOURCE_NAMESPACE}_raw", None,
        current_namespace_format="${SOURCE_NAMESPACE}_raw",
    )


def test_changing_an_existing_prefix_is_still_refused() -> None:
    """Editing the value is asking for the rename, and the rename still cannot
    happen. Grandfathering the stored value must not grandfather the field."""
    with pytest.raises(ValidationError):
        _reject_unsupported_naming(None, "svc_", current_stream_prefix="service_")


def test_clearing_a_stored_prefix_is_allowed() -> None:
    """The one edit that moves toward what the engine can do."""
    _reject_unsupported_naming(None, "", current_stream_prefix="service_")


def test_a_prefix_is_refused_where_none_was_stored() -> None:
    """Create passes no current value, so the default must keep refusing."""
    with pytest.raises(ValidationError):
        _reject_unsupported_naming(None, "service_", current_stream_prefix=None)


def test_one_unchanged_field_does_not_excuse_the_other() -> None:
    """A save that keeps the stored prefix and adds a namespace format is still
    asking for something new, and only the new field should be named."""
    with pytest.raises(ValidationError) as caught:
        _reject_unsupported_naming(
            "${SOURCE_NAMESPACE}", "service_",
            current_stream_prefix="service_", current_namespace_format=None,
        )
    fields = caught.value.details["unsupported_fields"]
    assert fields == ["namespace_format"]


# -- the screen and the API must agree ---------------------------------------

def test_the_capability_says_no_on_the_embedded_engine() -> None:
    """What `/auth/me` publishes to the frontend. Reported with a screenshot:
    the create form drew "Tiền tố bảng ở đích", somebody typed `to_1`, and the
    save came back with a validation error and no way forward. A box whose
    only possible outcome is a refusal should not be drawn."""
    from app.core.config import settings
    assert settings.supports_destination_naming is False


def test_the_capability_says_yes_where_the_engine_can_rename(monkeypatch) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, "engine_type", "AIRBYTE_API", raising=False)
    assert settings.supports_destination_naming is True


@pytest.mark.parametrize("engine", ["AIRBYTE_EMBEDDED", "AIRBYTE_API", "SQL_DIRECT"])
def test_what_is_offered_is_exactly_what_is_accepted(engine: str, monkeypatch) -> None:
    """The invariant worth holding: one predicate decides both whether the
    frontend draws the field and whether the API accepts it. Two predicates
    would drift, and the drift shows up as a form nobody can submit."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "engine_type", engine, raising=False)
    offered = settings.supports_destination_naming

    try:
        _reject_unsupported_naming(None, "svc_")
        accepted = True
    except ValidationError:
        accepted = False
    assert offered == accepted


def test_the_default_capability_is_permissive() -> None:
    """An API older than the field sends nothing, and the frontend falls back
    to this. Hiding a field that works is the worse of the two mistakes."""
    from app.schemas.domain import EngineCapabilities
    assert EngineCapabilities().destination_naming is True

