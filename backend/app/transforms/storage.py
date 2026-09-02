"""Durable storage for project revisions and dbt artifacts.

Postgres holds metadata and index rows only.  A dbt project revision is a set
of files and an artifact bundle is a handful of JSON documents that routinely
run to tens of megabytes; neither belongs in the product database, and a
`manifest.json` per parse would grow it without bound.

Two backends implement one interface:

``LocalObjectStore``
    A directory tree.  Used for development and for single-node deployments.

``S3ObjectStore``
    Any S3-compatible endpoint.  The production backend.  MinIO satisfies it
    too, which is how a local team can rehearse the production path without
    MinIO being a required service in the base compose file.

Nothing outside this module knows which one is in use, so Transform runs
unchanged against either and the choice is one environment variable.  Pipeline
and Airbyte do not touch this store at all.

Content addressing is not an optimisation here, it is what makes revisions
cheap: saving one file in a 400-file project stores one new blob and reuses 399
keys, so every save can freeze an immutable revision instead of a diff.
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import os
import shutil
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath

from app.core.config import settings
from app.core.errors import ValidationError


class ObjectNotFound(LookupError):
    """A key that should exist does not.

    Raised rather than returning ``None`` so a missing blob cannot be mistaken
    for an empty file -- an empty ``.sql`` is legal and a missing one is not.
    """


def content_key(data: bytes) -> str:
    """The storage key for a blob, derived from the blob.

    Sharded two levels deep. A flat prefix with a million objects under it is
    slow to list on a filesystem and awkward to reason about on S3; the shape
    below is what git uses, for the same reason.
    """
    digest = hashlib.sha256(data).hexdigest()
    return f"blobs/{digest[:2]}/{digest[2:4]}/{digest}"


def digest_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ObjectStore(ABC):
    """Immutable, content-addressed blob storage.

    Every method is async because the S3 implementation does real I/O.  The
    local implementation offloads to a thread rather than blocking the event
    loop, which matters when a parse writes a 40 MB manifest while requests are
    being served on the same process.
    """

    @abstractmethod
    async def put(self, key: str, data: bytes) -> None:
        """Store ``data`` at ``key``.

        Implementations must be idempotent: writing the same content-addressed
        key twice is the normal case, not a conflict.
        """

    @abstractmethod
    async def get(self, key: str) -> bytes:
        """Return the blob at ``key`` or raise :class:`ObjectNotFound`."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        ...

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove ``key``.  Missing keys are not an error."""

    async def put_content(self, data: bytes) -> str:
        """Store a blob under its own digest and return the key.

        Skips the write when the key is already present, which is what makes a
        save of one file in a large project cost one object instead of all of
        them.
        """
        key = content_key(data)
        if not await self.exists(key):
            await self.put(key, data)
        return key

    async def put_many(self, blobs: list[bytes]) -> list[str]:
        """Content-address several blobs concurrently, preserving order."""
        return list(await asyncio.gather(*(self.put_content(item) for item in blobs)))

    async def get_many(self, keys: list[str]) -> list[bytes]:
        return list(await asyncio.gather(*(self.get(key) for key in keys)))


def _validate_key(key: str) -> str:
    """Reject anything that could escape the store's root.

    Keys are built by this package, never by a request -- but a project file
    path does reach the key builder, and `models/../../etc/passwd` is a path a
    user can type into the editor.  One check here is cheaper than trusting
    every caller.
    """
    if not key or key.startswith("/") or key.endswith("/"):
        raise ValidationError("Invalid storage key.", code="TRANSFORM_STORAGE_KEY_INVALID")
    parts = PurePosixPath(key).parts
    if any(part in ("..", ".") for part in parts) or "\\" in key or "\x00" in key:
        raise ValidationError("Invalid storage key.", code="TRANSFORM_STORAGE_KEY_INVALID")
    return key


class LocalObjectStore(ObjectStore):
    """Filesystem-backed store, rooted at a single directory."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        candidate = (self.root / _validate_key(key)).resolve()
        # Belt and braces: `_validate_key` rejects traversal syntactically, this
        # rejects it after symlink resolution.
        if not candidate.is_relative_to(self.root):
            raise ValidationError(
                "Invalid storage key.", code="TRANSFORM_STORAGE_KEY_INVALID",
            )
        return candidate

    async def put(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(self._put_sync, self._path(key), data)

    @staticmethod
    def _put_sync(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename. A reader must never observe a half-written blob,
        # and a crash mid-write must not leave one addressed by a digest it does
        # not match.
        #
        # The temp name carries a random token, not just the pid: content
        # addressing means two callers storing the same bytes target the same
        # final path, and on Windows a rename over a file another thread still
        # holds open fails outright. That is the normal case here, not a rare
        # one -- a parse and a save can store identical blobs concurrently.
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:12]}.tmp")
        temporary.write_bytes(data)
        try:
            os.replace(temporary, path)
        except OSError:
            # Another writer got there first with the same content. The key is
            # the digest, so their blob is byte-identical to this one and the
            # write has effectively succeeded; drop the temp copy and move on.
            temporary.unlink(missing_ok=True)
            if not path.is_file():
                raise

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise ObjectNotFound(key) from exc
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.EISDIR):
                raise ObjectNotFound(key) from exc
            raise

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).is_file)

    async def delete(self, key: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(lambda: path.unlink(missing_ok=True))

    def usage_bytes(self) -> int:
        return sum(item.stat().st_size for item in self.root.rglob("*") if item.is_file())

    def clear(self) -> None:
        """Only for tests; a store is otherwise append-only."""
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)


class S3ObjectStore(ObjectStore):
    """S3-compatible store.

    Speaks S3 over httpx with SigV4 signed by hand rather than pulling in boto3.
    The product already depends on httpx, the four calls needed here are the
    simplest in the API, and an object store used only for immutable blobs does
    not justify a new transitive dependency tree in the API image.

    Works against AWS S3, MinIO, Cloudflare R2 and anything else that accepts
    SigV4 -- `endpoint_url` and `addressing_style` are the only differences.
    """

    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        access_key: str,
        secret_key: str,
        endpoint_url: str | None = None,
        prefix: str = "",
        force_path_style: bool = False,
        session_token: str | None = None,
    ) -> None:
        self.bucket = bucket
        self.region = region
        self.access_key = access_key
        self.secret_key = secret_key
        self.session_token = session_token
        self.prefix = prefix.strip("/")
        self.endpoint_url = (endpoint_url or f"https://s3.{region}.amazonaws.com").rstrip("/")
        # A custom endpoint is almost always MinIO or a local gateway, neither of
        # which serves virtual-hosted buckets, so path style is the default there.
        self.force_path_style = force_path_style or endpoint_url is not None

    def _full_key(self, key: str) -> str:
        key = _validate_key(key)
        return f"{self.prefix}/{key}" if self.prefix else key

    def _url(self, key: str) -> tuple[str, str, str]:
        """Return (url, host, canonical_uri) for a key."""
        import urllib.parse

        full = self._full_key(key)
        # Each path segment is escaped separately: the slashes are structure,
        # everything else is data. Signing and requesting must agree exactly or
        # S3 answers 403 with no hint as to which side is wrong.
        quoted = "/".join(urllib.parse.quote(part, safe="") for part in full.split("/"))
        base = urllib.parse.urlsplit(self.endpoint_url)
        if self.force_path_style:
            host = base.netloc
            canonical_uri = f"/{self.bucket}/{quoted}"
        else:
            host = f"{self.bucket}.{base.netloc}"
            canonical_uri = f"/{quoted}"
        return f"{base.scheme}://{host}{canonical_uri}", host, canonical_uri

    def _headers(
        self, method: str, key: str, payload: bytes,
    ) -> tuple[str, dict[str, str]]:
        """Sign one request with AWS SigV4 and return (url, headers)."""
        import datetime
        import hmac

        url, host, canonical_uri = self._url(key)
        now = datetime.datetime.now(datetime.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(payload).hexdigest()

        headers = {
            "host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        if self.session_token:
            headers["x-amz-security-token"] = self.session_token

        signed_headers = ";".join(sorted(headers))
        canonical_headers = "".join(
            f"{name}:{headers[name]}\n" for name in sorted(headers)
        )
        canonical_request = "\n".join([
            method, canonical_uri, "", canonical_headers, signed_headers, payload_hash,
        ])
        scope = f"{date_stamp}/{self.region}/s3/aws4_request"
        to_sign = "\n".join([
            "AWS4-HMAC-SHA256", amz_date, scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ])

        def sign(key: bytes, message: str) -> bytes:
            return hmac.new(key, message.encode(), hashlib.sha256).digest()

        signing_key = sign(
            sign(sign(sign(f"AWS4{self.secret_key}".encode(), date_stamp),
                      self.region), "s3"), "aws4_request",
        )
        signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()
        headers["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        return url, headers

    async def _request(
        self, method: str, key: str, payload: bytes = b"",
    ):
        import httpx

        url, headers = self._headers(method, key, payload)
        async with httpx.AsyncClient(timeout=60.0) as client:
            return await client.request(method, url, content=payload or None, headers=headers)

    async def put(self, key: str, data: bytes) -> None:
        response = await self._request("PUT", key, data)
        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"Object storage rejected a write ({response.status_code}): "
                f"{response.text[:500]}"
            )

    async def get(self, key: str) -> bytes:
        response = await self._request("GET", key)
        if response.status_code == 404:
            raise ObjectNotFound(key)
        if response.status_code != 200:
            raise RuntimeError(
                f"Object storage rejected a read ({response.status_code}): "
                f"{response.text[:500]}"
            )
        return response.content

    async def exists(self, key: str) -> bool:
        response = await self._request("HEAD", key)
        if response.status_code == 200:
            return True
        if response.status_code == 404:
            return False
        raise RuntimeError(
            f"Object storage rejected a HEAD ({response.status_code})."
        )

    async def delete(self, key: str) -> None:
        response = await self._request("DELETE", key)
        if response.status_code not in (200, 202, 204, 404):
            raise RuntimeError(
                f"Object storage rejected a delete ({response.status_code})."
            )


@dataclass(slots=True)
class StorageHealth:
    backend: str
    writable: bool
    detail: str | None = None


@lru_cache(maxsize=1)
def object_store() -> ObjectStore:
    """The configured store, built once.

    Cached because ``S3ObjectStore`` holds credentials read from settings and
    ``LocalObjectStore`` creates its root directory; neither wants doing per
    request.
    """
    backend = (settings.transform_storage_backend or "local").strip().lower()
    if backend == "local":
        return LocalObjectStore(settings.transform_storage_local_dir)
    if backend in ("s3", "minio", "s3-compatible"):
        missing = [
            name for name, value in (
                ("TRANSFORM_STORAGE_S3_BUCKET", settings.transform_storage_s3_bucket),
                ("TRANSFORM_STORAGE_S3_ACCESS_KEY", settings.transform_storage_s3_access_key),
                ("TRANSFORM_STORAGE_S3_SECRET_KEY", settings.transform_storage_s3_secret_key),
            ) if not value
        ]
        if missing:
            raise ValidationError(
                "Object storage is selected for Transform but not configured: "
                + ", ".join(missing),
                code="TRANSFORM_STORAGE_UNCONFIGURED",
            )
        return S3ObjectStore(
            bucket=str(settings.transform_storage_s3_bucket),
            region=settings.transform_storage_s3_region,
            access_key=str(settings.transform_storage_s3_access_key),
            secret_key=str(settings.transform_storage_s3_secret_key),
            endpoint_url=settings.transform_storage_s3_endpoint_url,
            prefix=settings.transform_storage_s3_prefix,
            force_path_style=settings.transform_storage_s3_force_path_style,
        )
    raise ValidationError(
        f"Unknown Transform storage backend `{backend}`. Use `local` or `s3`.",
        code="TRANSFORM_STORAGE_BACKEND_UNKNOWN",
    )


async def check_storage() -> StorageHealth:
    """Prove the store is writable, for the readiness endpoint.

    A Transform whose store is unreachable cannot save a file or run anything,
    and that should be visible on a health page rather than as a 500 the first
    time somebody presses Save.
    """
    backend = (settings.transform_storage_backend or "local").strip().lower()
    try:
        store = object_store()
        probe = b"appbi-transform-storage-probe"
        key = await store.put_content(probe)
        if await store.get(key) != probe:
            return StorageHealth(backend, False, "Readback did not match what was written.")
        return StorageHealth(backend, True)
    except Exception as exc:  # noqa: BLE001 - health must report, never raise
        return StorageHealth(backend, False, f"{type(exc).__name__}: {exc}")
