"""Generate a canonical, exportable dbt project from AppBI product models."""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import yaml

from app.core.errors import ValidationError
from app.models.transform import Transform, TransformInput, TransformModel

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def require_identifier(value: str, label: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise ValidationError(
            f"{label} must start with a letter or underscore and contain only letters, "
            "numbers, and underscores."
        )
    return value


def project_name(transform: Transform) -> str:
    return f"appbi_transform_{str(transform.id).replace('-', '')[:12]}"


def _model_config(model: TransformModel, transform: Transform) -> dict[str, Any]:
    config: dict[str, Any] = {
        "materialized": model.materialization.lower(),
    }
    if model.output_schema:
        config["schema"] = model.output_schema
    if model.relation_name:
        config["alias"] = model.relation_name
    if model.tags:
        config["tags"] = model.tags
    if model.materialization == "INCREMENTAL":
        for key in ("unique_key", "incremental_strategy", "on_schema_change"):
            value = (model.config_json or {}).get(key)
            if value not in (None, ""):
                config[key] = value
    # Warehouse-specific physical layout. On BigQuery an unpartitioned mart is a
    # recurring bill rather than a one-off mistake, so these have to be reachable
    # even though the product does not model them field by field.
    for key in ("partition_by", "cluster_by", "labels", "require_partition_filter",
                "partition_expiration_days"):
        value = (model.config_json or {}).get(key)
        if value not in (None, "", [], {}):
            config[key] = value
    return config


def _config_block(config: dict[str, Any]) -> str:
    args = ",\n    ".join(f"{key}={json.dumps(value)}" for key, value in config.items())
    return "{{ config(\n    " + args + "\n) }}\n\n"


def _sources(inputs: list[TransformInput]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str | None, str], list[dict[str, Any]]] = defaultdict(list)
    for item in inputs:
        asset = item.asset
        # A warehouse table may be called `Orders-2024`, which is not something
        # `{{ source(...) }}` can name. dbt separates the two for exactly this:
        # `name` is the reference, `identifier` is the real relation.
        reference = re.sub(r"[^A-Za-z0-9_]", "_", asset.relation_name)
        if not reference or not (reference[0].isalpha() or reference[0] == "_"):
            reference = f"t_{reference}"
        table: dict[str, Any] = {
            "name": reference,
            "description": (
                f"Managed AppBI input asset {asset.id}; physical identity "
                f"{asset.physical_identity}."
            ),
            "columns": [
                {"name": column.get("name"), "description": column.get("description", "")}
                for column in (asset.schema_metadata or {}).get("columns", [])
                if column.get("name")
            ],
        }
        if reference != asset.relation_name:
            table["identifier"] = asset.relation_name
        grouped[(item.source_name, asset.catalog_name, asset.schema_name)].append(table)
    sources = []
    for (name, database, schema), tables in grouped.items():
        entry: dict[str, Any] = {"name": name, "schema": schema, "tables": tables}
        if database:
            entry["database"] = database
        sources.append(entry)
    return sources


def _tests_yaml(models: list[TransformModel]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for model in models:
        columns: dict[str, list[Any]] = defaultdict(list)
        for test in model.tests:
            if test.deleted_at is not None or not test.column_name:
                continue
            severity = test.severity.lower()
            rule = test.rule.upper()
            config: dict[str, Any] = {"severity": severity}
            # `where` scopes a test to a slice of the table. On BigQuery that is
            # the difference between testing today's partition and paying to
            # scan the whole history on every build.
            where = (test.config_json or {}).get("where")
            if where:
                config["where"] = where
            if rule == "NOT_NULL":
                spec: Any = {"not_null": {"config": config}}
            elif rule == "UNIQUE":
                spec = {"unique": {"config": config}}
            elif rule == "ACCEPTED_VALUES":
                # dbt 1.12 wants generic-test arguments under `arguments:`;
                # leaving them at the top level still runs but deprecation-warns
                # on every build and stops working in a later release.
                spec = {"accepted_values": {
                    "arguments": {"values": (test.config_json or {}).get("values", [])},
                    "config": config,
                }}
            elif rule == "RELATIONSHIPS":
                target = (test.config_json or {}).get("to")
                field = (test.config_json or {}).get("field")
                if not target or not field:
                    continue
                spec = {"relationships": {
                    "arguments": {
                        "to": "{{ ref('" + str(target) + "') }}",
                        "field": field,
                    },
                    "config": config,
                }}
            else:
                continue
            columns[test.column_name].append(spec)
        entry: dict[str, Any] = {"name": model.name}
        if model.description:
            entry["description"] = model.description
        if columns:
            entry["columns"] = [
                {"name": column, "data_tests": specs} for column, specs in columns.items()
            ]
        entries.append(entry)
    return {"version": 2, "models": entries}


# `graph.sources` only carries sources some model actually references, so the
# relations to read-probe are passed in explicitly -- an input attached to the
# Transform but not yet used in SQL must still be verified. Likewise every
# distinct output schema is write-probed, not just the Transform default, or a
# per-model schema override validates green and fails at build time.
VALIDATION_MACRO = """{% macro appbi_validate_write(schema_names, relations) %}
  {% for relation in relations %}
    {% set probe_relation = api.Relation.create(
        database=relation.get('database') or target.database,
        schema=relation['schema'],
        identifier=relation['identifier'],
    ) %}
    {% call statement('appbi_read_probe_' ~ loop.index, fetch_result=True) %}
      select * from {{ probe_relation }} limit 1
    {% endcall %}
  {% endfor %}
  {% for schema_name in schema_names %}
    {% set schema_relation = api.Relation.create(database=target.database, schema=schema_name) %}
    {% do adapter.create_schema(schema_relation) %}
    {% set existing_probe = adapter.get_relation(database=target.database, schema=schema_name, identifier='__appbi_transform_probe') %}
    {% if existing_probe %}
      {% do adapter.drop_relation(existing_probe) %}
    {% endif %}
    {% set probe = api.Relation.create(database=target.database, schema=schema_name, identifier='__appbi_transform_probe', type='table') %}
    {% call statement('appbi_write_probe_' ~ loop.index, fetch_result=False) %}
      create table {{ probe }} as select 1 as appbi_probe
    {% endcall %}
    {% do adapter.drop_relation(probe) %}
  {% endfor %}
{% endmacro %}
"""

SCHEMA_MACRO = """{% macro generate_schema_name(custom_schema_name, node) -%}
  {%- if custom_schema_name is none -%}
    {{ target.schema }}
  {%- else -%}
    {{ custom_schema_name | trim }}
  {%- endif -%}
{%- endmacro %}
"""


@dataclass(slots=True)
class GeneratedProject:
    name: str
    files: dict[str, str]

    def export_zip(self) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for path, content in sorted(self.files.items()):
                archive.writestr(f"{self.name}/{path}", content)
        return output.getvalue()


def generate_project(
    transform: Transform,
    models: list[TransformModel],
    inputs: list[TransformInput],
) -> GeneratedProject:
    require_identifier(transform.default_schema, "Output schema")
    name = project_name(transform)
    project = {
        "name": name,
        "version": "1.0.0",
        "config-version": 2,
        "profile": "appbi_runtime",
        # An export run against an older dbt fails deep inside compilation with
        # a confusing message; this makes the version requirement the first
        # thing dbt checks.
        "require-dbt-version": [">=1.12.0", "<1.13.0"],
        "model-paths": ["models"],
        "macro-paths": ["macros"],
        "test-paths": ["tests"],
        "seed-paths": ["seeds"],
        "snapshot-paths": ["snapshots"],
        "target-path": "target",
        "clean-targets": ["target", "dbt_packages"],
        # Folder defaults, the way a dbt project is normally organised. Each
        # model still writes its own `materialized`, so these are the fallback a
        # reader expects to find rather than the operative setting.
        "models": {
            name: {
                "staging": {"+materialized": "view"},
                "core": {"+materialized": "table"},
                "mart": {"+materialized": "table"},
            },
        },
    }
    files: dict[str, str] = {
        "dbt_project.yml": yaml.safe_dump(project, sort_keys=False),
        "models/sources.yml": yaml.safe_dump(
            {"version": 2, "sources": _sources(inputs)}, sort_keys=False, allow_unicode=True,
        ),
        "models/schema.yml": yaml.safe_dump(
            _tests_yaml(models), sort_keys=False, allow_unicode=True,
        ),
        "macros/appbi_validation.sql": VALIDATION_MACRO,
        "macros/generate_schema_name.sql": SCHEMA_MACRO,
        "README.md": (
            f"# {transform.name}\n\nExported from AppBI. Configure the `appbi_runtime` "
            "profile locally before running dbt. No credentials are included.\n"
        ),
    }
    for model in models:
        require_identifier(model.name, "Model name")
        layer = model.layer.lower()
        files[f"models/{layer}/{model.name}.sql"] = (
            _config_block(_model_config(model, transform)) + model.sql.rstrip() + "\n"
        )
    return GeneratedProject(name=name, files=files)
