"""A completed OAuth consent, waiting for the wizard to pick it up.

The refresh token itself is not here -- it is in the ordinary secret store,
envelope-encrypted like every other credential, and this row holds only the
reference. What this table adds is the handoff: the browser is redirected back
from Google or Microsoft with nothing sensitive in the URL, and the wizard
carries an opaque id until it saves.

Deliberately short-lived and single-use. An unconsumed grant is a live refresh
token with nothing pointing at it, so it expires on its own and the worker
deletes it along with the secret it references.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class OAuthGrant(Base):
    __tablename__ = "oauth_grants"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True)
    connector_key: Mapped[str] = mapped_column(String(120), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)

    #: Where the credential actually lives.
    secret_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    #: The account that granted consent, so the wizard can show *which* Google
    #: account was connected rather than an anonymous "connected" tick.
    account_label: Mapped[str] = mapped_column(String(255), default="",
                                               server_default="", nullable=False)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # The purge query.
        Index("ix_oauth_grants_expires_at", "expires_at"),
    )
