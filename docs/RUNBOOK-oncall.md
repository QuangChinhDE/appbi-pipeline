# Runbook — on-call

## Ownership — fill this in before the first page

The rest of this runbook says what to do. It cannot say **who**, and a rota
that exists only as an assumption is the reason a page goes unanswered at 3am.
These are organisational facts, not code, so they are recorded here and checked
at release rather than inferred.

| | Value | Set by |
|---|---|---|
| Primary on-call | `TO BE ASSIGNED` | the team that owns this deployment |
| Secondary / escalation after 15 min unacknowledged | `TO BE ASSIGNED` | |
| Escalation after 45 min | `TO BE ASSIGNED` | engineering manager |
| Paging channel | `TO BE ASSIGNED` | PagerDuty / Opsgenie / rota tool |
| Business-hours definition | `TO BE ASSIGNED` | affects which alerts page vs ticket |
| Data-owner contact for pipeline failures | per workspace, in Settings → Alerts | product |

`python scripts/release-gate.py check` fails while any `TO BE ASSIGNED`
remains. That is deliberate: this file is the kind of thing that stays a
placeholder for a year because nothing ever forces the question.

### Which alerts page, and which do not

| Alert | Severity | Action |
|---|---|---|
| `AppBIEngineUnreachable` | page | the whole product can sync nothing |
| `AppBIMetricsDegraded` | page | monitoring is blind; every other rule is now silent |
| `AppBIRunStuck` | page during business hours, ticket otherwise | data is late, nothing is broken |
| `AppBIFailureWave` | page | a shared cause — credentials, network, engine upgrade |
| `AppBIQueueBacklog` | ticket | work is arriving faster than it drains |
| a product alert rule (per pipeline) | never pages | belongs to the data owner |

Names come from `deploy/monitoring/alerts.yaml`, which is the source of truth.
A test fails if this table and that file disagree — they did, and the runbook
named two alerts that do not exist.

The distinction that matters: an alert nobody is expected to act on
immediately must not page, or the ones that are get ignored with them.

### Silences

- Silence with an **expiry**, never open-ended. A silence with no end is an
  alert that has been deleted by someone who did not have to say so.
- Maximum 24 hours without a linked ticket; maximum 7 days with one.
- Silencing `AppBIMetricsDegraded` silences everything else by
  implication, because every other rule reads metrics this endpoint produces.
  Treat it as an outage, not a mute.
- Record the reason. "Noisy" is not a reason; "engine upgrade window, ticket
  OPS-123" is.

### After a page

Fill in the incident notes at the end of this runbook. The specific thing worth
capturing is which signal *first* told you what was wrong — that is the one to
alert on next time, and it is usually not the one that paged.

## Two alerting systems, and which is which

| | For | Reaches |
|---|---|---|
| **Product alert rules** | data owners: this pipeline failed, this schema changed | the workspace, in-app / email / webhook |
| **Infrastructure alerts** | on-call: the platform itself is unwell | your pager |

They answer different questions and must not be merged. A pipeline failing
because a customer revoked a password is not a page. The engine being
unreachable for ten minutes is.

## What to scrape

`GET /metrics`, unauthenticated, Prometheus text format, not under `/api/v1`.

| Metric | Type | Meaning |
|---|---|---|
| `appbi_engine_reachable{engine_type,engine_version}` | gauge | 1 if the engine answered a health check |
| `appbi_runs_total{status}` | gauge | runs by status |
| `appbi_runs_active` | gauge | runs queued or executing now |
| `appbi_oldest_active_run_seconds` | gauge | age of the longest-running active run |
| `appbi_pipelines_total{status}` | gauge | pipelines by status |
| `appbi_metrics_up` | gauge | 1 if the scrape collected cleanly |

`appbi_metrics_up` exists because a scrape endpoint that returns 500 takes the
monitoring down along with the thing it monitors. A failed collection returns
200 with this at 0.

The endpoint is not exposed through the public ingress. Scrape it on the
internal network, the same way you would any other service endpoint.

## Alert rules

They live in [`../deploy/monitoring/alerts.yaml`](../deploy/monitoring/alerts.yaml)
and are applied with the rest of your Prometheus configuration.

This runbook used to carry its own copy of them. That copy drifted — it named
`AppBIMetricsCollectionFailing` and `AppBIRunsStuck` while the rules file
declared `AppBIMetricsDegraded` and `AppBIRunStuck` — so an operator searching
their alerting system for a name from this page would have found nothing. There
is now one copy and a test that the names on this page match it.

`for:` on every rule is deliberate. `appbi_engine_reachable` drops to 0 during
a routine engine restart, and an alert that fires on a single scrape teaches
people to ignore it.

## Health endpoints, and which to point at what

| Endpoint | Answers | Point it at |
|---|---|---|
| `/healthz` | is this process alive | liveness probe |
| `/readyz` | can it serve traffic — database required, engine reported | load balancer, readiness probe |
| `/readyz?deep=1` | is the whole dependency chain healthy — engine required | deploy gate, smoke test |

**Do not wire `/readyz?deep=1` to a load balancer.** It fails when the engine is
down, which would remove every API instance from rotation during an engine
outage — nobody could then read run history, see the alert, or acknowledge it.
Turning a partial outage into a total one is not a health check.

`READINESS_REQUIRE_ENGINE=true` makes plain `/readyz` strict, for deployments
that genuinely want that. Understand the paragraph above before setting it.

---

## Engine unreachable

**Symptom:** `appbi_engine_reachable == 0`; the UI shows the engine banner;
syncs queue and do not start.

```bash
curl -s "http://localhost:8010/readyz?deep=1" | python -m json.tool
docker ps --filter name=appbi-airbyte --format "{{.Names}}\t{{.Status}}"
docker logs --tail 100 appbi-airbyte-server
```

Common causes, in the order they actually occur:

1. **Airbyte restarting.** The server takes a minute or two. Wait, then re-check.
2. **Airbyte's database is down.** `airbyte-server` logs a connection error on
   loop. Fix Postgres first.
3. **Temporal is down.** The server answers `/health` but every job hangs.
   `docker logs appbi-airbyte-temporal`.
4. **Configuration drift.** `AIRBYTE_API_URL` points somewhere that no longer
   exists. The boot log carries `startup.engine` with the URL in use.

The product stays usable throughout — history, configuration and alerts all
read from its own database. Nothing needs to be failed over.

## Runs stuck active

**Symptom:** `appbi_oldest_active_run_seconds` climbing past a few hours.

```bash
docker exec appbi-pipeline-postgres psql -U appbi -d appbi_integration -c \
  "select id, pipeline_id, status, started_at, heartbeat_at, engine_job_ref
     from pipeline_runs where status in ('RUNNING','QUEUED','STARTING')
     order by started_at;"
```

- `heartbeat_at` far behind `started_at`: the worker died mid-run. The reaper
  marks it stale after `STALE_RUN_SECONDS` (default 6h); cancel it through the
  API to reclaim the slot sooner.
- Heartbeat current, no progress: the job is genuinely running on Airbyte. Look
  at the run's logs in the UI before cancelling — a large first full refresh
  legitimately takes hours.
- `QUEUED` and nothing running: concurrency limits
  (`MAX_CONCURRENT_RUNS_*`) or a stopped worker. `docker ps | grep pipeline-worker`.

Cancel through the product, never in Airbyte directly — the product owns the
run record and would not learn about an engine-side cancellation.

## A wave of failures

Check whether it is one cause or many:

```bash
docker exec appbi-pipeline-postgres psql -U appbi -d appbi_integration -c \
  "select error_code, error_category, count(*)
     from pipeline_runs
    where status='FAILED' and created_at > now() - interval '2 hours'
    group by 1,2 order by 3 desc;"
```

One dominant `error_code` is an infrastructure problem — the destination is
full, the engine is degraded, a network path closed. A spread across many codes
is normal operation and belongs to the data owners, not on-call.

`error_fingerprint` groups the same failure across pipelines, which is how you
tell "forty pipelines broke" from "one thing broke forty times".

## Metrics degraded

`appbi_metrics_up == 0` for ten minutes. The scrape is answering — that is why
this is not simply "the endpoint is down" — but the collection behind it is
failing, almost always because the database query it runs is failing or timing
out.

Every other rule on this page reads metrics this collection produces. While
this is firing, they are not silent because things are healthy; they are silent
because nothing is being measured. Treat it as an outage of the monitoring, and
do not silence it to reduce noise.

```bash
kubectl -n appbi logs deploy/appbi-api --tail=100 | grep -i metrics
kubectl -n appbi exec deploy/appbi-api --   python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/metrics').read().decode()[:400])"
```

If `/metrics` returns 200 with `appbi_metrics_up 0.0`, the endpoint is doing
exactly what it was designed to do: fail without taking the monitoring down
with it. Look at the database.

## Queue backlog

`appbi_runs_active > 20` for thirty minutes. Work is arriving faster than it
drains. This is a ticket rather than a page: nothing is broken, and the usual
cause is a schedule change or a backfill nobody mentioned.

Check whether it is throughput or a stuck worker:

```bash
kubectl -n appbi get deploy appbi-worker
kubectl -n appbi logs deploy/appbi-worker --tail=100
```

Concurrency is capped by the product, not by the engine
(`MAX_CONCURRENT_RUNS_GLOBAL`, `..._PER_WORKSPACE`). Raising it without
checking that the engine can absorb the extra load moves the queue rather than
draining it.

## After any incident

Record the Airbyte platform version and the connector versions that were in
play. In `AIRBYTE_API` mode the engine picks connector versions, so an incident
can be caused by a connector upgrade the product did not make:

```bash
curl -s -H "Cookie: $SESSION" http://localhost:8010/api/v1/admin/compatibility \
  | python -m json.tool | head -40
```

`version_matches_engine: false` on a connector means the deployment is running
a different tag than the product locked. That is expected in API mode, and it
is also the first thing to check when a connector starts behaving differently
with no change on our side.
