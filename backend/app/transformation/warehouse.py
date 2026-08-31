"""Exact physical relation verification for supported warehouses."""

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
