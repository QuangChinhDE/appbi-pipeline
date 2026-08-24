# AppBI Data Integration Platform

A working implementation of `BA_AppBI_Data_Integration_Airbyte.md`: an AppBI-styled
Integration Hub where users connect **Sources**, **Destinations** and **Pipelines**, and
**real Airbyte connectors move real data** behind a product-owned control plane.

Everything runs in **one Docker Compose project**, Postgres included.

**Where this is:** [CURRENT_STATUS.md](CURRENT_STATUS.md) — one page: what has
been proven against a real Airbyte, and the one thing still blocking production.

```
Browser ──► nginx ──► Next.js FE ──► Product API/BFF ──► IntegrationEngineAdapter ──► Airbyte connectors
                                            │                                              │
                                       Product DB                                    Source / Destination
```

---

## 1. Quick start

```bash
python scripts/production.py install --config deploy/demo.yaml
```

That is the whole thing. It generates the encryption key and JWT secret, builds
and starts the stack, waits until the API is genuinely serving *and* the engine
answers, reconciles, and prints where to go — **http://localhost:8080**,
`admin@appbi.local`.

Running it twice is a no-op: existing secrets are kept, because regenerating
the encryption key would orphan a database full of credentials nobody could
then decrypt.

The three-step version below still works and is worth knowing, but it has two
places to get wrong and one of them fails later with an error about encryption
rather than about the step that was missed:

```bash
cp .env.example .env
python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
# paste it into SECRET_ENCRYPTION_KEY in .env
docker compose up -d --build
```

### Going to production

```bash
cp deploy/production.yaml.example deploy/production.yaml   # then fill it in
python scripts/production.py install --config deploy/production.yaml
python scripts/production.py doctor  --config deploy/production.yaml
```

The example refuses to install until every placeholder is replaced, and it
names each one. It also refuses a floating image tag, an engine version nobody
certified, a secret written as a literal instead of a reference, and a config
that points the product and the engine at the same database. Those are the
failures that are cheap here and expensive three steps later.

### Only as much stack as you need

The full certification stack is fourteen containers, because it runs both this
product and an Airbyte deployment on one machine. That is right for proving the
two work together and wrong for editing a React component.

```bash
python scripts/stack.py lite       #  4 containers - API and schema work
python scripts/stack.py embedded   #  7 containers - local demo with the UI
python scripts/stack.py airbyte    # 14 containers - real Airbyte, certification
python scripts/stack.py status     # what is running, and what it costs
python scripts/stack.py stop       # stop the Airbyte half, keep the product
```

For UI work, `stack.py lite` plus `npm run dev` in `frontend/` reloads in a
second instead of rebuilding an image.

`.env.example` sets `COMPOSE_FILE` so a local checkout also loads
`docker-compose.embedded.yml`, which runs connector images on this machine's
Docker daemon. The base compose file mounts no Docker socket anywhere: a
container that can reach the daemon can start another one with the host
filesystem mounted, and the production engine (`AIRBYTE_API`) never needs it.
A production environment simply does not set `COMPOSE_FILE`.

| Account | Role | Password |
|---|---|---|
| `admin@appbi.local` | Platform admin / Owner | `Admin@12345` |
| `dataadmin@appbi.local` | Data Admin | `Admin@12345` |
| `operator@appbi.local` | Operator | `Admin@12345` |
| `analyst@appbi.local` | Analyst (read-only) | `Admin@12345` |

Optional but recommended — pre-pull the connector images so the first
*Test connection* is fast instead of downloading ~1 GB inline:

```bash
bash scripts/pull-connectors.sh
```

### Ports

| Service | URL |
|---|---|
| App (via nginx) | http://localhost:8080 |
| Frontend direct | http://localhost:3100 |
| Product API + OpenAPI | http://localhost:8010/docs |
| Postgres | `localhost:55433` (user `appbi`) |

Change any of them in `.env`.

---

## 2. What actually runs the sync

The platform ships two interchangeable engine adapters behind one interface
(`IntegrationEngineAdapter`, spec §24):

| `ENGINE_TYPE` | Adapter | What it does |
|---|---|---|
| `AIRBYTE_EMBEDDED` *(default)* | `app/adapters/airbyte_protocol` | Runs official `airbyte/source-*` and `airbyte/destination-*` images on the host Docker daemon over the **Airbyte Protocol** |
| `AIRBYTE_API` | `app/adapters/airbyte_api` | Delegates to an existing self-managed Airbyte deployment via its Config API |

The embedded engine executes exactly what an Airbyte worker executes:

```
docker run  airbyte/source-postgres:3.8.5       read  --config c.json --catalog cat.json [--state s.json]
   │   RECORD / STATE messages, inspected in flight
   ▼
docker run -i airbyte/destination-postgres:3.0.17 write --config c.json --catalog cat.json
```

It speaks the full protocol: `spec`, `check`, `discover`, `read`/`write`, `RECORD`,
`STATE`, `LOG`, `TRACE`, plus the refresh protocol (`generation_id`,
`minimum_generation_id`, `sync_id`) that modern destinations require. Destination-committed
state is persisted per pipeline, so incremental syncs genuinely resume.

Nothing here is simulated. After a first sync you can look in the warehouse:

```bash
docker exec appbi-pipeline-postgres psql -U appbi -d demo_warehouse \
  -c "\dt synced_*.*" -c "SELECT count(*) FROM <schema>.orders;"
```

Rows arrive with Airbyte's own `_airbyte_raw_id`, `_airbyte_extracted_at`,
`_airbyte_generation_id` and `_airbyte_meta` columns.

### Where the connector catalogue comes from

`backend/app/resources/connector_registry.json` is **generated**, not hand-written:

```bash
python scripts/build-connector-registry.py            # fetch + regenerate
python scripts/build-connector-registry.py --icons    # also vendor the logos
```

It reads Airbyte's official OSS registry (the same document Airbyte's own UI uses),
which carries each connector's image, pinned tag, JSON-Schema spec, docs URL and
support level. Two rules the generator enforces:

- **Certification is ours.** `SUPPORTED` is reserved for connectors this product has
  actually run; Airbyte's own rating travels separately in `support_level`.
- **Tested pins win.** For curated connectors the tag the contract suite passed against
  overrides whatever the registry currently points at.

Logos are vendored into `resources/connector_icons/` and served by our own API at
`/api/v1/connectors/{key}/icon.svg`, so the browser never calls the upstream registry
and the catalogue still renders offline (§11.4).

### Connector Builder — sources for APIs nobody shipped a connector for

`/builder` builds a source from a form instead of from code. It compiles to a
**declarative manifest** and runs on Airbyte's own generic runner
(`airbyte/source-declarative-manifest`, pinned), so a connector built here is
executed by exactly the same path as a certified one — the manifest travels in
the config, and only the adapter knows that (guardrail 5).

The loop is: describe the API → **test read against the live endpoint** → publish.

It covers the surface Airbyte's own builder exposes over the low-code CDK:

| | |
|---|---|
| **Auth** | None · API key · Bearer · Basic · OAuth 2.0 (refresh token) · JWT · Session token |
| **Pagination** | None · Page number · Offset · Cursor (from the body) · `Link` header |
| **Incremental** | Datetime cursor, start/end parameters, window `step`, granularity, lookback |
| **Partitioning** | List of values · parent stream (`SubstreamPartitionRouter`) |
| **Records** | Selector path, Jinja record filter, `AddFields` / `RemoveFields` |
| **Requests** | Query params, headers, JSON or form body, composite primary keys |
| **Errors** | Max retries, constant / exponential backoff, or the API's own `Retry-After` |
| **Config** | User-declared input fields, referenced as `{{ config['key'] }}` |
| **Manifest** | Read the compiled YAML, or paste one in to replace the draft |

Things worth knowing:

- The test panel shows the **HTTP request and response** the connector made, not
  just a record count. A `200` carrying an error envelope is a real outcome, and
  the panel makes it visible rather than reporting "1 record" and stopping.
- A successful read **infers the stream schema** from the sample. Without that a
  built source discovers a stream with no columns, and the pipeline wizard has
  nothing to select.
- Credentials are referenced as `{{ config[...] }}`, never inlined: the manifest
  is stored in our database and sent to the browser (§21).
- Publishing requires a green test read, and creates a catalogue entry owned by
  the workspace, marked `Tự xây dựng` / `Built here` — never "certified".
- YAML **import fails loudly** on anything the editor cannot render, rather than
  dropping components silently on the next save.

```bash
# what the engine actually receives
curl -b cookies.txt localhost:8010/api/v1/builder/projects/<id>/manifest
```

### Pointing at a real Airbyte deployment

```dotenv
ENGINE_TYPE=AIRBYTE_API
AIRBYTE_API_URL=http://airbyte-server:8001
AIRBYTE_WORKSPACE_ID=<workspace uuid>
```

No product code, API contract or screen changes — that is the whole point of the
adapter boundary.

---

## 3. Ready-made demo data

Postgres boots with two extra databases so a first pipeline moves genuine rows:

- **`demo_source`** — `shop.customers` (500), `shop.orders` (2 000), `shop.products` (200),
  readable by the least-privilege user `demo_reader` / `demo_reader_pw`.
- **`demo_warehouse`** — owned by `demo_writer` / `demo_writer_pw`, where the destination
  connector creates its own tables.

Both are reachable from connector containers at host `postgres`, port `5432`.

### Build a pipeline in the UI

1. **Sources → Add source → PostgreSQL**
   `host=postgres`, `port=5432`, `database=demo_source`, `schemas=[shop]`,
   `username=demo_reader`, `password=demo_reader_pw`, SSL `disable`,
   update method `Standard`. → **Test connection** → **Save**.
2. **Destinations → Add destination → PostgreSQL**
   `host=postgres`, `database=demo_warehouse`, `schema=analytics`,
   `username=demo_writer`, `password=demo_writer_pw`. → **Test** → **Save**.
3. **Pipelines → Create pipeline** → pick both → discover → select `customers` and
   `orders` → incremental on `updated_at` with dedupe → daily 02:00 → **Create**.

`Sample Data (Faker)` needs no credentials at all if you just want to see a sync run.

---

## 4. Architecture

```
appbi-pipeline/
├── docker-compose.yml         one project: postgres, redis, api, worker, frontend, nginx
├── docker/postgres/init/      demo_source + demo_warehouse seed
├── backend/app/
│   ├── adapters/              THE engine boundary — nothing above this knows Airbyte
│   │   ├── base.py            IntegrationEngineAdapter protocol (§24.1)
│   │   ├── dto.py             the only shapes that cross the boundary
│   │   ├── error_mapper.py    connector failure → product error category (§16.7)
│   │   ├── airbyte_protocol/  embedded engine (docker + Airbyte Protocol)
│   │   └── airbyte_api/       Airbyte Config API engine
│   ├── api/v1/                routes; presenters own every serialized shape
│   ├── core/                  config, db, errors, permissions, secrets, context
│   ├── models/                product-owned schema (§22)
│   ├── services/              domain logic — no engine vocabulary
│   ├── bootstrap.py           schema + catalog + tenant seed
│   └── worker.py              executor · reconciler · scheduler · catalog · janitor
└── frontend/src/
    ├── app/(main)/            overview, sources, destinations, pipelines, runs,
    │                          monitoring, alerts, connectors, audit, settings
    ├── components/            AppBI design system + integration components
    └── lib/                   API client, i18n (VI/EN), formatting, query keys
```

### Guardrails held (spec §2.1)

| # | Guardrail | How |
|---|---|---|
| 1 | FE never calls the engine | Browser only reaches `/api/v1`; the daemon is not exposed |
| 2 | No access to Airbyte's DB | Only the adapter talks to the engine, over its own interface |
| 3 | No engine id in URLs or payloads | `engine_mappings` holds them; presenters never emit them |
| 4 | No plaintext credentials outside the secret store | Envelope encryption; masked in responses, redacted in logs and audit |
| 5 | Adapter is the only engine-aware code | `grep -r airbyte backend/app --include=*.py -l` → only `adapters/` |
| 6 | Versions pinned | `app/resources/connector_registry.json`, surfaced in Settings → Engine |
| 7 | Upgrades gated by contract tests | `backend/tests/test_adapter_contract.py` |
| 8 | Everything workspace-scoped | `workspace_id` comes from the session, never the request body |
| 9 | Mutations audited | `services/audit.py`, visible at `/audit` |
| 10 | Capability-driven UI | Sync modes, cursors and PKs come from the discovered catalog |

### Decisions taken (spec §47–48)

- **ADR-004 Schedule ownership → Product.** The canonical schedule lives in the product
  DB and the product worker fires it; the engine connection stays manual-like. Quota,
  overlap policy and audit therefore have one home.
- **ADR-005 Reconciliation → polling worker.** Predictable, and it survives a browser or
  API restart. Event callbacks can be added later with the reconciler as the safety net.
- **ADR-006 Catalog → normalized cache.** The bundled registry seeds the catalog so a
  registry or daemon outage cannot break the Sources page; an admin refresh reads real
  `spec` output from the connector images.
- **ADR-003 Secrets → envelope encryption in the product DB.** `SecretStore` is a Protocol,
  so Vault or a cloud secret manager drops in without touching a service.

---

## 5. Operating it

```bash
docker compose ps                      # service health
docker compose logs -f worker          # sync execution
docker compose logs -f api             # structured JSON logs with trace_id

# connector logs for one run (also visible in the UI)
docker exec appbi-pipeline-worker sh -c 'ls -t /engine/logs | head'
```

### Health and metrics

| Endpoint | Answers | Point it at |
|---|---|---|
| `/healthz` | is the process alive | liveness probe |
| `/readyz` | can it serve traffic — DB required, engine reported | load balancer |
| `/readyz?deep=1` | is the whole chain healthy — engine required | deploy gate |
| `/metrics` | Prometheus exposition | your scraper, on the internal network |

**Do not point a load balancer at `?deep=1`.** It fails when the engine is down,
which would remove every API instance from rotation during an engine outage —
nobody could then read run history or acknowledge the alert saying so.

Also useful: `/docs`, `/api/v1/engine/status`, `/api/v1/admin/compatibility`.

### Deploying to Kubernetes

`deploy/kubernetes/` — plain Kustomize manifests: API, worker, migration job,
frontend, NetworkPolicies, PodDisruptionBudget, ingress, RBAC.

```bash
kubectl create namespace appbi
kubectl -n appbi create secret generic appbi-secrets --from-literal=...   # see the README there
kubectl apply -k deploy/kubernetes
```

Postgres, Redis and Airbyte are deliberately not included — the first two
should be managed services, and Airbyte has its own chart and lifecycle. Full
notes in [deploy/kubernetes/README.md](deploy/kubernetes/README.md).

Schema-validated in CI, but **not yet applied to a real cluster**; that is the
remaining production blocker.

### Runbooks

| | |
|---|---|
| [Backup / restore](docs/RUNBOOK-backup-restore.md) | `scripts/backup.py` — records which KEK each dump belongs to |
| [Secret rotation](docs/RUNBOOK-secret-rotation.md) | `scripts/rotate-kek.py` — rotates the master key without decrypting a credential |
| [On-call](docs/RUNBOOK-oncall.md) | metrics, alert rules, and what each symptom means |
| [Egress](docs/RUNBOOK-egress.md) | what a connector can reach, measured per target |
| [Airbyte workspace](docs/RUNBOOK-airbyte-workspace.md) | why `AIRBYTE_WORKSPACE_ID` is configured, never guessed |
| [Engine upgrade](docs/RUNBOOK-engine-upgrade.md) | certifying a different Airbyte |
| [Engine portability](docs/ENGINE-PORTABILITY.md) | running on something that is not Airbyte at all |

Prometheus rules: [deploy/monitoring/alerts.yaml](deploy/monitoring/alerts.yaml).

### Tuning

| Variable | Default | Meaning |
|---|---:|---|
| `MAX_CONCURRENT_RUNS_GLOBAL` | 4 | Platform-wide concurrent syncs |
| `MAX_CONCURRENT_RUNS_PER_WORKSPACE` | 3 | Per-tenant cap |
| `MIN_SCHEDULE_INTERVAL_SECONDS` | 300 | Rejects schedules that run too often |
| `RUN_TIMEOUT_SECONDS` | 7200 | A run past this is killed and marked `TIMED_OUT` |

Over quota, a run waits as `QUEUED` with a reason — it is never failed (§28.3).

---

## 6. Tests

```bash
# unit + contract (no engine needed for the unit suite)
docker compose run --rm --entrypoint "" api sh -c "pip install -q pytest pytest-asyncio && python -m pytest tests -q"

# adapter contract suite against the live engine (the upgrade gate, §29.3)
docker compose run --rm --entrypoint "" api sh -c "pip install -q pytest pytest-asyncio && RUN_ENGINE_CONTRACT=1 python -m pytest tests/test_adapter_contract.py -q"
```

Three suites drive the running system:

```bash
# journey A over HTTP: source -> test -> destination -> discover -> pipeline -> sync
python scripts/e2e.py --source postgres     # or --source faker (no credentials needed)

# live UAT cases: incremental resume, cancel idempotency, retry lineage,
# dependency-blocked delete, tenant isolation, RBAC, secret leakage
python scripts/verify.py

# the same journey through a real browser, with screenshots
cd frontend && npm i --no-save playwright &&   SHOT_DIR=../e2e-shots node ../scripts/ui-journey.mjs
```

Two adversarial audits run against the same instance. They do not test the happy
path — they try to break it:

```bash
# API: bad enums, unknown timezones, duplicate names, cross-tenant ids,
# RBAC escalation, secret leakage, pagination edges, error-envelope shape
python scripts/audit-api.py

# UI: every screen in four states (populated tenant, empty tenant, read-only
# role, error paths) across three viewports; reports layout overflow,
# unlabelled controls, sub-20px targets, colour-only status, dead ends,
# console noise and slow calls
cd frontend && SHOT_DIR=../audit-shots node ../scripts/audit-ui.mjs

# i18n gate: vi/en key parity and no hardcoded Vietnamese outside the catalog
npm run check:i18n
```

Last verified on a clean `docker compose up -d --build`:

| Suite | Result |
|---|---|
| Backend unit + structural contract | 130 passed, 26 skipped (live-only) |
| Adapter contract vs. live engine | 16 passed |
| `scripts/e2e.py --source postgres` | 2 500 rows synced |
| `scripts/verify.py` | 28/28 checks |
| `scripts/ui-journey.mjs` | pipeline HEALTHY, no page or 5xx errors |
| `scripts/audit-api.py` | 0 findings |
| `scripts/audit-ui.mjs` | 0 findings |
| `scripts/audit-behaviour.mjs` | 22 checks, 0 findings |
| `npm run check:i18n` | 792 vi / 792 en keys, coverage OK |

---

## 7. Known limits

- **Licensing.** Release gate `LIC-001` (§31) is **not** cleared here. Running Airbyte
  connector images commercially needs a legal review; this build is for internal /
  PoC use.
- **Connector set.** The catalogue is the **whole Airbyte OSS registry** — 598 sources
  and 56 destinations — generated by `scripts/build-connector-registry.py`. Four of them
  (`source-postgres`, `source-faker`, `destination-postgres`) are marked
  `SUPPORTED` because this product has run them end to end; every other connector ships
  as `BETA`: selectable and fully rendered from its real Airbyte spec, but not something
  we have verified. Certifying one more is a pass of the contract suite plus an entry in
  `CURATED` (§53).
- **OAuth connectors.** The form renderer handles `oneOf` and secrets, but the interactive
  OAuth consent flow is V1.1 (§3.2).
- **Email / webhook alerts.** The channel abstraction exists; only `IN_APP` is delivered.
- **First check is slow.** A connector container cold-starts in tens of seconds. The wizard
  therefore issues a signed check token so saving does not repeat the check.
- **Certification is read, not declared.** `compatibility.yaml` records what was
  verified per connector and the generated registry reads it, so the catalogue
  cannot claim more than the evidence supports. `source-file` is `BETA` for
  exactly this reason: only check/discover/full_refresh were ever run on it.
- **Connector egress is policed in two places.** `app/core/egress.py` refuses
  private and link-local targets (syntactically on save, by resolution before a
  request goes out), and connector containers run on a network that reaches
  neither the API nor Redis. Neither layer stops a redirect or a runtime-built
  URL — only an egress proxy would, and that is not built.
- **Migrations.** Alembic owns schema evolution: `alembic upgrade head` builds the
  whole schema from empty, and CI asserts `alembic check` finds no drift between
  the migrations and the models. A fresh local stack still uses `create_all` for
  speed and is then stamped at head, so it is tracked from the first boot.
