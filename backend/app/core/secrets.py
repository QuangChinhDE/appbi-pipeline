"""Credential store (section 21).

Product entities never hold plaintext -- they hold a `secret_ref`. The default
store keeps the payload in a dedicated table under envelope encryption: a fresh
AES data key per secret, itself wrapped with the KEK from the environment. That
means KEK rotation rewraps small data keys instead of re-encrypting every
credential, and a DB dump on its own is useless.

`SecretStore` is a Protocol so a Vault / cloud-secrets backend can be dropped in
without touching a single service.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import uuid
from typing import Any, Protocol

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import utcnow

logger = logging.getLogger(__name__)

MASK = "********"


def build_kek(raw: str) -> Fernet:
    """Turn key material into a Fernet, applying the same policy as _kek().

    Split out so key rotation can hold two keys at once — the old one to unwrap
    with, the new one to wrap with — without either of them having to be the
    process-wide SECRET_ENCRYPTION_KEY.
    """
    raw = (raw or "").strip()
    if not raw:
        raise RuntimeError("no key material given")
    try:
        material = base64.urlsafe_b64decode(raw.encode())
    except Exception:  # noqa: BLE001
        material = b""
    if len(material) != 32:
        hint = (
            "Generate one with: python -c "
            '"import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"'
        )
        if not settings.allow_derived_encryption_key:
            raise RuntimeError(
                "Key must be a 32-byte urlsafe-base64 value. " + hint
                + " (set ALLOW_DERIVED_ENCRYPTION_KEY=true only for local development)"
            )
        logger.warning(
            "Key is not a 32-byte urlsafe-base64 value; deriving one from it because "
            "ALLOW_DERIVED_ENCRYPTION_KEY is set. Never do this outside development. "
            + hint
        )
        material = hashlib.sha256(raw.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(material))


def _kek() -> Fernet:
    """Resolve the key-encryption key.

    A proper 32-byte urlsafe-base64 key is used as-is. Anything else is refused,
    because stretching a passphrase means every stored credential is protected
    by the entropy of that passphrase, and a warning in a log nobody reads is
    not consent. Local development can opt in with
    `ALLOW_DERIVED_ENCRYPTION_KEY`.
    """
    raw = settings.secret_encryption_key.strip()
    if not raw:
        raise RuntimeError("SECRET_ENCRYPTION_KEY is not set")
    try:
        return build_kek(raw)
    except RuntimeError as exc:
        # Name the variable, since "key must be..." on its own does not say which.
        raise RuntimeError(f"SECRET_ENCRYPTION_KEY: {exc}") from exc


class SecretStore(Protocol):
    async def write(self, session: AsyncSession, workspace_id: uuid.UUID,
                    payload: dict[str, Any], *, ref: str | None = None) -> str: ...

    async def read(self, session: AsyncSession, ref: str) -> dict[str, Any]: ...

    async def delete(self, session: AsyncSession, ref: str) -> None: ...


class EncryptedDbSecretStore:
    """Envelope-encrypted secrets in the product database."""

    provider = "PRODUCT_DB_ENVELOPE"

    async def write(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        payload: dict[str, Any],
        *,
        ref: str | None = None,
    ) -> str:
        from app.models.secret import SecretRecord  # local import: avoids cycle

        data_key = Fernet.generate_key()
        ciphertext = Fernet(data_key).encrypt(json.dumps(payload).encode())
        wrapped = _kek().encrypt(data_key)

        if ref:
            existing = await session.scalar(select(SecretRecord).where(SecretRecord.ref == ref))
            if existing is not None:
                existing.wrapped_data_key = wrapped.decode()
                existing.ciphertext = ciphertext.decode()
                existing.field_names = sorted(payload.keys())
                existing.rotated_at = utcnow()
                existing.version += 1
                await session.flush()
                return existing.ref

        new_ref = ref or f"secret://{settings.app_env}/{uuid.uuid4().hex}"
        session.add(
            SecretRecord(
                ref=new_ref,
                workspace_id=workspace_id,
                provider=self.provider,
                wrapped_data_key=wrapped.decode(),
                ciphertext=ciphertext.decode(),
                field_names=sorted(payload.keys()),
                rotated_at=utcnow(),
            )
        )
        await session.flush()
        return new_ref

    async def read(self, session: AsyncSession, ref: str) -> dict[str, Any]:
        from app.models.secret import SecretRecord

        record = await session.scalar(select(SecretRecord).where(SecretRecord.ref == ref))
        if record is None:
            return {}
        data_key = _kek().decrypt(record.wrapped_data_key.encode())
        return json.loads(Fernet(data_key).decrypt(record.ciphertext.encode()).decode())

    async def delete(self, session: AsyncSession, ref: str) -> None:
        from app.models.secret import SecretRecord

        record = await session.scalar(select(SecretRecord).where(SecretRecord.ref == ref))
        if record is not None:
            await session.delete(record)

    async def describe(self, session: AsyncSession, ref: str | None) -> dict[str, Any]:
        """Metadata only -- what the FE is allowed to see about a credential."""
        from app.models.secret import SecretRecord

        if not ref:
            return {"configured": False, "fields": {}, "rotated_at": None}
        record = await session.scalar(select(SecretRecord).where(SecretRecord.ref == ref))
        if record is None:
            return {"configured": False, "fields": {}, "rotated_at": None}
        return {
            "configured": True,
            "provider": record.provider,
            "rotated_at": record.rotated_at,
            "version": record.version,
            "fields": {name: MASK for name in (record.field_names or [])},
        }


secret_store = EncryptedDbSecretStore()


def generate_kek() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


async def rewrap_all(
    session: AsyncSession, *, old_key: str, new_key: str, batch: int = 200
) -> tuple[int, int]:
    """Re-wrap every stored data key under a new KEK.

    This is the whole reason the store is envelope-encrypted. Each secret has
    its own data key; only that key is wrapped with the KEK. Rotating the KEK
    therefore means unwrapping and rewrapping a few dozen bytes per record —
    the ciphertext holding the actual credential is never touched, never
    decrypted, and never rewritten.

    Returns (rotated, skipped). Skipped means the record did not unwrap with
    `old_key`: on a re-run of a partially completed rotation those are the ones
    already done, which is why this is safe to run twice.
    """
    from app.models.secret import SecretRecord

    old_fernet = build_kek(old_key)
    new_fernet = build_kek(new_key)

    rotated = skipped = 0
    offset = 0
    while True:
        records = list((await session.scalars(
            select(SecretRecord).order_by(SecretRecord.id).offset(offset).limit(batch)
        )).all())
        if not records:
            break

        for record in records:
            try:
                data_key = old_fernet.decrypt(record.wrapped_data_key.encode())
            except InvalidToken:
                # Either already rotated, or wrapped under a third key. Either
                # way, rewrapping it with a key we cannot verify would destroy
                # it, so it is counted and left alone.
                skipped += 1
                continue
            record.wrapped_data_key = new_fernet.encrypt(data_key).decode()
            rotated += 1

        # Commit per batch: a rotation interrupted halfway leaves a mixture,
        # and the skip-on-InvalidToken path above is what lets a re-run finish
        # the job rather than corrupt what already succeeded.
        await session.commit()
        offset += len(records)

    return rotated, skipped
