"""Secrets that live below the top level of a connector spec.

Reported from the UI: a BigQuery / Google Sheets key was entered and saved, the
database held it, and coming back to the form showed an empty box -- no dots,
no sign anything was stored. The person cannot tell whether the connection has
a key at all.

`destination-bigquery` puts `credentials_json` at the top level, which worked.
`source-google-sheets` and `destination-google-sheets` put *every* secret
inside `credentials`, a `oneOf` over OAuth and a service account. The split
that encrypts them already walks to any depth. Two things around it did not:

  * what the API tells the form is stored (`field_names`, top-level keys only),
  * and what an update does to a branch it was handed only part of.

The second is the dangerous one, so it is tested here as carefully as the
first: a shallow `{**stored, **incoming}` replaces a whole branch, so saving
the form after re-entering one key of a pair drops the other.
"""

from __future__ import annotations

import pytest

from app.services.catalog import (
    apply_secret_updates,
    configured_secret_fields,
    secret_paths,
    split_configuration,
    strip_unchanged_secrets,
)

#: The shape both Google Sheets connectors use, trimmed to what matters.
SHEETS_SPEC = {
    "type": "object",
    "properties": {
        "spreadsheet_id": {"type": "string"},
        "credentials": {
            "type": "object",
            "title": "Authentication",
            "oneOf": [
                {
                    "title": "Authenticate via Google (OAuth)",
                    "properties": {
                        "auth_type": {"const": "Client"},
                        "client_id": {"type": "string", "airbyte_secret": True},
                        "client_secret": {"type": "string", "airbyte_secret": True},
                        "refresh_token": {"type": "string", "airbyte_secret": True},
                    },
                },
                {
                    "title": "Service Account Key Authentication",
                    "properties": {
                        "auth_type": {"const": "Service"},
                        "service_account_info": {"type": "string", "airbyte_secret": True},
                    },
                },
            ],
        },
    },
}

#: Top level, the case that already worked and must keep working.
BIGQUERY_SPEC = {
    "type": "object",
    "properties": {
        "project_id": {"type": "string"},
        "dataset_id": {"type": "string"},
        "credentials_json": {"type": "string", "airbyte_secret": True},
    },
}


# ── which fields the form is told about ───────────────────────────────────

def test_a_nested_secret_is_named_by_its_full_path() -> None:
    """The reported bug. The form looks its fields up by path; being told only
    `credentials` meant `credentials.service_account_info` matched nothing and
    rendered as an empty box beside a database that held the key."""
    assert "credentials.service_account_info" in secret_paths(SHEETS_SPEC)


def test_every_branch_of_a_oneof_is_covered() -> None:
    paths = set(secret_paths(SHEETS_SPEC))
    assert paths == {
        "credentials.client_id",
        "credentials.client_secret",
        "credentials.refresh_token",
        "credentials.service_account_info",
    }


def test_a_top_level_secret_is_still_named() -> None:
    assert secret_paths(BIGQUERY_SPEC) == ["credentials_json"]


def test_a_spec_with_no_secrets_names_none() -> None:
    assert secret_paths({"properties": {"host": {"type": "string"}}}) == []


def test_a_stored_path_is_reported_as_configured() -> None:
    fields = configured_secret_fields(SHEETS_SPEC, ["credentials.service_account_info"])
    assert fields["credentials.service_account_info"] == "********"


def test_an_unstored_path_is_not_reported() -> None:
    """Claiming a field is stored when it is not would leave somebody looking
    at dots for a credential that does not exist, wondering why check fails."""
    fields = configured_secret_fields(SHEETS_SPEC, ["credentials.service_account_info"])
    assert "credentials.client_id" not in fields


def test_a_record_written_before_paths_existed_still_resolves() -> None:
    """Older records name the root key only. Expanding it beats showing an
    empty box; the next save records exact paths."""
    fields = configured_secret_fields(SHEETS_SPEC, ["credentials"])
    assert "credentials.service_account_info" in fields
    assert "credentials.client_id" in fields


def test_nothing_stored_reports_nothing() -> None:
    assert configured_secret_fields(SHEETS_SPEC, []) == {}


# ── what a save does to what is already there ─────────────────────────────

def test_re_entering_one_secret_keeps_its_siblings() -> None:
    """The data-loss path. A shallow merge replaces the whole `credentials`
    branch, so a form that resubmits one OAuth field drops the other two."""
    stored = {"credentials": {"client_id": "id-1",
                              "client_secret": "sec-1",
                              "refresh_token": "ref-1"}}
    merged = apply_secret_updates(stored, {"credentials": {"client_secret": "sec-2"}})

    assert merged["credentials"]["client_secret"] == "sec-2"
    assert merged["credentials"]["client_id"] == "id-1"
    assert merged["credentials"]["refresh_token"] == "ref-1"


def test_a_new_branch_is_added_whole() -> None:
    merged = apply_secret_updates({"a": {"x": 1}}, {"b": {"y": 2}})
    assert merged == {"a": {"x": 1}, "b": {"y": 2}}


def test_a_scalar_replaces_a_scalar() -> None:
    assert apply_secret_updates({"k": "old"}, {"k": "new"}) == {"k": "new"}


def test_nothing_incoming_changes_nothing() -> None:
    stored = {"credentials": {"service_account_info": "{...}"}}
    assert apply_secret_updates(stored, {}) == stored


# ── the mask coming back ──────────────────────────────────────────────────

@pytest.mark.parametrize("unchanged", ["", "********"])
def test_a_resubmitted_mask_is_not_a_new_value(unchanged: str) -> None:
    """The form loads showing dots. Submitting it unchanged must not write the
    dots over the key."""
    assert strip_unchanged_secrets({"credentials_json": unchanged}) == {}


@pytest.mark.parametrize("unchanged", ["", "********"])
def test_a_nested_mask_is_stripped_too(unchanged: str) -> None:
    """Only the top level was filtered, so a nested mask survived as a real
    value and overwrote the stored key with eight asterisks."""
    stripped = strip_unchanged_secrets(
        {"credentials": {"service_account_info": unchanged}}
    )
    assert stripped == {}


def test_a_real_nested_value_survives_the_strip() -> None:
    stripped = strip_unchanged_secrets(
        {"credentials": {"service_account_info": '{"type":"service_account"}'}}
    )
    assert stripped == {"credentials": {"service_account_info": '{"type":"service_account"}'}}


def test_a_masked_field_beside_a_changed_one_keeps_only_the_change() -> None:
    stripped = strip_unchanged_secrets(
        {"credentials": {"client_id": "********", "client_secret": "new-secret"}}
    )
    assert stripped == {"credentials": {"client_secret": "new-secret"}}


def test_a_branch_emptied_by_stripping_disappears() -> None:
    """Leaving `{"credentials": {}}` behind would make the caller think a
    credential was submitted, and trigger a connection check for nothing."""
    assert strip_unchanged_secrets({"credentials": {"client_id": ""}}) == {}


def test_false_and_zero_are_real_values() -> None:
    """A secret that happens to be falsy is still a secret. Filtering on
    truthiness instead of on the two sentinels would drop it."""
    assert strip_unchanged_secrets({"a": False, "b": 0}) == {"a": False, "b": 0}


# ── the whole round trip, on the shape that broke ─────────────────────────

def test_saving_a_sheets_form_without_retyping_keeps_the_key() -> None:
    """End to end over the real shape: the form comes back with the mask in
    place, the user changes the spreadsheet id, and the service account key
    must still be there afterwards."""
    stored = {"credentials": {"service_account_info": '{"type":"service_account"}'}}

    submitted = {
        "spreadsheet_id": "sheet-2",
        "credentials": {"auth_type": "Service", "service_account_info": "********"},
    }
    config, inline = split_configuration(SHEETS_SPEC, submitted)
    merged = apply_secret_updates(stored, strip_unchanged_secrets(inline))

    assert config["spreadsheet_id"] == "sheet-2"
    assert config["credentials"]["auth_type"] == "Service"
    assert merged["credentials"]["service_account_info"] == '{"type":"service_account"}'


def test_actually_changing_the_key_replaces_it() -> None:
    stored = {"credentials": {"service_account_info": "old"}}
    submitted = {"spreadsheet_id": "s", "credentials": {"auth_type": "Service",
                                                        "service_account_info": "new"}}
    _, inline = split_configuration(SHEETS_SPEC, submitted)
    merged = apply_secret_updates(stored, strip_unchanged_secrets(inline))
    assert merged["credentials"]["service_account_info"] == "new"
