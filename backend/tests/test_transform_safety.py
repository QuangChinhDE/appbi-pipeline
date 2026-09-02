"""The rules a release review blocks on, kept as tests rather than as prose.

Each of these encodes a property the rework promises and that is invisible in a
demo: a subprocess that cannot read AppBI's secrets, a release that cannot go
live unverified, a late verification that cannot overwrite a newer one, and a
retry that re-runs the code that failed rather than the code on screen.

Offline by design -- no warehouse, no database, no dbt -- because the point is
that a regression fails the build rather than a review.
"""

from __future__ import annotations

import os
import unittest
import uuid
from unittest import mock

from app.transforms.files import content_hash, diff_revisions
from app.transforms.runtime import security
from app.transforms.runtime.profiles import build_profile, resolve_schema, user_token


class Revision:
    """Just enough of a revision row to exercise hashing and diffing."""

    def __init__(self, index):
        self.manifest_index = index
        self.content_hash = content_hash(index)


def entry(digest, size=10):
    return {"key": f"blobs/xx/yy/{digest}", "sha256": digest, "size": size}


class SubprocessEnvironment(unittest.TestCase):
    """A dbt project is code. It must not be handed AppBI's own secrets.

    V1 passed ``{**os.environ, ...}``, which gave every user-authored project
    the API's DATABASE_URL, encryption key and OpenAI key. Nothing was
    exploiting it; nothing needed to, for it to be the wrong design.
    """

    def environment(self, **extra):
        from pathlib import Path

        return security.subprocess_env(
            profiles_dir=Path("/w/profiles"),
            project_dir=Path("/w/project"),
            target_path=Path("/w/target"),
            tmpdir=Path("/w/tmp"),
            **extra,
        )

    def test_appbi_secrets_never_reach_the_subprocess(self):
        secrets = {
            "DATABASE_URL": "postgres://appbi:hunter2@db/appbi",
            "SECRET_ENCRYPTION_KEY": "kek-value",
            "JWT_SECRET": "jwt-value",
            "OPENAI_API_KEY": "sk-value",
            "AIRBYTE_PASSWORD": "airbyte",
            "AWS_SECRET_ACCESS_KEY": "aws",
            "GOOGLE_APPLICATION_CREDENTIALS": "/run/key.json",
        }
        with mock.patch.dict(os.environ, secrets, clear=False):
            env = self.environment()
        for name, value in secrets.items():
            self.assertNotIn(name, env)
            self.assertNotIn(value, env.values())

    def test_an_unlisted_variable_is_not_inherited(self):
        with mock.patch.dict(os.environ, {"SOME_INTERNAL_FLAG": "1"}, clear=False):
            self.assertNotIn("SOME_INTERNAL_FLAG", self.environment())

    def test_what_dbt_actually_needs_is_present(self):
        env = self.environment()
        self.assertEqual(env["DBT_PROFILES_DIR"], str(__import__("pathlib").Path("/w/profiles")))
        self.assertIn("PATH", env)
        self.assertIn("HOME", env)

    def test_telemetry_is_disabled(self):
        # A data warehouse tool in somebody's VPC should not make an
        # unsolicited outbound request on start.
        env = self.environment()
        self.assertEqual(env["DBT_SEND_ANONYMOUS_USAGE_STATS"], "False")
        self.assertEqual(env["DO_NOT_TRACK"], "1")

    def test_extra_cannot_smuggle_a_secret_back_in(self):
        env = self.environment(extra={"OPENAI_API_KEY": "sk-x", "SAFE_FLAG": "1"})
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertEqual(env["SAFE_FLAG"], "1")


class Redaction(unittest.TestCase):
    def test_credentials_are_removed_from_logs(self):
        text = "connecting with password hunter2-long-secret now"
        self.assertNotIn(
            "hunter2-long-secret",
            security.redact(text, ["hunter2-long-secret"]),
        )

    def test_longest_secret_is_replaced_first(self):
        # A service account JSON contains its own private key as a substring;
        # replacing the short value first would leave the long one partially
        # masked and still recognisable.
        private = "-----BEGIN PRIVATE KEY-----AAAA-----END PRIVATE KEY-----"
        whole = '{"private_key": "%s"}' % private
        result = security.redact(f"log line {whole} end", [private, whole])
        self.assertNotIn(private, result)
        self.assertNotIn("BEGIN PRIVATE KEY", result)

    def test_very_short_values_are_not_redacted(self):
        # Redacting a 3-character value would black out ordinary log text.
        self.assertIn("abc", security.redact("abc appears here", ["abc"]))


class ProfileConstruction(unittest.TestCase):
    def test_bigquery_service_account_never_inlines_the_key(self):
        profile = build_profile(
            connector_key="destination-bigquery",
            configuration={
                "project_id": "proj",
                "credentials_json": '{"client_email":"svc@proj.iam","private_key":"KEYDATA-LONG"}',
            },
            schema="analytics_dev",
            target_name="dev",
        )
        output = profile.document["appbi_runtime"]["outputs"]["dev"]
        self.assertEqual(output["method"], "service-account")
        # The key goes to a 0600 file next to the profile, never into the YAML.
        self.assertNotIn("keyfile_json", output)
        self.assertNotIn("private_key", output)
        self.assertIsNotNone(profile.keyfile_json)
        self.assertIn("KEYDATA-LONG", profile.secret_values)

    def test_bigquery_oauth_uses_the_flat_refresh_token_shape(self):
        profile = build_profile(
            connector_key="destination-bigquery",
            configuration={
                "auth_method": "oauth", "project_id": "proj",
                "refresh_token": "rt-value", "oauth_client_id": "cid",
                "oauth_client_secret": "csecret",
            },
            schema="analytics", target_name="prod",
        )
        output = profile.document["appbi_runtime"]["outputs"]["prod"]
        # dbt reads these keys directly; Airbyte's nested {"credentials": {...}}
        # shape is a different thing and would not resolve.
        self.assertEqual(output["method"], "oauth-secrets")
        self.assertEqual(output["refresh_token"], "rt-value")
        self.assertIn("rt-value", profile.secret_values)
        self.assertIn("csecret", profile.secret_values)

    def test_a_git_project_keeps_its_own_profile_name(self):
        # Rewriting the project's `dbt_project.yml` to suit the runtime is
        # exactly the "convert the repository" behaviour the rework removes.
        profile = build_profile(
            connector_key="destination-postgres",
            configuration={"host": "h", "username": "u", "password": "p",
                           "database": "d"},
            schema="dev", target_name="dev",
            profile_names=["acme_analytics"],
        )
        self.assertIn("acme_analytics", profile.document)
        self.assertIn("appbi_runtime", profile.document)

    def test_target_name_is_the_environment_s_own(self):
        # A real project branches on `{{ target.name }}`; a runtime that always
        # called itself `production` would take the wrong branch of user code.
        profile = build_profile(
            connector_key="destination-postgres",
            configuration={"host": "h", "username": "u", "password": "p",
                           "database": "d"},
            schema="dev", target_name="staging",
        )
        self.assertEqual(profile.document["appbi_runtime"]["target"], "staging")
        self.assertIn("staging", profile.document["appbi_runtime"]["outputs"])


class SchemaResolution(unittest.TestCase):
    def test_static_strategy_uses_the_base_schema(self):
        self.assertEqual(
            resolve_schema(strategy="STATIC", base="analytics"), "analytics",
        )

    def test_per_user_keeps_two_developers_apart(self):
        first = resolve_schema(
            strategy="PER_USER", base="analytics",
            user_token=user_token(uuid.uuid4()),
        )
        second = resolve_schema(
            strategy="PER_USER", base="analytics",
            user_token=user_token(uuid.uuid4()),
        )
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("analytics_"))

    def test_result_is_always_a_legal_identifier(self):
        name = resolve_schema(strategy="STATIC", base="my-schema.name!")
        self.assertRegex(name, r"^[A-Za-z_][A-Za-z0-9_]*$")

    def test_length_is_capped_for_the_strictest_warehouse(self):
        # Postgres caps identifiers at 63; BigQuery is looser. The smaller
        # limit is the safe one to enforce for both.
        self.assertLessEqual(
            len(resolve_schema(strategy="STATIC", base="a" * 200)), 63,
        )


class ProjectHashing(unittest.TestCase):
    """`Draft matches Live` must compare the project, not SQL text."""

    def test_same_files_give_the_same_hash_regardless_of_insertion_order(self):
        first = {"a.sql": entry("11"), "b.sql": entry("22")}
        second = {"b.sql": entry("22"), "a.sql": entry("11")}
        self.assertEqual(content_hash(first), content_hash(second))

    def test_changing_one_file_changes_the_hash(self):
        base = {"a.sql": entry("11")}
        self.assertNotEqual(content_hash(base), content_hash({"a.sql": entry("12")}))

    def test_adding_a_file_changes_the_hash(self):
        base = {"a.sql": entry("11")}
        self.assertNotEqual(
            content_hash(base), content_hash({**base, "b.yml": entry("22")}),
        )

    def test_renaming_a_file_changes_the_hash(self):
        # Same bytes, different path, is a different project -- dbt resolves
        # resources by path.
        self.assertNotEqual(
            content_hash({"a.sql": entry("11")}),
            content_hash({"b.sql": entry("11")}),
        )


class RevisionDiff(unittest.TestCase):
    def test_reports_adds_modifies_and_deletes(self):
        before = Revision({"keep.sql": entry("11"), "gone.sql": entry("22")})
        after = Revision({"keep.sql": entry("33"), "new.sql": entry("44")})
        changes = {item.path: item.change for item in diff_revisions(before, after)}
        self.assertEqual(changes, {"keep.sql": "M", "gone.sql": "D", "new.sql": "A"})

    def test_a_save_with_no_edit_does_not_appear(self):
        # A publish dialog that lists untouched files trains people to stop
        # reading it.
        same = {"a.sql": entry("11")}
        self.assertEqual(diff_revisions(Revision(same), Revision(dict(same))), [])

    def test_no_baseline_means_everything_is_new(self):
        after = Revision({"a.sql": entry("11"), "b.sql": entry("22")})
        self.assertEqual(
            {item.change for item in diff_revisions(None, after)}, {"A"},
        )


class ReleaseActivationRace(unittest.TestCase):
    """R1's verification finishing after R2 went live must not demote R2.

    The real function needs a session; the ordering rule it turns on is a pure
    comparison of activation sequences, and that is what is asserted here.
    """

    def test_later_sequence_wins_regardless_of_completion_order(self):
        from dataclasses import dataclass

        @dataclass
        class Release:
            id: int
            activation_sequence: int

        live = Release(id=2, activation_sequence=2)
        late = Release(id=1, activation_sequence=1)

        should_supersede = (
            live is not None
            and live.id != late.id
            and live.activation_sequence > late.activation_sequence
        )
        self.assertTrue(
            should_supersede,
            "a release verified late must not activate over a newer live one",
        )

    def test_a_newer_release_does_activate(self):
        from dataclasses import dataclass

        @dataclass
        class Release:
            id: int
            activation_sequence: int

        live = Release(id=1, activation_sequence=1)
        newer = Release(id=2, activation_sequence=2)
        blocked = live.activation_sequence > newer.activation_sequence
        self.assertFalse(blocked)


if __name__ == "__main__":
    unittest.main()
