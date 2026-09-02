"""Git-backed projects: clone, status, diff, commit, push, pull.

The rule the blueprint sets is that a Git project stays a dbt project.  Nothing
here converts, translates, strips or normalises: the working tree is the
project, and a repository cloned in and pushed back out is the repository that
came in plus whatever the person actually edited.

This is where V2 differs most sharply from V1.  V1's Git support was one-way and
lossy by construction -- it read a repository, converted it into
``TransformModel`` rows, and warned about everything it could not represent.
Round-tripping was not a missing feature, it was impossible.  Here the file set
*is* the state, so a commit is just the working revision with a message on it.

No git binary.  Everything goes through the GitHub REST API, which is one
dependency the product already has (`httpx`) instead of a binary in the image
and a working copy on a disk that two containers do not share.
"""

from __future__ import annotations

import base64
import io
import tarfile
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.db import utcnow
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.permissions import Action, Module
from app.core.secrets import secret_store
from app.services import audit
from app.transforms import files as file_service
from app.transforms.models import (
    TransformGitBinding, TransformProject, TransformProjectRevision,
)
from app.transforms.runtime.workspace import find_project_root, validate_path
from app.transforms.storage import object_store

#: Only GitHub.  Accepting an arbitrary host would turn these endpoints into a
#: request forwarder that reaches whatever the API container can reach.
ALLOWED_HOSTS = frozenset({"github.com", "www.github.com"})

API = "https://api.github.com"
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

MAX_ARCHIVE_BYTES = 60 * 1024 * 1024
MAX_FILES = 5_000

#: Paths never brought in from a repository or written back to it.
#:
#: `target/` and `dbt_packages/` are build output; a repository that has
#: committed them by mistake should not have them resurrected on every pull.
SKIP_PREFIXES = ("target/", "dbt_packages/", ".git/", "logs/")


@dataclass(slots=True)
class RepoRef:
    owner: str
    repo: str
    ref: str | None
    subdirectory: str

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"


def parse_repo_url(url: str) -> RepoRef:
    """Split a GitHub URL into owner, repo, ref and subdirectory.

    Accepts what people actually paste: the address bar of a repository, of a
    branch, or of a folder inside one, with or without a `.git` suffix.
    """
    text = (url or "").strip()
    if text.startswith("git@github.com:"):
        text = "https://github.com/" + text[len("git@github.com:"):]
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in ALLOWED_HOSTS:
        raise ValidationError(
            "Only repositories on github.com are supported.",
            code="TRANSFORM_GIT_HOST_UNSUPPORTED",
        )
    parts = [segment for segment in parsed.path.split("/") if segment]
    if len(parts) < 2:
        raise ValidationError(
            "The address should look like https://github.com/<owner>/<repository>.",
            code="TRANSFORM_GIT_URL_INVALID",
        )
    owner, repo = parts[0], parts[1].removesuffix(".git")
    ref: str | None = None
    subdirectory = ""
    if len(parts) > 3 and parts[2] in ("tree", "blob"):
        ref = parts[3]
        subdirectory = "/".join(parts[4:])
    return RepoRef(owner=owner, repo=repo, ref=ref, subdirectory=subdirectory)


async def _request(
    method: str,
    path: str,
    *,
    token: str | None,
    json_body: dict[str, Any] | None = None,
    accept: str | None = None,
    timeout: float = 30.0,
) -> httpx.Response:
    headers = dict(_HEADERS)
    if accept:
        headers["Accept"] = accept
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            return await client.request(
                method, f"{API}{path}", headers=headers, json=json_body,
            )
    except httpx.HTTPError as exc:
        raise ValidationError(
            "Could not reach GitHub.",
            code="TRANSFORM_GIT_UNREACHABLE",
            technical_message=f"{type(exc).__name__}: {exc}",
        ) from exc


def _check(response: httpx.Response, *, action: str) -> httpx.Response:
    if response.status_code in (401, 403):
        raise ValidationError(
            "GitHub refused access. The token may have expired, been revoked, or "
            "may not have permission to " + action + ".",
            code="TRANSFORM_GIT_FORBIDDEN",
        )
    if response.status_code == 404:
        raise NotFoundError("That repository, branch or path was not found on GitHub.")
    if response.status_code == 409:
        raise ConflictError(
            "GitHub rejected this because the branch moved. Pull first, then try again.",
            code="TRANSFORM_GIT_CONFLICT",
        )
    if response.status_code >= 400:
        raise ValidationError(
            f"GitHub returned {response.status_code}.",
            code="TRANSFORM_GIT_FAILED",
            technical_message=response.text[:2000],
        )
    return response


async def head_commit(
    ref: RepoRef, branch: str | None, token: str | None,
) -> str | None:
    """The commit a branch currently points at, in one small request.

    Polling by downloading the tarball would cost megabytes per project per
    interval to learn nothing most of the time.  This costs a few hundred bytes,
    so the polling loop can run often without being expensive.
    """
    suffix = branch or ref.ref or "HEAD"
    response = await _request(
        "GET", f"/repos/{ref.owner}/{ref.repo}/commits/{suffix}",
        token=token, accept="application/vnd.github.sha",
    )
    if response.status_code >= 400:
        _check(response, action="read this repository")
    return response.text.strip() or None


async def fetch_tree(
    ref: RepoRef, branch: str | None, token: str | None,
) -> dict[str, bytes]:
    """Download the repository as a tarball and read every file in it.

    A tarball rather than a clone: no git binary in the image, one request, and
    no working copy left on disk.  Everything is bounded -- archive size, file
    count -- because the URL is user-supplied and an unbounded extract is a way
    to take the API container down.

    Unlike V1 this reads *every* file, not only the ones it knows how to
    convert.  A `.py` model, a `.csv` seed, a `.md` doc block and a
    `.sqlfluff` config are all part of the project, and dropping them because
    the product has no opinion about them is the data loss the rework forbids.
    """
    suffix = f"/{branch}" if branch else (f"/{ref.ref}" if ref.ref else "")
    response = _check(
        await _request(
            "GET", f"/repos/{ref.owner}/{ref.repo}/tarball{suffix}",
            token=token, timeout=120.0,
        ),
        action="read this repository",
    )
    if len(response.content) > MAX_ARCHIVE_BYTES:
        raise ValidationError(
            f"That repository is larger than the {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB limit.",
            code="TRANSFORM_GIT_TOO_LARGE",
        )

    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
        for member in archive:
            if len(files) >= MAX_FILES:
                raise ValidationError(
                    f"That repository has more than {MAX_FILES} files.",
                    code="TRANSFORM_GIT_TOO_MANY_FILES",
                )
            if not member.isfile():
                continue
            # GitHub wraps everything in one generated top directory.
            path = member.name.split("/", 1)[1] if "/" in member.name else member.name
            if not path or path.startswith("/") or ".." in path.split("/"):
                continue
            if path.startswith(SKIP_PREFIXES):
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            files[path] = handle.read()
    return files


def scope_to_project(
    files: dict[str, bytes], subdirectory: str,
) -> tuple[dict[str, bytes], str]:
    """Narrow a repository's files to the dbt project inside it.

    Returns the files rooted at the project, plus the subdirectory they came
    from -- which has to be remembered so a push writes them back where they
    belong rather than to the repository root.
    """
    root = subdirectory.strip("/")
    if not root:
        detected = find_project_root(list(files))
        if detected is None:
            raise ValidationError(
                "No `dbt_project.yml` was found in that repository. If the project "
                "is in a subfolder, give the path to it.",
                code="TRANSFORM_GIT_NO_PROJECT",
            )
        root = detected

    if not root:
        scoped = dict(files)
    else:
        prefix = f"{root}/"
        scoped = {
            path[len(prefix):]: content
            for path, content in files.items()
            if path.startswith(prefix)
        }
    if "dbt_project.yml" not in scoped:
        raise ValidationError(
            f"`{root or '/'}` does not contain a `dbt_project.yml`.",
            code="TRANSFORM_GIT_NO_PROJECT",
        )

    # Revalidate every path: a repository can contain names this product will
    # not store, and finding out at write time is too late.
    clean: dict[str, bytes] = {}
    skipped: list[str] = []
    for path, content in scoped.items():
        try:
            clean[validate_path(path).value] = content
        except ValidationError:
            skipped.append(path)
    if skipped and len(skipped) > len(scoped) // 2:
        raise ValidationError(
            "Most of the files in that project have paths this product cannot "
            "store. The first few are: " + ", ".join(skipped[:5]),
            code="TRANSFORM_GIT_PATHS_UNSUPPORTED",
        )
    return clean, root


# ── binding ───────────────────────────────────────────────────────────────


async def binding(
    session: AsyncSession, project: TransformProject,
) -> TransformGitBinding:
    row = await session.scalar(select(TransformGitBinding).where(
        TransformGitBinding.project_id == project.id,
        TransformGitBinding.deleted_at.is_(None),
    ))
    if row is None:
        raise ValidationError(
            "This project is not connected to a repository.",
            code="TRANSFORM_GIT_NOT_BOUND",
        )
    return row


async def token_for(
    session: AsyncSession, git: TransformGitBinding,
) -> str | None:
    if not git.secret_ref:
        return None
    secrets = await secret_store.read(session, git.secret_ref)
    return secrets.get("token")


async def bind(
    session: AsyncSession,
    ctx: RequestContext,
    project: TransformProject,
    *,
    repo_url: str,
    branch: str,
    subdirectory: str,
    token: str | None,
    auto_pull: bool = False,
    interval_minutes: int = 15,
) -> TransformGitBinding:
    ref = parse_repo_url(repo_url)
    secret_ref = None
    if token:
        secret_ref = await secret_store.write(
            session, ctx.workspace_id, {"token": token},
        )
    row = TransformGitBinding(
        id=uuid.uuid4(),
        project_id=project.id,
        provider="github",
        repo_url=ref.url,
        branch=branch or ref.ref or "main",
        subdirectory=subdirectory.strip("/"),
        secret_ref=secret_ref,
        auto_pull=auto_pull,
        interval_minutes=max(1, min(int(interval_minutes), 1440)),
        next_pull_at=utcnow() if auto_pull else None,
    )
    session.add(row)
    project.mode = "GIT"
    await session.flush()
    return row


# ── status and diff ───────────────────────────────────────────────────────


@dataclass(slots=True)
class GitStatus:
    branch: str
    repo_url: str
    subdirectory: str
    head_commit_sha: str | None
    remote_commit_sha: str | None
    behind: bool
    #: Files the editor has saved that the last commit does not contain.
    changes: list[file_service.FileDiff] = field(default_factory=list)
    last_pulled_at: Any = None
    last_status: str | None = None
    last_message: str | None = None
    auto_pull: bool = False
    interval_minutes: int = 15


async def status(
    session: AsyncSession,
    project: TransformProject,
    *,
    check_remote: bool = False,
) -> GitStatus:
    """What is saved here versus what is committed there.

    Three states, kept distinct as the blueprint requires: a file can be clean,
    saved-but-uncommitted, or committed.  Conflating Save with Commit is what
    makes a Git UI untrustworthy, so the diff below is specifically
    working-revision-versus-last-committed-revision, not versus the last save.
    """
    git = await binding(session, project)
    working = await file_service.working_revision(session, project)

    committed = await session.scalar(
        select(TransformProjectRevision)
        .where(
            TransformProjectRevision.project_id == project.id,
            TransformProjectRevision.git_commit_sha == git.head_commit_sha,
        )
        .order_by(TransformProjectRevision.revision_number.desc())
        .limit(1)
    ) if git.head_commit_sha else None

    remote = git.remote_commit_sha
    if check_remote:
        token = await token_for(session, git)
        remote = await head_commit(parse_repo_url(git.repo_url), git.branch, token)
        git.remote_commit_sha = remote

    return GitStatus(
        branch=git.branch,
        repo_url=git.repo_url,
        subdirectory=git.subdirectory,
        head_commit_sha=git.head_commit_sha,
        remote_commit_sha=remote,
        behind=bool(remote and git.head_commit_sha and remote != git.head_commit_sha),
        changes=file_service.diff_revisions(committed, working),
        last_pulled_at=git.last_pulled_at,
        last_status=git.last_status,
        last_message=git.last_message,
        auto_pull=git.auto_pull,
        interval_minutes=git.interval_minutes,
    )


async def diff_file(
    session: AsyncSession, project: TransformProject, path: str,
) -> dict[str, Any]:
    """Both sides of one file, for the diff view.

    The comparison is done in the browser; the server's job is to hand over the
    two versions.  Computing a unified diff here would mean choosing a diff
    algorithm and a context width for a viewer that can do better with both
    texts in hand.
    """
    git = await binding(session, project)
    working = await file_service.working_revision(session, project)
    committed = await session.scalar(
        select(TransformProjectRevision)
        .where(
            TransformProjectRevision.project_id == project.id,
            TransformProjectRevision.git_commit_sha == git.head_commit_sha,
        )
        .order_by(TransformProjectRevision.revision_number.desc())
        .limit(1)
    ) if git.head_commit_sha else None

    safe = validate_path(path)
    store = object_store()

    async def side(revision: TransformProjectRevision | None) -> str | None:
        if revision is None or safe.value not in (revision.manifest_index or {}):
            return None
        data, _ = await file_service.read_file(revision, safe.value, store=store)
        return data.decode("utf-8", errors="replace")

    return {
        "path": safe.value,
        "committed": await side(committed),
        "working": await side(working),
    }


# ── pull ──────────────────────────────────────────────────────────────────


async def pull(
    session: AsyncSession,
    ctx: RequestContext,
    project: TransformProject,
    *,
    force: bool = False,
    discard_local: bool = False,
) -> dict[str, Any]:
    """Bring the remote branch in as a new revision.

    Refuses when the editor holds uncommitted changes, unless ``discard_local``
    says otherwise.  A pull that silently overwrote somebody's unsaved model
    would be the single worst thing this module could do, and "the remote is
    authoritative" is not a good enough reason to do it without being asked.
    """
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    git = await binding(session, project)
    token = await token_for(session, git)
    ref = parse_repo_url(git.repo_url)

    remote = await head_commit(ref, git.branch, token)
    git.remote_commit_sha = remote
    if remote and remote == git.head_commit_sha and not force:
        git.last_pulled_at = utcnow()
        git.last_status = "UNCHANGED"
        git.last_message = None
        return {"changed": False, "commit_sha": remote, "files_changed": 0}

    current = await status(session, project)
    if current.changes and not discard_local:
        raise ConflictError(
            f"{len(current.changes)} file(s) here have not been committed. "
            "Commit them, or choose to discard them, before pulling.",
            code="TRANSFORM_GIT_LOCAL_CHANGES",
            details={"changes": [item.path for item in current.changes[:20]]},
        )

    try:
        tree = await fetch_tree(ref, git.branch, token)
        scoped, root = scope_to_project(tree, git.subdirectory)
    except ValidationError as exc:
        git.last_pulled_at = utcnow()
        git.last_status = "FAILED"
        git.last_message = str(exc)[:1000]
        raise

    working = await file_service.working_revision(session, project)
    revision = await file_service.replace_all(
        session, project, files=scoped, actor_id=ctx.user_id,
        store=object_store(), git_commit_sha=remote, git_branch=git.branch,
    )
    changed = file_service.diff_revisions(working, revision)

    git.head_commit_sha = remote
    git.subdirectory = root
    git.last_pulled_at = utcnow()
    git.last_status = "OK"
    git.last_message = None
    if git.auto_pull:
        from datetime import timedelta

        git.next_pull_at = utcnow() + timedelta(minutes=git.interval_minutes)

    # A pull is a project change like any other: the project's own name may have
    # moved with it.
    facts = await file_service.project_facts(revision)
    if facts.name:
        project.dbt_project_name = facts.name

    await audit.record(
        session, ctx, "transform.git.pulled", resource_type="TRANSFORM",
        resource_id=project.id, resource_name=project.name,
        after={"commit_sha": remote, "files_changed": len(changed)},
    )
    return {
        "changed": True,
        "commit_sha": remote,
        "files_changed": len(changed),
        "changes": [
            {"path": item.path, "change": item.change} for item in changed[:200]
        ],
    }


# ── commit and push ───────────────────────────────────────────────────────


async def commit_and_push(
    session: AsyncSession,
    ctx: RequestContext,
    project: TransformProject,
    *,
    message: str,
    paths: list[str] | None = None,
) -> dict[str, Any]:
    """Write the working revision back to the branch as one commit.

    Built from the Git data API rather than a `git` binary: create a blob per
    changed file, a tree on top of the current one, a commit pointing at it, and
    then move the branch ref.  Moving the ref last and non-forcefully is what
    makes a concurrent push a 409 rather than a silent overwrite of somebody
    else's commit.

    ``paths`` stages a subset.  Everything else stays uncommitted here, which is
    the state Git users expect and V1 had no way to represent.
    """
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    text = (message or "").strip()
    if not text:
        raise ValidationError(
            "A commit needs a message.", code="TRANSFORM_GIT_NO_MESSAGE",
        )

    git = await binding(session, project)
    token = await token_for(session, git)
    if not token:
        raise ValidationError(
            "Pushing needs a token with write access to this repository.",
            code="TRANSFORM_GIT_NO_TOKEN",
        )
    ref = parse_repo_url(git.repo_url)

    current = await status(session, project, check_remote=True)
    if current.behind:
        raise ConflictError(
            "The branch has moved on GitHub since this project was last pulled. "
            "Pull first so your commit is on top of it.",
            code="TRANSFORM_GIT_BEHIND",
            details={"remote_commit_sha": current.remote_commit_sha},
        )
    staged = [
        item for item in current.changes
        if paths is None or item.path in set(paths)
    ]
    if not staged:
        raise ValidationError(
            "There is nothing to commit.", code="TRANSFORM_GIT_NOTHING_TO_COMMIT",
        )

    working = await file_service.working_revision(session, project)
    store = object_store()
    prefix = f"{git.subdirectory}/" if git.subdirectory else ""

    # One blob per changed file.  Unchanged files are not sent: the base tree
    # already has them, which is what keeps a one-line edit a one-blob push.
    tree_entries: list[dict[str, Any]] = []
    for change in staged:
        repo_path = f"{prefix}{change.path}"
        if change.change == "D":
            # A null sha is how the Git data API expresses a deletion in a tree.
            tree_entries.append({
                "path": repo_path, "mode": "100644", "type": "blob", "sha": None,
            })
            continue
        data, _ = await file_service.read_file(working, change.path, store=store)
        blob = _check(await _request(
            "POST", f"/repos/{ref.owner}/{ref.repo}/git/blobs",
            token=token,
            json_body={
                "content": base64.b64encode(data).decode("ascii"),
                "encoding": "base64",
            },
        ), action="write to this repository").json()
        tree_entries.append({
            "path": repo_path, "mode": "100644", "type": "blob", "sha": blob["sha"],
        })

    base_sha = current.remote_commit_sha or current.head_commit_sha
    if not base_sha:
        raise ValidationError(
            "This project has no known commit to build on.",
            code="TRANSFORM_GIT_NO_BASE",
        )
    base_commit = _check(await _request(
        "GET", f"/repos/{ref.owner}/{ref.repo}/git/commits/{base_sha}", token=token,
    ), action="read this repository").json()

    tree = _check(await _request(
        "POST", f"/repos/{ref.owner}/{ref.repo}/git/trees",
        token=token,
        json_body={"base_tree": base_commit["tree"]["sha"], "tree": tree_entries},
    ), action="write to this repository").json()

    commit = _check(await _request(
        "POST", f"/repos/{ref.owner}/{ref.repo}/git/commits",
        token=token,
        json_body={
            "message": text,
            "tree": tree["sha"],
            "parents": [base_sha],
        },
    ), action="write to this repository").json()

    # Non-forced. If somebody pushed between the check above and now, GitHub
    # rejects this rather than discarding their commit.
    _check(await _request(
        "PATCH", f"/repos/{ref.owner}/{ref.repo}/git/refs/heads/{git.branch}",
        token=token, json_body={"sha": commit["sha"], "force": False},
    ), action="push to this branch")

    # Stamp the revision with the commit it became, so the next status compares
    # against the right baseline.
    working.git_commit_sha = commit["sha"]
    working.git_branch = git.branch
    git.head_commit_sha = commit["sha"]
    git.remote_commit_sha = commit["sha"]
    git.last_status = "OK"
    git.last_message = None

    await audit.record(
        session, ctx, "transform.git.committed", resource_type="TRANSFORM",
        resource_id=project.id, resource_name=project.name,
        after={"commit_sha": commit["sha"], "files": len(staged), "message": text[:200]},
    )
    return {
        "commit_sha": commit["sha"],
        "files_committed": len(staged),
        "branch": git.branch,
        "url": f"{ref.url}/commit/{commit['sha']}",
    }


async def list_branches(
    session: AsyncSession, project: TransformProject,
) -> list[dict[str, Any]]:
    git = await binding(session, project)
    token = await token_for(session, git)
    ref = parse_repo_url(git.repo_url)
    response = _check(await _request(
        "GET", f"/repos/{ref.owner}/{ref.repo}/branches?per_page=100", token=token,
    ), action="read this repository")
    return [
        {
            "name": item.get("name"),
            "commit_sha": (item.get("commit") or {}).get("sha"),
            "current": item.get("name") == git.branch,
            "protected": bool(item.get("protected")),
        }
        for item in response.json()
        if isinstance(item, dict)
    ]


async def checkout(
    session: AsyncSession,
    ctx: RequestContext,
    project: TransformProject,
    *,
    branch: str,
    discard_local: bool = False,
) -> dict[str, Any]:
    """Switch the project to another branch and load its files."""
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    git = await binding(session, project)

    current = await status(session, project)
    if current.changes and not discard_local:
        raise ConflictError(
            f"{len(current.changes)} file(s) here have not been committed. "
            "Commit them, or choose to discard them, before switching branch.",
            code="TRANSFORM_GIT_LOCAL_CHANGES",
            details={"changes": [item.path for item in current.changes[:20]]},
        )

    git.branch = branch
    git.head_commit_sha = None
    git.remote_commit_sha = None
    result = await pull(session, ctx, project, force=True, discard_local=True)
    await audit.record(
        session, ctx, "transform.git.checked_out", resource_type="TRANSFORM",
        resource_id=project.id, resource_name=project.name,
        after={"branch": branch},
    )
    return {"branch": branch, **result}


async def due_for_pull(
    session: AsyncSession, limit: int = 25,
) -> list[TransformGitBinding]:
    now = utcnow()
    return list((await session.scalars(
        select(TransformGitBinding)
        .join(TransformProject, TransformProject.id == TransformGitBinding.project_id)
        .where(
            TransformGitBinding.auto_pull.is_(True),
            TransformGitBinding.deleted_at.is_(None),
            TransformGitBinding.next_pull_at.is_not(None),
            TransformGitBinding.next_pull_at <= now,
            TransformProject.deleted_at.is_(None),
            TransformProject.status == "ACTIVE",
        )
        .limit(limit)
    )).all())


async def configure(
    session: AsyncSession,
    ctx: RequestContext,
    project: TransformProject,
    *,
    branch: str | None = None,
    subdirectory: str | None = None,
    token: str | None = None,
    auto_pull: bool | None = None,
    interval_minutes: int | None = None,
) -> TransformGitBinding:
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    git = await binding(session, project)
    if branch is not None:
        git.branch = branch.strip() or git.branch
    if subdirectory is not None:
        git.subdirectory = subdirectory.strip("/")
    if token:
        old = git.secret_ref
        git.secret_ref = await secret_store.write(
            session, ctx.workspace_id, {"token": token},
        )
        if old:
            await secret_store.delete(session, old)
    if auto_pull is not None:
        git.auto_pull = auto_pull
        git.next_pull_at = utcnow() if auto_pull else None
    if interval_minutes is not None:
        git.interval_minutes = max(1, min(int(interval_minutes), 1440))
    await session.flush()
    return git
