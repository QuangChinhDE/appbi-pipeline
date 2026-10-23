"""Payloads of the pipeline Dictionary tab."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class DictionaryField(BaseModel):
    path: str
    name: str
    depth: int
    type: str
    nullable: bool
    is_cursor: bool = False
    is_primary_key: bool = False
    description: str | None = None
    description_vi: str | None = None
    #: USER, CONNECTOR, AI, or None while nothing defines the field yet.
    origin: str | None = None


class DictionaryStream(BaseModel):
    stream_id: uuid.UUID
    name: str
    namespace: str | None = None
    selected: bool
    fields: list[DictionaryField] = Field(default_factory=list)


class DictionaryView(BaseModel):
    pipeline_id: uuid.UUID
    #: Whether undefined fields will be filled in by the worker.
    ai_enabled: bool
    streams: list[DictionaryStream] = Field(default_factory=list)


class DictionaryEdit(BaseModel):
    """Only the meaning is editable; path and type belong to the schema."""

    stream_id: uuid.UUID
    path: str = Field(min_length=1, max_length=500)
    #: Empty reverts the field to the connector's or the AI's definition.
    description: str = Field(default="", max_length=2000)
