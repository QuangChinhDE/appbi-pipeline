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
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.errors import AppError, ValidationError
from app.core.logging import log_event
from app.core.permissions import Action, Module
from app.core.secrets import secret_store
from app.core.db import utcnow
from app.models.transform import Transform, TransformInput, TransformModel, TransformTest
from app.schemas.domain import DataAssetRegister, TransformCreate
from app.services import audit, transforms as service
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


async def head_commit(
    owner: str, repo: str, ref: str | None, token: str | None,
) -> str | None:
    """The commit a branch currently points at, in one small request.

    Polling by downloading the tarball would cost megabytes per Transform per
    interval to learn nothing most of the time. This costs a few hundred bytes,
    so the sync loop can run often without being expensive.
    """
    suffix = ref or "HEAD"
    url = f"https://api.github.com/repos/{owner}/{repo}/commits/{suffix}"
    headers = {
        "Accept": "application/vnd.github.sha",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise ValidationError(
            "Không kết nối được tới GitHub.",
            code="TRANSFORM_IMPORT_FETCH_FAILED",
            technical_message=f"{type(exc).__name__}: {exc}",
        ) from exc
    if response.status_code in (401, 403):
        raise ValidationError(
            "GitHub từ chối truy cập. Token có thể đã hết hạn hoặc bị thu hồi.",
            code="TRANSFORM_IMPORT_FORBIDDEN",
        )
    if response.status_code == 404:
        raise ValidationError(
            "Không tìm thấy repository hoặc nhánh này.",
            code="TRANSFORM_IMPORT_NOT_FOUND",
        )
    if response.status_code >= 400:
        return None
    return response.text.strip() or None


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


async def _resolve_sources(
    session: AsyncSession,
    ctx: RequestContext,
    destination_id: uuid.UUID,
    plan: repo_import.ImportPlan,
    warnings: list[str],
) -> tuple[dict[tuple[str, str], Any], dict[tuple[str | None, str, str], Any]]:
    """Register every source the project reads, keeping the ones that resolve.

    A source that is not a real relation in this warehouse cannot become an
    input and the models reading it will not compile. Rather than fail the whole
    import for one missing table, each failure is reported by name and its SQL
    is left exactly as the repository wrote it.
    """
    registered: dict[tuple[str, str], Any] = {}
    direct: dict[tuple[str | None, str, str], Any] = {}
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
            where = ".".join(
                part for part in (source.catalog, source.schema, source.relation) if part
            )
            warnings.append(
                f"Không đọc được bảng `{where}` từ kho dữ liệu này nên chưa nối làm "
                f"nguồn được; câu lệnh giữ nguyên như trong repository. {exc.message}"
            )
            continue
        registered[(source.alias, source.table)] = asset
        if source.direct:
            direct[(source.catalog, source.schema, source.relation)] = asset
    return registered, direct


def _build_mappings(
    registered: dict[tuple[str, str], Any],
    direct: dict[tuple[str | None, str, str], Any],
    alias_by_asset: dict[uuid.UUID, str],
) -> tuple[
    dict[tuple[str, str], tuple[str, str]],
    dict[tuple[str | None, str, str], tuple[str, str]],
]:
    """Aliases read back from the inputs, not recomputed.

    The project generator assigns one source per schema and numbers collisions;
    guessing that rule here would drift the moment either side changes.
    """
    def target(asset: Any) -> tuple[str, str] | None:
        alias = alias_by_asset.get(asset.id)
        if not alias:
            return None
        return alias, re.sub(r"[^A-Za-z0-9_]", "_", asset.relation_name)

    mapping = {
        key: found for key, asset in registered.items()
        if (found := target(asset)) is not None
    }
    literal = {
        key: found for key, asset in direct.items()
        if (found := target(asset)) is not None
    }
    return mapping, literal


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
    registered, direct_assets = await _resolve_sources(
        session, ctx, destination_id, plan, warnings,
    )

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
    mapping, literal_mapping = _build_mappings(registered, direct_assets, alias_by_asset)

    by_name: dict[str, TransformModel] = {}
    for item in plan.models:
        # Both rewrites, in order: `source()` calls the repository wrote, then
        # table names it spelled out. A project that does neither is untouched.
        sql = repo_import.rewrite_sources(item.sql, mapping)
        sql = repo_import.rewrite_direct_tables(sql, literal_mapping)
        model = TransformModel(
            transform_id=transform.id, name=item.name, layer=item.layer,
            materialization=item.materialization,
            sql=sql,
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

    # Record which models the repository owns. Without it a later sync cannot
    # tell a file that was deleted upstream from a model somebody wrote here,
    # and the safe reading of that ambiguity is to delete nothing.
    transform.git_sync = {
        **(transform.git_sync or {}),
        "managed": sorted(item.name for item in plan.models),
    }
    log_event(
        logger, logging.INFO, "transform.import.created",
        transform_id=str(transform.id), kind=plan.kind,
        models=len(plan.models), sources=len(registered), warnings=len(warnings),
    )
    return transform, warnings


# --------------------------------------------------------------------------
# Staying in step with the repository
# --------------------------------------------------------------------------

#: Below this, polling costs more requests than the change rate justifies.
MIN_SYNC_MINUTES = 5
DEFAULT_SYNC_MINUTES = 30


def git_state(transform: Transform) -> dict[str, Any]:
    """What the FE is allowed to know about the connection. Never the token."""
    config = transform.git_sync or {}
    if not config.get("repo_url"):
        return {"connected": False}
    return {
        "connected": True,
        "repo_url": config.get("repo_url"),
        "ref": config.get("ref"),
        "subdirectory": config.get("subdirectory") or "",
        "enabled": bool(config.get("enabled")),
        "interval_minutes": config.get("interval_minutes") or DEFAULT_SYNC_MINUTES,
        "auto_publish": bool(config.get("auto_publish")),
        "has_token": bool(config.get("secret_ref")),
        "last_commit": config.get("last_commit"),
        "last_synced_at": config.get("last_synced_at"),
        "last_status": config.get("last_status"),
        "last_message": config.get("last_message"),
        "managed": config.get("managed") or [],
        "next_sync_at": transform.git_next_sync_at,
    }


def _schedule_next(transform: Transform) -> None:
    config = transform.git_sync or {}
    if not config.get("enabled") or not config.get("repo_url"):
        transform.git_next_sync_at = None
        return
    minutes = max(
        MIN_SYNC_MINUTES, int(config.get("interval_minutes") or DEFAULT_SYNC_MINUTES),
    )
    transform.git_next_sync_at = utcnow() + timedelta(minutes=minutes)


async def _token_for(session: AsyncSession, transform: Transform) -> str | None:
    ref = (transform.git_sync or {}).get("secret_ref")
    if not ref:
        return None
    payload = await secret_store.read(session, ref)
    return payload.get("token") or None


async def configure_git(
    session: AsyncSession,
    ctx: RequestContext,
    transform: Transform,
    *,
    repo_url: str | None = None,
    ref: str | None = None,
    subdirectory: str | None = None,
    token: str | None = None,
    enabled: bool | None = None,
    interval_minutes: int | None = None,
    auto_publish: bool | None = None,
) -> dict[str, Any]:
    """Attach, adjust or detach the repository behind this Transform.

    A token goes straight to the encrypted secret store and only its reference
    is kept here. Passing no token leaves the stored one alone, so changing the
    interval does not mean re-entering a credential.
    """
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    config = dict(transform.git_sync or {})

    if repo_url is not None:
        if not repo_url.strip():
            # Detaching: drop the credential rather than leave it orphaned.
            if config.get("secret_ref"):
                await secret_store.delete(session, config["secret_ref"])
            transform.git_sync = {}
            transform.git_next_sync_at = None
            await session.flush()
            return git_state(transform)
        owner, repo, url_ref, url_subdir = parse_repo_url(repo_url)
        config["repo_url"] = f"https://github.com/{owner}/{repo}"
        config["ref"] = (ref if ref is not None else url_ref) or None
        config["subdirectory"] = (
            (subdirectory if subdirectory is not None else url_subdir) or ""
        ).strip("/")
    else:
        if ref is not None:
            config["ref"] = ref or None
        if subdirectory is not None:
            config["subdirectory"] = subdirectory.strip("/")

    if token is not None:
        if token.strip():
            config["secret_ref"] = await secret_store.write(
                session, ctx.workspace_id, {"token": token.strip()},
                ref=config.get("secret_ref"),
            )
        elif config.get("secret_ref"):
            await secret_store.delete(session, config["secret_ref"])
            config.pop("secret_ref", None)

    if enabled is not None:
        config["enabled"] = bool(enabled)
    if interval_minutes is not None:
        config["interval_minutes"] = max(MIN_SYNC_MINUTES, int(interval_minutes))
    if auto_publish is not None:
        config["auto_publish"] = bool(auto_publish)

    transform.git_sync = config
    _schedule_next(transform)
    await session.flush()
    await audit.record(
        session, ctx, "transform.git.configured", resource_type="TRANSFORM",
        resource_id=transform.id, resource_name=transform.name,
        after={"repo_url": config.get("repo_url"), "enabled": bool(config.get("enabled"))},
    )
    return git_state(transform)


async def _apply_plan(
    session: AsyncSession,
    ctx: RequestContext,
    transform: Transform,
    plan: repo_import.ImportPlan,
) -> tuple[list[str], list[str], list[str]]:
    """Bring the Transform's models in line with the repository.

    Git owns the models it produced and nothing else. A model written here by
    hand is left alone even when the repository has no such file: removing a
    colleague's work because it is absent from a repo they never committed to
    would be the worst possible reading of the word sync.
    """
    warnings = list(plan.warnings)
    config = dict(transform.git_sync or {})
    managed = set(config.get("managed") or [])

    registered, direct_assets = await _resolve_sources(
        session, ctx, transform.destination_id, plan, warnings,
    )
    merged = list(dict.fromkeys(
        [item.data_asset_id for item in transform.inputs]
        + [asset.id for asset in registered.values()]
    ))
    await service._replace_inputs(session, transform, merged)

    inputs = list((await session.scalars(select(TransformInput).where(
        TransformInput.transform_id == transform.id,
    ))).all())
    alias_by_asset = {item.data_asset_id: item.source_name for item in inputs}
    mapping, literal_mapping = _build_mappings(registered, direct_assets, alias_by_asset)

    current = {
        model.name: model for model in transform.models if model.deleted_at is None
    }
    changed: list[str] = []
    for item in plan.models:
        sql = repo_import.rewrite_direct_tables(
            repo_import.rewrite_sources(item.sql, mapping), literal_mapping,
        )
        model = current.get(item.name)
        if model is None:
            session.add(TransformModel(
                transform_id=transform.id, name=item.name, layer=item.layer,
                materialization=item.materialization, sql=sql,
                description=item.description,
                created_by=ctx.user_id, updated_by=ctx.user_id,
            ))
            changed.append(item.name)
            continue
        unchanged = (
            (model.sql or "").strip() == sql.strip()
            and model.layer == item.layer
            and model.materialization == item.materialization
        )
        if unchanged:
            continue
        model.sql = sql
        model.layer = item.layer
        model.materialization = item.materialization
        model.description = item.description or model.description
        model.updated_by = ctx.user_id
        model.version += 1
        changed.append(item.name)

    incoming = {item.name for item in plan.models}
    removed: list[str] = []
    for name, model in current.items():
        if name in incoming or name not in managed:
            continue
        model.deleted_at = utcnow()
        removed.append(name)

    config["managed"] = sorted(incoming)
    transform.git_sync = config
    await session.flush()
    return changed, removed, warnings


async def sync_now(
    session: AsyncSession,
    ctx: RequestContext,
    transform: Transform,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Fetch the repository and apply it, unless it has not moved.

    The commit is checked before anything is downloaded. Most polls find nothing
    and that case should cost one small request, not a tarball. `force`
    re-applies the commit already recorded, which is what somebody pressing Sync
    after fixing warehouse permissions is actually asking for.
    """
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    config = dict(transform.git_sync or {})
    if not config.get("repo_url"):
        raise ValidationError(
            "Transform này chưa nối với repository nào.",
            code="TRANSFORM_GIT_NOT_CONNECTED",
        )
    token = await _token_for(session, transform)
    owner, repo, _, _ = parse_repo_url(config["repo_url"])
    ref = config.get("ref")

    def finish(status: str, message: str, **extra: Any) -> dict[str, Any]:
        current = dict(transform.git_sync or {})
        current.update({
            "last_status": status,
            "last_message": message,
            "last_synced_at": utcnow().isoformat(),
        })
        current.update(extra)
        transform.git_sync = current
        _schedule_next(transform)
        # The commit is reported on every outcome, not only when it moved: a
        # caller asking "what is running right now" gets the same answer either
        # way, and a null on an unchanged sync reads like the record was lost.
        return {
            "status": status, "message": message,
            "last_commit": current.get("last_commit"),
            **extra,
        }

    try:
        head = await head_commit(owner, repo, ref, token)
    except ValidationError as exc:
        result = finish("FAILED", exc.message)
        await session.flush()
        log_event(logger, logging.WARNING, "transform.git.sync_failed",
                  transform_id=str(transform.id), error=exc.code)
        return result

    if head and head == config.get("last_commit") and not force:
        result = finish("UNCHANGED", "Repository chưa có commit mới.")
        await session.flush()
        return result

    try:
        plan, _origin = await _plan_for(
            ctx, config["repo_url"], ref, config.get("subdirectory"), token,
        )
    except ValidationError as exc:
        result = finish("FAILED", exc.message)
        await session.flush()
        log_event(logger, logging.WARNING, "transform.git.sync_failed",
                  transform_id=str(transform.id), error=exc.code)
        return result

    changed, removed, warnings = await _apply_plan(session, ctx, transform, plan)
    if changed or removed:
        message = f"Đã cập nhật {len(changed)} bảng"
        if removed:
            message += f", gỡ {len(removed)} bảng không còn trong repository"
        message += "."
    else:
        message = "Repository có commit mới nhưng không đổi bảng nào."
    published = None
    if (changed or removed) and config.get("auto_publish"):
        # Only worth doing when something moved. Publishing an unchanged draft
        # would fill the release history with identical snapshots.
        try:
            release = await service.publish_release(
                session, ctx, transform,
                notes=f"Tự động xuất bản sau khi đồng bộ {(head or '')[:7]}",
            )
            published = release.release_number
        except AppError as exc:
            warnings.append(f"Đồng bộ xong nhưng chưa xuất bản được: {exc.message}")

    if published is not None:
        message += f" Đã xuất bản phiên bản {published}."
    result = finish(
        "APPLIED", message,
        last_commit=head or config.get("last_commit"),
        changed=changed, removed=removed, warnings=warnings,
    )
    await audit.record(
        session, ctx, "transform.git.synced", resource_type="TRANSFORM",
        resource_id=transform.id, resource_name=transform.name,
        after={"commit": head, "changed": len(changed), "removed": len(removed)},
    )
    log_event(logger, logging.INFO, "transform.git.synced",
              transform_id=str(transform.id), commit=head,
              changed=len(changed), removed=len(removed))
    await session.flush()
    return result


async def due_for_sync(session: AsyncSession, limit: int = 25) -> list[Transform]:
    """Transforms whose polling interval has elapsed."""
    return list((await session.scalars(
        select(Transform).where(
            Transform.status == "ACTIVE",
            Transform.deleted_at.is_(None),
            Transform.git_next_sync_at.is_not(None),
            Transform.git_next_sync_at <= utcnow(),
        ).limit(limit)
    )).all())
