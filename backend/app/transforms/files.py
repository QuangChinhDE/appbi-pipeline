"""The project file service.

Every mutation of a dbt project goes through here, and every one of them
produces a new revision.  A revision is cheap because files are content
addressed: saving one file in a 400-file project stores one blob and reuses 399
keys, so there is no reason to make saving anything less than atomic.

Optimistic concurrency is mandatory rather than optional.  Two people editing
one project is the normal case for analytics engineering, and a last-write-wins
save silently destroys the other person's work.  Every write names the revision
it was based on; a stale base is a 409 carrying both versions, not an overwrite.

Nothing here interprets file *content*.  A `.sql` file is bytes, a `.yml` file is
bytes.  The one exception is reading `dbt_project.yml` to learn the project's
own name and profile, which the runtime needs before dbt can be started at all
-- and even that never rewrites the file.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.transforms.models import TransformProject, TransformProjectRevision
from app.transforms.runtime.workspace import (
    ProjectPath, check_file_size, check_project_size, validate_path,
)
from app.transforms.storage import ObjectNotFound, ObjectStore, digest_of, object_store


@dataclass(slots=True)
class FileEntry:
    path: str
    size: int
    sha256: str
    #: False for anything the editor should offer as a download instead of text.
    is_text: bool

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass(slots=True)
class FileTreeNode:
    name: str
    path: str
    type: str  # "file" | "directory"
    size: int | None = None
    is_text: bool = True
    children: list["FileTreeNode"] | None = None


def content_hash(index: dict[str, dict[str, Any]]) -> str:
    """A hash over the whole canonical file set.

    Path and digest, in sorted order.  Two revisions with the same hash contain
    the same project, which is what makes "Draft matches Live" a single string
    comparison rather than a diff -- and what a release records so a restore can
    prove it restored the right bytes.
    """
    digest = hashlib.sha256()
    for path in sorted(index):
        digest.update(path.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(str(index[path].get("sha256", "")).encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


# ── reading ───────────────────────────────────────────────────────────────


async def get_revision(
    session: AsyncSession, revision_id: uuid.UUID,
) -> TransformProjectRevision:
    revision = await session.get(TransformProjectRevision, revision_id)
    if revision is None:
        raise NotFoundError("That project version no longer exists.")
    return revision


async def working_revision(
    session: AsyncSession, project: TransformProject,
) -> TransformProjectRevision:
    if project.working_revision_id is None:
        raise NotFoundError("This project has no files yet.")
    return await get_revision(session, project.working_revision_id)


def list_files(revision: TransformProjectRevision) -> list[FileEntry]:
    entries = [
        FileEntry(
            path=path,
            size=int(entry.get("size") or 0),
            sha256=str(entry.get("sha256") or ""),
            is_text=bool(entry.get("is_text", True)),
        )
        for path, entry in (revision.manifest_index or {}).items()
    ]
    entries.sort(key=lambda item: item.path)
    return entries


def file_tree(revision: TransformProjectRevision) -> list[FileTreeNode]:
    """The explorer's tree, directories before files at each level.

    Built from paths rather than stored as a tree, because the flat index is
    what a revision is: a directory in a dbt project has no identity of its own,
    it exists exactly as long as something is in it.
    """
    root: dict[str, Any] = {}
    for entry in list_files(revision):
        segments = entry.path.split("/")
        cursor = root
        for segment in segments[:-1]:
            cursor = cursor.setdefault(segment, {})
            if not isinstance(cursor, dict):  # a file and a directory share a name
                break
        else:
            cursor[segments[-1]] = entry
    return _to_nodes(root, "")


def _to_nodes(level: dict[str, Any], prefix: str) -> list[FileTreeNode]:
    directories: list[FileTreeNode] = []
    files: list[FileTreeNode] = []
    for name, value in level.items():
        path = f"{prefix}/{name}" if prefix else name
        if isinstance(value, dict):
            directories.append(FileTreeNode(
                name=name, path=path, type="directory", children=_to_nodes(value, path),
            ))
        else:
            files.append(FileTreeNode(
                name=name, path=path, type="file", size=value.size, is_text=value.is_text,
            ))
    directories.sort(key=lambda item: item.name.lower())
    files.sort(key=lambda item: item.name.lower())
    return directories + files


async def read_file(
    revision: TransformProjectRevision, path: str, *, store: ObjectStore | None = None,
) -> tuple[bytes, FileEntry]:
    safe = validate_path(path)
    entry = (revision.manifest_index or {}).get(safe.value)
    if entry is None:
        raise NotFoundError(f"`{safe.value}` is not in this project.")
    store = store or object_store()
    try:
        data = await store.get(str(entry["key"]))
    except ObjectNotFound as exc:
        raise NotFoundError(
            f"The stored copy of `{safe.value}` is missing."
        ) from exc
    return data, FileEntry(
        path=safe.value,
        size=int(entry.get("size") or len(data)),
        sha256=str(entry.get("sha256") or ""),
        is_text=bool(entry.get("is_text", True)),
    )


async def read_all(
    revision: TransformProjectRevision, *, store: ObjectStore | None = None,
) -> dict[str, bytes]:
    """Every file in a revision.  Used for export and for Git commits."""
    import asyncio

    store = store or object_store()
    index = revision.manifest_index or {}
    paths = list(index)
    blobs = await asyncio.gather(*(store.get(str(index[path]["key"])) for path in paths))
    return dict(zip(paths, blobs))


# ── writing ───────────────────────────────────────────────────────────────


@dataclass(slots=True)
class FileChange:
    """One mutation.  ``content`` is None for a delete."""

    path: str
    content: bytes | None = None
    #: For a rename/move: where the file came from.
    from_path: str | None = None


async def apply_changes(
    session: AsyncSession,
    project: TransformProject,
    *,
    changes: list[FileChange],
    expected_revision_id: uuid.UUID | None,
    actor_id: uuid.UUID | None,
    store: ObjectStore | None = None,
    git_commit_sha: str | None = None,
    git_branch: str | None = None,
) -> TransformProjectRevision:
    """Write a batch of changes as one new revision.

    A batch, not a file: Save All is one action to the person doing it, and
    splitting it into six revisions would give six parses, six chances to catch
    the project half-updated, and a history nobody can read.

    ``expected_revision_id`` is the revision the editor was looking at.  A
    mismatch raises :class:`ConflictError` and nothing is written.
    """
    if not changes:
        raise ValidationError("Nothing to save.", code="TRANSFORM_SAVE_EMPTY")

    current = await working_revision(session, project)
    if expected_revision_id is not None and expected_revision_id != current.id:
        raise ConflictError(
            "Somebody else changed this project while you were editing. "
            "Compare the two versions before saving.",
            code="TRANSFORM_REVISION_STALE",
            details={
                "expected_revision_id": str(expected_revision_id),
                "current_revision_id": str(current.id),
            },
        )

    store = store or object_store()
    index: dict[str, dict[str, Any]] = dict(current.manifest_index or {})

    for change in changes:
        if change.from_path is not None:
            source = validate_path(change.from_path)
            if source.value not in index:
                raise NotFoundError(f"`{source.value}` is not in this project.")
            moved = index.pop(source.value)
            if change.content is None and change.path:
                # A pure move keeps the blob; only the path changes.
                index[validate_path(change.path).value] = moved
                continue

        if change.content is None:
            target = validate_path(change.path)
            if target.value not in index:
                raise NotFoundError(f"`{target.value}` is not in this project.")
            index.pop(target.value)
            continue

        target = validate_path(change.path)
        check_file_size(change.content, target.value)
        key = await store.put_content(change.content)
        index[target.value] = {
            "key": key,
            "size": len(change.content),
            "sha256": digest_of(change.content),
            "is_text": target.is_text,
        }

    check_project_size(len(index))
    if "dbt_project.yml" not in index:
        raise ValidationError(
            "A dbt project must have a `dbt_project.yml`. Deleting it would leave "
            "nothing dbt can run.",
            code="TRANSFORM_PROJECT_FILE_REQUIRED",
        )

    return await _new_revision(
        session, project, index=index, parent=current, actor_id=actor_id,
        git_commit_sha=git_commit_sha, git_branch=git_branch,
    )


async def replace_all(
    session: AsyncSession,
    project: TransformProject,
    *,
    files: dict[str, bytes],
    actor_id: uuid.UUID | None,
    store: ObjectStore | None = None,
    git_commit_sha: str | None = None,
    git_branch: str | None = None,
    parent: TransformProjectRevision | None = None,
) -> TransformProjectRevision:
    """Replace the whole file set.

    Used for the first revision of a project, and for a Git pull -- where the
    remote tree is authoritative and a file deleted upstream must disappear
    here, which a per-file merge would not achieve.
    """
    store = store or object_store()
    check_project_size(len(files))
    index: dict[str, dict[str, Any]] = {}
    for raw_path, content in files.items():
        target = validate_path(raw_path)
        check_file_size(content, target.value)
        index[target.value] = {
            "key": await store.put_content(content),
            "size": len(content),
            "sha256": digest_of(content),
            "is_text": target.is_text,
        }
    if parent is None and project.working_revision_id is not None:
        parent = await get_revision(session, project.working_revision_id)
    return await _new_revision(
        session, project, index=index, parent=parent, actor_id=actor_id,
        git_commit_sha=git_commit_sha, git_branch=git_branch,
    )


async def _new_revision(
    session: AsyncSession,
    project: TransformProject,
    *,
    index: dict[str, dict[str, Any]],
    parent: TransformProjectRevision | None,
    actor_id: uuid.UUID | None,
    git_commit_sha: str | None = None,
    git_branch: str | None = None,
) -> TransformProjectRevision:
    number = (
        await session.scalar(
            select(TransformProjectRevision.revision_number)
            .where(TransformProjectRevision.project_id == project.id)
            .order_by(TransformProjectRevision.revision_number.desc())
            .limit(1)
        )
    ) or 0

    revision = TransformProjectRevision(
        id=uuid.uuid4(),
        project_id=project.id,
        revision_number=number + 1,
        content_hash=content_hash(index),
        manifest_index=index,
        file_count=len(index),
        total_bytes=sum(int(entry.get("size") or 0) for entry in index.values()),
        git_commit_sha=git_commit_sha or (parent.git_commit_sha if parent else None),
        git_branch=git_branch or (parent.git_branch if parent else None),
        parent_revision_id=parent.id if parent else None,
        frozen=False,
        created_by=actor_id,
        created_at=utcnow(),
    )
    session.add(revision)
    await session.flush()

    project.working_revision_id = revision.id
    project.updated_by = actor_id
    # The parse that will follow decides this; until it runs, what the editor
    # holds has not been checked. Saying UNKNOWN is honest, saying OK is not.
    project.parse_status = "PENDING"
    return revision


async def freeze(
    session: AsyncSession, revision: TransformProjectRevision,
) -> TransformProjectRevision:
    """Mark a revision immutable and start a successor for the editor.

    A release points at the frozen revision.  The editor keeps writing, but to
    a new revision, so a save a minute after publishing cannot alter what
    production is about to run.
    """
    if revision.frozen:
        return revision
    revision.frozen = True
    await session.flush()
    return revision


# ── dbt_project.yml ───────────────────────────────────────────────────────


@dataclass(slots=True)
class ProjectFacts:
    """The few things AppBI must know before it can start dbt.

    Read, never written.  A project's own `name` and `profile` decide what
    `profiles.yml` has to contain; without them the runtime cannot construct a
    profile the project will accept.
    """

    name: str | None
    profile: str | None
    version: str | None
    require_dbt_version: Any = None
    model_paths: list[str] | None = None
    valid: bool = True
    error: str | None = None


async def project_facts(
    revision: TransformProjectRevision, *, store: ObjectStore | None = None,
) -> ProjectFacts:
    try:
        data, _ = await read_file(revision, "dbt_project.yml", store=store)
    except NotFoundError:
        return ProjectFacts(
            None, None, None, valid=False,
            error="This project has no `dbt_project.yml`.",
        )
    try:
        document = yaml.safe_load(data.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        # Reported rather than raised: a project with a broken `dbt_project.yml`
        # still opens in the editor, because that is where it gets fixed.
        return ProjectFacts(
            None, None, None, valid=False,
            error=f"`dbt_project.yml` is not valid YAML: {exc}",
        )
    if not isinstance(document, dict):
        return ProjectFacts(
            None, None, None, valid=False,
            error="`dbt_project.yml` does not contain a mapping.",
        )
    paths = document.get("model-paths") or document.get("source-paths")
    return ProjectFacts(
        name=_string(document.get("name")),
        profile=_string(document.get("profile")),
        version=_string(document.get("version")),
        require_dbt_version=document.get("require-dbt-version"),
        model_paths=[str(item) for item in paths] if isinstance(paths, list) else None,
    )


def _string(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


# ── diff ──────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class FileDiff:
    path: str
    #: A | M | D
    change: str
    size_before: int | None = None
    size_after: int | None = None


def diff_revisions(
    before: TransformProjectRevision | None, after: TransformProjectRevision,
) -> list[FileDiff]:
    """What changed between two revisions, by content rather than by timestamp.

    Comparing digests means a file saved with no edit does not appear, which is
    what makes the publish dialog trustworthy -- a list that includes untouched
    files trains people to stop reading it.
    """
    old = (before.manifest_index if before else {}) or {}
    new = after.manifest_index or {}
    changes: list[FileDiff] = []

    for path in sorted(set(old) | set(new)):
        left, right = old.get(path), new.get(path)
        if left is None:
            changes.append(FileDiff(path, "A", None, int(right.get("size") or 0)))
        elif right is None:
            changes.append(FileDiff(path, "D", int(left.get("size") or 0), None))
        elif left.get("sha256") != right.get("sha256"):
            changes.append(FileDiff(
                path, "M", int(left.get("size") or 0), int(right.get("size") or 0),
            ))
    return changes
