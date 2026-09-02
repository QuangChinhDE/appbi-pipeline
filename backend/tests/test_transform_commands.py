"""The command contract: what can be run, and what argv it becomes.

These are the checks that stand between a browser and a subprocess. A selector
is user input that reaches an exec call, so the properties asserted here --
closed command set, no shell, no interpolation, argv as a list -- are the ones
that make "the FE looks like a dbt command bar" safe to offer at all.
"""

from __future__ import annotations

import unittest

from app.core.errors import ValidationError
from app.transforms.runtime.commands import (
    COMMANDS, PRODUCTION_ALLOWED, SCHEDULABLE, build_argv, validate_command,
)


def argv_for(command, **kwargs):
    return build_argv(
        validate_command(command, **kwargs),
        target="dev",
        profiles_dir="/w/profiles",
        project_dir="/w/project",
        target_path="/w/target",
    )


class CommandValidation(unittest.TestCase):
    def test_unknown_command_is_rejected(self):
        with self.assertRaises(ValidationError) as caught:
            validate_command("drop-database")
        self.assertEqual(caught.exception.code, "TRANSFORM_COMMAND_UNKNOWN")

    def test_every_command_declares_a_dbt_subcommand(self):
        for name, spec in COMMANDS.items():
            self.assertTrue(spec.argv, f"{name} has no argv")
            self.assertEqual(name, spec.name)

    def test_shell_metacharacters_survive_as_one_argv_element(self):
        # Not rejected -- there is no shell, so this is simply a selector that
        # matches nothing. What matters is that it stays a single element and
        # never becomes a second command.
        argv = argv_for("build", selector="fct_orders; rm -rf /")
        self.assertIn("fct_orders; rm -rf /", argv)
        self.assertEqual(argv.count("--select"), 1)
        self.assertNotIn("rm", argv)

    def test_selector_cannot_start_with_a_dash(self):
        with self.assertRaises(ValidationError):
            validate_command("build", selector="--profiles-dir /etc")

    def test_selector_rejects_control_characters(self):
        with self.assertRaises(ValidationError):
            validate_command("build", selector="fct\x00orders")
        with self.assertRaises(ValidationError):
            validate_command("build", selector="fct\norders")

    def test_selector_length_is_bounded(self):
        with self.assertRaises(ValidationError):
            validate_command("build", selector="a" * 1001)

    def test_graph_operators_are_passed_through_untouched(self):
        # dbt owns node-selection semantics. Re-implementing any of it here
        # would produce a different graph from the one that actually runs.
        for selector in ("+fct_orders", "fct_orders+", "+fct_orders+",
                         "tag:daily", "path:models/marts", "resource_type:model",
                         "@fct_orders", "fct_orders stg_orders"):
            with self.subTest(selector=selector):
                argv = argv_for("build", selector=selector)
                self.assertIn(selector, argv)

    def test_full_refresh_only_where_dbt_supports_it(self):
        self.assertIn("--full-refresh", argv_for("build", full_refresh=True))
        with self.assertRaises(ValidationError):
            validate_command("compile", full_refresh=True)

    def test_parse_takes_no_selector(self):
        with self.assertRaises(ValidationError):
            validate_command("parse", selector="fct_orders")

    def test_run_operation_requires_a_macro_name(self):
        with self.assertRaises(ValidationError):
            validate_command("run-operation")
        with self.assertRaises(ValidationError):
            validate_command("run-operation", macro="drop table; --")
        command = validate_command("run-operation", macro="grant_select")
        self.assertTrue(command.privileged)

    def test_macro_rejected_on_other_commands(self):
        with self.assertRaises(ValidationError):
            validate_command("build", macro="anything")

    def test_var_names_must_be_identifiers(self):
        with self.assertRaises(ValidationError):
            validate_command("build", vars={"a b": 1})
        # Values keep their JSON type: a var may legitimately be a list.
        command = validate_command("build", vars={"regions": ["vn", "sg"]})
        self.assertEqual(command.vars["regions"], ["vn", "sg"])

    def test_preview_limit_is_bounded(self):
        with self.assertRaises(ValidationError):
            validate_command("show", limit=0)
        with self.assertRaises(ValidationError):
            validate_command("show", limit=100_000)

    def test_selector_and_named_selector_are_exclusive(self):
        with self.assertRaises(ValidationError):
            validate_command("build", selector="x", selector_name="nightly")


class ArgvConstruction(unittest.TestCase):
    def test_every_element_is_a_string(self):
        argv = argv_for("build", selector="+fct_orders", full_refresh=True,
                        vars={"day": "2026-01-01"})
        self.assertTrue(all(isinstance(item, str) for item in argv))

    def test_directories_are_explicit(self):
        argv = argv_for("parse")
        for flag, value in (
            ("--project-dir", "/w/project"),
            ("--profiles-dir", "/w/profiles"),
            ("--target-path", "/w/target"),
            ("--target", "dev"),
        ):
            self.assertIn(flag, argv)
            self.assertEqual(argv[argv.index(flag) + 1], value)

    def test_source_freshness_is_two_words(self):
        argv = argv_for("source-freshness")
        self.assertEqual(argv[4:6], ["source", "freshness"])

    def test_show_asks_for_json_and_excludes_indirect_tests(self):
        argv = argv_for("show", selector="fct_orders", limit=50)
        self.assertIn("--output", argv)
        self.assertEqual(argv[argv.index("--output") + 1], "json")
        # Without this dbt also selects the tests attached to the model and
        # then refuses, because a test has nothing to show.
        self.assertIn("--indirect-selection", argv)
        self.assertEqual(argv[argv.index("--indirect-selection") + 1], "empty")

    def test_vars_are_json_encoded_once(self):
        argv = argv_for("build", vars={"day": "2026-01-01"})
        self.assertEqual(argv[argv.index("--vars") + 1], '{"day":"2026-01-01"}')

    def test_target_name_is_validated(self):
        with self.assertRaises(ValidationError):
            build_argv(
                validate_command("parse"), target="dev; rm -rf /",
                profiles_dir="/w/p", project_dir="/w/j", target_path="/w/t",
            )


class CommandClassification(unittest.TestCase):
    def test_reads_do_not_take_the_write_lock(self):
        for name in ("parse", "compile", "show", "ls", "docs-generate"):
            self.assertFalse(COMMANDS[name].writes, name)

    def test_writes_take_the_write_lock(self):
        for name in ("build", "run", "seed", "snapshot", "test"):
            self.assertTrue(COMMANDS[name].writes, name)

    def test_privileged_commands_are_the_ones_that_execute_arbitrary_sql(self):
        privileged = {name for name, spec in COMMANDS.items() if spec.privileged}
        self.assertEqual(privileged, {"run-operation", "clone"})

    def test_run_operation_cannot_be_scheduled(self):
        # Unattended execution of an arbitrary macro is not something a cron
        # entry should be able to arrange.
        self.assertNotIn("run-operation", SCHEDULABLE)
        self.assertNotIn("clone", SCHEDULABLE)

    def test_production_allows_ordinary_work_but_not_arbitrary_macros(self):
        self.assertIn("build", PRODUCTION_ALLOWED)
        self.assertNotIn("run-operation", PRODUCTION_ALLOWED)


if __name__ == "__main__":
    unittest.main()
