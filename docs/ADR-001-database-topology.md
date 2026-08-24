# ADR-001 — The product's database is separate from Airbyte's

**Status:** accepted · 2026-08-23

## The question

Airbyte keeps its own configuration and job history in Postgres. So does this
product. Should they share?

On the staging stack they currently do share an *instance* — `airbyte` and
`appbi_integration` are two databases on `appbi-pipeline-postgres`. That is
convenient and it is how the question came up.

## Decision

**Two databases always. Two instances in production.**

| | Staging / local | Production |
|---|---|---|
| Same instance | acceptable | no |
| Same database | never | never |

The product refuses to start if `DATABASE_URL` points at a database containing
Airbyte's schema, and warns in production if Airbyte's database is visible on
the same instance (`app/core/readiness.py`, `check_database_separation`).

## Why not one database

**The guardrail becomes unenforceable.** The architecture says the product
never reads Airbyte's metadata database — it goes through the Config API, so
that Airbyte's internal schema stays Airbyte's business. Measured on the
staging stack before this ADR: the product's role could read **all 47** of
Airbyte's tables. Nothing would have noticed a service starting to do so, and a
single convenient `SELECT` during an incident becomes a dependency on a schema
nobody promised us.

**Migrations collide.** Both sides run their own migrations against their own
schema on their own upgrade schedule. Sharing a database means Airbyte's
bootloader and this product's Alembic run `ALTER` statements in the same
namespace, and an upgrade of either can block the other. The product already
refuses to adopt a database whose shape it cannot account for; sharing would
make that check fire on Airbyte's tables.

**Blast radius.** Airbyte writes a row per attempt and keeps job history
indefinitely by default. A busy deployment filling the disk should degrade
syncs, not take down the control plane that reports the outage and lets someone
act on it.

**Different load shapes.** Airbyte's database is write-heavy and churny; the
product's is mostly small reads with occasional writes. They want different
connection pools, different tuning, and eventually different instance sizes.

## Why the same instance is tolerable outside production

Cost and operational simplicity are real. On staging the trade is fine and the
warning says why it is a trade rather than a mistake. In production the coupling
is not worth the saving on a managed Postgres.

## The part that matters for where this product is going

This product is not a UI over Airbyte. It already owns things Airbyte has no
concept of — its own scheduling, tenancy and RBAC, its own alerting, health
model and audit trail, and a Connector Builder — and the plan is for it to grow
further past what Airbyte provides.

That makes the storage decision a directional one, not just an operational one.

**The product's schema is not, and must not become, a mirror of Airbyte's.** It
already is not: engine identity lives in exactly one table, `engine_mappings`,
which maps a product resource to whatever the engine calls it. Nothing else in
the schema knows an engine exists. That is what let a third adapter —
`sql_direct`, which shares no protocol, catalog shape or job model with Airbyte
— plug in without a single change to the interface (see
[ENGINE-PORTABILITY.md](ENGINE-PORTABILITY.md)).

Sharing a database would quietly undo that. The moment the product's tables sit
beside Airbyte's, joining across them becomes possible, then convenient, then
load-bearing — and the boundary that makes the engine replaceable is gone. Not
through a decision anyone would defend in review, but through a series of
individually reasonable queries.

So the separation is not primarily about disk or noisy neighbours. It is what
keeps "Airbyte is the current engine" true instead of "Airbyte is the
architecture".

## What this means concretely

- `deploy/kubernetes/` contains no Postgres. The product points at a managed
  instance; Airbyte's Helm release points at its own.
- The product's database role has no grant on Airbyte's database. On a shared
  staging instance that is worth setting up anyway: it turns the guardrail into
  something Postgres enforces rather than something people remember.

  Verified on the staging stack — before, the product's role could read all 47
  of Airbyte's tables; after, `psql -U appbi_product -d airbyte` returns
  *"User does not have CONNECT privilege"* while the product's own queries are
  unaffected:

  ```sql
  CREATE ROLE appbi_product LOGIN PASSWORD '<from the secret store>';

  REVOKE CONNECT ON DATABASE airbyte FROM PUBLIC;
  REVOKE ALL      ON DATABASE airbyte FROM appbi_product;

  GRANT CONNECT, ALL PRIVILEGES ON DATABASE appbi_integration TO appbi_product;
  GRANT USAGE, CREATE ON SCHEMA public TO appbi_product;
  GRANT ALL PRIVILEGES ON ALL TABLES    IN SCHEMA public TO appbi_product;
  GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO appbi_product;

  -- The line people forget. Without it the next migration creates tables the
  -- product cannot read, and the failure looks like a broken migration.
  ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES    TO appbi_product;
  ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO appbi_product;
  ```

  `REVOKE CONNECT ... FROM PUBLIC` is the one that does the work. Postgres
  grants `CONNECT` to `PUBLIC` by default, so revoking it only from the product
  role changes nothing.
- Backups are taken as a pair and restored as a pair, because the two hold
  references to each other. See
  [RUNBOOK-backup-restore.md](RUNBOOK-backup-restore.md).
- Anything the product needs to know about engine state it asks the Config API
  for, through the adapter. If something is genuinely not available there, that
  is an adapter gap to fix, not a reason to read the other database.

## Consequences accepted

Two databases to back up, monitor and pay for. A paired restore is more
involved than a single one, and the runbook has to say so — it does.

## Alternatives rejected

**One database, separate schemas.** Solves the migration collision and nothing
else: the guardrail is still one `SET search_path` away, and the blast radius
is unchanged.

**Product reads Airbyte's tables directly for reporting.** Faster to write, and
it makes every Airbyte upgrade a potential outage for a schema nobody
documented as an interface. The Config API is slower and is a contract.
