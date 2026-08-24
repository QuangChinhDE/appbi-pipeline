# Runbook — connector egress

## The thing to be clear about first

The product's preflight (`backend/app/core/egress.py`) refuses private and
link-local targets before a connector is saved. That is a **guardrail**, not a
boundary. It runs in the product's process, and in `AIRBYTE_API` mode the
product is not the process that makes the request — Airbyte's worker starts the
connector container, and no code in this repository runs inside it. Redirects,
retries, and a hostname that resolves differently at request time all happen
past that check.

What actually constrains a connector is the network it runs on.

## Measured, not assumed

```bash
python scripts/verify-egress.py
```

It opens sockets from a container on `appbi-pipeline_connectors` — the same
network Airbyte's worker starts connectors on — and reports what answers.
Non-zero exit means something reachable should not have been, so it works as a
CI gate and as a post-deploy check.

Result on the default profile (Airbyte 0.59.1 Compose staging, 2026-08-23):

| Target | Result | |
|---|---|---|
| `appbi-pipeline-api:8000` | blocked | the product's own API |
| `appbi-pipeline-redis:6379` | blocked | the job queue |
| `appbi-airbyte-server:8001` | blocked | the engine's control API |
| `appbi-pipeline-postgres:5432` | **reachable** | intended: the databases connectors read and write |
| `1.1.1.1:443` | **reachable** | intended: SaaS connectors need outbound |
| `169.254.169.254:80` | blocked | cloud metadata — the classic SSRF target |

The metadata endpoint being unreachable is the one worth noting. It is what an
SSRF against a connector would go for first, and Docker's bridge does not route
link-local addresses.

## Profiles

### Default — outbound allowed

`docker-compose.airbyte.yml`. Connectors reach the data plane and the internet,
and nothing on the control plane. This is what SaaS connectors need.

Restricting *which* external hosts may be called is not expressible here. See
"Allowlisting" below.

### Hardened — no outbound at all

```bash
docker compose -f docker-compose.yml \
               -f docker-compose.airbyte.yml \
               -f docker-compose.egress.yml up -d

python scripts/verify-egress.py --expect-internet-blocked
```

`docker-compose.egress.yml` marks the `connectors` network `internal`, which
removes its route off the bridge. A connector can then open a socket to what is
attached to that network and to nothing else.

Suits a deployment whose sources are internal databases. **SaaS connectors stop
working** under it — that is the trade, and it is fail-closed rather than
fail-open.

Switching profiles recreates the network, so containers attached to it must be
stopped first (`docker compose ... down`, then up with the overlay).

## Why there is no forward proxy here

The obvious middle ground — a proxy on the connector network with an allowlist,
and `HTTP_PROXY` injected into connector containers — does not work on this
Airbyte.

Airbyte 0.59.1 has no supported mechanism for passing arbitrary environment
variables into job containers. Verified against the shipped jars rather than
the documentation: no `JOB_DEFAULT_ENV` machinery exists in
`io.airbyte.airbyte-commons-worker-0.59.1.jar` or the config models. Later
Airbyte lines add job env configuration; if the deployment moves to one, a
proxy profile becomes possible and should be added here.

A transparent proxy would need iptables redirection inside each connector
container, which means modifying images the product does not own.

## Allowlisting — where it actually belongs

An allowlist is a host firewall or egress gateway concern.

### Single host, Docker

Docker inserts its own iptables rules and overwrites `FORWARD`. The chain
intended for operator rules is `DOCKER-USER`, which Docker leaves alone.

Find the connector network's subnet:

```bash
docker network inspect appbi-pipeline_connectors \
  --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'
```

Then, with `172.20.0.0/16` standing in for whatever that printed:

```bash
# Allow the deployment's own networks and established replies.
iptables -I DOCKER-USER -s 172.20.0.0/16 -m conntrack \
         --ctstate ESTABLISHED,RELATED -j ACCEPT

# Allow named destinations. Resolve to addresses deliberately: a hostname rule
# is evaluated once, at insertion, and a rule that silently stops matching when
# a CDN rotates is worse than no rule.
iptables -I DOCKER-USER -s 172.20.0.0/16 -d 203.0.113.10 -j ACCEPT

# Refuse the rest, and reject rather than drop so a connector fails fast with a
# usable error instead of hanging until its timeout.
iptables -A DOCKER-USER -s 172.20.0.0/16 -j REJECT --reject-with icmp-admin-prohibited
```

Rules in `DOCKER-USER` are not persistent. Install them with
`iptables-persistent`, a systemd unit, or the host's configuration management —
a firewall that disappears on reboot is one people stop believing in.

Re-run `python scripts/verify-egress.py` afterwards. Adding a rule and not
measuring it is how allowlists come to exist only on paper.

### Kubernetes — the product's own policies, measured

`deploy/kubernetes/base/networkpolicy.yaml`, exercised on kind + **Calico**
(2026-08-23). Calico matters: kind's default CNI accepts NetworkPolicy objects
and does not enforce them, so a green apply against it proves the YAML parses
and nothing else.

From a pod labelled `app.kubernetes.io/name=appbi-api`:

| Target | Result | |
|---|---|---|
| DNS (`kube-dns`) | **works** | the rule the render bug had silently broken |
| Database on an allowed CIDR | **reachable** | the allow-path, proving the policy is not simply dropping everything |
| Internet (`1.1.1.1:443`) | blocked | the product calls nothing outbound |
| Cloud metadata | blocked | |
| Redis, when not in the allowed CIDR | blocked | |

Two things that test taught, both worth knowing before someone repeats it:

**A `commonLabels` transformer had rewritten the kube-dns selector.** Kustomize's
`commonLabels` applies to selectors as well as metadata, so the DNS rule pointed
at pods carrying `app.kubernetes.io/part-of: appbi-integration` — which kube-dns
does not. On kind's non-enforcing CNI everything looked fine. Under Calico, DNS
would have failed and every other rule would have looked like the destination
was down. Fixed with `labels: [{includeSelectors: false}]`, and there is now a
test on the *rendered* output rather than the source files.

**`appbi-default-deny` selects every pod in the namespace**, so a Postgres run
*inside* `appbi` gets no ingress and is unreachable even when its address is in
the allowed egress CIDR. That is correct for the intended shape — production
uses managed Postgres and Redis outside the cluster — but it will surprise
anyone who runs the database in-cluster for a test. Either put it in another
namespace, as the verified run did, or add an explicit ingress rule for it.

### Connectors on Kubernetes

Everything above constrains the **product**. Connector pods run in Airbyte's
namespace, launched by Airbyte's workload launcher, and the policy that
constrains them belongs there:

```bash
# Apply the overlay, never the base: the base carries a deliberately wrong
# data-plane CIDR so an unedited copy fails closed.
kubectl -n <airbyte-namespace> apply -k deploy/kubernetes/airbyte/overlays/production
```

It has its own `base/` and `overlays/`, separate from the product's, on
purpose: those ship with the product, and this does not — whoever operates the
Airbyte release applies it. The release gate renders **both** overlays and
refuses to record a certification while either still carries a repository
placeholder. It used to check only the product's, which is how an egress rule
that still allowed `10.0.0.0/24` would have passed as configured.

**Applied on a live Airbyte 1.8.5 cluster (2026-08-23)** while syncs were
actually running. The check that matters most is the selector:

| | |
|---|---|
| `airbyte=job-pod` matches | 9 connector pods |
| ...and control-plane pods | **0** |

Both halves of that are load-bearing. A NetworkPolicy that matches no pods is
the classic silent failure — it applies cleanly, appears in `get networkpolicy`,
and constrains nothing. And one that over-matches would strangle Airbyte's own
server and worker, which does not read as a policy problem; it reads as Airbyte
being broken.

The rule people leave out: **a connector pod must be able to reach the workload
API.** Without it the job does not fail — it hangs until the attempt times out,
which is a much worse way to find out.

#### Enforced, and measured against a control

kindnet accepts these objects and ignores them, so the applied-on-a-live-cluster
run above proves the selector and nothing about the rules. A second cluster with
**Calico** proves the rules. Both probes ran in the same namespace, from the same
image, differing only in the label:

| | pod labelled `airbyte=job-pod` | control, unlabelled |
|---|---|---|
| DNS | resolves | resolves |
| Workload API (in-namespace) | reachable | reachable |
| Internet `:443` | reachable | reachable |
| Private `10.0.0.0/8` (kube API) | **blocked** | **reachable** |
| Cloud metadata `169.254.169.254` | blocked | blocked |

The row that proves anything is `10.0.0.0/8`. It is reachable from an unlabelled
pod and blocked from a connector pod, so the policy — not the cluster's
topology — is doing the blocking, and the RFC1918 carve-out inside the
`0.0.0.0/0` rule works: a connector may reach the internet without thereby
reaching the rest of the private network.

**The metadata row proves nothing here, and saying so matters.** It is blocked
for both pods because kind does not route link-local addresses at all — the same
thing that made the Docker measurement inconclusive. On a cloud node
`169.254.169.254` *is* routable, which is exactly where the carve-out earns its
place. Treat that row as "the rule is present and correct", not as "measured".

Per-hostname allowlisting is still not expressible: `ipBlock` takes addresses.
An egress gateway (Istio, Cilium) is the tool for that.

## Reviewing the rules

Egress rules go stale quietly: a connector is removed and its allowance stays,
or a vendor changes address and the rule keeps matching nothing.

At each release, run `python scripts/verify-egress.py` and check the allowlist
against the connectors actually in use:

```bash
docker exec appbi-pipeline-postgres psql -U appbi -d appbi_integration -c \
  "select connector_key, usage_count from connector_definitions
    where usage_count > 0 order by usage_count desc;"
```

An allowed destination that no live connector needs should be removed.
