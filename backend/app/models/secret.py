"""Stable import path for the credential record (avoids a core<->models cycle)."""

from app.models.ops import SecretRecord

__all__ = ["SecretRecord"]
