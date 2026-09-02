"""Project file paths, and materialising a revision onto disk.

Two responsibilities that belong together because both turn on the same
question: which paths are part of a dbt project, and what is a path allowed to
look like.

Path validation is the security boundary for the file API.  A save request names
its own path, and `models/../../../root/.ssh/authorized_keys` is a string a
person can type into a new-file dialog.  Everything is checked here, once,
against a POSIX-relative form -- so callers cannot each get it subtly wrong.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.core.config import settings
from app.core.errors import ValidationError
from app.transforms.storage import ObjectStore, digest_of

#: Directories dbt reads, plus the files that configure it.
#:
#: Not a whitelist of what may exist -- a project may contain a `.github/`
#: directory, a `README.md`, a `.sqlfluff` -- but the set the resource tree
#: understands and the scaffolder creates.
DBT_DIRECTORIES = (
    "models", "macros", "tests", "seeds", "snapshots", "analyses", "data",
    "docs", "assets",
)
DBT_ROOT_FILES = (
    "dbt_project.yml", "packages.yml", "dependencies.yml", "selectors.yml",
    "profile_template.yml", "README.md",
)

#: Suffixes the editor can open as text.  Everything else is offered as a
#: download rather than rendered, because a binary in a textarea is corruption
#: waiting for a Save.
TEXT_SUFFIXES = frozenset({
    ".sql", ".yml", ".yaml", ".md", ".csv", ".json", ".txt", ".py", ".jinja",
    ".sqlfluff", ".gitignore", ".toml", ".cfg", ".ini", ".rst", ".tsv",
})

#: Never written into a project workspace, whatever a request says.
#:
#: `target/` and `dbt_packages/` are dbt's own outputs: storing them in a
#: revision would make every parse dirty the project and race with the runtime.
#: `.git` is managed by the Git service, not by file saves.
RESERVED_PREFIXES = ("target/", "dbt_packages/", ".git/", "logs/", ".profiles/")
RESERVED_NAMES = ("target", "dbt_packages", ".git", "logs", ".profiles")

_SEGMENT = re.compile(r"^[A-Za-z0-9._\- ]{1,120}$")
MAX_DEPTH = 12


@dataclass(frozen=True, slots=True)
class ProjectPath:
    """A validated, POSIX-relative path inside a project."""

    value: str

    @property
    def suffix(self) -> str:
        return PurePosixPath(self.value).suffix.lower()

    @property
    def name(self) -> str:
        return PurePosixPath(self.value).name

    @property
    def parent(self) -> str:
        parent = str(PurePosixPath(self.value).parent)
        return "" if parent == "." else parent

    @property
    def is_text(self) -> bool:
        return self.suffix in TEXT_SUFFIXES or self.name in (
            ".gitignore", ".gitattributes", "Makefile",
        )

    def __str__(self) -> str:
        return self.value


def validate_path(raw: str, *, allow_reserved: bool = False) -> ProjectPath:
    """Normalise and check one project-relative path.

    Rejects, in order: emptiness, absolute paths, drive letters, backslashes,
    NULs, `.`/`..` segments, over-long or oddly-charactered segments, excessive
    depth, and dbt's own output directories.

    Normalisation is deliberately not `os.path.normpath`: that resolves `..`
    rather than refusing it, so `models/../../x` would quietly become `../x` and
    pass a later prefix check on a different platform.
    """
    if not raw or not isinstance(raw, str):
        raise ValidationError("A file path is required.", code="TRANSFORM_PATH_REQUIRED")
    # Only the outer whitespace of the whole string is stripped; a trailing
    # space inside a segment is rejected below rather than silently repaired,
    # because `a.sql ` and `a.sql` must not become two different rows.
    text = raw.strip("\r\n\t").replace("\\", "/")
    if not text:
        raise ValidationError("A file path is required.", code="TRANSFORM_PATH_REQUIRED")
    if "\x00" in text:
        raise ValidationError("That file path is not valid.", code="TRANSFORM_PATH_INVALID")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise ValidationError(
            "Use a path inside the project, not an absolute path.",
            code="TRANSFORM_PATH_ABSOLUTE",
        )
    while "//" in text:
        text = text.replace("//", "/")
    text = text.strip("/")

    segments = [segment for segment in text.split("/") if segment]
    if not segments:
        raise ValidationError("A file path is required.", code="TRANSFORM_PATH_REQUIRED")
    if len(segments) > MAX_DEPTH:
        raise ValidationError(
            "That path is nested too deeply.", code="TRANSFORM_PATH_TOO_DEEP",
        )
    for segment in segments:
        if segment in (".", ".."):
            raise ValidationError(
                "A file path cannot contain `.` or `..`.", code="TRANSFORM_PATH_TRAVERSAL",
            )
        if not _SEGMENT.match(segment):
            raise ValidationError(
                f"`{segment}` is not allowed in a file path. Use letters, numbers, "
                "dots, dashes and underscores.",
                code="TRANSFORM_PATH_SEGMENT_INVALID", details={"segment": segment},
            )
        if segment.endswith((".", " ")):
            # Windows silently strips these, so `a.sql ` and `a.sql` would be
            # two rows in storage and one file on the runtime's disk.
            raise ValidationError(
                "A path segment cannot end with a space or a dot.",
                code="TRANSFORM_PATH_SEGMENT_INVALID", details={"segment": segment},
            )

    normalised = "/".join(segments)
    if not allow_reserved:
        if segments[0] in RESERVED_NAMES or normalised.startswith(RESERVED_PREFIXES):
            raise ValidationError(
                f"`{segments[0]}` is generated by dbt and is not part of the project.",
                code="TRANSFORM_PATH_RESERVED", details={"path": normalised},
            )
    return ProjectPath(normalised)


def validate_directory(raw: str) -> str:
    """Check a directory path, which may legitimately be empty (the root)."""
    if raw in (None, "", "/", "."):
        return ""
    return validate_path(raw).value


def is_dbt_project_root(paths: list[str]) -> bool:
    return any(path == "dbt_project.yml" for path in paths)


def find_project_root(paths: list[str]) -> str | None:
    """Where `dbt_project.yml` sits in an arbitrary file list.

    A repository often keeps the dbt project in a subdirectory next to
    infrastructure or docs.  The shallowest match wins; a project vendoring
    another project's `dbt_project.yml` under `dbt_packages/` must not win over
    the real one, which is why those are skipped.
    """
    candidates = [
        path for path in paths
        if PurePosixPath(path).name == "dbt_project.yml"
        and not any(part in RESERVED_NAMES for part in PurePosixPath(path).parts[:-1])
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda path: (path.count("/"), path))
    parent = str(PurePosixPath(candidates[0]).parent)
    return "" if parent == "." else parent


@dataclass(slots=True)
class MaterialisedWorkspace:
    """A private directory holding one revision, ready for dbt.

    Deleted in ``cleanup`` regardless of outcome.  Nothing survives an
    invocation: not the project, not the profile, not the credentials.
    """

    root: Path
    project_dir: Path
    profiles_dir: Path
    target_path: Path
    tmpdir: Path

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


async def materialise(
    *,
    store: ObjectStore,
    files: dict[str, dict],
    workspace_root: Path,
    prefix: str,
) -> MaterialisedWorkspace:
    """Write a revision's files into a fresh private workspace.

    ``files`` is a revision's ``manifest_index``: path -> {key, size, sha256}.

    Blobs are fetched concurrently and each is verified against the digest the
    revision recorded.  A silently corrupted blob would otherwise reach dbt as a
    syntax error in a file the user never edited, which is close to impossible
    to diagnose from the other end.
    """
    workspace_root.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix=f"appbi-{prefix}-", dir=workspace_root))
    project_dir = root / "project"
    profiles_dir = root / "profiles"
    target_path = root / "target"
    tmpdir = root / "tmp"
    for directory in (project_dir, profiles_dir, target_path, tmpdir):
        directory.mkdir(parents=True, exist_ok=True)
    # The profile holds a warehouse credential for the life of the process.
    os.chmod(profiles_dir, 0o700)

    async def write(path: str, entry: dict) -> None:
        # Revalidated on the way out of storage as well as on the way in: a row
        # written by an older version of this code must not be able to place a
        # file outside the workspace now.
        safe = validate_path(path, allow_reserved=False)
        data = await store.get(str(entry["key"]))
        expected = entry.get("sha256")
        if expected and digest_of(data) != expected:
            raise ValidationError(
                f"The stored copy of `{path}` does not match its recorded checksum.",
                code="TRANSFORM_BLOB_CORRUPT", details={"path": path},
            )
        target = project_dir / safe.value
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_bytes, data)

    if files:
        await asyncio.gather(*(write(path, entry) for path, entry in files.items()))

    return MaterialisedWorkspace(
        root=root, project_dir=project_dir, profiles_dir=profiles_dir,
        target_path=target_path, tmpdir=tmpdir,
    )


def check_file_size(data: bytes, path: str) -> None:
    limit = settings.transform_max_file_bytes
    if len(data) > limit:
        raise ValidationError(
            f"`{path}` is {len(data) // 1024} KB, over the {limit // 1024} KB limit "
            "for a project file.",
            code="TRANSFORM_FILE_TOO_LARGE", details={"path": path, "limit": limit},
        )


def check_project_size(count: int) -> None:
    limit = settings.transform_max_project_files
    if count > limit:
        raise ValidationError(
            f"This project has {count} files, over the {limit} file limit.",
            code="TRANSFORM_PROJECT_TOO_LARGE", details={"limit": limit},
        )
