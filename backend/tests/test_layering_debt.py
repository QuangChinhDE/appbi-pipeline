"""Layering debt that exists today, frozen so it can shrink but never grow.

Two layering rules are stated in AGENTS.md and are not at zero:

1. Services on a request path do not commit -- the router owns the
   transaction. The services that do commit are the worker-driven ones
   (`runs.py`, `outbox.py`, `catalog.py`), which run outside any request.

2. Lower layers do not import upward. `adapters/` reads two constants from
   `services/builder_manifest`, lazily, and `core/secrets.py` imports
   `services/catalog.flatten_secret_paths` locally to avoid a cycle.

None of this is a defect worth a refactor on its own, and refactoring it here
would be exactly the unrelated core change governance work must not make. What
must not happen is the next exception arriving under cover of the existing
ones. So the current count per file is the allowance:

* a new file with a violation fails;
* an existing file with more violations fails;
* a file with fewer fails too, asking for its allowance to be lowered, so the
  register never keeps slack someone can quietly spend.

`scripts/guardrail.py` refuses a pull request that raises any number in
`DEBT`, so the allowance cannot be increased to make this pass.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP = ROOT / "backend" / "app"

#: "<rule>:<path relative to backend/app>" -> violations allowed. Only lower these.
DEBT: dict[str, int] = {
    "service-commit:services/runs.py": 9,
    "service-commit:services/outbox.py": 6,
    "service-commit:services/catalog.py": 2,
    "upward-import:adapters/airbyte_api/adapter.py": 1,
    "upward-import:adapters/airbyte_protocol/adapter.py": 1,
    "upward-import:core/secrets.py": 1,
}

COMMIT = re.compile(r"\.commit\(\)")
#: models, core and adapters sit below services and the API.
UPWARD = re.compile(r"^\s*(?:from|import)\s+app\.(?:services|api)\b", re.M)
LOWER_LAYERS = ("models", "core", "adapters")


def _files(sub: str) -> list[pathlib.Path]:
    return [p for p in (APP / sub).rglob("*.py") if "__pycache__" not in p.parts]


def measure() -> dict[str, int]:
    found: dict[str, int] = {}
    for path in _files("services"):
        count = len(COMMIT.findall(path.read_text(encoding="utf-8")))
        if count:
            found[f"service-commit:{path.relative_to(APP).as_posix()}"] = count
    for layer in LOWER_LAYERS:
        for path in _files(layer):
            count = len(UPWARD.findall(path.read_text(encoding="utf-8")))
            if count:
                found[f"upward-import:{path.relative_to(APP).as_posix()}"] = count
    return found


def test_layering_debt_does_not_grow() -> None:
    grown = {k: (DEBT.get(k, 0), v) for k, v in measure().items() if v > DEBT.get(k, 0)}
    assert not grown, (
        "New layering violations (allowed -> found). Services on a request path "
        "return and let the router commit; models/core/adapters must not import "
        "services or the API:\n  "
        + "\n  ".join(f"{k}: {a} -> {f}" for k, (a, f) in sorted(grown.items()))
    )


def test_paid_down_debt_lowers_its_allowance() -> None:
    found = measure()
    slack = {k: (v, found.get(k, 0)) for k, v in DEBT.items() if found.get(k, 0) < v}
    assert not slack, (
        "Debt was paid down -- lower these allowances in DEBT so the slack "
        "cannot be spent again:\n  "
        + "\n  ".join(f"{k}: {a} -> {f}" for k, (a, f) in sorted(slack.items()))
    )


def test_the_register_detects_a_new_violation(tmp_path, monkeypatch) -> None:
    """The ratchet is only worth something if it fires."""
    fake = tmp_path / "app"
    (fake / "services").mkdir(parents=True)
    (fake / "models").mkdir()
    (fake / "services" / "new_service.py").write_text("db.commit()\n", encoding="utf-8")
    (fake / "models" / "thing.py").write_text("from app.services import x\n", encoding="utf-8")
    monkeypatch.setattr(__import__(__name__, fromlist=["APP"]), "APP", fake)
    assert measure() == {
        "service-commit:services/new_service.py": 1,
        "upward-import:models/thing.py": 1,
    }
