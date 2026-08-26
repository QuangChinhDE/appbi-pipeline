"""A durable record of every engine mutation, written before it happens.

The problem this exists for
---------------------------

Creating a source means two writes to two systems: a resource on the engine,
holding the customer's credentials, and a row in the Product DB that knows
about it. The engine call happens first, because the row records what the
engine returned.

If the process dies between those two writes -- or the transaction rolls back
on a constraint, or the audit insert fails, or Postgres drops the connection --
the engine keeps a resource containing customer credentials that nothing in the
product knows exists. Nobody can see it, nobody can delete it, and it does not
appear in any list. The existing compensation only covered the engine call
itself failing, which is the easy half.

Reconciliation can notice a foreign resource afterwards, but it cannot tell an
orphan from a resource another deployment legitimately owns, and it has no
record of intent to retry against. That is the difference between a reconciler
and a ledger.

How it works
------------

The row is written and committed **before** the engine is called, in its own
transaction. So the intent survives anything that happens next:

    PENDING            the engine is about to be called, or was and we do not
                       know the outcome
    ENGINE_CREATED     the engine did the work and told us the reference; the
                       Product DB has not committed yet
    COMMITTED          both sides agree; nothing left to do
    COMPENSATION_REQUIRED
                       the Product DB did not commit and the engine resource
                       has to be removed
    COMPENSATED        it was removed
    FAILED             compensation itself keeps failing; a human is needed

A row still in `PENDING` or `ENGINE_CREATED` after the SLO is exactly the state
that used to be invisible, and the sweeper acts on it.

`product_resource_id` is the idempotency key, and it is the product's own row
id -- generated before the engine call, passed to the engine as the external
id. A retry therefore addresses the same engine resource rather than creating a
second one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime, Index, Integer, String, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class EngineOperationState:
    PENDING = "PENDING"
    ENGINE_CREATED = "ENGINE_CREATED"
    COMMITTED = "COMMITTED"
    COMPENSATION_REQUIRED = "COMPENSATION_REQUIRED"
    COMPENSATED = "COMPENSATED"
    FAILED = "FAILED"

    #: States that still need someone to do something about them.
    OPEN = (PENDING, ENGINE_CREATED, COMPENSATION_REQUIRED)
    #: States that are finished, one way or another.
    TERMINAL = (COMMITTED, COMPENSATED, FAILED)


class EngineOperation(Base):
    """One intended mutation of engine state, and how far it got."""

    __tablename__ = "engine_operations"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True)

    # SOURCE | DESTINATION | PIPELINE
    resource_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # CREATE | UPDATE | DELETE
    operation: Mapped[str] = mapped_column(String(20), nullable=False)

    # The product's own row id, generated before the engine is called and sent
    # to the engine as the external id. This is the idempotency key: a retry
    # addresses the same engine resource instead of making a second one.
    product_resource_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True)

    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=EngineOperationState.PENDING,
        server_default=EngineOperationState.PENDING, index=True)
    engine_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Enough to compensate without reading the product row, which may not exist.
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False)

    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # One open operation per resource per verb. A duplicate submit finds
        # the existing row rather than starting a second saga against the same
        # engine resource.
        UniqueConstraint("product_resource_id", "operation",
                         name="uq_engine_operation_resource_operation"),
        # The sweeper's query: everything not yet finished, oldest first.
        Index("ix_engine_operations_open", "state", "updated_at"),
    )
