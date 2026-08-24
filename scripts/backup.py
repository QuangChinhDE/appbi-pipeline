#!/usr/bin/env python3
"""Back up and restore the product's state.

Two things have to survive an incident together, and restoring one without the
other is worse than restoring neither:

  * The product database — pipelines, runs, schema snapshots, and the encrypted
    credential store.
  * The key-encryption key. Every credential in that database is wrapped with
    it. A dump restored without the matching KEK is a database full of secrets
    nobody can read, and every source in it fails on the next sync with a
    decryption error rather than anything that names the real cause.

So a backup here records which key it was taken under, and a restore refuses to
proceed against a different one unless told explicitly.

    python scripts/backup.py dump --out backups/
    python scripts/backup.py list backups/
    python scripts/backup.py restore backups/appbi-20260823T101500Z.sql.gz

Airbyte's own database is *not* covered. It is a separate deployment with its
own lifecycle; see docs/RUNBOOK-backup-restore.md for what that means when the
two are restored to different points in time.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import force_utf8  # noqa: E402

CONTAINER = os.getenv("POSTGRES_CONTAINER", "appbi-pipeline-postgres")
DATABASE = os.getenv("POSTGRES_DB", "appbi_integration")
USER = os.getenv("POSTGRES_USER", "appbi")


def key_fingerprint() -> str | None:
    """Identify the KEK without recording it.

    A backup has to be able to say "this was taken under a different key" at
    restore time. Storing the key to do that would put every credential's
    protection in the backup directory, so a salted digest goes in instead: it
    compares equal for the same key and reveals nothing.
    """
    key = os.getenv("SECRET_ENCRYPTION_KEY", "")
    if not key:
        return None
    return hashlib.sha256(b"appbi-kek-fingerprint:" + key.encode()).hexdigest()[:16]


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, **kwargs)


def cmd_dump(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dump_path = out_dir / f"appbi-{stamp}.sql.gz"
    meta_path = out_dir / f"appbi-{stamp}.json"

    print(f"dumping {DATABASE} from {CONTAINER} ...", flush=True)
    # --clean --if-exists so the restore is idempotent against a database that
    # already has objects; without it a partial restore leaves a mixture of old
    # and new and nothing says so.
    result = run(["docker", "exec", CONTAINER, "pg_dump",
                  "-U", USER, "-d", DATABASE, "--clean", "--if-exists"])
    if result.returncode != 0:
        print(result.stderr.decode(errors="replace")[:800], file=sys.stderr)
        return 1

    with gzip.open(dump_path, "wb") as handle:
        handle.write(result.stdout)

    digest = hashlib.sha256(dump_path.read_bytes()).hexdigest()
    fingerprint = key_fingerprint()
    meta = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": DATABASE,
        "bytes": dump_path.stat().st_size,
        "sha256": digest,
        "kek_fingerprint": fingerprint,
        # Recorded so a restore can warn when the engine half has moved on.
        "engine_type": os.getenv("ENGINE_TYPE", ""),
        "airbyte_workspace_id": os.getenv("AIRBYTE_WORKSPACE_ID", ""),
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    print(f"  {dump_path}  ({meta['bytes'] / 1_048_576:.1f} MiB)")
    print(f"  sha256 {digest[:16]}...")
    if fingerprint:
        print(f"  KEK fingerprint {fingerprint}")
    else:
        print("  !! SECRET_ENCRYPTION_KEY is not set in this shell, so the backup "
              "does not record which key its credentials are wrapped with. "
              "A restore cannot then warn you about a mismatch.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    directory = Path(args.directory)
    dumps = sorted(directory.glob("appbi-*.sql.gz"))
    if not dumps:
        print(f"no backups in {directory}")
        return 1

    current = key_fingerprint()
    for dump in dumps:
        meta_path = dump.with_suffix("").with_suffix(".json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        fingerprint = meta.get("kek_fingerprint")
        if fingerprint and current and fingerprint != current:
            note = "  <- different KEK; credentials in it will not decrypt here"
        elif not fingerprint:
            note = "  <- no KEK recorded"
        else:
            note = ""
        size = dump.stat().st_size / 1_048_576
        print(f"  {dump.name}  {size:6.1f} MiB  {meta.get('created_at', '?')}{note}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    dump_path = Path(args.dump)
    if not dump_path.exists():
        print(f"no such backup: {dump_path}", file=sys.stderr)
        return 1

    meta_path = dump_path.with_suffix("").with_suffix(".json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    # Integrity before anything destructive.
    if meta.get("sha256"):
        actual = hashlib.sha256(dump_path.read_bytes()).hexdigest()
        if actual != meta["sha256"]:
            print(f"checksum mismatch: the backup is not what it was when written.\n"
                  f"  expected {meta['sha256']}\n  actual   {actual}", file=sys.stderr)
            return 1
        print("checksum ok")

    recorded = meta.get("kek_fingerprint")
    current = key_fingerprint()
    if recorded and current and recorded != current:
        print(f"\nKEK MISMATCH\n"
              f"  backup taken under {recorded}\n"
              f"  this environment is {current}\n\n"
              "Every credential in this dump is wrapped with the backup's key. "
              "Restoring it here produces a database whose sources all fail on "
              "their next sync with a decryption error. Restore the key first, "
              "or pass --accept-key-mismatch if you intend to re-enter every "
              "credential by hand.", file=sys.stderr)
        if not args.accept_key_mismatch:
            return 1
        print("  proceeding anyway (--accept-key-mismatch)")

    if not args.yes:
        print(f"\nThis replaces the contents of {DATABASE} on {CONTAINER}.")
        if input("Type the database name to confirm: ").strip() != DATABASE:
            print("cancelled")
            return 1

    print(f"restoring {dump_path.name} into {DATABASE} ...", flush=True)
    with gzip.open(dump_path, "rb") as handle:
        sql = handle.read()

    process = subprocess.Popen(
        ["docker", "exec", "-i", CONTAINER, "psql", "-U", USER, "-d", DATABASE,
         "--set", "ON_ERROR_STOP=on"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    _, stderr = process.communicate(sql)
    if process.returncode != 0:
        print(stderr.decode(errors="replace")[-2000:], file=sys.stderr)
        return 1

    print("restored.")
    print("\nNext, and not optional:")
    print("  1. python scripts/stack.py airbyte   # bring the API back up")
    print("  2. Check /readyz?deep=1 reports the engine reachable")
    print("  3. Re-check one source per connector type. A restore rolls the")
    print("     product back but not Airbyte, so engine references in")
    print("     engine_mappings may point at resources that have since been")
    print("     deleted — see docs/RUNBOOK-backup-restore.md.")
    return 0


def main() -> int:
    force_utf8()
    if shutil.which("docker") is None:
        print("docker is not on PATH", file=sys.stderr)
        return 2

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    dump = sub.add_parser("dump", help="write a compressed dump plus metadata")
    dump.add_argument("--out", default="backups")

    listing = sub.add_parser("list", help="show backups and flag KEK mismatches")
    listing.add_argument("directory", nargs="?", default="backups")

    restore = sub.add_parser("restore", help="restore a dump into the product database")
    restore.add_argument("dump")
    restore.add_argument("--yes", action="store_true", help="skip the confirmation")
    restore.add_argument("--accept-key-mismatch", action="store_true",
                         help="restore even though the KEK differs (credentials will "
                              "not decrypt and must be re-entered)")

    args = parser.parse_args()
    return {"dump": cmd_dump, "list": cmd_list, "restore": cmd_restore}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
