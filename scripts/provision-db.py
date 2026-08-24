#!/usr/bin/env python3
"""Create the product's database role, and take away what it should not have.

    python scripts/provision-db.py --dsn postgresql://admin@host/postgres \\
        --product-db appbi_integration --role appbi_product
    python scripts/provision-db.py --dsn ... --verify

ADR-001 says the product and the engine never share a database. That is a
decision the application enforces at startup — but only for its own process.
This enforces it at the database, where it holds for anything that connects
with these credentials, including a psql session at 3am during an incident.

The SQL lived in the ADR as a code block nobody could run. A block of SQL in a
document is a suggestion; the difference matters because the one line people
forget (`REVOKE CONNECT ... FROM PUBLIC`) makes every other line pointless when
omitted, and nothing reports it.

Idempotent. `--verify` changes nothing and exits non-zero if the separation is
not actually in place, which is what `production.py doctor` calls.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import force_utf8  # noqa: E402


def connect(dsn: str):
    try:
        import psycopg
    except ImportError:
        raise SystemExit(
            "psycopg is not installed: pip install 'psycopg[binary]'\n"
            "(the backend already depends on it; run this from backend/ or "
            "install it here)")
    return psycopg.connect(dsn, autocommit=True)


def provision(dsn: str, *, role: str, password: str, product_db: str,
              engine_db: str) -> int:
    """Grant on the product's database, revoke on the engine's."""
    with connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
        exists = bool(cursor.fetchone())
        # CREATE/ALTER ROLE are utility statements: Postgres rejects a bind
        # parameter for the password outright. psycopg's sql.Literal does the
        # quoting instead -- string formatting here would be an injection point
        # reachable from whatever generates the password.
        from psycopg import sql

        statement = sql.SQL(
            "ALTER ROLE {role} WITH LOGIN PASSWORD {password}" if exists
            else "CREATE ROLE {role} LOGIN PASSWORD {password}"
        ).format(role=sql.Identifier(role), password=sql.Literal(password))
        print(f"  {'resetting the password for' if exists else 'creating'} role {role}")
        cursor.execute(statement)

        print(f"  granting on {product_db}")
        cursor.execute(f'GRANT CONNECT ON DATABASE "{product_db}" TO "{role}"')

        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (engine_db,))
        engine_present = bool(cursor.fetchone())

    if engine_present:
        with connect(dsn) as connection, connection.cursor() as cursor:
            print(f"  revoking on {engine_db}")
            # The line people forget. Postgres grants CONNECT to PUBLIC by
            # default, so revoking it from the role alone changes nothing at
            # all -- and the revoke appears to succeed.
            cursor.execute(f'REVOKE CONNECT ON DATABASE "{engine_db}" FROM PUBLIC')
            cursor.execute(f'REVOKE ALL ON DATABASE "{engine_db}" FROM "{role}"')
    else:
        print(f"  {engine_db} is not on this instance -- nothing to revoke, "
              "which is the topology ADR-001 actually wants")

    # Object-level grants have to run inside the product's own database.
    product_dsn = _switch_database(dsn, product_db)
    with connect(product_dsn) as connection, connection.cursor() as cursor:
        print(f"  granting objects inside {product_db}")
        for statement in (
            f'GRANT USAGE, CREATE ON SCHEMA public TO "{role}"',
            f'GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO "{role}"',
            f'GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO "{role}"',
            # Without these two the next migration creates tables the product
            # cannot read, and the failure looks like a broken migration rather
            # than a missing grant.
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
            f'GRANT ALL ON TABLES TO "{role}"',
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
            f'GRANT ALL ON SEQUENCES TO "{role}"',
        ):
            cursor.execute(statement)

    print("\nprovisioned. Verify it from the role's own credentials:")
    print(f"  python scripts/provision-db.py --dsn <role dsn> --verify "
          f"--engine-db {engine_db}")
    return 0


def _switch_database(dsn: str, database: str) -> str:
    head, _, _ = dsn.rpartition("/")
    return f"{head}/{database}"


def verify(dsn: str, *, engine_db: str) -> int:
    """Prove the separation from the role's own credentials.

    Connects as whoever the DSN names -- which must be the product's role, not
    an admin. Verifying as a superuser proves nothing: a superuser can reach
    everything by definition, and the check would pass on a broken deployment.
    """
    problems: list[str] = []

    with connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT current_user, current_database()")
        role, database = cursor.fetchone()
        print(f"  connected as {role} to {database}")

        cursor.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        row = cursor.fetchone()
        if row and row[0]:
            problems.append(
                f"{role} is a superuser, so this check cannot prove anything. "
                "Run it with the product's own least-privilege credentials.")

        cursor.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
            "AND table_name IN ('actor','actor_definition','connection','attempts')")
        if cursor.fetchone()[0] >= 3:
            problems.append(
                f"{database} contains Airbyte's own schema; the product must "
                "not share a database with the engine (ADR-001)")

        cursor.execute("SELECT has_database_privilege(current_user, %s, 'CONNECT')",
                       (engine_db,))
        row = cursor.fetchone()
        if row and row[0]:
            problems.append(
                f"{role} may CONNECT to {engine_db!r}. Revoke it -- and "
                "remember REVOKE CONNECT ... FROM PUBLIC, without which "
                "revoking from the role alone does nothing.")
        else:
            print(f"  {role} cannot connect to {engine_db}")

    if problems:
        print("\nSEPARATION NOT ENFORCED", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("\nseparation holds")
    return 0


def main() -> int:
    force_utf8()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dsn", required=True,
                        help="admin DSN to provision with, or the product's own "
                             "DSN when using --verify")
    parser.add_argument("--role", default="appbi_product")
    parser.add_argument("--product-db", default="appbi_integration")
    parser.add_argument("--engine-db", default="airbyte")
    parser.add_argument("--password", default="",
                        help="omit to be prompted; never pass it on a shared shell")
    parser.add_argument("--verify", action="store_true",
                        help="check only, change nothing")
    args = parser.parse_args()

    if args.verify:
        return verify(args.dsn, engine_db=args.engine_db)

    password = args.password or getpass.getpass(f"password for {args.role}: ")
    if not password:
        raise SystemExit("a password is required")
    return provision(args.dsn, role=args.role, password=password,
                     product_db=args.product_db, engine_db=args.engine_db)


if __name__ == "__main__":
    raise SystemExit(main())
