"""Browse a warehouse, and verify one relation actually exists.

Reused from V1 essentially unchanged, and deliberately so: connecting to a
warehouse and listing what is in it is the same problem before and after the
rework, and it is the one thing here that dbt is not asked to do.

What is *not* here any more is the data profiling and SQL pre-validation V1
carried. Both existed solely to feed AI model drafting, which this rework
removes -- see the blueprint's delete list. This module browses and verifies;
everything about a dbt project's own semantics belongs to dbt.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from app.core.errors import ValidationError


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


async def browse_catalogs(
    connector_key: str, configuration: dict[str, Any],
) -> list[str]:
    """Every project or database this account can see, home one first.

    A service account is often granted read on projects other than the one it
    lives in, and those are exactly the projects a Transform wants to read.
    Postgres has no equivalent -- one connection is one database -- so it
    answers with the single database it is pointed at.
    """
    if connector_key == "destination-bigquery":
        return await asyncio.to_thread(_catalogs_bigquery, configuration)
    if connector_key == "destination-postgres":
        name = configuration.get("database")
        return [str(name)] if name else []
    raise ValidationError(
        "This Destination cannot be browsed.",
        code="TRANSFORM_DESTINATION_UNSUPPORTED",
    )


def _catalogs_bigquery(configuration: dict[str, Any]) -> list[str]:
    try:
        client, home = _bigquery_client(configuration, None)
        seen = [item.project_id for item in client.list_projects()]
    except Exception as exc:
        raise ValidationError(
            "Không đọc được danh sách project của tài khoản này.",
            code="TRANSFORM_BROWSE_FAILED",
            technical_message=f"{type(exc).__name__}: {exc}",
        ) from exc
    # The home project first: it is the one the Destination writes to, and the
    # one somebody scanning the list is most likely looking for.
    ordered = [home] if home else []
    ordered += sorted(item for item in seen if item != home)
    return ordered


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
    """A client for either kind of BigQuery credential.

    A service account is a key the team holds; an OAuth grant is a person's own
    access, and the refresh token is what survives long enough to be stored. The
    two produce different credential objects, so the branch is here rather than
    at every call site that needs a client.
    """
    from google.cloud import bigquery

    if configuration.get("auth_method") == "oauth":
        from google.oauth2.credentials import Credentials

        credentials = Credentials(
            token=None,
            refresh_token=configuration.get("refresh_token"),
            client_id=configuration.get("oauth_client_id"),
            client_secret=configuration.get("oauth_client_secret"),
            token_uri=configuration.get("token_uri")
            or "https://oauth2.googleapis.com/token",
        )
        project = catalog_name or configuration.get("project_id")
        return bigquery.Client(project=project, credentials=credentials), project

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
    try:
        client, project = _bigquery_client(configuration, catalog_name)
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
