"""Turn a form describing a source table into ordinary dbt files.

The gap this closes is not that dbt is hard to run -- AppBI already runs it --
but that a dbt project's *first* file is hard to write without knowing dbt's
filing system.  A staging model is a `select` over a `source()`; the source has
to be declared in YAML before `ref` resolves; the tests live in a third file
again.  A person who knows their own data has to learn all three before they
can say the one thing they already know: "this table, these columns".

So the form asks what a person knows, and this writes what dbt requires.  What
lands on disk is unremarkable dbt -- a `.sql` file and YAML entries in the
conventional places -- so the next edit can be by hand, in the editor, with no
generator involved.  Nothing here is a hidden layer: it runs once, at the
moment you press the button, and then gets out of the way.

Merging matters more than generating.  A project usually already has
`_sources.yml` and `_staging.yml`, so these read the existing documents and add
to them, replacing an entry only when it names the same table or model.  A
hand-written description or an extra test on a neighbouring entry survives.
"""

from __future__ import annotations

import uuid
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.core.context import RequestContext
from app.services import audit
from app.transforms import files as file_service
from app.transforms import scaffold
from app.transforms.models import TransformProject, TransformProjectRevision
from app.transforms.storage import ObjectStore

SOURCES_PATH = "models/staging/_sources.yml"
STAGING_PATH = "models/staging/_staging.yml"


def _load(raw: bytes | None) -> dict[str, Any]:
    """An existing YAML document, or the empty shape dbt expects."""
    if not raw:
        return {"version": 2}
    try:
        parsed = yaml.safe_load(raw.decode("utf-8"))
    except yaml.YAMLError as exc:
        raise ValidationError(
            "Tệp YAML hiện tại không đọc được, nên không thể thêm vào.",
            code="TRANSFORM_YAML_UNREADABLE",
            technical_message=f"{type(exc).__name__}: {exc}",
        ) from exc
    if parsed is None:
        return {"version": 2}
    if not isinstance(parsed, dict):
        raise ValidationError(
            "Tệp YAML hiện tại không đúng cấu trúc dbt.",
            code="TRANSFORM_YAML_UNREADABLE",
        )
    parsed.setdefault("version", 2)
    return parsed


def _dump(document: dict[str, Any]) -> bytes:
    return yaml.safe_dump(
        document, sort_keys=False, allow_unicode=True, default_flow_style=False,
    ).encode("utf-8")


def _merge_source(document: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """Add one table to `sources:`, keeping every other source untouched."""
    sources = document.get("sources")
    if not isinstance(sources, list):
        sources = []
    existing = next(
        (item for item in sources
         if isinstance(item, dict) and item.get("name") == entry["name"]),
        None,
    )
    if existing is None:
        sources.append(entry)
    else:
        # Same source, new table: append unless this table is already declared,
        # in which case the newer definition wins.
        existing.setdefault("schema", entry.get("schema"))
        tables = existing.get("tables")
        if not isinstance(tables, list):
            tables = []
        incoming = entry["tables"][0]
        tables = [
            table for table in tables
            if not (isinstance(table, dict) and table.get("name") == incoming["name"])
        ]
        tables.append(incoming)
        existing["tables"] = tables
    document["sources"] = sources
    return document


def _merge_model(document: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    models = document.get("models")
    if not isinstance(models, list):
        models = []
    models = [
        item for item in models
        if not (isinstance(item, dict) and item.get("name") == entry["name"])
    ]
    models.append(entry)
    document["models"] = models
    return document


async def generate_staging_model(
    session: AsyncSession,
    ctx: RequestContext,
    project: TransformProject,
    *,
    source_name: str,
    schema_name: str,
    table_name: str,
    model_name: str,
    columns: list[dict[str, Any]],
    materialized: str = "view",
    description: str | None = None,
    expected_revision_id: uuid.UUID | None,
    store: ObjectStore | None = None,
) -> tuple[TransformProjectRevision, list[str]]:
    """Write the model and its YAML as one revision.

    One revision, not three: the model, its source declaration and its tests
    are a single act to the person who pressed the button, and a project that
    parsed cleanly before should parse cleanly after -- not be briefly broken
    between two saves.
    """
    source = scaffold.validate_identifier(source_name, field="Tên source")
    schema = scaffold.validate_identifier(schema_name, field="Tên schema")
    table = scaffold.validate_identifier(table_name, field="Tên bảng")
    model = scaffold.validate_identifier(model_name, field="Tên model")

    selected = [column for column in columns if column.get("selected", True)]
    if not selected:
        raise ValidationError("Chọn ít nhất một cột.", code="TRANSFORM_NO_COLUMNS")
    for column in selected:
        scaffold.validate_identifier(column["name"], field="Tên cột")
        if (alias := (column.get("alias") or "").strip()):
            scaffold.validate_identifier(alias, field="Tên cột sau khi đổi")

    model_path = f"models/staging/{model}.sql"
    revision = await file_service.working_revision(session, project)
    if model_path in (revision.manifest_index or {}):
        raise ValidationError(
            f"Đã có tệp {model_path}. Đổi tên model hoặc sửa tệp đó trực tiếp.",
            code="TRANSFORM_FILE_EXISTS",
        )

    sources_doc = _merge_source(
        _load(await _read(revision, SOURCES_PATH, store)),
        scaffold.source_yaml_entry(
            source_name=source, schema_name=schema, table_name=table,
            columns=selected,
        ),
    )
    staging_doc = _merge_model(
        _load(await _read(revision, STAGING_PATH, store)),
        scaffold.model_yaml_entry(
            model_name=model, columns=selected, description=description,
        ),
    )

    changes = [
        file_service.FileChange(
            path=model_path,
            content=scaffold.staging_model_sql(
                source_name=source, table_name=table,
                columns=selected, materialized=materialized,
            ).encode("utf-8"),
        ),
        file_service.FileChange(path=SOURCES_PATH, content=_dump(sources_doc)),
        file_service.FileChange(path=STAGING_PATH, content=_dump(staging_doc)),
    ]
    written = await file_service.apply_changes(
        session, project, changes=changes,
        expected_revision_id=expected_revision_id,
        actor_id=ctx.user_id, store=store,
    )
    await audit.record(
        session, ctx, "transform.model.generated", resource_type="TRANSFORM",
        resource_id=project.id, resource_name=project.name,
        after={"model": model, "source_table": f"{schema}.{table}",
               "columns": len(selected), "materialized": materialized},
    )
    return written, [change.path for change in changes]


async def _read(
    revision: TransformProjectRevision, path: str, store: ObjectStore | None,
) -> bytes | None:
    """The file's bytes, or None when the project does not have it yet."""
    if path not in (revision.manifest_index or {}):
        return None
    data, _ = await file_service.read_file(revision, path, store=store)
    return data
