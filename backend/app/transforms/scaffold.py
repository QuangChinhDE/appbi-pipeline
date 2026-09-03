"""Starter files for a new managed project.

This is all that remains of V1's ``generate_project``, and the difference is the
whole point of the rework: it runs **once**, when a project is created, and then
the files are the project.  Nothing regenerates them, nothing reconciles them
against database rows, and a person editing `dbt_project.yml` afterwards is
editing the truth rather than something that will be overwritten.

The starter is a real dbt project a dbt engineer would recognise: standard
directory layout, `staging`/`marts` folders, an example model with a `ref`, a
schema YAML with tests, and nothing AppBI-specific in it.  A project cloned out
of AppBI on day one should look like a project somebody started with `dbt init`.
"""

from __future__ import annotations

import re

import yaml

from app.core.errors import ValidationError

IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def validate_project_name(value: str) -> str:
    """dbt project names are Python-ish identifiers, lower case.

    Checked here rather than left to dbt because the failure otherwise arrives
    from deep inside `dbt parse` as a message about a package name, minutes
    after the person typed it.
    """
    name = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not IDENTIFIER.match(name):
        raise ValidationError(
            "A dbt project name must start with a letter or underscore and contain "
            "only lower-case letters, numbers and underscores.",
            code="TRANSFORM_PROJECT_NAME_INVALID", details={"value": value},
        )
    return name


def validate_schema_name(value: str, label: str = "Schema") -> str:
    name = (value or "").strip()
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$", name):
        raise ValidationError(
            f"{label} must start with a letter or underscore and contain only "
            "letters, numbers and underscores.",
            code="TRANSFORM_SCHEMA_NAME_INVALID", details={"value": value},
        )
    return name


def dbt_project_yml(
    *, project_name: str, profile_name: str = "appbi_runtime",
    dbt_version_range: tuple[str, str] = (">=1.12.0", "<1.13.0"),
) -> str:
    """A standard `dbt_project.yml`.

    The folder-level materialisations are the convention a dbt reader expects to
    find, and each model still declares its own `materialized`, so these are a
    default rather than the operative setting.
    """
    document = {
        "name": project_name,
        "version": "1.0.0",
        "config-version": 2,
        "profile": profile_name,
        # Without this, running against an older dbt fails deep inside
        # compilation with a message that names nothing useful.
        "require-dbt-version": list(dbt_version_range),
        "model-paths": ["models"],
        "macro-paths": ["macros"],
        "test-paths": ["tests"],
        "seed-paths": ["seeds"],
        "snapshot-paths": ["snapshots"],
        "analysis-paths": ["analyses"],
        "target-path": "target",
        "clean-targets": ["target", "dbt_packages"],
        "models": {
            project_name: {
                "staging": {"+materialized": "view"},
                "marts": {"+materialized": "table"},
            },
        },
    }
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


_EXAMPLE_STAGING = """\
-- A staging model: one source table, renamed and lightly typed.
--
-- `source()` points at an entry in `_sources.yml`.  Change that entry to a
-- table your warehouse actually has, then press Preview.

{{ config(materialized='view') }}

select
    1 as id,
    'example' as name,
    current_timestamp as loaded_at
"""

_EXAMPLE_MART = """\
-- A mart model, built from the staging model above via `ref()`.
--
-- dbt reads `ref()` to work out that this model depends on `stg_example`, which
-- is why the lineage graph and the build order are correct without anybody
-- declaring them.

{{ config(materialized='table') }}

with staged as (
    select * from {{ ref('stg_example') }}
)

select
    id,
    name,
    loaded_at
from staged
"""

_STAGING_YML = """\
version: 2

models:
  - name: stg_example
    description: >
      Replace this with your own staging model. Anything you write in this file
      is the project -- AppBI does not regenerate it.
    columns:
      - name: id
        description: Primary key.
        data_tests:
          - not_null
          - unique
"""

_MARTS_YML = """\
version: 2

models:
  - name: example_mart
    description: An example mart built from stg_example.
    columns:
      - name: id
        description: Primary key, carried through from staging.
        data_tests:
          - not_null
          - unique
"""


def _sources_yml(schema: str) -> str:
    document = {
        "version": 2,
        "sources": [{
            "name": "raw",
            "description": (
                "Point this at the schema your data lands in. If an AppBI "
                "Pipeline loads it, Transform will show that link on the source."
            ),
            "schema": schema,
            "tables": [{"name": "example_table"}],
        }],
    }
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


_GITIGNORE = """\
target/
dbt_packages/
logs/
"""


def _readme(project_name: str, display_name: str) -> str:
    return f"""\
# {display_name}

A dbt project. `{project_name}` is its dbt project name.

This is a standard dbt project: the files here are the source of truth. You can
clone it out, run `dbt build` locally against your own `profiles.yml`, commit it
to Git, and bring it back -- nothing in it is specific to AppBI.

## Layout

```
models/staging/   one model per source table, renamed and typed
models/marts/     models the business reads
macros/           reusable Jinja
tests/            singular tests (a query that should return no rows)
seeds/            small CSVs loaded with `dbt seed`
snapshots/        slowly-changing-dimension snapshots
```

## Running it outside AppBI

Define a profile named as `dbt_project.yml` says, then:

```
dbt deps
dbt build
```

No credentials are included in this project. AppBI keeps them separately and
builds a profile at run time.
"""


def starter_files(
    *,
    project_name: str,
    display_name: str,
    source_schema: str = "raw",
    profile_name: str = "appbi_runtime",
    with_examples: bool = True,
) -> dict[str, bytes]:
    """The file set a new managed project starts life with.

    ``with_examples=False`` produces the bare minimum -- a project file, the
    directory markers and a README -- for somebody who would rather start from
    an empty project than delete two example models.
    """
    name = validate_project_name(project_name)
    files: dict[str, str] = {
        "dbt_project.yml": dbt_project_yml(
            project_name=name, profile_name=profile_name,
        ),
        "README.md": _readme(name, display_name),
        ".gitignore": _GITIGNORE,
        # `packages.yml` present but empty is a hint that packages are a normal
        # thing to add here, and makes `dbt deps` a no-op rather than an error.
        "packages.yml": yaml.safe_dump({"packages": []}, sort_keys=False),
    }
    if with_examples:
        files.update({
            "models/staging/_sources.yml": _sources_yml(source_schema),
            "models/staging/_staging.yml": _STAGING_YML,
            "models/staging/stg_example.sql": _EXAMPLE_STAGING,
            "models/marts/_marts.yml": _MARTS_YML,
            "models/marts/example_mart.sql": _EXAMPLE_MART,
        })
    else:
        files["models/.gitkeep"] = ""
    for directory in ("macros", "tests", "seeds", "snapshots", "analyses"):
        files[f"{directory}/.gitkeep"] = ""
    return {path: content.encode("utf-8") for path, content in files.items()}


# ── file templates offered in the editor ──────────────────────────────────

#: What "New file" offers, so a beginner is not handed an empty buffer.
#:
#: Each is a real dbt idiom rather than a comment telling somebody to go and
#: read the docs.
TEMPLATES: dict[str, dict[str, str]] = {
    "model_staging": {
        "label": "Staging model",
        "path": "models/staging/stg_new_model.sql",
        "content": (
            "{{ config(materialized='view') }}\n\n"
            "with source as (\n"
            "    select * from {{ source('raw', 'your_table') }}\n"
            ")\n\n"
            "select\n"
            "    *\n"
            "from source\n"
        ),
    },
    "model_mart": {
        "label": "Mart model",
        "path": "models/marts/new_mart.sql",
        "content": (
            "{{ config(materialized='table') }}\n\n"
            "select\n"
            "    *\n"
            "from {{ ref('stg_new_model') }}\n"
        ),
    },
    "model_incremental": {
        "label": "Incremental model",
        "path": "models/marts/new_incremental.sql",
        "content": (
            "{{ config(\n"
            "    materialized='incremental',\n"
            "    unique_key='id',\n"
            "    on_schema_change='append_new_columns'\n"
            ") }}\n\n"
            "select\n"
            "    *\n"
            "from {{ ref('stg_new_model') }}\n\n"
            "{% if is_incremental() %}\n"
            "  -- Only the rows that arrived since the last build.\n"
            "  where loaded_at > (select coalesce(max(loaded_at), '1900-01-01') from {{ this }})\n"
            "{% endif %}\n"
        ),
    },
    "schema_yml": {
        "label": "Model documentation and tests",
        "path": "models/_new_schema.yml",
        "content": (
            "version: 2\n\n"
            "models:\n"
            "  - name: your_model\n"
            "    description: What this model is for.\n"
            "    columns:\n"
            "      - name: id\n"
            "        description: Primary key.\n"
            "        data_tests:\n"
            "          - not_null\n"
            "          - unique\n"
        ),
    },
    "sources_yml": {
        "label": "Source definition",
        "path": "models/_new_sources.yml",
        "content": (
            "version: 2\n\n"
            "sources:\n"
            "  - name: raw\n"
            "    schema: raw\n"
            "    freshness:\n"
            "      warn_after: {count: 12, period: hour}\n"
            "      error_after: {count: 24, period: hour}\n"
            "    loaded_at_field: loaded_at\n"
            "    tables:\n"
            "      - name: your_table\n"
        ),
    },
    "singular_test": {
        "label": "Singular test",
        "path": "tests/assert_new_condition.sql",
        "content": (
            "-- A singular test passes when it returns no rows.\n\n"
            "select\n"
            "    id\n"
            "from {{ ref('your_model') }}\n"
            "where id is null\n"
        ),
    },
    "macro": {
        "label": "Macro",
        "path": "macros/new_macro.sql",
        "content": (
            "{% macro cents_to_dollars(column_name, decimal_places=2) %}\n"
            "    round(1.0 * {{ column_name }} / 100, {{ decimal_places }})\n"
            "{% endmacro %}\n"
        ),
    },
    "snapshot": {
        "label": "Snapshot",
        "path": "snapshots/new_snapshot.sql",
        "content": (
            "{% snapshot your_snapshot %}\n\n"
            "{{ config(\n"
            "    target_schema='snapshots',\n"
            "    unique_key='id',\n"
            "    strategy='timestamp',\n"
            "    updated_at='updated_at'\n"
            ") }}\n\n"
            "select * from {{ source('raw', 'your_table') }}\n\n"
            "{% endsnapshot %}\n"
        ),
    },
    "analysis": {
        "label": "Analysis",
        "path": "analyses/new_analysis.sql",
        "content": (
            "-- Compiled by dbt but never materialised. Useful for a query you\n"
            "-- want version-controlled and Jinja-templated without a table.\n\n"
            "select\n"
            "    *\n"
            "from {{ ref('your_model') }}\n"
        ),
    },
    "empty": {
        "label": "Empty file",
        "path": "models/new_file.sql",
        "content": "",
    },
}


# ── generating a staging model from a real source table ───────────────────
#
# The wizard behind this exists because a dbt project's first hour is the part
# a non-specialist cannot do: you must know that a staging model is a `select`
# over a `source()`, that the source has to be declared in YAML before `ref`
# will resolve it, and that tests live in a third place again. Someone who
# knows their own data still has to learn dbt's filing system before they can
# express what they already know.
#
# So the form asks only what a person knows -- which table, which columns,
# view or table, which columns are unique and which are never empty -- and this
# writes what dbt requires. What comes out is ordinary dbt: a `.sql` file and
# YAML entries, in the conventional places, editable by hand afterwards. That
# is the whole point of generating rather than hiding.

#: Identifiers dbt and every warehouse accept without quoting.
_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")


def validate_identifier(value: str, *, field: str) -> str:
    name = (value or "").strip().lower()
    if not _IDENT.fullmatch(name):
        raise ValidationError(
            f"{field} chỉ gồm chữ thường, số và gạch dưới, và bắt đầu bằng chữ.",
            code="TRANSFORM_INVALID_NAME",
        )
    return name


def _yaml_block(payload: dict, indent: int) -> str:
    """`yaml.safe_dump` output re-indented to sit inside a larger document."""
    text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).rstrip("\n")
    pad = " " * indent
    return "\n".join(pad + line if line else line for line in text.splitlines())


def source_yaml_entry(
    *, source_name: str, schema_name: str, table_name: str,
    columns: list[dict], description: str | None = None,
) -> dict:
    """One `sources:` entry, shaped the way dbt expects to read it."""
    table: dict = {"name": table_name}
    if description:
        table["description"] = description
    column_entries = [
        {
            "name": column["name"],
            **({"data_tests": tests} if (tests := _tests_for(column)) else {}),
        }
        for column in columns
        if _tests_for(column)
    ]
    if column_entries:
        table["columns"] = column_entries
    return {
        "name": source_name,
        "schema": schema_name,
        "tables": [table],
    }


def _tests_for(column: dict) -> list[str]:
    tests: list[str] = []
    if column.get("unique"):
        tests.append("unique")
    if column.get("not_null"):
        tests.append("not_null")
    return tests


def staging_model_sql(
    *, source_name: str, table_name: str, columns: list[dict], materialized: str,
) -> str:
    """The `select` itself.

    Columns are emitted in the order given, which is the warehouse's own
    ordinal order, so the generated file reads like the table it came from.
    A column may carry an `alias`; renaming at the staging layer is the most
    common single edit and doing it here saves the round trip.
    """
    if materialized not in ("view", "table"):
        raise ValidationError(
            "Kiểu materialization phải là view hoặc table.",
            code="TRANSFORM_INVALID_MATERIALIZATION",
        )
    if not columns:
        raise ValidationError(
            "Chọn ít nhất một cột.", code="TRANSFORM_NO_COLUMNS",
        )

    width = max(len(column["name"]) for column in columns)
    lines = []
    for column in columns:
        name = column["name"]
        alias = (column.get("alias") or "").strip()
        if alias and alias != name:
            lines.append(f"    {name.ljust(width)} as {alias},")
        else:
            lines.append(f"    {name},")
    lines[-1] = lines[-1].rstrip(",")

    return (
        f"-- Staging model cho bảng `{table_name}`.\n"
        "--\n"
        "-- Tầng staging chỉ đổi tên và chọn cột, không đổi ý nghĩa dữ liệu.\n"
        "-- Mọi phép tính, join hay tổng hợp nên nằm ở tầng marts.\n"
        f"{{{{ config(materialized='{materialized}') }}}}\n"
        "\n"
        "select\n"
        + "\n".join(lines) + "\n"
        f"from {{{{ source('{source_name}', '{table_name}') }}}}\n"
    )


def model_yaml_entry(*, model_name: str, columns: list[dict],
                     description: str | None = None) -> dict:
    """The `models:` entry carrying the tests the form asked for."""
    entry: dict = {"name": model_name}
    if description:
        entry["description"] = description
    column_entries = [
        {
            "name": (column.get("alias") or column["name"]),
            **({"data_tests": tests} if (tests := _tests_for(column)) else {}),
        }
        for column in columns
        if _tests_for(column)
    ]
    if column_entries:
        entry["columns"] = column_entries
    return entry
