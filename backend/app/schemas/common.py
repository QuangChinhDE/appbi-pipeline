"""Shared response shapes (sections 76, 77, 78)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PageInfo(BaseModel):
    next_cursor: str | None = None
    has_more: bool = False
    total: int | None = None
    limit: int = 50
    offset: int = 0


class Paginated(BaseModel, Generic[T]):
    items: list[T]
    page: PageInfo
    summary: dict[str, Any] = Field(default_factory=dict)


class HealthBlock(BaseModel):
    """Backend-derived status so the FE never recomputes business state."""

    level: str
    code: str | None = None
    label: str
    last_checked_at: datetime | None = None
    message: str | None = None


class ActorRef(BaseModel):
    id: uuid.UUID
    name: str
    connector_key: str
    connector_display_name: str | None = None
    icon: str | None = None


class UserRef(BaseModel):
    id: uuid.UUID | None = None
    full_name: str | None = None
    email: str | None = None


class CredentialsView(BaseModel):
    """What the FE is allowed to know about a stored credential (section 21.3)."""

    configured: bool = False
    provider: str | None = None
    rotated_at: datetime | None = None
    version: int | None = None
    fields: dict[str, str] = Field(default_factory=dict)


class OperationView(ORMModel):
    id: uuid.UUID
    kind: str
    status: str
    resource_type: str | None = None
    resource_id: uuid.UUID | None = None
    progress_message: str | None = None
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


class Acknowledged(BaseModel):
    ok: bool = True
    message: str | None = None
