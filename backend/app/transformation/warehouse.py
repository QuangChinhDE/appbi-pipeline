"""Exact physical relation verification for supported warehouses."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from app.core.errors import ValidationError
from app.transformation import profiling


@dataclass(slots=True)
class VerifiedRelation:
    catalog_name: str | None
    schema_name: str
    relation_name: str
    relation_type: str
    columns: list[dict[str, Any]]


async def verify_relation(
    connector_key: str,
    configuration: dict[str, Any],
    *,
    catalog_name: str | None,
    schema_name: str,
    relation_name: str,
) -> VerifiedRelation:
    if connector_key == "destination-postgres":
        return await asyncio.to_thread(
            _verify_postgres, configuration, catalog_name, schema_name, relation_name,
        )
    if connector_key == "destination-bigquery":
        return await asyncio.to_thread(
            _verify_bigquery, configuration, catalog_name, schema_name, relation_name,
        )
    raise ValidationError(
        "This Destination cannot be used for Transform.",
        code="TRANSFORM_DESTINATION_UNSUPPORTED",
    )


@dataclass(slots=True)
class BrowsedRelation:
    schema_name: str
    relation_name: str
    relation_type: str
    #: None when the warehouse does not report it cheaply.
    row_count: int | None = None


async def browse_schemas(
    connector_key: str,
    configuration: dict[str, Any],
    *,
    catalog_name: str | None,
) -> list[str]:
    """Every schema in the warehouse this Destination points at.

    Transform inputs do not have to come from a Pipeline -- a dataset someone
    loaded by other means is just as valid a source. Until now the only way to
    use one was to remember its exact name and type it, which is a way of
    saying the feature existed without being usable.
    """
    if connector_key == "destination-bigquery":
        return await asyncio.to_thread(_schemas_bigquery, configuration, catalog_name)
    if connector_key == "destination-postgres":
        return await asyncio.to_thread(_schemas_postgres, configuration)
    raise ValidationError(
        "This Destination cannot be browsed.",
        code="TRANSFORM_DESTINATION_UNSUPPORTED",
    )


async def browse_relations(
    connector_key: str,
    configuration: dict[str, Any],
    *,
    catalog_name: str | None,
    schema_name: str,
) -> list[BrowsedRelation]:
    """The tables and views inside one schema."""
    if connector_key == "destination-bigquery":
        return await asyncio.to_thread(
            _relations_bigquery, configuration, catalog_name, schema_name,
        )
    if connector_key == "destination-postgres":
        return await asyncio.to_thread(_relations_postgres, configuration, schema_name)
    raise ValidationError(
        "This Destination cannot be browsed.",
        code="TRANSFORM_DESTINATION_UNSUPPORTED",
    )


def _bigquery_client(configuration: dict[str, Any], catalog_name: str | None):
    from google.cloud import bigquery
    from google.oauth2 import service_account

    raw = configuration.get("credentials_json")
    info = json.loads(raw) if isinstance(raw, str) else raw
    credentials = service_account.Credentials.from_service_account_info(info)
    project = catalog_name or configuration.get("project_id") or info.get("project_id")
    return bigquery.Client(project=project, credentials=credentials), project


def _schemas_bigquery(
    configuration: dict[str, Any], catalog_name: str | None,
) -> list[str]:
    try:
        client, _ = _bigquery_client(configuration, catalog_name)
        return sorted(item.dataset_id for item in client.list_datasets())
    except Exception as exc:
        raise ValidationError(
            "Không đọc được danh sách dataset của kho dữ liệu.",
            code="TRANSFORM_BROWSE_FAILED",
            technical_message=f"{type(exc).__name__}: {exc}",
        ) from exc


def _relations_bigquery(
    configuration: dict[str, Any], catalog_name: str | None, schema_name: str,
) -> list[BrowsedRelation]:
    try:
        client, project = _bigquery_client(configuration, catalog_name)
        out = [
            BrowsedRelation(
                schema_name=schema_name,
                relation_name=item.table_id,
                relation_type="VIEW" if item.table_type == "VIEW" else "TABLE",
            )
            for item in client.list_tables(f"{project}.{schema_name}")
        ]
    except Exception as exc:
        raise ValidationError(
            f"Không đọc được danh sách bảng trong dataset {schema_name}.",
            code="TRANSFORM_BROWSE_FAILED",
            technical_message=f"{type(exc).__name__}: {exc}",
        ) from exc
    return sorted(out, key=lambda item: item.relation_name)


def _postgres_connect(configuration: dict[str, Any]):
    import psycopg

    return psycopg.connect(
        host=configuration.get("host"),
        port=int(configuration.get("port") or 5432),
        dbname=configuration.get("database"),
        user=configuration.get("username"),
        password=configuration.get("password"),
        connect_timeout=15,
    )


def _schemas_postgres(configuration: dict[str, Any]) -> list[str]:
    try:
        with _postgres_connect(configuration) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select schema_name from information_schema.schemata "
                    "where schema_name not in ('information_schema', 'pg_catalog') "
                    "and schema_name not like 'pg_toast%' "
                    "and schema_name not like 'pg_temp%' order by 1"
                )
                return [row[0] for row in cursor.fetchall()]
    except Exception as exc:
        raise ValidationError(
            "Không đọc được danh sách schema của kho dữ liệu.",
            code="TRANSFORM_BROWSE_FAILED",
            technical_message=f"{type(exc).__name__}: {exc}",
        ) from exc


def _relations_postgres(
    configuration: dict[str, Any], schema_name: str,
) -> list[BrowsedRelation]:
    try:
        with _postgres_connect(configuration) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select table_name, table_type from information_schema.tables "
                    "where table_schema = %s order by table_name",
                    (schema_name,),
                )
                return [
                    BrowsedRelation(
                        schema_name=schema_name, relation_name=row[0],
                        relation_type="VIEW" if row[1] == "VIEW" else "TABLE",
                    )
                    for row in cursor.fetchall()
                ]
    except Exception as exc:
        raise ValidationError(
            f"Không đọc được danh sách bảng trong schema {schema_name}.",
            code="TRANSFORM_BROWSE_FAILED",
            technical_message=f"{type(exc).__name__}: {exc}",
        ) from exc


async def profile_relation(
    connector_key: str,
    configuration: dict[str, Any],
    *,
    catalog_name: str | None,
    schema_name: str,
    relation_name: str,
    columns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Measure what a table's columns actually contain.

    Runs against a bounded sample of the table, because the answer -- this
    column holds epochs, that one holds four distinct codes -- does not improve
    with a full scan, and a full scan of a fact table is a real bill.
    """
    if connector_key == "destination-postgres":
        return await asyncio.to_thread(
            _profile_postgres, configuration, catalog_name, schema_name,
            relation_name, columns,
        )
    if connector_key == "destination-bigquery":
        return await asyncio.to_thread(
            _profile_bigquery, configuration, catalog_name, schema_name,
            relation_name, columns,
        )
    raise ValidationError(
        "This Destination cannot be profiled.",
        code="TRANSFORM_DESTINATION_UNSUPPORTED",
    )


async def validate_sql(
    connector_key: str,
    configuration: dict[str, Any],
    *,
    catalog_name: str | None,
    sql: str,
) -> tuple[str | None, list[str]]:
    """Ask the warehouse whether a statement would run. None means yes.

    A drafted query that reads correctly and fails at run time is the expensive
    kind of wrong, and the failure is usually a function that exists in some
    other warehouse -- `decode`, `nvl`, `getdate`. No amount of instruction
    reliably prevents that; asking the engine does. BigQuery answers for free
    under a dry run, Postgres answers from a planner pass that is rolled back.
    """
    if connector_key == "destination-bigquery":
        return await asyncio.to_thread(_validate_bigquery, configuration, catalog_name, sql)
    if connector_key == "destination-postgres":
        return await asyncio.to_thread(_validate_postgres, configuration, sql)
    return None, []


def _validate_bigquery(
    configuration: dict[str, Any], catalog_name: str | None, sql: str,
) -> tuple[str | None, list[str]]:
    from google.cloud import bigquery
    from google.oauth2 import service_account

    raw = configuration.get("credentials_json")
    try:
        info = json.loads(raw) if isinstance(raw, str) else raw
        credentials = service_account.Credentials.from_service_account_info(info)
        project = catalog_name or configuration.get("project_id") or info.get("project_id")
        client = bigquery.Client(project=project, credentials=credentials)
        job = client.query(sql, job_config=bigquery.QueryJobConfig(
            dry_run=True, use_query_cache=False,
        ))
    except Exception as exc:  # the message is the point; it goes back to the model
        return _first_error_line(exc), []
    return None, [field.name for field in (job.schema or [])]


def _validate_postgres(
    configuration: dict[str, Any], sql: str,
) -> tuple[str | None, list[str]]:
    import psycopg

    try:
        with psycopg.connect(
            host=configuration.get("host"),
            port=int(configuration.get("port") or 5432),
            dbname=configuration.get("database"),
            user=configuration.get("username"),
            password=configuration.get("password"),
            connect_timeout=15,
        ) as connection:
            with connection.cursor() as cursor:
                # limit 0 plans and describes the statement without reading rows,
                # which is both the validity check and the output schema.
                cursor.execute(f"select * from ({sql}) as _v limit 0")
                names = [column.name for column in (cursor.description or [])]
            connection.rollback()
    except Exception as exc:
        return _first_error_line(exc), []
    return None, names


def _first_error_line(exc: Exception) -> str:
    """The one line a reader -- or a model -- can act on."""
    text = str(exc).strip()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("Location:", "Job ID:", "-----")):
            return stripped[:400]
    return f"{type(exc).__name__}: {text[:300]}"


def _assemble(
    columns: list[dict[str, Any]],
    totals: dict[str, Any],
    samples: dict[str, list[str]],
    json_keys: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Turn raw counts and samples into the profile the rest of the app reads."""
    rows = int(totals.get("_rows") or 0)
    out: list[dict[str, Any]] = []
    for index, column in enumerate(columns):
        name = column.get("name")
        if not name:
            continue
        distinct = totals.get(f"d_{index}")
        nulls = totals.get(f"n_{index}")
        values = samples.get(str(name), [])
        profile = profiling.ColumnProfile(
            name=str(name),
            data_type=str(column.get("data_type") or "unknown"),
            distinct_count=int(distinct) if distinct is not None else None,
            null_ratio=(float(nulls) / rows) if rows and nulls is not None else None,
            samples=values,
        )
        profile.unique_candidate = bool(
            rows and distinct is not None and int(distinct) == rows and not nulls
        )
        kind, notes = profiling.classify(values, profile.data_type)
        declared = profiling.kind_from_type(profile.data_type)
        if declared is not None:
            # Nothing was sampled from these, so the declared type is all there
            # is -- and it is enough to stop an author casting them by hand.
            kind, notes = declared[0], [declared[1]]
            keys = (json_keys or {}).get(str(name)) or []
            if keys:
                notes.append("Khoá cấp 1: " + ", ".join(keys) + ".")
        # A short distinct list means the values are a vocabulary -- but only if
        # they actually repeat. In a twenty-row table every column has under
        # twenty distinct values, and calling a primary key a code list would
        # send the model author looking for meanings that are not there.
        repeats = bool(
            rows and distinct is not None and int(distinct) * 2 <= rows
        )
        if profiling.looks_like_key(str(name)) and not profile.unique_candidate:
            # Repeating identifiers are references to another table, and the
            # values are opaque handles -- nothing to map, nothing to name.
            if kind in ("NUMERIC_TEXT", "FREE_TEXT", "UNKNOWN", "UUID"):
                kind = "FOREIGN_KEY"
                notes = [*notes, "Khoá tham chiếu sang bảng khác; giữ nguyên giá trị."]
        elif (repeats
                and profile.distinct_count is not None
                and 0 < profile.distinct_count <= profiling.CODED_MAX_DISTINCT
                and kind in ("FREE_TEXT", "NUMERIC_TEXT", "UNKNOWN")):
            kind = "CODED"
            notes = [*notes, "Danh sách mã cố định; ý nghĩa từng mã cần được khai báo."]
        profile.inferred_kind = kind
        profile.notes = notes
        out.append(profile.as_dict())
    return out


def _profile_postgres(
    configuration: dict[str, Any],
    catalog_name: str | None,
    schema_name: str,
    relation_name: str,
    columns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    import psycopg

    totals: dict[str, Any] = {}
    samples: dict[str, list[str]] = {}
    keys: dict[str, list[str]] = {}
    try:
        with psycopg.connect(
            host=configuration.get("host"),
            port=int(configuration.get("port") or 5432),
            dbname=configuration.get("database"),
            user=configuration.get("username"),
            password=configuration.get("password"),
            connect_timeout=15,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(profiling.build_profile_sql(
                    "postgres", None, schema_name, relation_name, columns,
                ))
                names = [description[0] for description in cursor.description]
                totals = dict(zip(names, cursor.fetchone() or ()))
                for column in columns:
                    name = column.get("name")
                    if not name or not profiling._countable(
                        str(column.get("data_type") or "")
                    ):
                        continue
                    cursor.execute(profiling.build_sample_sql(
                        "postgres", None, schema_name, relation_name, str(name),
                    ))
                    samples[str(name)] = [
                        str(row[0]) for row in cursor.fetchall() if row[0] is not None
                    ]
                for column in columns:
                    name = column.get("name")
                    declared = profiling.kind_from_type(str(column.get("data_type") or ""))
                    if not name or declared is None or declared[0] != "JSON":
                        continue
                    cursor.execute(profiling.build_json_sample_sql(
                        "postgres", None, schema_name, relation_name, str(name),
                    ))
                    found = profiling.json_keys([
                        str(row[0]) for row in cursor.fetchall() if row[0] is not None
                    ])
                    if found:
                        keys[str(name)] = found
    except Exception as exc:
        raise ValidationError(
            f"Không đọc được dữ liệu mẫu của {schema_name}.{relation_name}.",
            code="TRANSFORM_PROFILE_FAILED",
            technical_message=f"{type(exc).__name__}: {exc}",
        ) from exc
    return _assemble(columns, totals, samples, keys)


def _profile_bigquery(
    configuration: dict[str, Any],
    catalog_name: str | None,
    schema_name: str,
    relation_name: str,
    columns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    from google.cloud import bigquery
    from google.oauth2 import service_account

    raw = configuration.get("credentials_json")
    totals: dict[str, Any] = {}
    samples: dict[str, list[str]] = {}
    keys: dict[str, list[str]] = {}
    try:
        info = json.loads(raw) if isinstance(raw, str) else raw
        credentials = service_account.Credentials.from_service_account_info(info)
        project = catalog_name or configuration.get("project_id") or info.get("project_id")
        client = bigquery.Client(project=project, credentials=credentials)
        row = list(client.query(profiling.build_profile_sql(
            "bigquery", project, schema_name, relation_name, columns,
        )).result())[0]
        totals = dict(row.items())
        for column in columns:
            name = column.get("name")
            if not name or not profiling._countable(
                str(column.get("data_type") or "")
            ):
                continue
            values = client.query(profiling.build_sample_sql(
                "bigquery", project, schema_name, relation_name, str(name),
            )).result()
            samples[str(name)] = [str(item.v) for item in values if item.v is not None]
        for column in columns:
            name = column.get("name")
            declared = profiling.kind_from_type(str(column.get("data_type") or ""))
            if not name or declared is None or declared[0] != "JSON":
                continue
            raw = client.query(profiling.build_json_sample_sql(
                "bigquery", project, schema_name, relation_name, str(name),
            )).result()
            found = profiling.json_keys([str(item.v) for item in raw if item.v])
            if found:
                keys[str(name)] = found
    except Exception as exc:
        raise ValidationError(
            f"Không đọc được dữ liệu mẫu của {schema_name}.{relation_name}.",
            code="TRANSFORM_PROFILE_FAILED",
            technical_message=f"{type(exc).__name__}: {exc}",
        ) from exc
    return _assemble(columns, totals, samples, keys)


def _verify_postgres(
    configuration: dict[str, Any],
    catalog_name: str | None,
    schema_name: str,
    relation_name: str,
) -> VerifiedRelation:
    import psycopg

    try:
        with psycopg.connect(
            host=configuration.get("host"),
            port=int(configuration.get("port") or 5432),
            dbname=configuration.get("database"),
            user=configuration.get("username"),
            password=configuration.get("password"),
            connect_timeout=15,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select table_type
                    from information_schema.tables
                    where table_catalog = current_database()
                      and table_schema = %s and table_name = %s
                    """,
                    (schema_name, relation_name),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValidationError(
                        f"Relation {schema_name}.{relation_name} does not exist.",
                        code="TRANSFORM_RELATION_NOT_FOUND",
                    )
                cursor.execute(
                    """
                    select column_name, data_type, is_nullable, ordinal_position
                    from information_schema.columns
                    where table_catalog = current_database()
                      and table_schema = %s and table_name = %s
                    order by ordinal_position
                    """,
                    (schema_name, relation_name),
                )
                columns = [
                    {"name": item[0], "data_type": item[1], "nullable": item[2] == "YES",
                     "position": item[3]}
                    for item in cursor.fetchall()
                ]
        return VerifiedRelation(
            catalog_name=str(configuration.get("database") or catalog_name or "") or None,
            schema_name=schema_name,
            relation_name=relation_name,
            relation_type="VIEW" if row[0] == "VIEW" else "TABLE",
            columns=columns,
        )
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError(
            "AppBI could not inspect this Postgres relation.",
            code="TRANSFORM_RELATION_VERIFY_FAILED",
            technical_message=f"{type(exc).__name__}: {exc}",
        ) from exc


def _verify_bigquery(
    configuration: dict[str, Any],
    catalog_name: str | None,
    schema_name: str,
    relation_name: str,
) -> VerifiedRelation:
    from google.cloud import bigquery
    from google.oauth2 import service_account

    raw = configuration.get("credentials_json")
    try:
        info = json.loads(raw) if isinstance(raw, str) else raw
        credentials = service_account.Credentials.from_service_account_info(info)
        project = catalog_name or configuration.get("project_id") or info.get("project_id")
        client = bigquery.Client(project=project, credentials=credentials)
        table = client.get_table(f"{project}.{schema_name}.{relation_name}")
    except Exception as exc:
        raise ValidationError(
            f"Relation {schema_name}.{relation_name} could not be verified in BigQuery.",
            code="TRANSFORM_RELATION_VERIFY_FAILED",
            technical_message=f"{type(exc).__name__}: {exc}",
        ) from exc
    return VerifiedRelation(
        catalog_name=project,
        schema_name=schema_name,
        relation_name=relation_name,
        relation_type="VIEW" if table.table_type == "VIEW" else "TABLE",
        columns=[
            {"name": field.name, "data_type": field.field_type, "nullable": field.mode != "REQUIRED"}
            for field in table.schema
        ],
    )
