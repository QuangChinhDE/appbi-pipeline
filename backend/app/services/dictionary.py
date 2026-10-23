"""The data dictionary of a pipeline: every field of every stream, and what it means.

The structure comes from the stream's schema (`schema_service.field_tree`);
the meaning comes from three places, the first that has one wins:

1. ``USER``      -- a person's edit, scoped to the pipeline's source
2. ``CONNECTOR`` -- the `description` the connector ships in its own schema
3. ``AI``        -- the worker's definition, shared across workspaces, written
                    from the field's name and type only (`fill_with_ai`)

Nothing here is on the sync path. A failure in this module costs the
Dictionary tab, never a run.
"""

from __future__ import annotations

import csv
import io
import logging
import uuid
import zipfile
from typing import Any
from xml.sax.saxutils import escape

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import RequestContext
from app.core.db import utcnow
from app.core.errors import ValidationError
from app.core.logging import log_event
from app.models.dictionary import ORIGIN_AI, ORIGIN_USER, DataDictionaryEntry
from app.models.integration import Pipeline, PipelineStream, Source
from app.services import audit, schema_service

logger = logging.getLogger(__name__)

ORIGIN_CONNECTOR = "CONNECTOR"

#: Longest definition accepted from a person or from the model.
MAX_DESCRIPTION = 2000


# ── read ─────────────────────────────────────────────────────────────────────

async def _entries(session: AsyncSession, source: Source,
                   streams: list[str]) -> tuple[dict, dict]:
    """(user edits, shared AI definitions), keyed by (stream, path)."""
    if not streams:
        return {}, {}
    rows = (await session.scalars(
        select(DataDictionaryEntry).where(
            DataDictionaryEntry.stream_name.in_(streams),
            (DataDictionaryEntry.source_id == source.id)
            | ((DataDictionaryEntry.source_id.is_(None))
               & (DataDictionaryEntry.connector_key == source.connector_key)),
        )
    )).all()
    user = {(r.stream_name, r.field_path): r for r in rows if r.origin == ORIGIN_USER}
    shared = {(r.stream_name, r.field_path): r for r in rows if r.origin == ORIGIN_AI}
    return user, shared


def _resolve(field: dict[str, Any], user, shared) -> dict[str, Any]:
    if user is not None:
        return {"description": user.description,
                "description_vi": user.description_vi or user.description,
                "origin": ORIGIN_USER}
    if field.get("description") or field.get("description_vi"):
        return {"description": field.get("description") or field.get("description_vi"),
                "description_vi": field.get("description_vi") or field.get("description"),
                "origin": ORIGIN_CONNECTOR}
    # A shared definition written for another type is stale, not wrong-looking
    # truth: hide it until the worker rewrites it.
    if shared is not None and shared.field_type == field["type"]:
        return {"description": shared.description,
                "description_vi": shared.description_vi or shared.description,
                "origin": ORIGIN_AI}
    return {"description": None, "description_vi": None, "origin": None}


async def build(session: AsyncSession, pipeline: Pipeline) -> list[dict[str, Any]]:
    """Every stream of the pipeline, each with its resolved field tree."""
    source = await session.get(Source, pipeline.source_id)
    streams = sorted(pipeline.streams, key=lambda s: (not s.selected, s.stream_name))
    user, shared = await _entries(session, source, [s.stream_name for s in streams])
    out: list[dict[str, Any]] = []
    for stream in streams:
        cursors = set(stream.cursor_fields or [])
        keys = {".".join(pk) for pk in (stream.primary_key_fields or [])}
        fields = []
        for field in schema_service.field_tree(stream.json_schema or {}):
            key = (stream.stream_name, field["path"])
            fields.append({
                "path": field["path"], "name": field["name"], "depth": field["depth"],
                "type": field["type"], "nullable": field["nullable"],
                "is_cursor": field["path"] in cursors,
                "is_primary_key": field["path"] in keys,
                **_resolve(field, user.get(key), shared.get(key)),
            })
        out.append({
            "stream_id": stream.id, "name": stream.stream_name,
            "namespace": stream.namespace, "selected": stream.selected,
            "fields": fields,
        })
    return out


# ── edit ─────────────────────────────────────────────────────────────────────

async def set_description(session: AsyncSession, ctx: RequestContext, pipeline: Pipeline,
                          stream_id: uuid.UUID, path: str, description: str) -> None:
    """Record a person's definition of one field. Empty text reverts it.

    Only the meaning is editable. The path and type are the schema's, and an
    edit addressed to a field the schema does not have is refused rather than
    stored against nothing.
    """
    stream: PipelineStream | None = next(
        (s for s in pipeline.streams if s.id == stream_id), None)
    if stream is None:
        raise ValidationError("That stream is not part of this pipeline.",
                              code="DICTIONARY_UNKNOWN_STREAM")
    field = next((f for f in schema_service.field_tree(stream.json_schema or {})
                  if f["path"] == path), None)
    if field is None:
        raise ValidationError("That field is not in the stream's schema.",
                              code="DICTIONARY_UNKNOWN_FIELD")
    text = (description or "").strip()
    if len(text) > MAX_DESCRIPTION:
        raise ValidationError(f"Keep the definition under {MAX_DESCRIPTION} characters.",
                              code="DICTIONARY_TOO_LONG")

    source = await session.get(Source, pipeline.source_id)
    existing = await session.scalar(select(DataDictionaryEntry).where(
        DataDictionaryEntry.source_id == source.id,
        DataDictionaryEntry.stream_name == stream.stream_name,
        DataDictionaryEntry.field_path == path,
    ))
    before = existing.description if existing else None
    now = utcnow()
    if not text:
        if existing:
            await session.delete(existing)
    elif existing:
        existing.description = existing.description_vi = text
        existing.field_type = field["type"]
        existing.updated_by = ctx.user_id
        existing.updated_at = now
    else:
        session.add(DataDictionaryEntry(
            connector_key=source.connector_key, workspace_id=pipeline.workspace_id,
            source_id=source.id, stream_name=stream.stream_name, field_path=path,
            field_type=field["type"], description=text, description_vi=text,
            origin=ORIGIN_USER, updated_by=ctx.user_id, created_at=now, updated_at=now,
        ))
    await audit.record(
        session, ctx, "pipeline.dictionary_edited",
        resource_type="PIPELINE", resource_id=pipeline.id, resource_name=pipeline.name,
        before={"stream": stream.stream_name, "path": path, "description": before},
        after={"stream": stream.stream_name, "path": path, "description": text or None},
    )


# ── export ───────────────────────────────────────────────────────────────────

EXPORT_COLUMNS = ("Stream", "Selected", "Field path", "Type", "Nullable",
                  "Primary key", "Cursor", "Definition", "Source of definition")


def export_rows(streams: list[dict[str, Any]], locale: str) -> list[list[str]]:
    rows = []
    for stream in streams:
        for f in stream["fields"]:
            text = f["description_vi"] if locale == "vi" else f["description"]
            rows.append([
                stream["name"], "yes" if stream["selected"] else "no", f["path"],
                f["type"], "yes" if f["nullable"] else "no",
                "yes" if f["is_primary_key"] else "", "yes" if f["is_cursor"] else "",
                text or "", f["origin"] or "",
            ])
    return rows


def to_csv(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_COLUMNS)
    writer.writerows(rows)
    # BOM, so Excel opens Vietnamese text as UTF-8 rather than mojibake.
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def to_xlsx(rows: list[list[str]]) -> bytes:
    """A one-sheet workbook, written with the standard library.

    The format needs four XML parts in a zip; adding a spreadsheet dependency
    to the image for that is not worth it.
    """
    def cell(col: int, row: int, value: str) -> str:
        ref = f"{chr(65 + col)}{row}"
        return (f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">'
                f"{escape(str(value))}</t></is></c>")

    lines = [list(EXPORT_COLUMNS), *rows]
    sheet_rows = "".join(
        f'<row r="{r}">' + "".join(cell(c, r, v) for c, v in enumerate(values)) + "</row>"
        for r, values in enumerate(lines, start=1)
    )
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>"),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Dictionary" sheetId="1" r:id="rId1"/></sheets></workbook>'),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>"),
        "xl/worksheets/sheet1.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<sheetData>{sheet_rows}</sheetData></worksheet>"),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return buffer.getvalue()


# ── AI fill (worker) ─────────────────────────────────────────────────────────

#: Fields sent to the model in one request.
AI_BATCH = 60
#: Requests per worker pass, so a large backlog drains over passes instead of
#: spending a burst of tokens at once.
AI_BATCHES_PER_PASS = 4


class _Definition(BaseModel):
    path: str
    description: str = Field(description="One sentence in English.")
    description_vi: str = Field(description="The same meaning in Vietnamese.")


class _Definitions(BaseModel):
    fields: list[_Definition]


_INSTRUCTIONS = (
    "You write a data dictionary. For each field of one stream of a data "
    "connector, say in one short sentence what the field holds, judged from "
    "the connector, the stream, the field path and its type. A path with "
    "`[]` is an element of an array; `a.b` is `b` inside object `a`. Epoch "
    "integers named *_at / since / last_update are Unix timestamps in seconds. "
    "Do not invent business rules; when a name is opaque, describe it by its "
    "type and position. Answer for every path you are given, in English and "
    "in Vietnamese."
)


async def pending(session: AsyncSession) -> dict[tuple[str, str], dict[str, str]]:
    """Fields with no meaning yet, grouped by (connector, stream).

    Read from the pipelines' own stream schemas: those are the fields somebody
    is actually looking at. A field the connector describes, a field with a
    current shared definition, and a field a person defined are all skipped.
    """
    rows = (await session.execute(
        select(Source.connector_key, PipelineStream.stream_name, PipelineStream.json_schema)
        .join(Pipeline, Pipeline.id == PipelineStream.pipeline_id)
        .join(Source, Source.id == Pipeline.source_id)
        .where(Pipeline.deleted_at.is_(None))
    )).all()
    wanted: dict[tuple[str, str], dict[str, str]] = {}
    for connector_key, stream_name, schema in rows:
        for field in schema_service.field_tree(schema or {}):
            if field.get("description") or field.get("description_vi"):
                continue
            wanted.setdefault((connector_key, stream_name), {})[field["path"]] = field["type"]
    if not wanted:
        return {}
    existing = (await session.execute(
        select(DataDictionaryEntry.connector_key, DataDictionaryEntry.stream_name,
               DataDictionaryEntry.field_path, DataDictionaryEntry.field_type)
        .where(DataDictionaryEntry.source_id.is_(None))
    )).all()
    for connector_key, stream_name, path, field_type in existing:
        fields = wanted.get((connector_key, stream_name))
        if fields is not None and fields.get(path) == field_type:
            del fields[path]
    return {key: fields for key, fields in wanted.items() if fields}


async def fill_with_ai(session: AsyncSession) -> int:
    """Define a bounded number of undefined fields. Returns how many.

    Sends field names and types only -- never a record value, never a
    configuration. Off without an OpenAI key.
    """
    if not settings.openai_api_key.strip():
        return 0
    from app.models.engine import ConnectorDefinition
    from app.services.builder_ai.client import OpenAIBuilderClient

    todo = await pending(session)
    if not todo:
        return 0
    client = OpenAIBuilderClient()
    titles = dict((await session.execute(
        select(ConnectorDefinition.connector_key, ConnectorDefinition.display_name)
    )).all())
    written, batches = 0, 0
    for (connector_key, stream_name), fields in todo.items():
        items = sorted(fields.items())
        for start in range(0, len(items), AI_BATCH):
            if batches >= AI_BATCHES_PER_PASS:
                return written
            batches += 1
            chunk = dict(items[start:start + AI_BATCH])
            prompt = (
                f"Connector: {titles.get(connector_key, connector_key)} ({connector_key})\n"
                f"Stream: {stream_name}\n"
                "Fields (path: type):\n"
                + "\n".join(f"- {path}: {kind}" for path, kind in chunk.items())
            )
            answer = await client.structured(
                model=settings.openai_model_planner, instructions=_INSTRUCTIONS,
                prompt=prompt, schema=_Definitions, actor_id="dictionary-worker",
                operation="dictionary.define",
            )
            now = utcnow()
            for item in answer.fields:
                if item.path not in chunk:
                    continue
                row = await session.scalar(select(DataDictionaryEntry).where(
                    DataDictionaryEntry.source_id.is_(None),
                    DataDictionaryEntry.connector_key == connector_key,
                    DataDictionaryEntry.stream_name == stream_name,
                    DataDictionaryEntry.field_path == item.path,
                ))
                if row is None:
                    row = DataDictionaryEntry(
                        connector_key=connector_key, stream_name=stream_name,
                        field_path=item.path, origin=ORIGIN_AI, created_at=now)
                    session.add(row)
                row.field_type = chunk[item.path]
                row.description = item.description.strip()[:MAX_DESCRIPTION]
                row.description_vi = item.description_vi.strip()[:MAX_DESCRIPTION]
                row.updated_at = now
                written += 1
            await session.commit()
            log_event(logger, logging.INFO, "dictionary.ai_defined",
                      connector=connector_key, stream=stream_name, fields=len(chunk))
    return written
