#!/usr/bin/env python3
"""Rotate the key-encryption key without touching a single credential.

The credential store is envelope-encrypted: each secret has its own data key,
and only that data key is wrapped with `SECRET_ENCRYPTION_KEY`. Rotating the
KEK therefore means unwrapping and rewrapping a few dozen bytes per record. The
ciphertext holding the actual password is never decrypted and never rewritten.

    python scripts/rotate-kek.py generate
    python scripts/rotate-kek.py plan     --new-key "<new>"
    python scripts/rotate-kek.py rotate   --new-key "<new>"

Run it inside the API container, which already has the database and the current
key:

    docker exec -e NEW_KEK="<new>" appbi-pipeline-api \\
        python -m scripts.rotate_kek rotate

or, from the host, against a stack that is up (see the runbook). Back up first:
this rewrites a column in every secret row.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import force_utf8  # noqa: E402


def _load_app():
    """Import the application lazily, with a usable message when it is absent.

    This script is useful from the host (to generate a key) and from inside the
    container (to do the work), and only the second needs the app importable.
    """
    # Two layouts have to work: the repo, where the app is ../backend, and the
    # container, where the script is copied to /scripts and the app lives at
    # the WORKDIR. Neither is derivable from the other, so both are tried.
    candidates = [
        Path(__file__).resolve().parent.parent / "backend",
        Path("/srv"),
        Path.cwd(),
    ]
    for candidate in candidates:
        if (candidate / "app").is_dir():
            sys.path.insert(0, str(candidate))
            break

    try:
        from app.core.db import SessionLocal
        from app.core.secrets import build_kek, rewrap_all
    except ImportError as exc:
        raise SystemExit(
            f"Cannot import the application ({exc}).\n"
            "  Run this inside the API container, which has the dependencies and "
            "the database connection:\n"
            "    docker exec appbi-pipeline-api python /scripts/rotate-kek.py ..."
        )
    return SessionLocal, build_kek, rewrap_all


def cmd_generate() -> int:
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    print(key)
    print("\nStore this in the secret manager BEFORE rotating. A rotation that "
          "completes with the new key lost leaves every credential unreadable.",
          file=sys.stderr)
    return 0


async def _run(new_key: str, dry_run: bool) -> int:
    SessionLocal, build_kek, rewrap_all = _load_app()
    from app.core.config import settings

    old_key = settings.secret_encryption_key
    if not old_key:
        print("SECRET_ENCRYPTION_KEY is not set; there is nothing to rotate from.",
              file=sys.stderr)
        return 1
    if new_key == old_key:
        print("The new key is the current key. Nothing to do.", file=sys.stderr)
        return 1

    # Validate both before touching anything: a typo in the new key discovered
    # halfway through is the worst possible time to discover it.
    try:
        build_kek(old_key)
        build_kek(new_key)
    except RuntimeError as exc:
        print(f"Key rejected: {exc}", file=sys.stderr)
        return 1

    from sqlalchemy import func, select

    from app.models.secret import SecretRecord

    async with SessionLocal() as session:
        total = await session.scalar(select(func.count()).select_from(SecretRecord))
        print(f"{total} secret record(s) in the store")

        if dry_run:
            print("\nplan: unwrap each data key with the current KEK and rewrap "
                  "with the new one.\n"
                  "  ciphertext is not read, not decrypted and not rewritten\n"
                  "  records that do not unwrap with the current key are skipped, "
                  "which makes a re-run safe\n"
                  "\nRe-run with `rotate` to apply.")
            return 0

        rotated, skipped = await rewrap_all(session, old_key=old_key, new_key=new_key)

    print(f"\nrotated {rotated}, skipped {skipped}")
    if skipped:
        print("  Skipped records did not unwrap with the current key. That is "
              "expected when finishing an interrupted rotation; if this is a "
              "first run, some records belong to a third key and need "
              "investigating before you retire the old one.")

    print("\nNow, in this order:")
    print("  1. Put the new key in SECRET_ENCRYPTION_KEY for api, worker and migrate.")
    print("  2. Restart them.")
    print("  3. Test one source per connector to prove a credential decrypts.")
    print("  4. Only then remove the old key from the secret store.")
    print("\nBackups taken before this point are wrapped with the OLD key. Keep "
          "it as long as you keep them - scripts/backup.py records which key "
          "each dump belongs to.")
    return 0


def main() -> int:
    force_utf8()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("generate", help="print a new 32-byte urlsafe-base64 key")

    for name, help_text in (("plan", "report what a rotation would do"),
                            ("rotate", "rewrap every data key under the new KEK")):
        parser_ = sub.add_parser(name, help=help_text)
        parser_.add_argument("--new-key", default=os.getenv("NEW_KEK", ""),
                             help="the new KEK (or the NEW_KEK environment variable)")

    args = parser.parse_args()
    if args.command == "generate":
        return cmd_generate()

    if not args.new_key:
        print("--new-key (or NEW_KEK) is required.\n"
              "  Generate one: python scripts/rotate-kek.py generate", file=sys.stderr)
        return 1
    return asyncio.run(_run(args.new_key, dry_run=args.command == "plan"))


if __name__ == "__main__":
    raise SystemExit(main())
