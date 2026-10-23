# AppBI Pipeline — agent contract

Provider-neutral instructions for any coding agent working in this repository.
Claude Code reads this file through `.claude/CLAUDE.md`; other agents read it
directly. Area-specific rules live in `.claude/rules/` and are not repeated
here. Executable checks outrank everything in this file.

## What this is

A self-hosted data platform. A workspace connects **sources** and
**destinations**, runs **pipelines** (ingestion) and **transforms** (dbt
projects), and watches the result. AppBI owns the product: auth, RBAC,
tenancy, scheduling, audit, UI. Execution is delegated to engines it does not
own — Airbyte for ingestion, dbt for transform.

The browser talks only to `/api/v1`. The engine is never addressable from
outside (guardrail 1). Code comments reference a numbered spec (`section 34.1`,
`guardrail 5`); that spec lives in `docs/`, which is deliberately untracked —
do not assume you can read it, and do not invent its contents.

## Architecture boundaries

Verified from the code, not from directory names.

| Layer | Path | Owns |
|---|---|---|
| UI | `frontend/src/` | Next.js App Router, TanStack Query, presentation only |
| API / BFF | `backend/app/api/v1/*.py`, `backend/app/main.py` | routing, request validation, the normalized error envelope |
| Domain | `backend/app/services/` | business rules, orchestration, tenancy checks |
| Persistence | `backend/app/models/` (SQLAlchemy) + `backend/migrations/` | schema and rows |
| Engine boundary | `backend/app/adapters/` | the *only* code that understands an engine |
| Connectors | `backend/app/connectors/` | AppBI's own first-party connectors (base_vn, kiotviet, zalo) |
| Transform | `backend/app/transforms/` | a vertical slice: its own router, services and dbt runtime |
| Background | `backend/app/worker.py`, `backend/app/transform_worker.py` | run execution, outbox, scheduling |
| Deployment | `docker-compose*.yml`, `deploy/`, `run.sh`, `run.ps1` | runtime contracts |

**`backend/app/repositories/` and `backend/app/workers/` are empty packages.**
There is no repository layer and no workers package in this product. Do not
create one to satisfy a pattern you expect; services talk to SQLAlchemy
sessions directly.

Rules that must not be broken casually:

- Business logic belongs in `services/` (or `transforms/`), not in a router.
- **The router owns the transaction boundary**: it authorizes with
  `ctx.require(...)`, calls the service, then commits. Services on a request
  path mutate and return; they do not commit.
- Only `adapters/` may know an engine exists. A service depends on the
  `IntegrationEngineAdapter` Protocol in `backend/app/adapters/base.py` and gets
  an instance from `backend/app/adapters/registry.py::get_adapter()`.
- There are **three** engine implementations, not two: `AIRBYTE_EMBEDDED`,
  `AIRBYTE_API`, and `SQL_DIRECT` — which is not Airbyte at all and exists to
  prove the adapter interface abstracts an engine rather than abstracting
  Airbyte. It is the check on whether a change respects the boundary.
- Transform's router lives in `backend/app/transforms/api.py`, not `api/v1/`.
  That is deliberate; leave it there.
- The frontend never calls an engine and never reaches a database.

## Where the area rules live

| Area | Rule file |
|---|---|
| `backend/app/**` | `.claude/rules/backend.md` |
| adapters, transforms, connectors, lock files | `.claude/rules/engine-boundary.md` |
| migrations and models | `.claude/rules/database-migrations.md` |
| secrets, auth, permissions, config | `.claude/rules/security.md` |
| `frontend/**` | `.claude/rules/frontend.md` |
| tests and CI | `.claude/rules/testing.md` |
| guardrail, verifier, workflows, agent config | `.claude/rules/governance.md` |

Read the rule for every area your diff touches before editing it.

## Source-of-truth hierarchy

When two sources disagree, the higher one wins:

1. Executable tests and deterministic checks (`backend/tests/`, CI, `scripts/verify.py`)
2. Explicit contracts — `engine-lock.json`, `connector-lock.json`, `compatibility.yaml`, `docker-compose*.yml`, `deploy/`
3. Current implementation patterns in the code
4. `README.md` and tracked comments
5. This file and `.claude/rules/`
6. An agent's local memory

Local memory is one machine's working notes. It never overrides an executable
contract. Before acting on a remembered fact, verify the file, flag or command
still exists.

## Engine policy

`engine-lock.json` pins Airbyte **0.59.1** with a stated reason: it is the last
release that runs a sync under Docker Compose — 0.63+ routes connector jobs
through the workload launcher, which resolves `kubernetes.default.svc` and has
no Docker mode. That pin is architecture, not stale dependency hygiene.

Without explicit task intent saying so, never:

- bump the Airbyte version or any image digest in `engine-lock.json`
- edit `connector-lock.json` or `compatibility.yaml` by hand (they are generated
  by `scripts/build-connector-lock.py` / recorded deliberately)
- reimplement engine behaviour inside AppBI because calling the adapter is awkward
- change the engine's Docker topology
- replace dbt-native semantics in `backend/app/transforms/` with hand-rolled SQL execution

An engine change is a project: intent, compatibility analysis, affected
deployments, tests, rollback. Never incidental cleanup.

## Connector compatibility

Anything touching connector execution, definitions or specs must read
`connector-lock.json`, `compatibility.yaml`, the relevant connector under
`backend/app/connectors/`, and the adapter it runs through. A one-file adapter
diff is not evidence that a connector change is isolated.

## Required workflow for non-trivial work

1. Read the existing implementation before proposing anything.
2. Name the layers affected.
3. Name the regression surface.
4. Find the tests that cover it — before writing code.
5. Make the smallest coherent change.
6. Run targeted checks continuously (`python scripts/verify.py fast`).
7. Read your own diff.
8. Run `python scripts/verify.py task` before claiming done.
9. Report what was and was not verified.

## How much process a change earns

Those nine steps are the floor. What a change adds to them follows from what it
can break, not from how large the diff is. `python scripts/guardrail.py`
reports the level it computes from the diff; the higher of its level and yours
applies.

| Risk | Typical work | Adds to the floor | Feature docs |
|---|---|---|---|
| **Low** | copy, i18n text, isolated visual polish, local styling, a rename, a local component edit with no contract change | nothing | none |
| **Medium** | a shared frontend primitive, workflow or UI behaviour, query or state behaviour, formatting and scheduling presentation, cross-screen consistency | read the owning backend/frontend contract first; targeted regression or runtime evidence; CI on the PR. Independent review only if ambiguity survives your own diff read | a plan, in the task or PR description |
| **High** | auth, RBAC, workspace/tenant isolation, credentials and secrets, migrations and persisted-data changes, destructive data operations, adapters and the engine boundary, deployment contracts, the notification/incident model, shared API contracts, the guardrail itself | a plan before code; targeted regression coverage; an independent review in a fresh session; CI green before merge | `docs/features/<slug>/` from `docs/features/_TEMPLATE/` |

**Escalation outranks the first guess.** A Low or Medium task becomes High the
moment the work turns up a cross-layer contract change, a data migration, a
security consequence, or a product invariant you cannot state with confidence.
Re-enter at High rather than finishing at the level you started from.

**One independent pass is enough.** A finding a review has already accepted is
not re-reviewed; re-open only what a concrete blocker reopens. Local work never
earns a full-product audit.

## Change discipline

- Minimise blast radius. No unrelated cleanup, no speculative refactor.
- Reuse existing abstractions before adding one.
- Never upgrade a dependency opportunistically. Not Airbyte, dbt, Next.js,
  FastAPI, SQLAlchemy, Python, Node or Postgres.
- Do not silently change a public API shape, persisted data behaviour or a
  deployment contract. Backward compatibility holds unless the task changes it.

## Database discipline

See `.claude/rules/database-migrations.md`. In short: inspect the chain first,
use Alembic as the repo already does, keep exactly one head, assume installed
databases with real rows exist, and get explicit approval before anything
destructive.

## Security discipline

Passwords, tokens, connector credentials, service-account keys, JWT secrets and
encryption keys never appear in UI, logs, source, fixtures, error responses,
screenshots or committed evidence. `.env*` (except the two examples),
`secrets/`, `evidence-*.json` and service-account JSON are gitignored for this
reason — do not negate those rules. Never regenerate or overwrite an existing
encryption or auth secret; that destroys every credential already stored.

## Never weaken the protection system

The verifier, the guardrail, the agent-config validator, their tests and the CI
jobs that run them are protected the same way an engine lock is. Do not delete,
skip, loosen or re-scope a check to make a task pass. In CI the protection check
runs from the merge base, so a pull request cannot approve its own weakening; a
deliberate change needs the owner approval described in
`.claude/rules/governance.md`.

## Definition of Done

Do not claim completion unless `python scripts/verify.py task` has run and
exited 0. Its states mean:

- **PASS**: the step ran and succeeded.
- **FAIL**: the step ran and failed.
- **SKIPPED**: a step this level does not require was not run (`fast` only).
- **UNVERIFIED**: a required step could not run. `task` and `full` treat this as failure.
- **BLOCKED**: the guardrail refused the diff.

Never weaken, skip or delete a check to make a task pass.

End every coding task with exactly these four sections:

**Changed** — what materially changed.
**Verified** — commands actually executed, and their results.
**Not verified** — what could not be run, and why.
**Risks** — residual risk.

Never write "tested" or "verified" about something you only reasoned about.
