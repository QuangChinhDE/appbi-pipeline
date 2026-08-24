# Current status — AppBI Data Integration

One page. `PRODUCTION_READINESS_REVIEW.md` is the full review log and reads
chronologically; this is where it has got to.

**Updated:** 2026-08-24 (PM review v12 - Git and production release decision)

---

## Where this is

```
Architecture / API direction        accepted
P0 - real Airbyte integration       closed, evidence below
P0 - Airbyte adapter on Kubernetes  accepted narrowly: 1.8.5, 11/11 operations
Single-host demo                    PASS on this machine; not a production proof
Production core                     ACCEPTED for feature freeze and rehearsal
Cross-machine/offline install       NOT PROVEN
Controlled production pilot         CONDITIONAL NO-GO: five launch gates below
Broad GA / all connectors           NOT IN CURRENT LAUNCH SCOPE
```

## PM v12 - Git and production release decision

**Production deployment: NO-GO.** The RC1 rehearsal found the right blocker,
but it has not yet produced a deployable, reproducible release.

**Push for code review:** allowed only after this workspace is attached to the
real Git repository and the branch baseline is green. This checkout currently
has no `.git` directory, so PM cannot identify the source commit, inspect the
diff, push it, or bind the image/evidence to a real commit. The `BUILD_SHA` in
the local `.env` is therefore not release provenance.

The release has four immediate blockers:

1. **Client-credentials auth is not wired into production.** The adapter knows
   how to obtain a bearer token, but the production schema/example, secret
   renderer, `verify_engine`, `doctor`, readiness validation and operational
   scripts still expose only Basic username/password. A production install
   cannot currently deliver `AIRBYTE_CLIENT_ID` and
   `AIRBYTE_CLIENT_SECRET` to the application through the supported path.
2. **Airbyte's workload launcher is not running.** With no dataplane
   credentials, no connector job can start. G1 and every sync-dependent gate
   remain open. Bootstrap the Airbyte application/dataplane credentials through
   supported chart/secret inputs; an internal, temporary webapp bootstrap is an
   acceptable fallback. Do not seed Airbyte's database directly.
3. **The reproducible CI lane is still the old target.** It installs chart
   `1.8.5` with auth disabled, not chart V2 `2.0.17` with bearer auth. The RC1
   manual result therefore cannot yet be reproduced by CI.
4. **The exact production golden path has not run.** After the launcher starts,
   rerun all 11 adapter operations plus full refresh, incremental zero re-read,
   a deliberately long cancel/timeout, worker restart and alert delivery on the
   auth-enabled target topology.

Independent test result in the current workspace: `240 passed, 18 skipped`
when `BUILD_SHA=unknown`; the checked-in environment value makes the default
run fail one build-identity assertion. Six live-Postgres tests passed. This is
good core evidence, but not a production release gate.

Detailed file evidence and the shortest release sequence are in
`PRODUCTION_READINESS_REVIEW.md` under **PM review v12**.

## PM v11 - shortest path to production

The Sprint A/A.1 core fixes are accepted. PM independently reran 233 backend
tests, 5 live-Postgres behavioural tests and the schema drift check. The next
phase is not another general bug hunt. Freeze product features and prepare one
small production pilot.

The pilot is GO only when these five gates have evidence from the exact release:

1. **Production topology:** AppBI and Airbyte run on the target Kubernetes
   platform; Airbyte 1.8.5 uses Helm chart V2 2.0.17; auth, TLS, external
   datastores, object storage and the enforcing CNI are enabled.
2. **Reproducible release:** product, Airbyte platform, chart and launch-scope
   connector images are in the internal registry/artifact store and pinned by
   digest. A second clean Linux runner installs with public upstream blocked.
3. **Recoverability:** paired AppBI + Airbyte backup/restore and previous-release
   redeploy are executed on that topology, with the KEK and artifact ids bound
   to the release evidence.
4. **Run safety and operations:** a deliberately long sync proves timeout,
   cancel, worker restart and engine outage recovery; alert delivery reaches a
   named primary and backup operator. `production.py status` must return failure
   when health fails.
5. **Business scope:** `LIC-001` is cleared in writing and the pilot connector
   list is explicit. Recommended first golden path is `source-postgres` to
   `destination-postgres`; `source-faker` remains test-only.

This is a **pilot**, not a claim that all 654 catalogue entries are supported.
Use one environment, low concurrency, capped data volume and a small set of
design partners. A connector is enabled only after its own check, discover,
full refresh, incremental, cancel and recovery evidence.

The full saga/outbox, automatic rollback, integrated DB-role doctor check,
portable dotenv parser and runtime-image slimming may follow the pilot. Their
temporary controls are manual reconcile/orphan review, a reviewed rollback
runbook, an explicit `provision-db.py --verify` preflight and Kubernetes-only
production operation. Digest pinning and the internal mirror do **not** move
after the pilot because they are what make another machine and an upstream
outage survivable.

Detailed acceptance evidence and the reclassified P1 list are in
`PRODUCTION_READINESS_REVIEW.md` under **PM review v11**.

## PILOT-G1 is closed - and my previous diagnosis was wrong

Airbyte **1.8.5 on Helm chart V2 2.0.17**, auth enabled, external Postgres,
Calico enforcing, **workload-launcher Running**. Evidence:
[evidence/rc1-topology.md](evidence/rc1-topology.md).

```
chart      : airbyte-2.0.17  (app 1.8.5)  deployed
auth       : ENABLED -- Config API answers 401 without credentials
database   : external, outside the cluster, 66 tables
in-cluster postgres : none
workload-launcher   : 1/1 Running
```

**I reported this as blocked upstream. It was not - it was my own workaround.**
The bootloader failed on a missing secret key, I installed with `--no-hooks` to
get past it, and the bootloader *is* the migration. Then `airbyte-auth-secrets`
did not exist, so I hand-wrote it with invented credentials - and every
subsequent `401 at DataplaneApi.initializeDataplane` was the server correctly
rejecting values it had never issued. The server creates that secret itself,
with all six credential keys; I had been overwriting it and blaming Airbyte.

What unlocked it is a values path absent from the chart's documented values:
`global.auth.instanceAdmin.password`. I found it by rendering the template with
`--set` on each candidate and checking which key in the secret took a value.

Six chart-V2 traps are now written into
[values-certification-v2.yaml](deploy/kubernetes/airbyte/values-certification-v2.yaml)
so the next person does not repeat the afternoon.

The lesson worth keeping: when a workaround produces new symptoms, suspect the
workaround before the upstream.

### Gates

| Gate | |
|---|---|
| **G1** | **Closed** |
| G2 | Evidence v2, binding, internal registry with digests. Remaining: clean Linux runner with public upstream blocked |
| G3 | Unblocked by G1; the paired restore drill has not been run |
| G4 | Timeout/cancel and `status` exit code done. Golden path, worker restart and alerting on this topology have not been run |
| G5 | Legal |

## PM v12 - three of four technical P0s closed

| Finding | State |
|---|---|
| **P0-REL-012** bearer auth on the deploy path | **Closed.** Readiness validates both schemes and refuses production with no credentials or with both; the config schema, `.env.production.example`, `_secret_env`, `validate()`, `verify_engine` and `doctor` all speak client credentials. A render test proves `AIRBYTE_CLIENT_ID`/`AIRBYTE_CLIENT_SECRET` reach the Pod |
| **P1-AUTH-001** real protocol tests | **Closed.** Six `MockTransport` tests execute the flow: token POST shape, bearer header, token reuse, one refresh on 401, retry cap, unregistered-credential message, missing-token response |
| **P0-CI-001** CI certifies the wrong target | **Closed.** Chart `2.0.17` + app `1.8.5` from the V2 repo, with `values-certification-v2.yaml` and auth enabled |
| **P0-REL-013** Git provenance | **Closed.** Branch `rc1-production-rehearsal`, commit `0ac5740`, clean tree, no `.env` or secret committed. Image rebuilt from that commit; `/admin/compatibility` reports the same SHA as `git rev-parse HEAD`. The repo has no remote yet — point it at the organisation's before pushing |
| **P0-PLAT-001** workload-launcher | **Not closed, and blocked upstream.** See below |

The failing test PM saw was mine: the assertion read `Settings()`, which reads
the machine's `.env`. It now reads the field default and passes with any `.env`.

### Why the launcher is still down

Followed the path PM chose — Kubernetes Secret plus supported chart values, no
writing to Airbyte's own tables:

```
dataplane                     = 1 row    (group created)
dataplane_client_credentials  = 0 rows   (credentials never registered)
launcher: CrashLoopBackOff -> 401 at DataplaneApi.initializeDataplane
```

The webapp bootstrap route PM allowed as a fallback is also closed:
`docker pull airbyte/webapp:1.8.5` → **not found**. Chart 2.0.17 references an
image that is not published, so enabling the webapp cannot work either.

For community edition + auth enabled + app 1.8.5 there is currently no path
within dev's control that does not write directly to Airbyte's schema, which is
exactly what the decision forbids — and I agree it should.

This needs an operational call: ask Airbyte how a dataplane registers under
community auth, or pick an app version whose `airbyte/webapp` image exists and
recertify that version. **No launcher means no connector pods, so the golden
path on the production topology still has not run.** I did not turn auth off to
get a green sync.

Verification: 246 tests, 6 live Postgres, 6 auth-protocol, both audits clean,
clean tree, product build SHA matches HEAD.

## RC1 target-topology rehearsal - two P0s only this could find

AppBI and Airbyte were stood up on the topology PILOT-G1 asks for: Helm **chart
V2 2.0.17** (app 1.8.5), **auth enabled**, **a separate external Postgres for
each system**, **Calico enforcing**, and an **internal registry**.

**The finding that matters: the adapter could not authenticate against a
production Airbyte at all.**

```
Config API, no credentials  -> 401   (auth is genuinely enforced)
Config API, HTTP Basic      -> 401   (including the instance admin's own login)
```

The adapter spoke only Basic. Airbyte 1.8.5 on chart V2 with auth enabled does
not accept it. Nothing caught this because **every certification so far ran
with auth disabled** - `values-certification.yaml` says so explicitly, which is
correct for proving the adapter contract and means the auth path had never been
exercised. Fixed: the adapter now does client credentials against
`/api/v1/applications/token` and sends a bearer token, refreshing exactly once
on a 401.

**Second P0, not fixed:** the `workload-launcher` cannot start - it needs
dataplane client credentials, and both `application` and
`dataplane_client_credentials` are empty. The chart does not generate them, and
community edition bootstraps them through the webapp, which this profile
disables because the product has its own UI. **No launcher means no connector
jobs**, so G1 is not closed.

That is a decision about how Airbyte is operated, not a code change, and it is
the kind of thing a rehearsal exists to surface.

Four chart-V2 traps, all hit and all recorded in the review: `global.secrets`
does not add arbitrary keys; the bootloader is a Helm hook and is deleted on
failure; the database key is `name:` and defaults to `db-airbyte`; Temporal
requires TLS against an external Postgres by default.

Evidence: [backend/evidence/rc1-topology.md](backend/evidence/rc1-topology.md).

## RC1 - the dev half of the five pilot gates

PM v11 froze the core and named five gates. Dev's RC1 scope was four items:
timeout/cancel, `status` exit code, evidence v2, digest-based release inputs.
All four are done, with behavioural evidence.

| | |
|---|---|
| Timeout ownership | Nothing owned it: `timeout_seconds` was set on every request and only the embedded runner read it, so a hung Airbyte sync stayed RUNNING forever and held the pipeline's one active-run slot. The reconciler now cancels **on the engine first**, then marks `TIMED_OUT`. An unreachable engine defers rather than lying about state |
| `status` exit code | Printed FAIL and returned 0. Now 0 healthy / 1 unhealthy, proven both ways |
| Evidence v2 | The product reports its own build (`BUILD_SHA` baked in, served at `/admin/compatibility`). Evidence records build, engine, workspace and run ids; the gate rejects a mismatched build, an `unknown` build, a different engine, forged run ids, and v1 evidence |
| Digest mirror | `scripts/mirror.py plan/push/lock/verify`. 15 artefacts for the pilot, not 654 — and a connector in launch scope with no certified version fails the plan |
| Chart V2 | Config pins app `1.8.5` and chart `2.0.17` separately, because they are not the same number and V1 is deprecated |

### Where the five gates actually stand

| Gate | Status |
|---|---|
| G1 target topology | **Nearly there.** Chart V2 2.0.17 + app 1.8.5 + auth enforced + two external Postgres + Calico stood up and measured. Remaining: dataplane credentials so the workload launcher can start |
| G2 reproducible release | **Mostly done.** Evidence binding, mirror tooling, and a real internal registry with four images pushed by digest. Remaining: a clean Linux runner installing with public upstream blocked |
| G3 recoverability | **Blocked on G1.** Paired restore needs the target topology to restore into |
| G4 bounded execution | **Half done.** Timeout/cancel and `status` are proven. A long-running sync against real Airbyte, worker restart mid-sync, and alerts reaching named people are not |
| G5 business scope | **Not dev.** Pilot scope is in the config and enforced; `LIC-001` and operator names are legal and process |

Verification: 239 tests, 6 live Postgres, 5 real-kubectl render, evidence
binding rejects all five tampering cases, mirror plan refuses uncertified
connectors.

## Sprint A.1 - the four reopened findings, closed

PM v10 was right that "all six P0 closed" was wrong, and right about why the
tests missed it: they searched source text. `render_from_config()` could not
execute at all while a test asserting its existence stayed green.

| Finding | Now | Evidence |
|---|---|---|
| P0-REL-001 renderer | The Kustomize tree is copied into the temp root and referenced relatively; the load restriction stays on | 5 tests call `render_from_config()` with real kubectl and parse the output. They immediately found a second defect: the ingress TLS host was never patched, so the certificate would have been issued for the example hostname |
| P0-REL-001 config binding | Registry/tag, workspace id, engine URL, ingress host (from `api_url`, the field the example actually has), and every secret as an explicit `secretKeyRef` | Asserted on rendered output, not on the patch |
| P0-REL-001 secret namespace | Only what is bound into the Pods is required, in the product's namespace. Airbyte's own database credential is a topology declaration, not a runtime dependency | |
| P0-REL-001 gate ordering | Licence, on-call and placeholder gates run **before** migrate and rollout | `DEPLOYMENT REFUSED`, nothing applied |
| P0-CORE-004 recovery | `EngineResourceGoneError` is the only answer that means absence. 401/403/429/5xx/timeout all defer | 10-case matrix on real Postgres: only confirmed-not-found and never-started end FAILED |
| P0-CORE-002 drift | The stray `DROP INDEX` is gone, fixups no longer run on a versioned database, and `f2c0a15b8e37` restores the index on deployments that already lost it | Live database: head, no drift, all declared indexes present. CI now runs `app.bootstrap` then `scripts/check-schema-drift.py` |
| P0-CORE-001 sessions | `session_version` on the user and in the token; a password change revokes every session issued before it and reissues the caller's cookie. Bootstrap password goes through the full policy; the email is validated | Two tokens from one bootstrap secret; both stale after the change |

One self-inflicted defect worth recording: the session columns first went into
`d4a1f07c2b18`, a revision that had **already run**. Alembic skipped it and the
migrate container died on the missing column. They now live in their own
revision. Do not edit an applied migration.

Verification: 233 tests (was 226), 5 live Postgres tests, 5 real-kubectl render
tests, 20 concurrent API triggers returning one run id, both audits clean.

This closes the core audit, not the launch gates. PM v11 supersedes the earlier
requirement to close all eight P1s before any production use: only the five
pilot gates at the top of this file block a controlled pilot. `LIC-001` remains
`NOT_CLEARED` and is one of those five gates.

## PM v10 - the audit that produced the above

The developer's changes are meaningful, but an independent code/runtime audit
reopened four findings. The current state is:

| Finding | PM v10 verdict | Evidence |
|---|---|---|
| P0-CORE-001 bootstrap credential | **PARTIAL** | Default/demo accounts are correctly removed from the production branch. However, bootstrap accepts a weak one-time password and changing it does not invalidate JWTs already issued with that password. |
| P0-CORE-002 migration | **PARTIAL** | Job delete/apply/wait/rollout ordering and Alembic-head init gates are correct. However, `app.bootstrap` then drops an Alembic-managed index; live `alembic check` fails on `ix_connector_definitions_display_name`. |
| P0-CORE-003 duplicate run | **CLOSED** | Two partial unique indexes exist. PM sent 20 concurrent requests through the real API: 20 responses, one run id. |
| P0-CORE-004 worker restart | **REOPENED** | Recovery treats every non-503 `AppError` as proof that the job is gone. PM reproduced Airbyte `401` -> local run `FAILED`; a live engine job could continue writing. |
| P0-REL-001 production entrypoint | **REOPENED** | Real render fails before apply because the generated Kustomization references an absolute resource outside its temp root. Workspace/auth/secret refs are also not bound to Pods, Airbyte secrets are checked in the AppBI namespace, and legal/release gates run after deployment. |
| P0-SEC-001 cookie/dependency audit | **CLOSED narrowly** | Production rejects insecure cookies; npm and pip audits are clean and CI-gated. Image/SBOM/signing work remains Sprint B. |

### Independent verification

```text
pytest including 4 live Postgres tests  226 passed, 12 skipped
20 concurrent real API triggers         20 x HTTP 202, 1 unique run id
npm audit --omit=dev                    0 vulnerabilities
pip-audit --strict                      no known vulnerabilities
frontend typecheck + production build   PASS
i18n                                    794/794
four static Kustomize targets           PASS
connector lock                          PASS (4 entries)
embedded E2E                            2,500 first pass, 2 new rows second pass
cancel in PM E2E                        NOT PROVEN; run completed before cancel
```

Static Kustomize targets rendering successfully does not prove the generated
production overlay. PM ran `render_from_config()` itself and it failed with
`new root ... cannot be absolute`.

### Stop-ship order for dev

1. Repair the production renderer and make every reviewed config value bind to
   the rendered workload. Add a real render/dry-run integration test.
2. Classify engine recovery outcomes: only a confirmed job-not-found response
   may mark a run lost; auth, permission, rate-limit, 5xx and transport errors
   must defer.
3. Invalidate all sessions issued before a bootstrap password change; validate
   bootstrap email/password strength and fix the one-time Secret lifecycle.
4. Remove schema mutation outside Alembic for versioned databases; CI must run
   `app.bootstrap` and then `alembic check` on the same database.
5. Run static legal/on-call/provenance gates before any migration or rollout;
   keep post-deploy evidence as a second gate.

The detailed findings and acceptance criteria are at the end of
`PRODUCTION_READINESS_REVIEW.md` under **PM review v10**. Sprint B/C/D and
`P0-PLAT-001` remain open after these Sprint A corrections. Current decision:
**NO-GO for production**.

## Dev Sprint A report (superseded by PM v10 above)

Each one shipped, passed 207 tests, and would have reached production. The
theme is the same in all six: a check in the wrong place.

| Finding | Was | Now |
|---|---|---|
| P0-CORE-001 | `SEED_DEMO_DATA` declared and never read; production got `admin@appbi.local` and three accounts sharing `Admin@12345` | Demo identities exist only in the demo branch. A fresh production database has **no** account and refuses to start without a one-time bootstrap secret; the account it creates must change its password before it can do anything else |
| P0-CORE-002 | Completed migration Job not re-run, its pod template immutable, and a Flux annotation that meant nothing to `kubectl apply -k`; init containers checked only that `alembic_version` had a row | Orchestrator deletes the Job, applies it alone, waits for completion, *then* rolls out. Init containers compare the database revision with the image's Alembic head |
| P0-CORE-003 | Check-then-insert with `replicas: 2`; two concurrent triggers both wrote | Two partial unique indexes. Measured: 20 concurrent triggers produce exactly 1 run |
| P0-CORE-004 | `WORKER_ID` unchanged across a container restart, so a restart failed live Airbyte jobs and users retried into duplicates | Recovery asks the engine. Adopt / lost / **deferred** — an unreachable engine decides nothing |
| P0-REL-001 | `cmd_install` warned and returned 0; the config did not drive the manifests | Production install is fail-closed. The config renders an ephemeral overlay whose output is asserted against the config before apply |
| P0-SEC-001 | `COOKIE_SECURE` defaulted false with no manifest setting it; 2 high npm and 29 pip advisories | Startup refuses production without it. `npm audit --omit=dev` and `pip-audit --strict` both clean, and both now gate CI |

Evidence for each, including the failing-before numbers, is in
[PRODUCTION_READINESS_REVIEW.md](PRODUCTION_READINESS_REVIEW.md) under
"Sprint A".

**This does not make the product production-ready.** Sprint B (release
integrity and supply chain), Sprint C (production-shaped rehearsal on Helm
chart V2) and Sprint D (legal, on-call, launch scope) are untouched, and
`LIC-001` is still `NOT_CLEARED`.

This developer report is retained as history. The current PM decision is at the
end of `PRODUCTION_READINESS_REVIEW.md` under **PM review v10**. The engine
integration is real and accepted; the product is still **not production-ready**.

### Historical PM v9 blockers

1. Fresh production bootstrap always seeds predictable privileged/demo users;
   `SEED_DEMO_DATA=false` is not read by `bootstrap.py`.
2. Kubernetes migration/upgrade ordering is unsafe: a completed fixed-name Job
   is not rerun by `kubectl apply`, and init containers check only that an
   Alembic row exists, not that the database is at the image's head revision.
3. Two API replicas can race and enqueue two active runs for one pipeline;
   `Idempotency-Key` has no unique database constraint.
4. A worker container restart in the same Pod can mark a still-running Airbyte
   job failed, making a duplicate retry possible.
5. `production.py install` can return exit 0 after reconcile or release-gate
   failure, while its production config is not wired into rendered manifests.
6. Frontend dependency audit currently reports two high-severity packages, and
   production does not set `COOKIE_SECURE=true`.
7. Airbyte K8s CI still uses deprecated Helm chart V1. Production must be
   recertified on chart V2, with auth and production-shaped dependencies.
8. `LIC-001`, evidence-v2, assigned on-call and an upstream-independent artifact
   bundle remain open.

Execution order and acceptance criteria are in PM review v9. Do not schedule a
GO review from the current `207 passed` result; those tests do not exercise the
failure modes above.

### The database question, answered

Two databases, always; two instances in production. The product refuses to start
when its database contains a known Airbyte schema. A least-privilege Postgres
role was proven live and `scripts/provision-db.py` can provision/verify it, but
`production.py doctor` does not currently invoke that verification. Reasoning is in
[docs/ADR-001-database-topology.md](docs/ADR-001-database-topology.md).

The product is a control plane. It owns pipelines, schedules, runs, credentials
and the UI; **Airbyte runs the connectors**, reached through
`IntegrationEngineAdapter` in `AIRBYTE_API` mode. No Airbyte identifier appears
in a product URL or payload, the browser never reaches Airbyte, and the product
never touches Airbyte's database.

## What has been proven, by running it

Airbyte `0.59.1`, `ENGINE_TYPE=AIRBYTE_API`, all eleven adapter operations:

| | |
|---|---|
| Source / destination check | `source-postgres`, `destination-postgres` — HEALTHY / PASSED |
| Discover | 3 streams with primary keys, sync modes, field types |
| Sync (full refresh) | 2,700 records, 453,605 bytes — matches the source exactly |
| Sync (incremental) | second run read **0** rows — the cursor persists |
| Warehouse result | 2,007 rows / 2,007 distinct ids — `append_dedup` correct |
| Cancel | `CANCEL_REQUESTED` → `CANCELLED` |
| Job status, stats, logs | totals, per-stream, paginated, ANSI stripped |
| Connector Builder | build → test → publish → source → sync: 100 rows |
| Egress (hardened profile) | internet blocked, sync still succeeds |
| KEK rotation | 13 credentials rewrapped; source still authenticates |
| Backup / restore drill | Paired dump restored; row counts identical, **21/21 credentials decrypted** |

Evidence: `compatibility.yaml` -> `airbyte_api_certification`.
Reproduce: `python scripts/e2e.py --source postgres --engine airbyte-api --evidence evidence-e2e.json`.

### And on Kubernetes, which is what production runs

Airbyte **1.8.5** via the official Helm chart on Kubernetes 1.30.4, connectors
executing as pods in Airbyte's namespace:

| | |
|---|---|
| Sync (full refresh) | 2,507 records, 429,904 bytes - `500 + 2007` matches the source exactly |
| Sync (incremental) | second run read **0** rows |
| Cancel | `CANCEL_REQUESTED` -> `CANCELLED` |
| Job logs | 285 lines through the product API |
| Connector Builder | tested and published on the cluster's declarative runner |

Three defects that only a real 1.x deployment could surface, all fixed:

- `/api/v1/workspaces/list` is **404** on 1.8.5. The adapter now tries three
  routes and declares them as alternatives so the probe understands.
- Job logs moved from `logLines` to a structured `events` array. The adapter read
  only the former, so every log view was silently empty.
- A cold cluster reports `ENGINE_UNAVAILABLE` while a connector pod pulls its
  image. `pull-engine-images.py --into-kind` pre-pulls.

Repeatable: CI lane `airbyte-k8s-contract`. Manual:
[docs/RUNBOOK-engine-upgrade.md](docs/RUNBOOK-engine-upgrade.md).

## Beyond Airbyte

The architecture claims the engine is swappable. That claim now has a third
adapter behind it that is not Airbyte in any respect —
`backend/app/adapters/sql_direct/`: plain SQL between Postgres databases, no
connector images, no protocol, no server-side connection or job objects.

It runs: 3 streams discovered with correct primary keys, 2,007 records synced,
0 on the incremental second pass. **The interface needed no change.**

Three genuine Airbyte leaks above the boundary were found and closed by the
exercise: a service importing the Airbyte protocol module, secret detection
that only understood `airbyte_secret`, and the product hard-coding an Airbyte
image for the Connector Builder runner. A test now fails if any layer outside
`adapters/` imports an engine again.

Honest limit: the Connector Builder does not port. It compiles to the Airbyte
low-code CDK and there is no neutral target — `sql_direct` declines it rather
than pretending. Details and the four places the interface pinched:
[docs/ENGINE-PORTABILITY.md](docs/ENGINE-PORTABILITY.md).

## Production launch gate

Airbyte on Kubernetes is certified for `1.8.5` in this repo, but production is
still **NO-GO**. The current release blockers are:

1. `LIC-001` is `NOT_CLEARED`. The release gate now **reads** it and blocks —
   clearing it is a decision for legal, not a code change.
2. Release evidence is not yet bound to the deployed product build, engine,
   workspace and exact E2E run ids. **Still open, and it is code.**
3. ~~`scripts/production.py` / production config do not exist~~ — both exist:
   `scripts/production.py` with `install/upgrade/status/doctor/logs/rollback`,
   `deploy/production.yaml.example` and `deploy/demo.yaml`.
4. No single rehearsal has run AppBI K8s and Airbyte K8s together with
   production auth, managed datastores/object storage, TLS and enforcing CNI.
   **Still open, and it needs infrastructure rather than code.**
5. ~~The Airbyte connector policy is outside the rendered release overlay~~ —
   it now has `airbyte/base` + `overlays/production`, and the release gate
   renders and checks both overlays.
6. ~~651 `BETA` connectors are selectable~~ — the default launch scope is
   `SUPPORTED_ONLY`. 654 connectors are listed, 3 are selectable, and the
   create path returns `CONNECTOR_NOT_IN_LAUNCH_SCOPE` rather than relying on
   a greyed-out card.

If the production target is still `1.8.5`, run the production-shaped
certification again after the release gate is repaired. If the target moves to
a newer 1.x/2.x version, certification must be re-run.

Historical note from before the K8s certification:

Before the 1.8.5 run, certification was only on Compose 0.59.1 while
production would be Kubernetes 1.x/2.x.

That historical gap existed because 0.59.x is the last Airbyte line with a
Compose distribution, so the staging stack could not be the production target.
The 1.8.5 K8s certification now closes that specific gap; a move to any newer
1.x/2.x version must repeat the same gate.

The re-certification path is measurable rather than speculative:

```bash
python scripts/verify-engine-api.py --url <the real one>   # minutes
RUN_ENGINE_CONTRACT=1 pytest tests/test_adapter_contract.py # an hour
python scripts/e2e.py --engine airbyte-api --evidence evidence-e2e.json
python scripts/release-gate.py record --evidence evidence-e2e.json --out certification.json
python scripts/release-gate.py check certification.json
```

[docs/RUNBOOK-engine-upgrade.md](docs/RUNBOOK-engine-upgrade.md).

## Closed since PM review v5

PM review v5 found a render-time manifest bug: `commonLabels` injected
`app.kubernetes.io/part-of: appbi-integration` into the external `kube-dns`
`podSelector`. That is now fixed with `labels.includeSelectors: false`,
rendered-output tests, and a Calico smoke run.

Product NetworkPolicy proof is now closed for the product namespace: DNS works,
an allowed database is reachable, internet and cloud metadata are blocked.

## Also open

| | Current state |
|---|---|
| Connector egress | Measured under Calico with a control. The production CIDR is now in an overlay the release gate renders and checks; cloud metadata still cannot be measured on kind, which the runbook says rather than claims |
| Airbyte API boundary | `airbyte-server-ingress` restricts the Config API to the product's api/worker pods, and `doctor` fails a production profile whose engine reports `auth: none`. **An auth-enabled certification run has not happened** |
| A real on-call rotation | Alert names no longer drift — the runbook's own copy of the rules is gone and a test compares the table against `alerts.yaml` both ways. Owners remain `TO BE ASSIGNED`, and the gate blocks on that |
| Disaster recovery | Mismatch detection is proven in both directions. A paired restore into a fresh product + Airbyte environment is **still not evidenced** |
| Database separation | The startup guard now scans every non-system schema, and `scripts/provision-db.py` provisions and verifies the least-privilege role — the SQL is no longer only in the ADR |
| One-command operations | `scripts/production.py` exists and was exercised from a wiped machine: no containers, no images, no `.env`. See below |

## Deployed from nothing, in one command

The product containers/volumes/images, Airbyte platform images, four pinned
connector images, certification cluster and `.env` were removed, and then:

```bash
python scripts/production.py install --config deploy/demo.yaml
```

Exit 0. This proved the demo profile on this machine, not a fully cold or
production-shaped machine: 37 other catalogue connector images remained and
could retain shared layers. It generated the encryption key and JWT secret, built five images,
started six containers, waited until the API was serving *and* the engine
answered, reconciled, and printed the URL. Re-running it kept the existing
secrets rather than orphaning the credentials in the database.

Two real defects surfaced by doing it rather than assuming it: the demo's
`env://` password reference resolved to nothing, so reconcile came back 401
while the install still reported success; and `doctor` run from a fresh shell
had the same gap. Both fixed — `env://` now falls back to `.env`.

## Running it

```bash
python scripts/stack.py lite       #  4 containers — API/schema work
python scripts/stack.py embedded   #  7 containers — local demo with UI
python scripts/stack.py airbyte    # 14 containers — real Airbyte, certification
python scripts/stack.py status     # what is running, and what it costs
python scripts/stack.py stop       # stop the Airbyte half, keep the product
```

The 14-container stack exists because it runs both the product and an Airbyte
deployment on one machine. It is for certification, not for editing a React
component.

`scripts/production.py` now provides the requested commands, but PM v9 found
that the production path is not yet idempotent or fail-closed: configuration is
not wired into rendered manifests, migration ordering is unsafe, and install
can return zero after release checks fail. Treat it as work in progress, not a
production entrypoint.

## Deploying

`deploy/kubernetes/` — plain manifests, `kubectl apply -k .`. API, worker,
migration job, frontend, NetworkPolicies, PDB, ingress. Postgres, Redis and
Airbyte are deliberately absent: the first two should be managed services, and
Airbyte has its own chart and lifecycle.

`base/` is the shape, `overlays/production/` supplies the environment. Apply
the overlay — the base holds a deliberately wrong database CIDR so applying it
by mistake fails closed.

**Applied to a real cluster** (kind, Kubernetes 1.30): migrations ran from
empty, API 2/2 and worker 1/1 Running with zero restarts, `/readyz` 200 while
`/readyz?deep=1` returned 503 because that cluster had no Airbyte — the
readiness split working exactly as designed.

**NetworkPolicy verified under Calico**, because kind's default CNI accepts
policies without enforcing them. From an API-labelled pod: DNS works, an
allowed database is reachable, internet and cloud metadata blocked.

Three defects those runs found that schema validation could not: a
`commonLabels` transformer that had silently rewritten the kube-dns selector
(DNS would have been blocked in any enforcing cluster), an init container
pinned to a `bitnami/kubectl` tag that does not exist, and an unset
`imagePullPolicy`. All fixed, all now covered by tests — including tests on the
**rendered** output, since every source-level check was green throughout.

Guarded by tests that the ConfigMap only names real settings, readiness is
never `?deep=1`, no pod gets a runtime socket or root, every image is one this
project builds, and the network policy is deny-by-default.

## Operations

| | |
|---|---|
| [Backup / restore](docs/RUNBOOK-backup-restore.md) | `scripts/backup.py` — records which KEK each dump belongs to and refuses a mismatched restore |
| [Secret rotation](docs/RUNBOOK-secret-rotation.md) | `scripts/rotate-kek.py` — rewraps data keys without decrypting a credential |
| [On-call](docs/RUNBOOK-oncall.md) | `/metrics`, alert rules, and what each symptom means |
| [Egress](docs/RUNBOOK-egress.md) | measured, per target, with `scripts/verify-egress.py` |
| [Airbyte workspace](docs/RUNBOOK-airbyte-workspace.md) | why `AIRBYTE_WORKSPACE_ID` is configured and never guessed |
| [Engine upgrade](docs/RUNBOOK-engine-upgrade.md) | how to certify a different Airbyte |
| [Engine portability](docs/ENGINE-PORTABILITY.md) | what running on something other than Airbyte takes |

Health endpoints: `/healthz` liveness · `/readyz` load balancer · `/readyz?deep=1`
deploy gate. Do not point a load balancer at the deep one — it fails when the
engine is down, which would take the whole UI out during an engine outage.

## Gates

Per PR: backend tests (194 locally on 2026-08-24), frontend typecheck, i18n parity, secret scan,
Kubernetes manifest schema validation.
On merge to main: full engine contract, live UAT, migrations from empty,
supply-chain lock, API and UI audits.
Nightly / on demand: `airbyte-api-contract` — the real Airbyte, egress check,
and an unsigned JSON certification artifact. The `airbyte-k8s-contract` lane
does the same against Airbyte on Kubernetes.

A release requires `python scripts/release-gate.py check` to pass. It refuses
certification that is stale (>7 days), from a different commit, from a dirty
tree, from the wrong engine, or missing any of the eleven operations. PM v8
found that it does not yet bind the evidence to the live build/run, does not
check `LIC-001`, and does not inspect the separately shipped connector policy;
passing it is therefore necessary but not sufficient until those findings close.

The operations come from `compatibility.yaml` rather than a second list in the
gate — the two had already drifted, nine against eleven — and the evidence
comes from files the verifiers write (`scripts/e2e.py --evidence`). `--verified`
used to default to "all of them", which let an artifact assert its own
evidence; recording now fails without an evidence file.
