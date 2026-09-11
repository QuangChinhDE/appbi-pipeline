"""Who may set a destination rename, and what happens when an engine cannot.

`stream_prefix` and `namespace_format` are honoured everywhere now -- the
embedded runner builds two catalogs and maps the stream on the way past, the
same job Airbyte does in its replication worker. `test_stream_naming.py` pins
that mapping.

What is left here is the guard around it, and it has been wrong twice:

1. It read the pipeline's *stored* value on update, so every pipeline created
   before the guard existed was refused on any edit. Setting a schedule was
   answered with a message about `stream_prefix`. Reported as "why can't this
   pipeline have a schedule?"
2. Reading only the payload was not enough either: the settings form submits
   every field it holds, so the stored prefix comes back on a save that only
   changed the schedule. Refusing the value rather than the change left the
   pipeline stranded all the same.

So it refuses a *change*, and only where the engine genuinely cannot rename.
Nothing reports that today; the test forces it, because an engine added later
that cannot rename must fail here and be reported through
`supports_destination_naming` rather than discovered one failed save at a time.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings, settings
from app.core.errors import ValidationError
from app.services.pipelines import _reject_unsupported_naming


@pytest.fixture
def cannot_rename(monkeypatch):
    """A hypothetical engine with no way to name a destination table.

    Patched on the class: `supports_destination_naming` is a property, so an
    instance attribute would not take.
    """
    monkeypatch.setattr(Settings, "supports_destination_naming", property(lambda _: False))


# ── where the engine can rename, which is everywhere today ────────────────

def test_the_engine_in_use_can_rename() -> None:
    assert settings.supports_destination_naming is True


def test_asking_for_a_prefix_is_allowed() -> None:
    _reject_unsupported_naming(None, "service_")


def test_asking_for_a_namespace_format_is_allowed() -> None:
    _reject_unsupported_naming("${SOURCE_NAMESPACE}_raw", None)


# ── where it cannot ───────────────────────────────────────────────────────

def test_a_new_prefix_is_refused(cannot_rename) -> None:
    with pytest.raises(ValidationError) as caught:
        _reject_unsupported_naming(None, "service_")
    assert "stream_prefix" in str(caught.value.details["unsupported_fields"])


def test_a_new_namespace_format_is_refused(cannot_rename) -> None:
    with pytest.raises(ValidationError):
        _reject_unsupported_naming("${SOURCE_NAMESPACE}_raw", None)


def test_asking_for_neither_is_allowed(cannot_rename) -> None:
    """The shape every ordinary edit takes: a schedule change mentions neither
    field, and must not be refused because of a value stored months ago."""
    _reject_unsupported_naming(None, None)


def test_clearing_the_field_is_allowed(cannot_rename) -> None:
    """An empty string is how the form clears it. Refusing that would leave the
    one pipeline that wants to stop using a prefix unable to say so."""
    _reject_unsupported_naming("", "")


def test_resubmitting_the_stored_prefix_is_allowed(cannot_rename) -> None:
    """The bug users hit. Choosing a schedule posted `stream_prefix:
    "service_"` back untouched, and the save failed over a field nobody had
    touched."""
    _reject_unsupported_naming(None, "service_", current_stream_prefix="service_")


def test_changing_an_existing_prefix_is_still_refused(cannot_rename) -> None:
    """Editing the value is asking for the rename, and the rename still cannot
    happen. Grandfathering a stored value must not grandfather the field."""
    with pytest.raises(ValidationError):
        _reject_unsupported_naming(None, "svc_", current_stream_prefix="service_")


def test_one_unchanged_field_does_not_excuse_the_other(cannot_rename) -> None:
    """A save that keeps the stored prefix and adds a namespace format is still
    asking for something new, and only the new field should be named."""
    with pytest.raises(ValidationError) as caught:
        _reject_unsupported_naming(
            "${SOURCE_NAMESPACE}", "service_",
            current_stream_prefix="service_", current_namespace_format=None,
        )
    assert caught.value.details["unsupported_fields"] == ["namespace_format"]


def test_the_message_names_the_way_out(cannot_rename) -> None:
    """A refusal that does not say what to do instead is a dead end. Two
    pipelines on one warehouse are separated by a schema each."""
    with pytest.raises(ValidationError) as caught:
        _reject_unsupported_naming(None, "svc_")
    assert "schema" in str(caught.value)


# ── the screen and the API must agree ─────────────────────────────────────

def test_what_is_offered_is_exactly_what_is_accepted() -> None:
    """One predicate decides both whether the frontend draws the field and
    whether the API accepts it. Two would drift, and the drift shows up as a
    form nobody can submit -- which is exactly how this was reported, with a
    screenshot of `to_1` typed into a box that could only ever fail."""
    try:
        _reject_unsupported_naming(None, "svc_")
        accepted = True
    except ValidationError:
        accepted = False
    assert settings.supports_destination_naming == accepted


def test_they_still_agree_where_the_engine_cannot_rename(cannot_rename) -> None:
    try:
        _reject_unsupported_naming(None, "svc_")
        accepted = True
    except ValidationError:
        accepted = False
    assert settings.supports_destination_naming == accepted is False


def test_the_default_capability_is_permissive() -> None:
    """An API older than the field sends nothing, and the frontend falls back
    to this. Hiding a field that works is the worse of the two mistakes."""
    from app.schemas.domain import EngineCapabilities
    assert EngineCapabilities().destination_naming is True
