# qa/ — everything that proves the product works

Nothing in this directory ships. It exists to answer "does this work", and it
is kept apart from the code that answers "what does this do" so that either
question can be reviewed on its own.

The split is by purpose, not by language:

| | |
|---|---|
| `backend/` | `scripts/` |
| the pytest suite | operating a deployment |
| `e2e/`, `audit/`, `probes/` | install, upgrade, backup, rotate, mirror, reconcile |

If you are reviewing what this product *is*, read `backend/app`, `frontend/src`,
`deploy/` and `scripts/`. Nothing here runs in production.

## Layout

```
qa/
  backend/   pytest suite — unit, structural and live-Postgres
  e2e/       drives the product's own API end to end
  audit/     adversarial probes: API, UI, behaviour, i18n coverage
  probes/    checks a deployment's network and engine contract
```

## Running it

```bash
# From the repository root. `pytest.ini` there points at qa/backend and puts
# backend/ on the path, so `import app` works without the suite living inside
# the application package.
python -m pytest -q
```

Most of the suite runs anywhere. Two groups need infrastructure and skip
themselves without it, because a unit suite that silently needs Postgres is a
unit suite that fails on somebody's laptop:

```bash
# Real Postgres: bootstrap, forced password change, concurrency, recovery
# taxonomy, the outbox fault-injection suite, OAuth grant lifecycle.
RUN_CORE_LIVE=1 python -m pytest -q

# A live engine: the adapter contract suite.
RUN_ENGINE_CONTRACT=1 python -m pytest -q qa/backend/test_adapter_contract.py
```

`qa/backend/test_production_render.py` needs `kubectl` and skips without it —
its whole point is to run the real renderer rather than read its source.

## The rest

```bash
python qa/e2e/e2e.py --source postgres          # full journey through the API
python qa/e2e/verify.py                          # the BA UAT scenarios
python qa/e2e/certify-connector.py source-bigquery --config secrets/bq.json

python qa/audit/audit-api.py                     # adversarial API probe
node  qa/audit/check-i18n.mjs src                # EN/VI parity, no hardcoded copy

python qa/probes/verify-egress.py                # connector network policy
python qa/probes/verify-engine-api.py --url ...  # the engine contract the adapter needs
```

## One thing that crosses the line

`_console.py` lives in `scripts/`, and the QA scripts import it. It is the
Windows console encoding fix: without it the first Vietnamese character in an
API response ends a script with a `UnicodeEncodeError` that reads like the
product failed. Duplicating it would mean two copies to fix, so the import
reaches across rather than the file being copied.
