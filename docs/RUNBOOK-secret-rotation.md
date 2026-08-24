# Runbook — secret rotation

Three different things get called "rotating a secret" here, and they have
nothing in common except the word.

| What | How | Blast radius |
|---|---|---|
| A connector credential (one database password) | Product UI, or `PATCH /api/v1/sources/{id}` | One source |
| `SECRET_ENCRYPTION_KEY` (the KEK) | `scripts/rotate-kek.py` | Every stored credential |
| `JWT_SECRET` | Env change + restart | Every logged-in session |

## Rotating one connector credential

Edit the source or destination and enter the new value. The store writes a new
data key, re-encrypts, bumps `version` and sets `rotated_at`; the old ciphertext
is replaced, not kept.

Then press Test. A saved-but-wrong credential is indistinguishable from a
working one until the next scheduled sync fails at 2am.

## Rotating the KEK

Every credential is wrapped with a per-secret data key, and only that data key
is wrapped with the KEK. So rotating the KEK rewrites a few dozen bytes per
record and never decrypts a password. It is cheap, and there is no reason to
avoid doing it on a schedule.

### Before

```bash
# 1. A backup, wrapped with the CURRENT key.
SECRET_ENCRYPTION_KEY="$OLD" python scripts/backup.py dump --out backups/

# 2. A new key, stored in the secret manager BEFORE anything is rewritten.
python scripts/rotate-kek.py generate
```

Store it first. A rotation that completes with the new key lost leaves every
credential unreadable and no way back except re-entering all of them.

### Rotate

The script needs the database and the current key, so it runs inside the API
container:

```bash
docker cp scripts appbi-pipeline-api:/
docker exec appbi-pipeline-api python /scripts/rotate-kek.py plan   --new-key "$NEW"
docker exec appbi-pipeline-api python /scripts/rotate-kek.py rotate --new-key "$NEW"
```

On Git Bash, prefix with `MSYS_NO_PATHCONV=1` so `/scripts/...` is not rewritten
into a Windows path.

`plan` reports the record count and changes nothing.

### After — in this order

1. Set `SECRET_ENCRYPTION_KEY` to the new key for **api, worker and migrate**.
   Missing one leaves a service that cannot read anything it is asked to sync.
2. Restart them.
3. Prove a credential decrypts:
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
           values = await secret_store.read(session, record.ref)
           print(f'decrypted {len(values)} field(s)')

   asyncio.run(main())"
   ```
4. Run a source test through the product for one source per connector type.
5. **Only then** retire the old key — and only from the running configuration.
   Keep it as long as you keep backups taken under it; `scripts/backup.py`
   records which key each dump belongs to, and `list` flags the mismatch.

### If it is interrupted

Safe to re-run. Records that no longer unwrap with the old key are counted as
skipped rather than touched, so a second pass finishes the remainder. The
reported skip count on a *first* run is the number that need investigating —
those belong to neither key.

While a rotation is half-done the running services can still read only the
records still under the old key. Finish it before restarting anything.

## Rotating `JWT_SECRET`

Signs session cookies. Changing it invalidates every session immediately;
everyone logs in again. There is no staged rollover — the secret is read at
verification time, and a cookie signed with the old one simply fails.

Do it during a quiet window, change it for `api` only (nothing else verifies
sessions), and restart.

## Cadence

| Secret | Suggested | Trigger for an immediate rotation |
|---|---|---|
| KEK | annually | anyone with access to it leaves; a backup is mishandled |
| `JWT_SECRET` | annually | the same, or a suspected session-token leak |
| Connector credentials | per the owning system's policy | a source system reports compromise |
| `POSTGRES_PASSWORD` | annually | as above; needs a coordinated restart of every service |

## What is not covered

`AIRBYTE_API_USERNAME` / `AIRBYTE_API_PASSWORD` belong to the Airbyte
deployment. Rotating them means changing them in Airbyte and in this product's
configuration together; the product will report the engine unreachable in the
window between. `/readyz?deep=1` is the check that tells you when the window
has closed.
