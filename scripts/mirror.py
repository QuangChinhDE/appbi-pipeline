#!/usr/bin/env python3
"""Copy everything a pilot install needs into an internal registry, by digest.

    python scripts/mirror.py plan    --config deploy/production.yaml
    python scripts/mirror.py push    --config deploy/production.yaml
    python scripts/mirror.py verify  --config deploy/production.yaml
    python scripts/mirror.py lock    --config deploy/production.yaml

A running deployment survives losing GitHub or Docker Hub. A *fresh install*
and a *restore* do not: the Helm chart lives on GitHub Pages, the platform and
connector images live on public registries, and none of that is under anyone
here's control. Mirroring is what turns "we could rebuild this" into something
that has been demonstrated.

Scope is deliberate. The catalogue has 654 connectors and the pilot ships
three; mirroring all of them costs tens of gigabytes and widens the attack
surface for connectors nobody has certified. `plan` prints exactly what will be
copied and why, so the scope is reviewable before anything is pulled.

Digests, not tags. A tag is a pointer someone upstream can move, and a
certification recorded against a moved tag certifies nothing. `lock` writes
`mirror-lock.json` with the digest of every artefact, and `verify` re-resolves
the internal copies and fails if any of them has drifted.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import force_utf8  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "mirror-lock.json"


def load_config(path: Path) -> dict:
    import yaml

    if not path.exists():
        raise SystemExit(f"no config at {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def run(command: list[str], *, check: bool = True, quiet: bool = False):
    if not quiet:
        print(f"  $ {' '.join(command)}", flush=True)
    result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    if check and result.returncode != 0:
        raise SystemExit(f"`{' '.join(command)}` failed: "
                         f"{(result.stderr or result.stdout).strip()[:400]}")
    return result


def planned_artifacts(config: dict) -> dict[str, list[dict]]:
    """What the pilot needs, grouped by why it needs it.

    Read from the config and the compatibility matrix rather than hand-listed:
    a second list drifts, and the one that drifts is always the one nobody
    re-reads before a release.
    """
    import yaml

    product = config["product"]
    engine = config["engine"]
    matrix = yaml.safe_load((ROOT / "compatibility.yaml").read_text(encoding="utf-8"))

    registry = str(product["registry"]).rstrip("/")
    tag = str(product["tag"])
    groups: dict[str, list[dict]] = {
        "product": [
            {"source": f"{registry}/{product.get('image', 'backend')}:{tag}",
             "why": "the API, worker and migration Job all run this image"},
            {"source": f"{registry}/{product.get('frontend_image', 'frontend')}:{tag}",
             "why": "the UI"},
        ],
        "engine-platform": [],
        "connectors": [],
        "chart": [],
    }

    version = str(engine["platform_version"])
    for component in ("server", "worker", "workload-launcher", "workload-api-server",
                      "cron", "bootloader", "connector-sidecar",
                      "workload-init-container", "temporal", "db"):
        groups["engine-platform"].append({
            "source": f"airbyte/{component}:{version}",
            "why": f"Airbyte {version} control plane"})

    # Only the launch scope. A connector nobody certified is not something to
    # take a supply-chain dependency on.
    allowlist = (config.get("pilot") or {}).get("connectors") or []
    observed = {}
    for key, block in matrix.items():
        if key.startswith("airbyte_api_certification") and isinstance(block, dict):
            observed.update(block.get("connector_versions_observed") or {})
    for name in allowlist:
        seen = observed.get(name) or {}
        engine_ran = seen.get("engine_ran")
        if not engine_ran:
            groups["connectors"].append({
                "source": f"airbyte/{name}:UNKNOWN",
                "why": "IN LAUNCH SCOPE BUT NEVER CERTIFIED -- no observed version"})
            continue
        groups["connectors"].append({
            "source": f"airbyte/{name}:{engine_ran}",
            "why": f"launch scope; the version {version} actually ran"})

    chart = (engine.get("chart") or {})
    if chart.get("version"):
        groups["chart"].append({
            "source": f"airbyte/airbyte:{chart['version']}",
            "why": f"Helm chart V2 {chart['version']} for Airbyte app {version}"})

    return groups


def cmd_plan(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    groups = planned_artifacts(config)
    total = 0
    problems: list[str] = []
    for group, items in groups.items():
        print(f"\n{group}:")
        for item in items:
            print(f"  {item['source']}")
            print(f"      {item['why']}")
            if "NEVER CERTIFIED" in item["why"]:
                problems.append(item["source"])
            total += 1
    print(f"\n{total} artefact(s). Not the full catalogue: 654 connectors exist "
          "and the pilot ships the allowlist above.")
    if problems:
        print("\nRefusing to plan a mirror that includes uncertified connectors:",
              file=sys.stderr)
        for name in problems:
            print(f"  - {name}", file=sys.stderr)
        return 1
    return 0


def _digest(reference: str) -> str:
    """The image's digest, resolved from the registry rather than assumed."""
    result = run(["docker", "buildx", "imagetools", "inspect", reference,
                  "--format", "{{json .Manifest.Digest}}"], check=False, quiet=True)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().strip('"')
    # Fall back to a local inspect: a freshly built product image is not in any
    # registry yet, and a digest of nothing is worse than saying so.
    result = run(["docker", "image", "inspect", reference,
                  "--format", "{{index .RepoDigests 0}}"], check=False, quiet=True)
    if result.returncode == 0 and "@" in result.stdout:
        return result.stdout.strip().split("@", 1)[1]
    return ""


def cmd_push(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    internal = (config.get("mirror") or {}).get("registry")
    if not internal:
        raise SystemExit(
            "config has no `mirror.registry`. The pilot must install from an "
            "internal registry, not from Docker Hub.")

    groups = planned_artifacts(config)
    pushed: list[dict] = []
    for group, items in groups.items():
        for item in items:
            source = item["source"]
            if "UNKNOWN" in source:
                raise SystemExit(f"cannot mirror {source}: no certified version")
            target = f"{str(internal).rstrip('/')}/{source.split('/', 1)[-1]}"
            print(f"\n{group}: {source}")
            run(["docker", "pull", source])
            run(["docker", "tag", source, target])
            run(["docker", "push", target])
            pushed.append({"group": group, "source": source, "target": target,
                           "digest": _digest(target)})

    LOCK.write_text(json.dumps({"registry": internal, "artifacts": pushed},
                               indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {LOCK.name} with {len(pushed)} digest(s)")
    return 0


def cmd_lock(args: argparse.Namespace) -> int:
    """Record digests without pushing, for artefacts already mirrored."""
    config = load_config(Path(args.config))
    internal = (config.get("mirror") or {}).get("registry")
    if not internal:
        raise SystemExit("config has no `mirror.registry`")

    artifacts = []
    for group, items in planned_artifacts(config).items():
        for item in items:
            target = f"{str(internal).rstrip('/')}/{item['source'].split('/', 1)[-1]}"
            artifacts.append({"group": group, "source": item["source"],
                              "target": target, "digest": _digest(target)})
    LOCK.write_text(json.dumps({"registry": internal, "artifacts": artifacts},
                               indent=2) + "\n", encoding="utf-8")
    missing = [a["target"] for a in artifacts if not a["digest"]]
    print(f"wrote {LOCK.name} with {len(artifacts)} entries")
    if missing:
        print("\nno digest resolved for:", file=sys.stderr)
        for name in missing:
            print(f"  - {name}", file=sys.stderr)
        return 1
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Has anything in the internal registry moved since the lock was written?"""
    if not LOCK.exists():
        raise SystemExit(f"no {LOCK.name}; run `mirror.py push` or `lock` first")
    lock = json.loads(LOCK.read_text(encoding="utf-8"))

    drifted: list[str] = []
    absent: list[str] = []
    for entry in lock.get("artifacts", []):
        current = _digest(entry["target"])
        if not current:
            absent.append(entry["target"])
        elif current != entry["digest"]:
            drifted.append(f"{entry['target']}: locked {entry['digest'][:19]}, "
                           f"registry has {current[:19]}")

    print(f"checked {len(lock.get('artifacts', []))} artefact(s)")
    if absent:
        print("\nnot in the internal registry:", file=sys.stderr)
        for name in absent:
            print(f"  - {name}", file=sys.stderr)
    if drifted:
        print("\ndigest drift -- an internal tag was moved:", file=sys.stderr)
        for name in drifted:
            print(f"  - {name}", file=sys.stderr)
    if absent or drifted:
        return 1
    print("every artefact matches its locked digest")
    return 0


def main() -> int:
    force_utf8()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("plan", "what would be mirrored, and why"),
        ("push", "pull from upstream, retag, push internally, write the lock"),
        ("lock", "record digests of what is already mirrored"),
        ("verify", "re-resolve every locked digest"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--config", default=str(ROOT / "deploy" / "production.yaml"))

    args = parser.parse_args()
    return {"plan": cmd_plan, "push": cmd_push,
            "lock": cmd_lock, "verify": cmd_verify}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
