"""Bring an existing dbt or Dataform repository into AppBI as a Transform.

A team moving off Dataform is not starting from nothing -- they have a Git
repository with the modelling already in it. The cost of switching is retyping
that, so this removes it: paste the repository URL, read what the conversion
would produce, and create the Transform from it.

Two decisions shape everything here.

The preview and the creation read the same repository through the same
converter, so what a user approves is what they get. The conversion is pure
(`app.transformation.repo_import`); only fetching and persisting live here.

And an imported model is only useful if it compiles. A dbt project names its
sources whatever its author chose and AppBI names them after the schema they
live in, so importing the SQL verbatim would leave every model broken on the
first reference. Instead the source tables are registered as real inputs,
verified against the warehouse, and the `source()` calls are rewritten to
match. Tables the warehouse does not have are reported rather than hidden.
"""

from __future__ import annotations

import io
import logging
import re
import tarfile
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.errors import ValidationError
from app.core.logging import log_event
from app.core.permissions import Action, Module
from app.models.transform import Transform, TransformInput, TransformModel, TransformTest
from app.schemas.domain import DataAssetRegister, TransformCreate
from app.services import transforms as service
from app.transformation import repo_import

logger = logging.getLogger(__name__)

#: Only GitHub. Accepting an arbitrary host would turn this endpoint into a
#: request forwarder that reaches whatever the API container can reach.
_ALLOWED_HOSTS = {"github.com", "www.github.com"}

_MAX_ARCHIVE_BYTES = 40 * 1024 * 1024
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_FILES = 4000
_READABLE = (".sql", ".sqlx", ".yml", ".yaml", ".json", ".md", ".js")


def parse_repo_url(url: str) -> tuple[str, str, str | None, str]:
    """Split a GitHub URL into owner, repo, ref and subdirectory.

    Accepts what people actually paste: the address bar of a repository, of a
    branch, or of a folder inside one, with or without a `.git` suffix.
    """
    text = (url or "").strip()
    if text.startswith("git@github.com:"):
        text = "https://github.com/" + text[len("git@github.com:"):]
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in _ALLOWED_HOSTS:
        raise ValidationError(
            "Chỉ hỗ trợ repository trên github.com.",
            code="TRANSFORM_IMPORT_HOST_UNSUPPORTED",
        )
    parts = [segment for segment in parsed.path.split("/") if segment]
    if len(parts) < 2:
        raise ValidationError(
            "Địa chỉ phải có dạng https://github.com/<chủ-sở-hữu>/<repository>.",
            code="TRANSFORM_IMPORT_URL_INVALID",
        )
    owner, repo = parts[0], parts[1].removesuffix(".git")
    ref: str | None = None
    subdirectory = ""
    if len(parts) > 3 and parts[2] in ("tree", "blob"):
        ref = parts[3]
        subdirectory = "/".join(parts[4:])
    return owner, repo, ref, subdirectory


async def fetch_files(
    owner: str, repo: str, ref: str | None, token: str | None,
) -> dict[str, str]:
    """Download the repository as a tarball and read its text files.

    A tarball rather than a clone: no git binary in the image, one request, and
    no working copy left on disk. Everything is bounded -- archive size, file
    size, file count -- because the URL is user-supplied and an unbounded
    extract is a way to take the API container down.
    """
    suffix = f"/{ref}" if ref else ""
    url = f"https://api.github.com/repos/{owner}/{repo}/tarball{suffix}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise ValidationError(
            "Không kết nối được tới GitHub.",
            code="TRANSFORM_IMPORT_FETCH_FAILED",
            technical_message=f"{type(exc).__name__}: {exc}",
        ) from exc

    if response.status_code in (401, 403):
        raise ValidationError(
            "GitHub từ chối truy cập. Repository riêng tư cần access token có quyền đọc.",
            code="TRANSFORM_IMPORT_FORBIDDEN",
        )
    if response.status_code == 404:
        raise ValidationError(
            "Không tìm thấy repository hoặc nhánh này.",
            code="TRANSFORM_IMPORT_NOT_FOUND",
        )
    if response.status_code >= 400:
        raise ValidationError(
            f"GitHub trả về lỗi {response.status_code}.",
            code="TRANSFORM_IMPORT_FETCH_FAILED",
        )
    if len(response.content) > _MAX_ARCHIVE_BYTES:
        raise ValidationError(
            "Repository quá lớn để import (giới hạn 40MB).",
            code="TRANSFORM_IMPORT_TOO_LARGE",
        )

    files: dict[str, str] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
            for member in archive:
                if len(files) >= _MAX_FILES:
                    break
                if not member.isfile() or member.size > _MAX_FILE_BYTES:
                    continue
                # GitHub wraps everything in one generated top directory.
                path = member.name.split("/", 1)[1] if "/" in member.name else member.name
                if not path or path.startswith("/") or ".." in path.split("/"):
                    continue
                if not path.endswith(_READABLE):
                    continue
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                try:
                    files[path] = handle.read().decode("utf-8")
                except UnicodeDecodeError:
                    continue
    except tarfile.TarError as exc:
        raise ValidationError(
            "Không giải nén được nội dung repository.",
            code="TRANSFORM_IMPORT_ARCHIVE_INVALID",
            technical_message=f"{type(exc).__name__}: {exc}",
        ) from exc
    if not files:
        raise ValidationError(
            "Repository không có tệp nào đọc được.",
            code="TRANSFORM_IMPORT_EMPTY",
        )
    return files


def _scope(files: dict[str, str], subdirectory: str) -> dict[str, str]:
    if not subdirectory:
        return files
    head = subdirectory.strip("/") + "/"
    scoped = {
        path[len(head):]: text for path, text in files.items() if path.startswith(head)
    }
    if not scoped:
        raise ValidationError(
            f"Không tìm thấy thư mục `{subdirectory}` trong repository.",
            code="TRANSFORM_IMPORT_SUBDIR_MISSING",
        )
    return scoped


async def _plan_for(
    ctx: RequestContext, repo_url: str, ref: str | None, subdirectory: str | None,
    token: str | None,
) -> tuple[repo_import.ImportPlan, dict[str, Any]]:
    owner, repo, url_ref, url_subdir = parse_repo_url(repo_url)
    chosen_ref = (ref or url_ref or "").strip() or None
    chosen_subdir = (subdirectory or url_subdir or "").strip("/")
    files = await fetch_files(owner, repo, chosen_ref, token)
    try:
        plan = repo_import.build_plan(_scope(files, chosen_subdir))
    except ValueError as exc:
        raise ValidationError(str(exc), code="TRANSFORM_IMPORT_UNRECOGNISED") from exc
    log_event(
        logger, logging.INFO, "transform.import.inspected",
        owner=owner, repo=repo, ref=chosen_ref or "(default)",
        kind=plan.kind, models=len(plan.models), warnings=len(plan.warnings),
    )
    return plan, {
        "owner": owner, "repo": repo, "ref": chosen_ref, "subdirectory": chosen_subdir,
    }


async def inspect(
    ctx: RequestContext,
    *,
    repo_url: str,
    ref: str | None = None,
    subdirectory: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Read the repository and describe the Transform it would become."""
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    plan, origin = await _plan_for(ctx, repo_url, ref, subdirectory, token)
    return {**plan.as_dict(), "origin": origin}


async def create_from_repository(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    repo_url: str,
    ref: str | None,
    subdirectory: str | None,
    token: str | None,
    name: str,
    destination_id: uuid.UUID,
    default_schema: str,
) -> tuple[Transform, list[str]]:
    """Create a Transform holding the repository's models, ready to run.

    Returns the Transform and the warnings a user needs to see afterwards --
    both the conversion's own and anything that went wrong while resolving the
    project's sources against this warehouse.
    """
    ctx.require(Module.TRANSFORMS, Action.CREATE)
    plan, origin = await _plan_for(ctx, repo_url, ref, subdirectory, token)
    if not plan.models:
        raise ValidationError(
            "Repository này không có model nào để import.",
            code="TRANSFORM_IMPORT_NO_MODELS",
        )
    warnings = list(plan.warnings)

    # Sources first: a source that does not resolve to a real relation cannot
    # become an input, and a model referencing it will not compile. Registering
    # them before the Transform exists means a total failure leaves nothing
    # half-created.
    registered: dict[tuple[str, str], Any] = {}
    for source in plan.sources:
        try:
            asset = await service.register_asset(
                session, ctx, destination_id,
                DataAssetRegister(
                    catalog_name=source.catalog or None,
                    schema_name=source.schema,
                    relation_name=source.relation,
                ),
            )
        except ValidationError as exc:
            warnings.append(
                f"Nguồn `{source.alias}.{source.table}` "
                f"({source.schema}.{source.relation}) không có trong kho dữ liệu này "
                f"nên chưa nối được: {exc.message}"
            )
            continue
        registered[(source.alias, source.table)] = asset

    transform = await service.create(session, ctx, TransformCreate(
        name=name.strip(),
        description=f"Import từ github.com/{origin['owner']}/{origin['repo']}",
        destination_id=destination_id,
        default_schema=default_schema,
        input_asset_ids=[asset.id for asset in registered.values()],
    ))

    # Read the aliases back rather than recomputing them: the generator assigns
    # one source per schema and numbers collisions, and guessing that here would
    # drift the moment either rule changes.
    inputs = list((await session.scalars(select(TransformInput).where(
        TransformInput.transform_id == transform.id,
    ))).all())
    alias_by_asset = {item.data_asset_id: item.source_name for item in inputs}
    mapping: dict[tuple[str, str], tuple[str, str]] = {}
    for key, asset in registered.items():
        alias = alias_by_asset.get(asset.id)
        if alias:
            mapping[key] = (alias, re.sub(r"[^A-Za-z0-9_]", "_", asset.relation_name))

    by_name: dict[str, TransformModel] = {}
    for item in plan.models:
        model = TransformModel(
            transform_id=transform.id, name=item.name, layer=item.layer,
            materialization=item.materialization,
            sql=repo_import.rewrite_sources(item.sql, mapping),
            description=item.description,
            created_by=ctx.user_id, updated_by=ctx.user_id,
        )
        session.add(model)
        by_name[item.name] = model
    await session.flush()

    for test in plan.tests:
        model = by_name.get(repo_import._safe_name(test.model))
        if model is None:
            warnings.append(
                f"Bỏ qua kiểm tra `{test.rule}` vì không tìm thấy model `{test.model}`."
            )
            continue
        session.add(TransformTest(
            model_id=model.id, column_name=test.column, rule=test.rule,
            severity="ERROR", config_json=test.config or {},
        ))
    await session.flush()

    log_event(
        logger, logging.INFO, "transform.import.created",
        transform_id=str(transform.id), kind=plan.kind,
        models=len(plan.models), sources=len(registered), warnings=len(warnings),
    )
    return transform, warnings
