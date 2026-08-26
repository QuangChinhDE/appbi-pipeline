#!/usr/bin/env python3
"""Record what a release was actually certified against, and refuse stale proof.

A nightly job that goes green is not a release gate. By the time anyone ships,
the green run may be a week old, from a different commit, against a different
Airbyte. This turns the run into an artifact — platform version, connector
versions, job ids, row counts, commit — and then checks that artifact before a
release is allowed.

    python scripts/release-gate.py record --out certification.json
    python scripts/release-gate.py check certification.json

`record` runs against a live deployment and writes what it found.
`check` reads the file and fails if the evidence is missing, stale, or from
different code. Neither invents anything: `check` only reads what `record`
observed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import force_utf8  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# How old certification may be before it stops counting as evidence. A week
# covers a normal release cadence; anything older has probably drifted from the
# deployment it claims to describe.
MAX_AGE = timedelta(days=7)

def required_operations() -> list[str]:
    """The operations the product claims are certified, read from the claim itself.

    Derived rather than duplicated. A second hand-kept list drifts, and it
    drifted: the gate required nine operations while `compatibility.yaml`
    claimed eleven, so the two Connector Builder operations were being asserted
    to the world and never gated on.

    Reading the claim also gets the direction right — the gate's job is "prove
    everything you claim", so narrowing the claim narrows what must be proven,
    and that narrowing is visible in review of the file that makes the claim.
    """
    import yaml  # not at import time: `check` should work without it installed

    path = ROOT / "compatibility.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))

    # The union across every certification block, not just the first. There is
    # one per platform line now (Compose and Kubernetes), and reading a single
    # block would let an operation claimed only on the other platform be
    # asserted to the world and gated on nothing.
    verified: dict[str, bool] = {}
    for key, block in document.items():
        if key.startswith("airbyte_api_certification") and isinstance(block, dict):
            verified.update(block.get("verified") or {})
    if not verified:
        raise SystemExit(
            f"{path} declares no `airbyte_api_certification*.verified` block, so "
            "there is nothing to gate on. A release gate with no required "
            "operations is not a gate.")
    return sorted(verified)


def load_evidence(paths: list[str]) -> tuple[dict[str, bool], list[str]]:
    """Which operations were actually exercised, according to the runs.

    Each verifier writes a small JSON file naming what it did. The gate reads
    those instead of trusting a flag: `--verified` used to default to the full
    list, which meant an artifact recorded after a couple of successful syncs
    asserted that cancel and the Connector Builder had been tested too. An
    assertion the operator did not make and the run did not support is exactly
    what a gate exists to catch.
    """
    exercised: dict[str, bool] = {}
    sources: list[str] = []
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            raise SystemExit(f"No evidence file at {path}. Produce one with "
                             "`python scripts/e2e.py --evidence <path> ...`")
        document = json.loads(path.read_text(encoding="utf-8"))
        for name, ok in (document.get("operations") or {}).items():
            # Any run that exercised it counts; none can un-exercise it.
            exercised[name] = exercised.get(name, False) or bool(ok)
        sources.append(f"{path.name} ({document.get('produced_by', 'unknown')})")
    return exercised, sources


def product(url: str, path: str, cookie: str | None) -> dict:
    request = urllib.request.Request(url.rstrip("/") + path)
    if cookie:
        request.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise SystemExit(
                f"The product API refused this session ({exc.code}). "
                "Log in and pass the cookie with --cookie or APPBI_COOKIE."
            )
        body = exc.read().decode(errors="replace")[:300]
        raise SystemExit(f"{path} answered {exc.code}: {body}")
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"Could not reach the product API at {url}: {exc.reason}. "
            "Is the stack up?  python scripts/stack.py status"
        )


def git(*args: str) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=ROOT,
                                capture_output=True, text=True, timeout=15)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        # Not every checkout is a git working tree — a release tarball is not.
        return ""



# Values a repository ships that an environment must replace before release.
# Each is deliberately wrong rather than plausible, so shipping one fails
# closed -- but "deliberately wrong" only helps if something checks.
PLACEHOLDERS = {
    "10.0.0.0/24": "the base's placeholder database subnet",
    "registry.internal/": "the example registry",
    "appbi.example.internal": "the example ingress host",
    "postgres.internal": "the example database host",
}


def check_deployment_placeholders(overlay: str, *, label: str = "") -> list[str]:
    """Refuse a release whose manifests still carry repository placeholders.

    `overlays/production` is a template until somebody edits it. It renders, it
    validates, and it would deploy -- pointing at a registry that does not
    exist and allowing a subnet that is not yours. The failure is not subtle
    once it happens; it is subtle right up until then.
    """
    result = subprocess.run(["kubectl", "kustomize", overlay],
                            capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        return [f"could not render {overlay}: {result.stderr.strip()[:200]}"]

    where = f" of {label}" if label else ""
    return [f"{value!r} ({description}) is still in the rendered manifests{where}"
            for value, description in PLACEHOLDERS.items()
            if value in result.stdout]


def check_evidence_binding(paths: list[str], live: dict, product_url: str,
                           cookie: str | None) -> list[str]:
    """Is this evidence about the deployment being released?

    Evidence v1 said which operations passed and nothing about what they passed
    against. So an artifact could bind a certification to a commit read from
    the release manager's working tree -- which is not the deployment, and on a
    production host there is no checkout at all.

    v2 records what the deployment said about itself, and this compares it with
    what the deployment says now. Four bindings, and each one closes a way for
    green evidence to describe something else:

    * build      -- the same image produced the evidence and is being released
    * engine     -- the same engine type and platform version
    * workspace  -- the same Airbyte tenant
    * run ids    -- the runs named in the evidence exist on this deployment

    The last is the one that cannot be forged by copying a file around.
    """
    problems: list[str] = []
    live_build = (live.get("build") or {}).get("sha") or ""
    live_engine = live.get("engine") or {}

    for raw in paths:
        document = json.loads(Path(raw).read_text(encoding="utf-8"))
        name = Path(raw).name

        if int(document.get("schema", 1)) < 2:
            problems.append(
                f"{name} is evidence v1, which records no deployment identity. "
                "Re-run scripts/e2e.py to produce v2.")
            continue

        deployment = document.get("deployment") or {}
        build = (deployment.get("build") or {}).get("sha") or ""
        if not build or build == "unknown":
            problems.append(
                f"{name} was produced against a build with no identity "
                "(BUILD_SHA=unknown). Release images must be built by the "
                "pipeline, which stamps it.")
        elif live_build and build != live_build:
            problems.append(
                f"{name} was produced against build {build[:12]}, but this "
                f"deployment is running {live_build[:12]}")

        engine = deployment.get("engine") or {}
        if engine.get("type") != live_engine.get("type"):
            problems.append(
                f"{name} was produced against engine {engine.get('type')}, "
                f"this deployment runs {live_engine.get('type')}")
        if engine.get("version") and live_engine.get("version") \
                and engine["version"] != live_engine["version"]:
            problems.append(
                f"{name} was produced against engine version "
                f"{engine['version']}, this deployment runs "
                f"{live_engine['version']}")

        theirs = deployment.get("workspace_fingerprint")
        ours = live.get("workspace_fingerprint")
        if theirs and ours and theirs != ours:
            problems.append(
                f"{name} was produced against workspace {theirs}, this "
                f"deployment uses {ours} -- a valid run in the wrong tenant")

        run_ids = document.get("run_ids") or []
        if not run_ids:
            problems.append(f"{name} names no run ids, so nothing ties it to "
                            "work this deployment actually did")
        else:
            missing = [run_id for run_id in run_ids
                       if not run_exists(product_url, run_id, cookie)]
            if missing:
                problems.append(
                    f"{name} names {len(missing)} run id(s) this deployment "
                    f"does not have: {', '.join(m[:8] for m in missing[:3])}")

    return problems


def run_exists(product_url: str, run_id: str, cookie: str | None) -> bool:
    request = urllib.request.Request(f"{product_url.rstrip('/')}/api/v1/runs/{run_id}")
    if cookie:
        request.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status == 200
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        # Anything else is the gate failing to check, not the run being absent.
        # Say so by treating it as present: a broken check must not silently
        # become a passing one, and the caller reports the HTTP problem itself.
        raise SystemExit(
            f"could not verify run {run_id} against {product_url}: HTTP {exc.code}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"could not reach {product_url} to verify run ids: {exc.reason}")

def check_release_gates() -> list[str]:
    """Every gate `compatibility.yaml` declares, not just the ones about code.

    The gate used to read `airbyte_api_certification` and nothing else, so
    `LIC-001: NOT_CLEARED` -- "Airbyte licensing approved for the intended
    delivery model" -- sat in the same file the gate was reading and blocked
    nothing. A release gate that ignores the legal gate is not gating the
    release; it is gating the tests.

    Fail-closed on anything that is not explicitly passing, including a status
    nobody has defined. An unrecognised status is a gate somebody added and
    nobody taught this to interpret, which is not a reason to let it through.
    """
    import yaml

    document = yaml.safe_load(
        (ROOT / "compatibility.yaml").read_text(encoding="utf-8"))
    gates = document.get("release_gates") or []
    if not gates:
        return ["compatibility.yaml declares no release_gates; there is "
                "nothing to check, which is not the same as everything passing"]

    passing = {"PASSING", "CLEARED", "PASSED", "NOT_APPLICABLE"}
    return [f"release gate {gate.get('id', '?')} is "
            f"{gate.get('status', 'undeclared')}: {gate.get('description', '')}"
            for gate in gates
            if str(gate.get("status", "")).upper() not in passing]


def check_oncall_assigned() -> list[str]:
    """Refuse a release with an unassigned rota.

    Not a code check, and that is the point: the runbook says exactly what to
    do when the engine goes down and cannot say who does it. That gap survives
    review indefinitely because nothing fails while it is open -- right up
    until the first page at 3am reaches nobody.
    """
    path = ROOT / "docs" / "RUNBOOK-oncall.md"
    if not path.exists():
        return ["docs/RUNBOOK-oncall.md is missing"]
    unassigned = sum(1 for line in path.read_text(encoding="utf-8").splitlines()
                     if "TO BE ASSIGNED" in line and line.lstrip().startswith("|"))
    if unassigned:
        return [f"{unassigned} on-call role(s) still marked TO BE ASSIGNED in "
                "docs/RUNBOOK-oncall.md"]
    return []


def cmd_record(args: argparse.Namespace) -> int:
    """Ask the running deployment what it is, and write it down."""
    artifact: dict = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "commit": git("rev-parse", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
    }

    # 1. What engine is the product actually talking to?
    compatibility = product(args.product_url, "/api/v1/admin/compatibility", args.cookie)
    engine = compatibility.get("engine") or {}
    artifact["product_version"] = compatibility.get("product_version")
    artifact["engine"] = {
        "type": engine.get("type"),
        "platform_version": engine.get("version"),
        "adapter_contract_version": engine.get("adapter_contract_version"),
        "reachable": engine.get("reachable"),
    }

    if engine.get("type") != "AIRBYTE_API":
        print(f"!! engine is {engine.get('type')}, not AIRBYTE_API. "
              "Certification against the embedded runner is not evidence for a "
              "production release.", file=sys.stderr)
        return 1

    # 2. Which connector versions will actually run? In API mode the engine
    #    chooses, so the product's lock file is not the answer — but neither is
    #    calling Airbyte from here. The product already asks the engine and
    #    reports both versions, and going around it would mean this gate
    #    certifies something the product itself does not know about. It also
    #    needs no route to an API that is deliberately not published.
    definitions: dict[str, dict] = {}
    for key, entry in (compatibility.get("connectors") or {}).items():
        engine_image = entry.get("engine_image")
        if not engine_image:
            continue  # never refreshed against the engine; nothing to certify
        definitions[key] = {
            "engine_image": engine_image,
            "bundled_image": entry.get("bundled_image"),
            "matches_bundled": entry.get("version_matches_engine"),
            "certification": entry.get("certification"),
        }
    artifact["engine"]["connector_versions"] = definitions

    # 3. The runs that prove it. Read from the product, not from Airbyte: what
    #    matters is what the product observed, since that is what users see.
    runs = product(args.product_url, "/api/v1/runs?page_size=25", args.cookie)
    evidence = []
    for run in runs.get("items", []):
        if run.get("status") not in {"SUCCEEDED", "CANCELLED"}:
            continue
        evidence.append({
            "run_id": run.get("id"),
            "status": run.get("status"),
            "records_synced": run.get("records_synced"),
            "bytes_synced": run.get("bytes_synced"),
            "started_at": run.get("started_at"),
        })
    artifact["runs"] = evidence[:10]

    # 4. Which operations were actually exercised. Read from evidence files the
    #    verifiers wrote, never inferred from the run list — nothing in a list
    #    of successful syncs says whether cancel was tested — and never taken
    #    on the operator's word, which is what made this fail open before.
    required = required_operations()
    exercised, sources = load_evidence(args.evidence)
    artifact["operations"] = {name: exercised.get(name, False) for name in required}
    artifact["evidence_sources"] = sources

    # 4. Is that evidence about *this* deployment? Recorded rather than
    #    assumed: the gate used to bind a certification to a commit from the
    #    release manager's checkout, which says nothing about what is running.
    artifact["evidence_binding"] = check_evidence_binding(
        args.evidence, compatibility, args.product_url, args.cookie)
    artifact["build"] = compatibility.get("build") or {}
    artifact["workspace_fingerprint"] = compatibility.get("workspace_fingerprint")

    unexpected = sorted(set(exercised) - set(required))
    if unexpected:
        # Not fatal: a verifier may cover more than the claim does. Worth
        # printing, because it usually means the claim is out of date.
        print(f"  evidence covers operations not claimed: {', '.join(unexpected)}")

    # 5. Whether anyone is on the other end of the alerts.
    artifact["oncall_gaps"] = check_oncall_assigned()

    # 6. Every gate the compatibility matrix declares, legal included.
    artifact["release_gate_failures"] = check_release_gates()

    # 7. Whether the manifests being released are configured or still a
    #    template. A gate that certifies the code and ignores what it is
    #    deployed with certifies half the release.
    # Both overlays. The gate used to render only the product's, so the
    # connector egress policy -- the one that decides what a connector may
    # reach on the network -- could ship with the repository's placeholder
    # subnet and the gate would say the release was configured.
    if args.overlay:
        artifact["overlay"] = args.overlay
        artifact["engine_policy_overlay"] = args.engine_policy_overlay
        problems_found = check_deployment_placeholders(args.overlay, label="the product")
        if args.engine_policy_overlay:
            problems_found += check_deployment_placeholders(
                args.engine_policy_overlay, label="the connector policy")
        artifact["deployment_placeholders"] = problems_found

    output = Path(args.out)
    output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {output}")
    print(f"  engine        : {artifact['engine']['type']} "
          f"{artifact['engine']['platform_version']}")
    print(f"  connectors    : {definitions or '(none matched)'}")
    print(f"  runs recorded : {len(evidence)}")
    missing = [name for name, ok in artifact["operations"].items() if not ok]
    if missing:
        print(f"  NOT verified  : {', '.join(missing)}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Is this artifact good enough to release on?"""
    path = Path(args.artifact)
    if not path.exists():
        print(f"No certification artifact at {path}. Run `record` against a live "
              "Airbyte deployment first.", file=sys.stderr)
        return 1

    artifact = json.loads(path.read_text(encoding="utf-8"))
    problems: list[str] = []

    recorded = datetime.fromisoformat(artifact["recorded_at"])
    age = datetime.now(timezone.utc) - recorded
    if age > MAX_AGE:
        problems.append(f"certification is {age.days} days old (limit {MAX_AGE.days}); "
                        "re-run it against the deployment being released")

    engine = artifact.get("engine") or {}
    if engine.get("type") != "AIRBYTE_API":
        problems.append(f"certified against {engine.get('type')}, not AIRBYTE_API")
    if not engine.get("platform_version"):
        problems.append("no Airbyte platform version recorded")
    if not engine.get("reachable"):
        problems.append("the engine was not reachable when this was recorded")

    if not artifact.get("commit"):
        # A gate that accepts "unknown code was certified" is not a gate. This
        # happens when `record` ran outside a git working tree.
        problems.append("no commit recorded; the certified code is not identifiable")
    if artifact.get("dirty"):
        problems.append("recorded from a dirty working tree; the code that was "
                        "certified is not identifiable")
    if args.commit and artifact.get("commit") != args.commit:
        problems.append(f"certified commit {artifact.get('commit', '?')[:12]} is not "
                        f"the one being released ({args.commit[:12]})")

    operations = artifact.get("operations") or {}
    if not artifact.get("evidence_sources"):
        problems.append("no evidence sources recorded; this artifact predates "
                        "evidence-based recording and cannot be trusted")
    missing = [name for name in required_operations() if not operations.get(name)]
    if missing:
        problems.append("operations not verified: " + ", ".join(missing))

    binding = artifact.get("evidence_binding")
    if binding:
        problems.extend(binding)
    elif binding is None:
        problems.append("no evidence binding recorded; this artifact predates "
                        "evidence v2 and cannot show its evidence describes "
                        "the deployment being released")

    build = (artifact.get("build") or {}).get("sha") or ""
    if not build or build == "unknown":
        problems.append("the deployment reports no build identity "
                        "(BUILD_SHA=unknown); a release image must be built by "
                        "the pipeline, which stamps it")

    gates = artifact.get("release_gate_failures")
    if gates:
        problems.extend(gates)
    elif gates is None:
        problems.append("no release_gates check recorded; this artifact "
                        "predates the legal gate and cannot show LIC-001 or "
                        "any other declared gate was cleared")

    oncall = artifact.get("oncall_gaps")
    if oncall:
        problems.append("; ".join(oncall))
    elif oncall is None:
        problems.append("no on-call check recorded; this artifact predates the "
                        "ownership gate and cannot show a rota exists")

    placeholders = artifact.get("deployment_placeholders")
    if placeholders:
        problems.append("the manifests still carry repository placeholders: "
                        + "; ".join(placeholders))
    elif placeholders is None:
        problems.append("no deployment placeholder check recorded; re-record "
                        "with --overlay so the manifests being released are "
                        "known to be configured rather than a template")

    if not artifact.get("runs"):
        problems.append("no runs recorded; a certification with no sync in it is "
                        "an assertion, not evidence")

    print(f"certification recorded {recorded.isoformat()} "
          f"({age.days}d ago), commit {artifact.get('commit', '?')[:12]}")
    print(f"  engine: {engine.get('type')} {engine.get('platform_version')}")
    print(f"  connectors: {engine.get('connector_versions')}")
    print(f"  runs: {len(artifact.get('runs') or [])}")

    if problems:
        print("\nRELEASE BLOCKED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("\nrelease gate: PASS")
    return 0


def main() -> int:
    force_utf8()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    record = sub.add_parser("record", help="capture what a live deployment is")
    record.add_argument("--out", default="certification.json")
    record.add_argument("--product-url", default="http://localhost:8010")
    record.add_argument("--cookie", default=os.getenv("APPBI_COOKIE"),
                        help="session cookie for the product API (or APPBI_COOKIE)")
    # Default empty: the engine runs in Compose beside the product now, so
    # there is no Airbyte namespace to apply a policy into. The flag stays for
    # a deployment that does run Airbyte on Kubernetes separately.
    record.add_argument("--engine-policy-overlay", default="",
                        help="Kustomize overlay for a policy applied in the "
                             "engine's namespace, if the engine runs on "
                             "Kubernetes. Empty by default.")
    record.add_argument("--overlay", default="deploy/kubernetes/overlays/production",
                        help="Kustomize overlay whose rendered output must be "
                             "free of repository placeholders; pass '' to skip")
    record.add_argument("--evidence", nargs="+", required=True,
                        help="JSON evidence files written by the verifiers "
                             "(scripts/e2e.py --evidence ...). Required: an "
                             "artifact that asserts its own evidence is not one.")

    check = sub.add_parser("check", help="decide whether a release may proceed")
    check.add_argument("artifact", nargs="?", default="certification.json")
    check.add_argument("--commit", default=os.getenv("GITHUB_SHA", ""),
                       help="the commit being released; must match the artifact")

    args = parser.parse_args()
    return cmd_record(args) if args.command == "record" else cmd_check(args)


if __name__ == "__main__":
    raise SystemExit(main())
