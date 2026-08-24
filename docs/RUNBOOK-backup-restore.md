# Runbook — backup and restore

## What is in scope

| | Backed up by | Note |
|---|---|---|
| Product database | `scripts/backup.py` | pipelines, runs, snapshots, encrypted credentials |
| Key-encryption key | **your secret store** | not in the dump, and the dump is useless without it |
| Airbyte's database | Airbyte's own procedure | separate deployment, separate lifecycle |
| Demo source / warehouse | nothing | test fixtures; recreated by `SEED_DEMO_DATA` |

The second row is the one that ruins restores. Every credential in the product
database is wrapped with `SECRET_ENCRYPTION_KEY` (envelope encryption: a data
key per secret, wrapped with the KEK). A dump restored without the matching KEK
gives you a database full of secrets nobody can unwrap — and the symptom is not
"missing key", it is every source failing its next sync with a decryption error.

So `backup.py` records a salted fingerprint of the KEK alongside each dump and
refuses to restore under a different one unless told to.

## Airbyte's own state — and why the two must be restored together

`scripts/backup.py` covers the **product** database. Airbyte is a separate
deployment with its own database, job logs and object storage, and its own
backup procedure. That split is deliberate, and it has a consequence nobody
notices until a restore:

**Restoring one without the other produces a silent mismatch.** The product's
`engine_mappings` rows point at Airbyte sources, destinations and connections
by id. Roll the product back a week and those ids still exist in Airbyte, now
describing something else. Roll Airbyte back and the product references
resources that are gone. Neither shows an error until a sync runs.

So a restore is a joint operation, and a drill is only a drill if it covers
both.

### What Airbyte holds

| | Where | Matters because |
|---|---|---|
| Config database | Airbyte's Postgres (`airbyte` DB) | sources, destinations, connections, and the ids the product stores |
| Job history and state | same database | incremental cursors: losing these re-reads everything |
| Job logs | MinIO / S3 (`airbyte-storage`) | forensics only; a run's logs, not its correctness |

For Compose staging the config database is inside `appbi-pipeline-postgres`:

```bash
# The role is the one in POSTGRES_USER (default `appbi`), not "airbyte" — this
# staging stack puts Airbyte's database on the product's Postgres instance and
# gives it no role of its own. A production Airbyte on its own database will
# have its own credentials; take them from that deployment's configuration.
docker exec appbi-pipeline-postgres pg_dump -U appbi -d airbyte --clean --if-exists   | gzip > backups/airbyte-$(date -u +%Y%m%dT%H%M%SZ).sql.gz
```

On Kubernetes it is whatever database the Helm values point at, and Airbyte's
own documentation is the authority. Take both dumps in the same window.

### RPO and RTO

| | Target | What sets it |
|---|---|---|
| RPO | 24 hours | nightly dumps of both databases |
| RTO | 2 hours | restore both, restart, re-check one source per connector type |

**Drill executed 2026-08-23** on the Compose staging stack. Paired dump of the
product and Airbyte databases, restored into scratch databases, verified:

| | |
|---|---|
| Product dump | 350 KB gz, sha256 recorded, KEK fingerprint recorded |
| Airbyte dump | 268 KB gz |
| Restore | `ON_ERROR_STOP=on`, no errors |
| Row counts before / after | 11 pipelines, 58 runs, 26 sources, 21 secrets, 47 engine mappings — identical |
| **Credentials decrypted** | **21 of 21** from the restored copy |

That last row is the one that matters. Row counts only prove the SQL replayed;
decrypting every credential proves the dump and the key belong together, which
is the failure this whole runbook exists to prevent.

Reproduce it:

```bash
docker exec appbi-pipeline-postgres psql -U appbi -d postgres   -c "CREATE DATABASE drill_product OWNER appbi;"
gunzip -c backups/appbi-*.sql.gz | docker exec -i appbi-pipeline-postgres   psql -U appbi -d drill_product --set ON_ERROR_STOP=on
docker exec -e DATABASE_URL="postgresql+asyncpg://appbi:appbi@postgres:5432/drill_product"   appbi-pipeline-api python -c "<the decrypt check below>"
```

Not yet drilled: restoring onto a **different** Airbyte deployment, which is
where the engine-reference mismatch below actually bites. That needs a second
Airbyte and is listed as open.

The realistic loss on a 24-hour RPO is one day of run history and any pipeline
created that day. Incremental cursors roll back with the state, so the next
sync re-reads from the restored cursor — duplicated work, not lost rows, and
`append_dedup` streams converge. A `full_refresh`/`overwrite` stream is
unaffected.

Shorten the RPO with more frequent dumps of the two databases **together**. A
product dump paired with an Airbyte dump from six hours earlier is worse than
either alone, because the mismatch is invisible.

## Taking a backup

```bash
python scripts/backup.py dump --out backups/
```

Produces two files:

```
backups/appbi-20260823T094623Z.sql.gz     the dump
backups/appbi-20260823T094623Z.json       sha256, KEK fingerprint, engine, workspace
```

`SECRET_ENCRYPTION_KEY` must be in the environment when this runs, or the
backup cannot record which key it belongs to and a later restore cannot warn
you. The script says so rather than silently omitting it.

### Scheduling

Nightly is the usual cadence. Anything that runs it needs the KEK in its
environment:

```bash
0 2 * * *  cd /srv/appbi && SECRET_ENCRYPTION_KEY="$(cat /run/secrets/kek)" \
           python scripts/backup.py dump --out /var/backups/appbi >> /var/log/appbi-backup.log 2>&1
```

Ship the dumps off the host. A backup on the machine it protects is not a
backup.

## Listing what you have

```bash
python scripts/backup.py list backups/
```

Flags any dump taken under a different KEK than the current environment, which
is how you notice a key rotation happened between backups.

## Restoring

```bash
python scripts/backup.py restore backups/appbi-20260823T094623Z.sql.gz
```

In order, it: verifies the sha256, compares the KEK fingerprint, asks for
confirmation, then restores with `ON_ERROR_STOP` so a partial restore fails
loudly rather than leaving a mixture.

### After a restore — the part people skip

A restore rolls the **product** back. It does not roll Airbyte back. The two
are now at different points in time, and the mismatch is silent:

- Sources/destinations/connections created **after** the backup still exist in
  Airbyte. The product no longer knows about them. They are orphans; they do
  not sync (the product schedules syncs), but they hold credentials.
- Resources deleted **after** the backup are gone from Airbyte, but rows in the
  restored `engine_mappings` still reference them. Those pipelines fail on
  their next run with a not-found from the engine.

Neither shows up until someone runs a sync. So:

```bash
# 1. Bring the stack up and confirm the engine is actually reachable.
python scripts/stack.py airbyte
curl -s "http://localhost:8010/readyz?deep=1" | python -m json.tool

# 2. What the product now thinks exists on the engine.
docker exec appbi-pipeline-postgres psql -U appbi -d appbi_integration -c \
  "select product_resource_type, product_resource_id, engine_resource_ref
     from engine_mappings order by product_resource_type;"

# 3. What Airbyte actually has.
python scripts/airbyte-workspace.py verify --id "$AIRBYTE_WORKSPACE_ID"
```

Then test one source per connector type through the product (`POST
/api/v1/sources/{id}/test`). A failing check here is the orphan case above, and
the fix is to recreate that actor through the product so it records a fresh
mapping.

## Restoring to a new environment

Migrating a deployment, or rehearsing a restore in staging:

1. Copy the KEK first. Without it, do not bother with the rest.
2. Restore the dump.
3. Set `AIRBYTE_WORKSPACE_ID` for the **new** Airbyte, and read
   [RUNBOOK-airbyte-workspace.md](RUNBOOK-airbyte-workspace.md) — the engine
   references in the dump belong to the old deployment and will not resolve.
4. Find out which ones, rather than guessing:

   ```bash
   python scripts/reconcile.py
   ```

   It asks the engine about every mapped resource and lists the ones that are
   not there, by product name. Exit 0 consistent, 1 resources missing, **2 the
   engine could not be reached** — three codes because "recreate these" and
   "wait, the engine is down" are opposite instructions and a single failure
   code hands you the wrong one half the time. Admins can read the same thing
   at `GET /api/v1/engine/reconcile`.

5. Recreate what it lists. There is no supported way to move engine resources
   between Airbyte deployments.

### The drill, run for real (2026-08-23)

Two Airbyte deployments existed at once: 0.59.1 on Compose, which this product
database was written against, and 1.8.5 on Kubernetes. Pointing the product at
the second without touching its database is the restore scenario exactly.

```
30 of 30 resources are not on this engine
   (17 more belong to another engine implementation and were not checked)
  MISSING  SOURCE       AB Postgres Source
  MISSING  DESTINATION  AB Postgres Warehouse
  MISSING  PIPELINE     AB Shop Sync
  ...
```

Recorded in `evidence/reconcile-cross-deployment.json`. Three things that run
established, none of which were obvious beforehand:

- **The product does not corrupt anything in this state.** It reads its own
  database, asks the engine, and reports. Nothing is written, nothing is
  deleted, and `/readyz?deep=1` stays green — the engine *is* healthy; it just
  has none of these resources.
- **"Missing" and "belongs to another engine" are different answers.** The
  first version of this report folded the 17 embedded-adapter rows into the
  missing list and told the operator to recreate them. They were fine. The
  report now partitions by the engine that wrote each row.
- **An unreachable engine reports nothing at all.** If a 5xx counted as absent,
  one engine restart would report every resource as lost, and the action that
  follows from that report is destructive.

**Both directions, which is what makes the tool trustworthy.** A checker that
only ever answers "missing" would pass the drill above. So the same database was
reconciled against each engine in turn, with nothing changed but the URL:

| Pointed at | present | missing | belongs to another engine |
|---|---|---|---|
| Airbyte 1.8.5 (Kubernetes) | 6 | 30 | 17 |
| Airbyte 0.59.1 (Compose) | 30 | 6 | 17 |

The six are the resources the Kubernetes runs created; the thirty are the
Compose deployment's. The numbers swap exactly, which is only possible if the
tool is reading the engine rather than guessing. Spot-checked against the Config
API directly: refs reported present answer `200`, refs reported missing answer
`404 Could not find configuration for SOURCE_CONNECTION`.

For a rehearsal that only checks the dump is readable, restore into a scratch
database and skip the engine entirely:

```bash
docker exec appbi-pipeline-postgres psql -U appbi -d postgres \
  -c "CREATE DATABASE restore_test OWNER appbi;"
POSTGRES_DB=restore_test python scripts/backup.py restore backups/<dump> --yes
```

## Testing the restore path

An untested backup is a hypothesis. Restore into a scratch database quarterly
and check three things:

- The dump applies with no errors.
- Row counts are plausible: `select count(*) from pipelines;` and
  `from pipeline_runs;`.
- A credential decrypts. This is the KEK check that actually matters:

```bash
docker exec appbi-pipeline-api python -c "
import asyncio
from sqlalchemy import select
from app.core.db import SessionLocal
from app.core.secrets import secret_store
from app.models.ops import SecretRecord

async def main():
    async with SessionLocal() as session:
        record = (await session.scalars(select(SecretRecord).limit(1))).first()
        if record is None:
            print('no secrets stored - nothing to prove')
            return
        values = await secret_store.read(session, record.ref)
        print(f'decrypted {len(values)} field(s) from {record.ref}')

asyncio.run(main())"
```

If that raises, the KEK and the dump do not belong together — regardless of
what the fingerprint said.
