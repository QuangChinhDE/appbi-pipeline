#!/usr/bin/env python3
"""Find credentials that were stored in plain configuration, and move them.

    python scripts/scan-plaintext-secrets.py                 # report only
    python scripts/scan-plaintext-secrets.py --fix           # move them
    python scripts/scan-plaintext-secrets.py --fix --audit   # redact audit too

Why this exists
---------------

`split_configuration()` claimed to walk a connector spec recursively and
descended exactly one level, through exactly one `oneOf`. Anything nested
deeper -- `destination-bigquery` keeps its HMAC secret at
`loading_method.credential.hmac_key_secret` -- was written into
`configuration_json` as plain text: readable in the database, copied into the
audit trail, and returned by an endpoint any VIEW role can call.

Fixing the splitter stops it happening again. It does nothing about rows
already written that way, and those rows are the actual exposure.

What `--fix` does
-----------------

For each source and destination, it re-runs the corrected split against the
connector's own spec. Anything the corrected split calls secret but that is
sitting in plain configuration is moved into the encrypted payload under the
same key path, and removed from `configuration_json`. The connector sees an
identical configuration afterwards, because the merge is the exact inverse.

Anything it moves has already been exposed. Rotate those credentials -- the
report says which resources are affected so the list is actionable. Moving
them limits further exposure; it does not undo it.

`--audit` additionally redacts matching values from stored audit payloads.
That is deliberately opt-in: audit is an append-only record and rewriting it
is a decision somebody should make on purpose.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _console import force_utf8  # noqa: E402


async def scan(fix: bool, redact_audit: bool) -> int:
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.core.secrets import secret_store
    from app.models.integration import Destination, Source
    from app.services.catalog import merge_configuration, split_configuration

    store = secret_store
    findings: list[dict] = []

    async with SessionLocal() as session:
        from app.models.engine import ConnectorDefinition

        specs = {
            row.connector_key: (row.spec_schema or {})
            for row in (await session.scalars(select(ConnectorDefinition))).all()
        }

        for model, kind in ((Source, "source"), (Destination, "destination")):
            for actor in (await session.scalars(select(model))).all():
                spec = specs.get(actor.connector_key) or {}
                if not spec:
                    print(f"  ?     {kind} {actor.id} uses {actor.connector_key}, "
                          "whose spec is not in this database; skipped")
                    continue

                plain = dict(actor.configuration_json or {})
                # Re-split what is stored. Whatever comes back on the secret
                # side was misfiled: it should never have been in here.
                keep, misfiled = split_configuration(spec, plain)
                if not misfiled:
                    continue

                paths = _paths(misfiled)
                findings.append({
                    "kind": kind, "id": str(actor.id), "name": actor.name,
                    "connector": actor.connector_key, "paths": paths,
                })
                print(f"  LEAK  {kind} {actor.name!r} ({actor.connector_key}): "
                      f"{', '.join(paths)}")

                if not fix:
                    continue

                # Merge the misfiled values into the existing encrypted payload
                # rather than replacing it, so credentials stored correctly are
                # not lost.
                existing = {}
                if actor.secret_ref:
                    try:
                        existing = await store.read(session, actor.secret_ref)
                    except Exception as exc:              # noqa: BLE001
                        print(f"        could not read {actor.secret_ref}: {exc}")
                actor.secret_ref = await store.write(
                    session, actor.workspace_id,
                    merge_configuration(existing, misfiled),
                    ref=actor.secret_ref)
                actor.configuration_json = keep
                print(f"        moved into {actor.secret_ref}")

        if redact_audit and findings:
            redacted = await _redact_audit(session, findings)
            print(f"\n  redacted {redacted} audit row(s)")

        if fix:
            await session.commit()

    print()
    if not findings:
        print("no plaintext credential found in stored configuration")
        return 0

    print(f"{len(findings)} resource(s) had a credential in plain configuration.")
    if fix:
        print("They have been moved into the encrypted store.")
    else:
        print("Re-run with --fix to move them.")
    print("\nEvery value listed above was readable before this ran. Moving it "
          "limits further exposure and does not undo it -- rotate these "
          "credentials at the source system.")
    return 1


def _paths(node, prefix: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            out += _paths(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if value is not None:
                out += _paths(value, f"{prefix}[{index}]")
    else:
        out.append(prefix)
    return out


async def _redact_audit(session, findings: list[dict]) -> int:
    """Replace known-sensitive keys in stored audit payloads."""
    from sqlalchemy import select

    from app.models.ops import AuditEvent

    sensitive = {path.split(".")[-1].split("[")[0]
                 for finding in findings for path in finding["paths"]}
    changed = 0
    for row in (await session.scalars(select(AuditEvent))).all():
        for column in ("before_summary", "after_summary"):
            payload = getattr(row, column, None)
            if not payload:
                continue
            cleaned, hit = _scrub(payload, sensitive)
            if hit:
                setattr(row, column, cleaned)
                changed += 1
    return changed


def _scrub(node, keys: set[str]):
    if isinstance(node, dict):
        out, hit = {}, False
        for key, value in node.items():
            if key in keys and not isinstance(value, (dict, list)):
                out[key], hit = "********", True
            else:
                out[key], sub = _scrub(value, keys)
                hit = hit or sub
        return out, hit
    if isinstance(node, list):
        results = [_scrub(value, keys) for value in node]
        return [r[0] for r in results], any(r[1] for r in results)
    return node, False


def _bind_from_config(path: str) -> None:
    """Take the database URL and KEK from the deployment's own config.

    Without this the command only works from inside the API container. Run from
    an operator's shell it picks up whatever `DATABASE_URL` happens to be in the
    environment, finds no rows, and prints a clean bill of health for a database
    it never opened. A scanner that cannot fail to connect is worse than none.
    """
    import importlib.util
    import os

    spec = importlib.util.spec_from_file_location(
        "production", ROOT / "scripts" / "production.py")
    production = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(production)

    # `env://` references resolve against the process environment, and for a
    # single-host deployment those values live in the repository's `.env` --
    # which the installer wrote. Load it first, or the demo profile fails to
    # bind for a reason that has nothing to do with the scan.
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            os.environ.setdefault(name.strip(), value.strip())

    config = production.load_config(Path(path))
    datastores = config.get("datastores") or {}
    secrets_config = config.get("secrets") or {}

    database_url = production.resolve_secret(
        str(datastores.get("database_url_ref") or ""))
    encryption_key = production.resolve_secret(
        str(secrets_config.get("encryption_key_ref") or ""))

    if not database_url:
        raise SystemExit(
            f"{path} does not yield a readable database URL. A `secret://` "
            "reference is deliberately unreadable from here, so run this "
            "inside the deployment instead.")
    if not encryption_key:
        raise SystemExit(
            f"{path} does not yield a readable encryption key, so misfiled "
            "credentials could not be re-encrypted even once found.")

    os.environ["DATABASE_URL"] = database_url
    os.environ["SECRET_ENCRYPTION_KEY"] = encryption_key
    print(f"bound to the database named by {path}")


def main() -> int:
    force_utf8()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="",
                        help="a production.py config to read the database URL "
                             "and encryption key from, so this can be run from "
                             "an operator's shell rather than only from inside "
                             "the API container")
    parser.add_argument("--fix", action="store_true",
                        help="move misfiled credentials into the encrypted store")
    parser.add_argument("--audit", action="store_true",
                        help="also redact matching values in stored audit rows")
    args = parser.parse_args()

    if args.config:
        _bind_from_config(args.config)

    print("scanning stored configuration against the corrected split\n")
    return asyncio.run(scan(args.fix, args.audit))


if __name__ == "__main__":
    raise SystemExit(main())
