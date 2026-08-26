# Runbook — certifying a different Airbyte

## What "certified" has to mean

The product is certified against Airbyte **0.59.1 on Docker Compose**. That is
recorded in `compatibility.yaml` under `airbyte_api_certification`, with the
eleven operations that were actually exercised and the connector versions that
actually ran.

Production on Kubernetes will not be that version. 0.59.x is the last Airbyte
line that shipped a Compose distribution; a current install is 1.x or 2.x via
`abctl` or the Helm chart. **Certification does not transfer.** The Config API
the adapter speaks is stable across those lines, which makes the upgrade
likely to work — likely is not certified.

This runbook is how to find out cheaply and then prove it.

## Step 1 — does the API surface still exist? (minutes)

```bash
python qa/probes/verify-engine-api.py --url https://airbyte.internal.example
```

It reads the 24 endpoints out of
`backend/app/adapters/airbyte_api/adapter.py` — not a hand-kept list, which
would drift silently — and probes each one. `404` means gone; `400/422/500`
means present and unhappy with an empty body, which is the expected answer.

Exit 1 lists exactly what is missing and where the adapter calls it. If
anything is missing, stop: the adapter needs work before the rest is worth
running.

Result on the certified deployment (0.59.1): 24/24 present.

## Step 2 — do the semantics still hold? (an hour)

Endpoint existence proves nothing about behaviour. The contract suite is what
proves behaviour:

```bash
docker compose ... run --rm --entrypoint "" \
  -e RUN_ENGINE_CONTRACT=1 api \
  sh -c "pip install -q pytest pytest-asyncio && python -m pytest tests/test_adapter_contract.py -q"
```

Twelve live scenarios that are skipped without `RUN_ENGINE_CONTRACT=1`. A green
structural run with those skipped proves the interface, not the engine — the
distinction PM flagged and the reason this step is separate.

## Step 3 — end to end, and record it (an hour)

```bash
python qa/e2e/e2e.py --source postgres --engine airbyte-api \
  --evidence evidence-e2e.json
python qa/probes/verify-egress.py
python scripts/release-gate.py record --evidence evidence-e2e.json \
  --out certification.json
python scripts/release-gate.py check certification.json
```

`--engine airbyte-api` asserts the deployment identity before testing anything.
Without it a run passes against whatever happens to be up, which is how a lane
meant to certify Airbyte silently certifies the embedded runner instead.

`--evidence` is required by `release-gate.py record` and is not optional here:
the file is what the gate reads instead of trusting a flag. Omit it and
recording refuses, which is the intended behaviour rather than a bug.

The e2e includes a second sync that fails if an incremental stream re-reads
everything — the check that separates a working cursor from a full refresh
wearing its name.

## Step 4 — update the record

In `compatibility.yaml`:

- add the version to `tested_platform_versions`
- update `airbyte_api_certification` with the platform version, the date, and
  the eleven operations
- update `connector_versions_observed` — the new deployment pins its own, and
  these routinely differ from `connector-lock.json`

Anything not exercised stays `false`. A matrix that overstates is worse than
one that admits a gap, because nobody re-checks a `true`.

---

## Airbyte with auth enabled: Community API credentials

Chart V2 2.0.17 (Airbyte app 1.8.5) with `auth.enabled: true` is the target
topology. In Community SIMPLE auth, Airbyte exposes exactly one Application.
Its implementation backs that Application with `instance-admin-client-id` and
`instance-admin-client-secret`; creating or deleting another Application is
explicitly unsupported. No browser action is required.

Chart 2.0.17 generates both keys in `airbyte-auth-secrets`, but its server
template injects only the admin password. Without the post-install patch the
API lists a synthetic Application with empty credentials and every token
exchange fails.

> **The tooling for this was deleted.** `scripts/patch-airbyte-community-auth.py`
> and `scripts/airbyte-application.py` went with the Kubernetes deployment path
> — see [engine.md](engine.md). The finding is kept because it is a property of
> chart 2.0.17, not of this repo, and anyone pointing the product at a
> Community Airbyte will meet it again: wire `AB_INSTANCE_ADMIN_CLIENT_ID` and
> `AB_INSTANCE_ADMIN_CLIENT_SECRET` into the server from `airbyte-auth-secrets`
> yourself, then copy them into AppBI's `AIRBYTE_CLIENT_ID` /
> `AIRBYTE_CLIENT_SECRET` through the production secret manager.

The engine this product ships runs in Compose with no auth layer of its own and
is not published to the host, so none of this applies to the default topology.

## Kubernetes — exercised, and what it cost

Airbyte **1.8.5** was brought up on a kind cluster (Kubernetes 1.30.4) via the
official Helm chart and the product was pointed at it. What that found is
below; the reproduction is at the end.

### What certifying against 1.8.5 caught

**`/api/v1/workspaces/list` returns 404.** Present on 0.59.1, gone on 1.8.5.
Replaced by `/api/v1/workspaces/list_by_organization_id` (needs an
`organizationId`; community edition uses the all-zeroes default) and
`/api/v1/workspaces/list_paginated` (needs an explicit `pagination` block —
omitting it is a 500, not a default). The adapter now tries all three in order
and declares them as `ALTERNATIVE_ROUTE_GROUPS`, which
`qa/probes/verify-engine-api.py` reads so a 404 on one member is not reported as
a broken adapter.

This is the entire argument for endpoint probing. It cost minutes to find and
would have been a production incident on the first workspace resolution.

**Connector versions differ again.** 1.8.5 pins `source-postgres:3.6.35`,
`destination-postgres:2.4.5`, `source-faker:6.2.24` — none of which match
either the product's lock file or what 0.59.1 ran. `source-declarative-manifest`
is 7.28.2 on both, because that one is the product's choice rather than the
engine's.

**A cold cluster times out and it looks like an engine fault.** The first run
failed with `ENGINE_UNAVAILABLE / ReadTimeout` on the very first `check`. The
engine was healthy; the connector *pod* was still pulling a 500MB image.
Pre-pull before certifying:

```bash
python scripts/pull-engine-images.py sources.json destinations.json
```

(The `--into-kind` variant that pushed images onto a kind node was removed with
the Kubernetes path. On a cluster, pre-pull onto the nodes by whatever means
that cluster provides — the point is that it happens before certifying, not
which command does it.)

**A port-forward is not a network.** Probing through
`kubectl port-forward` reported half the endpoints as taking >15s. The same
endpoints answer in 10ms over a real path. `kubectl port-forward` carries one
connection and serialises behind it, so anything measured through it is
measuring the tunnel. Attach the node to the network the product is on instead.

### Reproducing it

Not reproducible from this repository any more. The harness it needed — the
kind cluster `appbi-base-cert`, `deploy/kubernetes/airbyte/values-certification*.yaml`,
`docker-compose.k8s-cert.yml` and the scripts above — was removed when the
engine moved into Compose ([engine.md](engine.md)). The findings above are kept
because they are the reason the adapter probes alternative routes at all; the
commands that produced them are not, because commands that cannot run are worse
than no commands.

## Kubernetes deployment notes

The rest of this section is written from the adapter's requirements. Where the
run above contradicts it, the run wins.

### What the product needs from the deployment

| | |
|---|---|
| Config API reachable | from the product's `api` and `worker` pods, **not** from the browser |
| Auth | none, or basic via `AIRBYTE_API_USERNAME` / `AIRBYTE_API_PASSWORD` |
| A workspace id | see [RUNBOOK-airbyte-workspace.md](RUNBOOK-airbyte-workspace.md) |
| Connector images | pullable by Airbyte's job runner, not by the product |
| Egress control | a `NetworkPolicy` on the job namespace — see [RUNBOOK-egress.md](RUNBOOK-egress.md) |

The product never needs the Docker socket, Airbyte's database, or the Airbyte
UI. If a deployment plan asks for any of those, something has been misread.

### Version pinning

```yaml
# values.yaml — pin, never a floating tag. An engine that upgrades itself
# changes connector versions under a product that has certified the old ones.
global:
  image:
    tag: "1.x.y"   # the exact version certified above
```

### What changes in 1.x that matters here

- **A second API.** 1.x adds a public API at `/api/public/v1` alongside the
  Config API at `/api/v1`. The adapter uses the Config API, which is still
  present. Migrating to the public API would be a deliberate piece of work with
  its own certification, not a side effect of upgrading.
- **The workload plane.** Connector jobs run through the workload launcher,
  which is why 0.64+ cannot run in Compose. On Kubernetes this is the normal
  path and needs nothing from the product.
- **Auth.** 1.x can require authentication where 0.59 did not. The adapter
  supports basic auth; anything token-based needs adapter work, and
  `verify-engine-api.py` will report 401 rather than 404 — present but refusing,
  which is a different problem with a different fix.

### Sizing

Certification ran on a single host with roughly 1.7GB for Airbyte's server and
worker. That is a floor for one concurrent sync, not a production figure. Size
against expected concurrency, and remember the product enforces its own limits
(`MAX_CONCURRENT_RUNS_*`) independently of Airbyte's.

## Rolling back

The product stores engine references in `engine_mappings`. Rolling Airbyte back
to a snapshot the product has moved past leaves rows pointing at resources that
no longer exist; the symptom is not-found on the next sync.

Roll back both together, or recreate the affected actors through the product so
it records fresh mappings. Details in
[RUNBOOK-backup-restore.md](RUNBOOK-backup-restore.md#after-a-restore--the-part-people-skip).
