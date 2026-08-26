# The engine

The product runs **Airbyte 0.59.1**, vendored into this repository and running
in the same Docker Compose group as everything else. One `docker compose up -d`
brings up the product and the engine together. There is no Kubernetes.

```
docker compose up -d
```

| | |
|---|---|
| Product | postgres · migrate · api · worker · frontend · proxy |
| Engine | airbyte-bootloader · server · worker · temporal |
| Glue | airbyte-connector-pin |

Eight containers running, three of them run-once. The stock Airbyte compose is
ten on its own; what came out and why is at the bottom of this page.

## Why 0.59.1 and not something current

Airbyte's execution plane became Kubernetes-only, in stages. Verified against
the real images rather than the documentation:

| Version | In Docker Compose |
|---|---|
| 1.8.5 | the bootloader needs a Kubernetes namespace to create auth secrets, and again to register a dataplane. The first has a flag; the second does not. |
| 0.64.7 | bootloader, server, Temporal and the Config API all run — and every connector job is routed through the **workload launcher**, which resolves `kubernetes.default.svc`. Grepping the jars finds no way to opt out. |
| **0.59.1** | predates the workload launcher entirely. Zero workload jars; the worker starts connector containers on the Docker daemon. |

The workload launcher landed in the 0.63 line, which makes 0.63 the worst
version available for this: the control plane starts, everything looks healthy,
and no sync ever runs.

So the choice is 0.59.1 or Kubernetes. This product chose 0.59.1.

## What that costs

**Destination connectors must predate Airbyte's refresh protocol.** Any
destination declaring `supportsRefreshes: true` requires `generationId`,
`minimumGenerationId` and `syncId` in the configured catalog. Platform 0.59.1
predates the protocol and never sends them, so a modern destination dies on the
first record:

```
BeanInstantiationException: PostgresWriter
Caused by: NullPointerException: getGenerationId(...) must not be null
```

Which is why the destination pins are held below upstream:

| Connector | Upstream | Pinned here |
|---|---|---|
| `destination-postgres` | 3.0.16 | **2.0.10** |
| `destination-bigquery` | 3.0.22 | **2.4.19** |
| `destination-google-sheets` | 0.3.5 | 0.3.5 (no refresh support) |

Sources are unaffected — refreshes are a destination concern.

**Raising a destination past that line means raising the platform, which means
Kubernetes.** That is the whole trade, in one sentence.

## The trap that bites twice

Airbyte 0.59.1's bootloader seeds its connector definitions from Airbyte's
**current** catalogue, and it does it on *every start* — not once at first boot.
So a platform from 2024 comes up offering connectors published this month, and
`destination-postgres` arrives as 3.x.

Nothing warns about it. The definition looks healthy, `check` passes, and the
failure arrives at replication time. Pinning by hand works, and is undone by the
next `docker compose up`.

`airbyte-connector-pin` is a service in the stack that re-applies
`connector-lock.json` to the engine after the server is healthy. `api` and
`worker` both `depends_on` it with `service_completed_successfully`, so the
product cannot start against an unpinned engine.

```
$ docker logs appbi-airbyte-connector-pin
  pinned airbyte/source-bigquery 0.4.2 -> 0.4.5
  pinned airbyte/source-faker 6.1.0 -> 7.2.1
  pinned airbyte/destination-bigquery 3.0.22 -> 2.4.19
  pinned airbyte/destination-postgres 3.0.17 -> 2.0.10
engine connectors: 5 pinned, 2 already correct
```

One place decides connector versions: `connector-lock.json`.

## Keeping it when upstream stops

`docker pull` is not a supply chain — it is somebody else's decision about what
to keep, and a four-year-old platform is exactly the kind of thing that gets
removed.

```bash
python scripts/vendor-engine.py lock       # resolve every image to a digest
python scripts/vendor-engine.py save       # write the bytes to vendor/engine/
python scripts/vendor-engine.py restore    # load them on a new machine
python scripts/vendor-engine.py verify     # is what runs what we pinned
python scripts/vendor-engine.py connectors # push the lock into the engine
```

`engine-lock.json` records the digest of all five images and **is committed**.
`vendor/engine/*.tar` holds the actual bytes — 2.3 GB, git-ignored, travelling
with the project directory. Copy the folder to another machine, run `restore`,
and the engine works with Docker Hub unreachable.

Proven, not assumed: `airbyte/container-orchestrator:0.59.1` was deleted from
the daemon, restored from `vendor/engine/`, and `verify` reported its digest
matching the lock. (An earlier run of this proof used `minio/mc`, which is no
longer part of the stack — a proof naming an image the product does not ship is
not a proof of anything.)

`container-orchestrator` is in the lock and not in the Compose file. The worker
spawns it per job, so a machine without it starts cleanly and fails on the first
sync — the kind of omission that only shows up in front of a customer.

## The source

`vendor/engine/SOURCE.md` records the tag and how to fetch it. It is deliberately
not cloned by default: a fork is a decision, not a side effect of running a
script.

Before forking, note that Airbyte is **ELv2**. Running it is one question;
modifying and distributing it inside a commercial product is a different one,
and it is the same question `LIC-001` in `compatibility.yaml` is already open
on. A patch that is *not* a fork — an environment variable, a config change, a
sidecar — avoids it entirely and is worth trying first.

## What was removed

The product previously carried a second, Kubernetes-based deployment path for
the engine. With the engine running in Compose there is nothing on the other
end of it, so keeping it would have been worse than clutter — it described a
topology the product no longer supports.

| Removed | Was |
|---|---|
| `deploy/kubernetes/airbyte/` | Helm values, external Postgres, namespace network policies |
| `docker-compose.k8s-cert.yml` | pointed the product at an Airbyte on kind |
| `scripts/render-engine-values.py` | rendered the Helm values |
| `scripts/patch-airbyte-community-auth.py` | worked around a chart 2.0.17 auth bug |
| `scripts/airbyte-application.py` | created an Application on Airbyte 1.x |
| CI `airbyte-k8s-contract` | certified Airbyte on Kubernetes |
| kind cluster `appbi-base-cert` | the dev's test cluster |

`deploy/kubernetes/base` and `overlays/production` **stayed**: they deploy
AppBI itself, which is still a reasonable thing to want. The engine is then an
external URL.

## Verification

```
one-command cold start   docker compose down && docker compose up -d  -> all 8 up, 3m13s
deep readiness           200, engine_type AIRBYTE_API, engine ok
golden path              2,500 records (500 customers + 2,000 orders)
incremental              second sync reads 1 record per stream
cancel                   CANCEL_REQUESTED -> terminal CANCELLED
connector pins           destination-postgres 2.0.10, destination-bigquery 2.4.19,
                         held across a cold boot
engine images            5/5 match engine-lock.json
archive restore          container-orchestrator deleted from the daemon,
                         restored from vendor/engine/, digest matches
backend suite            469 passed, 37 skipped
```

The first sync on a genuinely cold machine is the one thing that does *not* fit
in the suite's budget; see "First sync on a cold machine" below.

**Cancel reaching a terminal `CANCELLED` is new.** The embedded runner could
never prove it — a sync finished before the cancel landed, every time — and it
has been an open acceptance gap for several review rounds. The real platform
closes it.

## First sync on a cold machine

`docker compose up -d` brings the stack up in about three minutes, and the
*first* sync after that can take another fifteen — not because anything is
wrong, but because the worker pulls each connector image the moment a job needs
it, and these are 0.8–1.5 GB each.

That is long enough to exceed the end-to-end suite's 900-second wait, which
then reports `FAILED: RUNNING` on a stack that is working correctly. Measured,
not guessed: the same golden path passes in minutes once the images are local.

So pull them up front. This needs no engine and no arguments:

```bash
python scripts/pull-engine-images.py
```

**It pulls eight images and no more** — the seven connectors in the product's
catalogue plus `source-declarative-manifest`, the runner that executes every
connector built inside the product. The engine's *own* catalogue is a different
thing: its bootloader seeds Airbyte's current registry, so it offers six
hundred-odd connectors, and none of the rest are ever pulled. Nobody can select
them in the product, so downloading them would be tens of gigabytes for nothing.

There is no second list of connector names anywhere. The set is derived from
`backend/app/resources/connector_registry.json`, which is the catalogue the
wizard shows, at every certification level — a BETA connector is still
selectable, so it still has to be local. Reading the set from
`connector-lock.json` instead would skip `destination-google-sheets`, because a
lock is a guarantee and BETA carries none.

That derivation is held by `test_the_prepull_set_is_exactly_the_product_catalogue`.
It exists because the script previously kept its own hardcoded list of four
repositories while the product shipped eight, so BigQuery and Google Sheets
were silently never pre-pulled — each stalling its first sync inside a job,
where the timeout surfaces as `ENGINE_UNAVAILABLE` and reads like a broken
engine rather than a cold cache.

Given the engine's definition lists, it pulls the tags the engine will *really*
start, still filtered to the product's repositories, and says so when a tag
disagrees with the catalogue — which is what a failed pin looks like:

```bash
python scripts/pull-engine-images.py sources.json destinations.json
```

This is deliberately not a container in the stack. It would add one back to
save a wait that happens once per machine, and it would pull on every `up`.

## Starting from nothing

Wiping the volumes and starting over found three defects that a restart never
could. Each only bites while Postgres is running the scripts in
`/docker-entrypoint-initdb.d`, and that happens exactly once per data
directory — so a stack restarted a hundred times can still be unable to start
from zero.

**The Postgres health check was probing the wrong socket.** `pg_isready` with
no host talks over the unix socket, and the official image keeps a temporary
server listening there *while it initialises*. So the probe went green in the
middle of initdb, `service_healthy` fired, and everything downstream started
against a database answering `the database system is starting up`.

Temporal is what died of it. It creates its own two databases on first boot;
the `create` failed, then `setup-schema`, and the Airbyte server exited 1 on a
schema version check — an error naming neither Postgres nor initialisation.
The fix is one flag: `pg_isready -h 127.0.0.1`, which probes TCP, and TCP is
not listening until init has finished.

**The Airbyte server had no dependency on Temporal at all.** It opens a
Temporal client while wiring its beans and exits if it cannot. It had worked
for months on luck: the `storage-init → minio` chain delayed it long enough.
Removing MinIO removed the delay, and the next cold start failed with
`UnknownHostException: airbyte-temporal`, which reads like a network fault
rather than a missing edge in the dependency graph.

**`service_started` was not readiness either.** Temporal listens on 7233 before
it has created its schema, so the wait has to be a health check —
`tctl cluster health`, against `$(hostname -i)` rather than `127.0.0.1`,
because the entrypoint binds Temporal to the container address and a loopback
probe is refused by a perfectly healthy server.

All three are held by tests over the Compose files
(`test_the_postgres_healthcheck_probes_over_tcp`,
`test_the_engine_waits_for_temporal_rather_than_for_luck`), each checked
against its own bug.

```
docker compose down -v && docker compose up -d     8/8 healthy in 4m31s
golden path on the fresh stack                     2,500 records
restart with volumes intact                        27s, 8/8 healthy
```

## What it costs on disk

Measured on a machine where the project had just been rebuilt from nothing.

| | On disk | Download |
|---|---|---|
| Airbyte engine (5 images) | 6.99 GB | 2.26 GB |
| Connectors (8 images) | 8.42 GB | 2.56 GB |
| postgres · nginx · python | 0.64 GB | 0.16 GB |
| Built here (backend, frontend) | 0.74 GB | — |
| Volumes after a first sync | 0.12 GB | — |
| **Total** | **~16.9 GB** | **~5.0 GB** |

The two columns differ because images are stored uncompressed and pulled
compressed; the on-disk figure is also a sum that does not subtract layers the
Airbyte images share, so the real directory is smaller than 16.9 GB.

Idle memory is about 2.1 GB across the eight containers, of which Airbyte's
server and worker are 1.5 GB.

`vendor/engine/` is a further 2.3 GB on disk if you keep the offline archive.
It is not needed to run — only to rebuild the engine when Docker Hub is
unreachable.

## Trimming the stack

The stock Airbyte compose runs ten containers. This one runs eight, and the
three that came out were each removed against evidence rather than a guess.

| Removed | Why |
|---|---|
| `airbyte-cron` | 513 MB. Its only scheduled job is `WorkspaceCleaner`, and that job fails on every tick here — `/tmp/workspace` is not mounted into it. It also runs the definitions updater, which fights `airbyte-connector-pin` for control of connector versions. It was costing half a gigabyte to do nothing and undo something. |
| `airbyte-minio` | 124 MB. Object storage exists in the stock compose so server and worker can read the same job logs. Both already mount the `airbyte_workspace` volume, so they already share a filesystem and `STORAGE_TYPE: LOCAL` writes somewhere both can see. |
| `airbyte-storage-init` | Existed only to create the MinIO bucket. |

Verified after each removal by the full golden path — 2,500 records, an
incremental second sync, a cancel reaching terminal `CANCELLED`, and the log
tail, which is the step that would break first if log storage were wrong.

A deployment that puts server and worker on different hosts loses the shared
volume and needs real object storage back: set `STORAGE_TYPE: S3` and point the
bucket and credential variables at it. Nothing else changes.

## Two places that both thought they owned the connector version

Worth writing down, because the symptom pointed at the wrong subsystem for a
while: a sync kept dying on `getGenerationId(...) must not be null` — the
signature of a destination too new for this platform — minutes after
`airbyte-connector-pin` had reported pinning `destination-postgres` to 2.0.10.
The pin was real. Something was undoing it.

It was the product. `connector_definitions` carries two version columns on
purpose: `version` is the tag the product pins, `engine_version` is the tag the
engine was last seen offering. `seed_catalog` refreshed `version` only inside
its `spec_source == "BUNDLED"` branch, so as soon as a row's spec had been read
back from the engine — which flips `spec_source` to `ENGINE` — the bundled pin
stopped applying to that row permanently.

The engine's bootloader re-seeds from Airbyte's current catalogue on every
start. That drift got copied into the product database once and became the
product's own answer, and `_ensure_definition_version` then pushed it back into
the engine on every resource creation, a few minutes after the pin service ran.

A version pin is not part of a spec. `existing.version` is now written
unconditionally; the observation stays in `engine_version`, where an operator
can see the gap. Held by
`test_an_engine_sourced_spec_does_not_freeze_the_version_pin`, which was
checked against the bug — it reports 3.0.17 when the fix is reverted.
