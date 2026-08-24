# Kubernetes deployment

Plain manifests, applied with Kustomize (built into kubectl). No Helm: this
product has a handful of objects and a few things that vary between
environments, and a chart would add templating language to hide that.

```
base/                    the shape — placeholders where an environment must decide
overlays/production/     the environment — real CIDRs, real registry, real tags
airbyte/base             NOT the product: policy for Airbyte's own namespace
airbyte/overlays/        its environment values, gated like the product's
```

`airbyte/` is applied by whoever operates the Airbyte release, into Airbyte's
namespace, not with `kubectl apply -k` below. It is here because the product
has an opinion about what connectors may reach; it is separate because the
product does not deploy them.

Apply the **overlay**, never the base:

```bash
kubectl apply -k deploy/kubernetes/overlays/production
```

The base carries a deliberately wrong database CIDR (`10.0.0.0/24`) and no
registry, so applying it by mistake fails closed instead of opening egress
somewhere unintended. A test refuses to let that placeholder reach a rendered
overlay. Copy `overlays/production` per environment.

**Applied to a real cluster** (kind, Kubernetes 1.30, 2026-08-23) with
Postgres and Redis stood up alongside. Result:

```
appbi-migrate   Completed   0 restarts   both migrations ran from an empty database
appbi-api       2/2 Running 0 restarts   readiness passing
appbi-worker    1/1 Running 0 restarts
```

Endpoints, from inside a pod:

| | |
|---|---|
| `/healthz` | 200 |
| `/readyz` | **200** — database required, engine reported |
| `/readyz?deep=1` | **503** — engine required, and there was no Airbyte in that cluster |
| `/metrics` | `appbi_metrics_up 1.0` |

Those two rows are the readiness split doing its job: no Airbyte, and the API
pods stayed in the Service. Had readiness used `?deep=1`, both would have been
pulled out.

### NetworkPolicy, verified under an enforcing CNI

kind's default CNI accepts NetworkPolicy objects and **does not enforce them**,
so the first run proved nothing about the policies. A second cluster with
Calico did. From a pod labelled `app.kubernetes.io/name=appbi-api`:

| | |
|---|---|
| DNS | **works** |
| Database on an allowed CIDR | **reachable** |
| Internet | blocked |
| Cloud metadata | blocked |
| Anything not named in a rule | blocked |

That run found the P1 in this directory: `commonLabels` rewrites *selectors*
as well as metadata, and had added a product label to the kube-dns selector.
kube-dns carries no such label, so DNS would have been blocked and every other
rule would have looked like the destination was down. Fixed with
`labels: [{includeSelectors: false}]`; there is now a test on the rendered
output, because every source-level check was green while this was broken.

One thing to know before repeating the test: `appbi-default-deny` selects
*every* pod in the namespace, so a Postgres run inside `appbi` gets no ingress
and stays unreachable even with its address in the allowed CIDR. That is right
for the intended shape — managed Postgres outside the cluster — and it is why
the verified run put the database in another namespace.

**Airbyte on Kubernetes is now certified too**, separately: Airbyte 1.8.5 via
the official Helm chart, connectors running as pods, all eleven adapter
operations exercised end to end. See
[../../docs/RUNBOOK-engine-upgrade.md](../../docs/RUNBOOK-engine-upgrade.md)
and `compatibility.yaml` → `airbyte_api_certification_kubernetes`. The runs on
this page prove these manifests are correct; that one proves the integration.

Two defects the run found, both now fixed and both invisible to schema
validation:

- The init container used `bitnami/kubectl:1.30`, a tag that does not exist —
  every API pod sat in `ImagePullBackOff`. Replaced with a wait that uses this
  project's own image and asks the database directly, which also removed the
  ServiceAccount, Role and RoleBinding it needed.
- `imagePullPolicy` was unset, so it defaulted from the tag name. Now explicit.

## What is here, and what is not

| | |
|---|---|
| **Here** | API deployment + service, worker deployment, migration job, config, NetworkPolicies, PodDisruptionBudget, ingress |
| **Not here** | Postgres, Airbyte |

Both are deliberately absent. Postgres should be a managed service in
production — running a database as a `Deployment` with an
`emptyDir` is the kind of thing that looks fine until the node reschedules.
Airbyte is its own deployment with its own Helm chart and its own lifecycle;
this product only needs to reach its API.

## Before applying

```bash
# 1. The namespace.
kubectl create namespace appbi

# 2. Secrets. Never in git — this is why there is no secret.yaml here.
kubectl -n appbi create secret generic appbi-secrets \
  --from-literal=SECRET_ENCRYPTION_KEY="$(python -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')" \
  --from-literal=JWT_SECRET="$(openssl rand -hex 32)" \
  --from-literal=DATABASE_URL='postgresql+asyncpg://user:pw@postgres.internal:5432/appbi' \
  --from-literal=DATABASE_URL_SYNC='postgresql+psycopg://user:pw@postgres.internal:5432/appbi' \
  --from-literal=AIRBYTE_WORKSPACE_ID='<from scripts/airbyte-workspace.py list>'

# 2b. The one-time bootstrap admin. A fresh production database has no account
#     at all — `SEED_DEMO_DATA=false` is honoured now, so nothing creates one.
#     The account these make must change its password before it can do
#     anything else, which is what stops the deployment secret from becoming a
#     standing credential.
kubectl -n appbi create secret generic appbi-bootstrap   --from-literal=BOOTSTRAP_ADMIN_EMAIL='ops@example.com'   --from-literal=BOOTSTRAP_ADMIN_PASSWORD="$(python -c 'import secrets;print(secrets.token_urlsafe(24))')"

# Delete it once someone has signed in and changed the password:
#   kubectl -n appbi delete secret appbi-bootstrap

# 3. Check the workspace id is the right one BEFORE anything runs. A valid
#    UUID from a different Airbyte passes every startup check and then creates
#    customer connections in someone else's tenant.
python scripts/airbyte-workspace.py --url https://airbyte.internal verify --id <uuid>

# 4. Point overlays/production at your Airbyte, subnet and registry.
kubectl apply -k deploy/kubernetes/overlays/production
```

`SECRET_ENCRYPTION_KEY` deserves a second look: generate it once, store it in
the same place as your database passwords, and keep it as long as you keep
backups. A restored dump without its key is a database of credentials nobody
can read — see [../../docs/RUNBOOK-backup-restore.md](../../docs/RUNBOOK-backup-restore.md).

## Order of operations

`migrate-job` runs `python -m app.bootstrap`, which brings the schema to head
and refuses to start on a database it cannot account for.

The API and worker each wait for it with an init container — Kubernetes has no
`dependsOn`, and starting against a half-migrated schema produces errors that
read like application bugs. The wait runs this project's own image and asks the
database whether `alembic_version` has a row, rather than watching the Job
through the API server: no external image, no RBAC, and it checks the thing
that actually matters instead of a proxy for it.

The worker needs this as much as the API does — without it, it crash-looped
three times waiting for the schema.

**Re-running `kubectl apply -k .` does NOT re-run the job.** This page used to
claim it did, and that was wrong twice over: a completed Job is not restarted
by an apply, and a Job's pod template is immutable, so applying a new image
over a finished one is rejected. The visible result of both is a deploy that
looks clean while the schema stays where it was.

Use the orchestrator, which deletes the Job, applies it on its own, and waits
for it to complete before rolling out anything that reads the schema:

```bash
python scripts/production.py install --config deploy/production.yaml
```

Applying by hand means doing that yourself:

```bash
kubectl -n appbi delete job appbi-migrate --ignore-not-found
kubectl -n appbi apply -k deploy/kubernetes/overlays/production
kubectl -n appbi wait --for=condition=complete job/appbi-migrate --timeout=10m
```

The init containers are the backstop: they compare the database's Alembic
revision with the head compiled into the image and refuse to start until they
match. They used to check only that `alembic_version` had a row, which any
older schema satisfies immediately.

## Health probes, and the one that matters

| Probe | Path | Why this one |
|---|---|---|
| liveness | `/healthz` | Is the process alive. Nothing else. |
| readiness | `/readyz` | Can it serve traffic. Database required; **engine reported but not required**. |

**Do not point readiness at `/readyz?deep=1`.** That variant fails when Airbyte
is unreachable, which would remove every API pod from the Service during an
engine outage — nobody could then read run history, see why the engine is down,
or acknowledge the alert. A partial outage would become a total one.

Use `?deep=1` for a post-deploy smoke check and for the release gate, where
"is the whole chain healthy" is the actual question.

## Egress

`networkpolicy.yaml` restricts what the product's pods may reach: DNS, the
database, Redis, and the Airbyte API. Nothing else.

That covers the **product**. It does not cover connectors — those run in
Airbyte's namespace, launched by Airbyte's workers, and the policy that
constrains them belongs there. See
[../../docs/RUNBOOK-egress.md](../../docs/RUNBOOK-egress.md); the measured
Compose equivalent is in that file. The product Kubernetes policy has been
run under Calico; the connector/Airbyte job-namespace Kubernetes policy is
still open because it has not been exercised.

## After applying

```bash
kubectl -n appbi rollout status deploy/appbi-api deploy/appbi-worker
kubectl -n appbi exec deploy/appbi-api -- \
  python -c "import urllib.request,json;print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/readyz?deep=1')))"
```

Then the certification run, which is the same one Airbyte went through:
[../../docs/RUNBOOK-engine-upgrade.md](../../docs/RUNBOOK-engine-upgrade.md).
