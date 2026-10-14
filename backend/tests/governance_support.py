"""Shared helpers for the governance tests (not itself a test module).

The governance checks read a whole repository -- tracked files, rule globs,
paths named in prose -- so a test of one has to hand it a repository. Copying
the real one is slow and drags in images; instead `make_repo` builds a throwaway
git repository that has every tracked path of this checkout as an empty file,
plus the real content of the files the checks actually read. A test then breaks
exactly one thing in it and asserts the check notices.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]

#: Files whose content the governance checks read. Everything else only needs
#: to exist.
REAL_CONTENT_PREFIXES = (
    "AGENTS.md", ".claude/", "docs/features/", "scripts/", ".github/",
    "engine-lock.json", "connector-lock.json", "compatibility.yaml",
    "backend/app/adapters/", "backend/app/services/", "backend/app/api/",
    "backend/app/transforms/",
)


def load_script(name: str) -> ModuleType:
    """Import a hyphenated script under scripts/ as a module."""
    path = ROOT / "scripts" / name
    module_name = "gov_" + path.stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [p for p in out.split("\0") if p and (ROOT / p).is_file()]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=test@example.invalid", "-c", "user.name=test",
         "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout


def make_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    for rel in tracked():
        dest = repo / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if rel.startswith(REAL_CONTENT_PREFIXES):
            shutil.copyfile(ROOT / rel, dest)
        else:
            dest.touch()
    git(repo, "init", "-q", "-b", "master")
    git(repo, "add", "-A", "-f")
    git(repo, "commit", "-q", "-m", "baseline")
    return repo
