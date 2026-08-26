#!/usr/bin/env python3
"""Keep the engine on this machine, so upstream cannot take it away.

    python scripts/vendor-engine.py lock     # resolve every image to a digest
    python scripts/vendor-engine.py save     # write the bytes to vendor/engine/
    python scripts/vendor-engine.py restore  # load them on a new machine
    python scripts/vendor-engine.py verify   # is what is running what we pinned
    python scripts/vendor-engine.py connectors  # make the engine run our pins

Why
---

The product runs Airbyte 0.59.1 as its execution engine. That version is the
last one that runs a sync under Docker Compose: from the 0.63 line onward every
connector job is routed through the workload launcher, which resolves
`kubernetes.default.svc` and has no Docker mode. So the choice is 0.59.1 or
Kubernetes, and this project chose 0.59.1.

A version that old will not stay on Docker Hub forever. `docker pull` is not a
supply chain — it is somebody else's decision about what to keep. `save` writes
the actual image bytes into `vendor/engine/`, and `restore` loads them on a
machine that has never seen Airbyte. Copy the project directory, run restore,
and the engine works with the registry unreachable.

Digests, not tags
-----------------

`airbyte/server:0.59.1` is a mutable pointer. The lock records the digest that
was tested, and `verify` fails if the running image is not it — so an upstream
retag cannot silently become what production runs.

What is not here
----------------

The Java source. `vendor/engine/SOURCE.md` records the exact commit and how to
fetch it, because a fork is a decision to take deliberately and not a side
effect of running this script. The images are what the product needs to run;
the source is what it needs to modify.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import force_utf8  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "engine"
LOCK = ROOT / "engine-lock.json"

#: The Airbyte platform, pinned. `container-orchestrator` is not in the Compose
#: file and is required anyway: the worker spawns it per job, so a machine
#: without it starts cleanly and then fails on the first sync.
PLATFORM_VERSION = "0.59.1"
# Exactly the images the stack runs, and nothing else -- an archive that keeps
# images the Compose file no longer references is 640 MB of bytes nobody can
# explain a year later.
#
# `container-orchestrator` is the exception that has to stay: it appears in no
# Compose file because the worker starts it per job. Leaving it out gives a
# machine that comes up clean and fails on the first sync.
IMAGES = [
    f"airbyte/bootloader:{PLATFORM_VERSION}",
    f"airbyte/server:{PLATFORM_VERSION}",
    f"airbyte/worker:{PLATFORM_VERSION}",
    f"airbyte/temporal:{PLATFORM_VERSION}",
    f"airbyte/container-orchestrator:{PLATFORM_VERSION}",
]


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True,
                          timeout=kwargs.pop("timeout", 1800), **kwargs)


def digest_of(image: str) -> str | None:
    result = run(["docker", "image", "inspect", image,
                  "--format", "{{json .RepoDigests}}"])
    if result.returncode != 0:
        return None
    try:
        for entry in json.loads(result.stdout or "[]"):
            if "@" in entry:
                return entry.split("@", 1)[1]
    except ValueError:
        pass
    return None


def archive_for(image: str) -> Path:
    return VENDOR / (image.replace("/", "__").replace(":", "__") + ".tar")


def cmd_lock(_args) -> int:
    missing = []
    entries = []
    for image in IMAGES:
        digest = digest_of(image)
        if digest is None:
            missing.append(image)
            print(f"  MISSING {image}  (docker pull {image})")
            continue
        entries.append({"image": image, "digest": digest})
        print(f"  ok      {image:52} {digest[:23]}...")

    if missing:
        print(f"\n{len(missing)} image(s) not present locally; pull them first.",
              file=sys.stderr)
        return 1

    LOCK.write_text(json.dumps({
        "platform": "airbyte",
        "platform_version": PLATFORM_VERSION,
        "why_this_version": (
            "The last Airbyte that runs a sync under Docker Compose. From the "
            "0.63 line onward, connector jobs go through the workload "
            "launcher, which resolves kubernetes.default.svc and has no Docker "
            "mode."
        ),
        "locked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "images": entries,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {LOCK.relative_to(ROOT)} with {len(entries)} images")
    return 0


def cmd_save(_args) -> int:
    if not LOCK.exists():
        print("run `lock` first", file=sys.stderr)
        return 2
    VENDOR.mkdir(parents=True, exist_ok=True)
    lock = json.loads(LOCK.read_text(encoding="utf-8"))

    total = 0
    for entry in lock["images"]:
        image, target = entry["image"], archive_for(entry["image"])
        if target.exists() and target.stat().st_size > 0:
            total += target.stat().st_size
            print(f"  have  {image:52} {target.stat().st_size/1e9:5.2f} GB")
            continue
        print(f"  save  {image} ...", flush=True)
        # Streamed to the file rather than through a pipe: these are gigabytes,
        # and buffering them in memory is how a save fails on a small machine.
        with target.open("wb") as handle:
            result = subprocess.run(["docker", "save", image],
                                    stdout=handle, stderr=subprocess.PIPE,
                                    timeout=3600)
        if result.returncode != 0:
            target.unlink(missing_ok=True)
            print(f"  FAILED {image}: {result.stderr.decode()[:200]}", file=sys.stderr)
            return 1
        total += target.stat().st_size
        print(f"        {target.stat().st_size/1e9:5.2f} GB")

    _write_source_note()
    print(f"\n{total/1e9:.1f} GB in {VENDOR.relative_to(ROOT)}")
    print("Copy that directory with the project; `restore` loads it anywhere.")
    return 0


def cmd_restore(_args) -> int:
    if not LOCK.exists():
        print("no engine-lock.json", file=sys.stderr)
        return 2
    lock = json.loads(LOCK.read_text(encoding="utf-8"))

    loaded = present = 0
    for entry in lock["images"]:
        image = entry["image"]
        if digest_of(image) is not None:
            present += 1
            continue
        archive = archive_for(image)
        if not archive.exists():
            print(f"  MISSING {image} and no archive at "
                  f"{archive.relative_to(ROOT)}", file=sys.stderr)
            return 1
        print(f"  load  {image} ...", flush=True)
        with archive.open("rb") as handle:
            result = subprocess.run(["docker", "load"], stdin=handle,
                                    capture_output=True, timeout=3600)
        if result.returncode != 0:
            print(f"  FAILED {image}: {result.stderr.decode()[:200]}", file=sys.stderr)
            return 1
        loaded += 1

    print(f"\n{loaded} image(s) loaded, {present} already present")
    return cmd_verify(None)


def cmd_verify(_args) -> int:
    """Is what is on this machine what was pinned?

    A tag can be moved. This is the check that the bytes about to run a
    customer's sync are the bytes that were tested.
    """
    if not LOCK.exists():
        print("no engine-lock.json", file=sys.stderr)
        return 2
    lock = json.loads(LOCK.read_text(encoding="utf-8"))

    problems = []
    for entry in lock["images"]:
        image, expected = entry["image"], entry["digest"]
        actual = digest_of(image)
        if actual is None:
            problems.append(f"{image} is not on this machine")
            print(f"  MISSING  {image}")
        elif actual != expected:
            problems.append(f"{image} is {actual}, locked at {expected}")
            print(f"  DRIFTED  {image}")
        else:
            print(f"  ok       {image}")

    if problems:
        print(f"\n{len(problems)} problem(s):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print("\n`restore` loads the pinned bytes from vendor/engine/.",
              file=sys.stderr)
        return 1
    print(f"\nall {len(lock['images'])} images match the lock")
    return 0


def cmd_connectors(_args) -> int:
    """Make the engine run the connector versions this product pinned.

    Airbyte 0.59.1's bootloader seeds its connector definitions from Airbyte's
    *current* catalogue, not from a 2024 snapshot. So an old platform comes up
    offering `destination-postgres:3.0.17` — a connector built for the refresh
    protocol, which this platform does not implement. The sync then dies on the
    first record:

        BeanInstantiationException: PostgresWriter
        Caused by: NullPointerException: getGenerationId(...) must not be null

    Nothing warns about it. The definition looks healthy, `check` passes, and
    the failure arrives at replication time.

    `connector-lock.json` is the product's decision about which connector bytes
    run. This pushes that decision into the engine, so there is one answer to
    "what version is this" instead of two that disagree.
    """
    import urllib.error
    import urllib.request

    lock_path = ROOT / "connector-lock.json"
    if not lock_path.exists():
        print("no connector-lock.json", file=sys.stderr)
        return 2
    wanted = {
        entry["image"].split(":")[0]: entry["image"].split(":")[1]
        for entry in json.loads(lock_path.read_text(encoding="utf-8"))["connectors"]
    }

    base = "http://localhost:8001/api/v1"

    def api(path: str, payload: dict) -> dict:
        # Through the server container: the Config API is deliberately not
        # published to the host.
        result = run(["docker", "exec", "appbi-airbyte-server", "curl", "-fsS",
                      "-X", "POST", f"{base}{path}",
                      "-H", "Content-Type: application/json",
                      "-d", json.dumps(payload)])
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip()[:200] or "no response")
        return json.loads(result.stdout or "{}")

    changed = aligned = 0
    for side, listing, id_field in (
        ("SOURCE", "source_definitions", "sourceDefinitionId"),
        ("DESTINATION", "destination_definitions", "destinationDefinitionId"),
    ):
        try:
            data = api(f"/{listing}/list", {})
        except Exception as exc:                            # noqa: BLE001
            print(f"could not list {listing}: {exc}", file=sys.stderr)
            return 1
        collection = data.get(listing.replace("_d", "D").replace("_", ""), [])
        collection = data.get("sourceDefinitions" if side == "SOURCE"
                              else "destinationDefinitions", collection)
        for entry in collection:
            repository = entry.get("dockerRepository", "")
            target = wanted.get(repository)
            if not target:
                continue
            running = entry.get("dockerImageTag")
            if running == target:
                aligned += 1
                print(f"  ok      {repository:38} {running}")
                continue
            api(f"/{listing}/update", {id_field: entry[id_field],
                                       "dockerImageTag": target})
            changed += 1
            print(f"  pinned  {repository:38} {running} -> {target}")

    print(f"\n{changed} definition(s) changed, {aligned} already correct")
    if changed:
        print("The engine now runs the versions in connector-lock.json.")
    return 0


def _write_source_note() -> None:
    (VENDOR / "SOURCE.md").write_text(f"""# Airbyte {PLATFORM_VERSION} source

The images in this directory are the engine. This note is about the *source*,
which is a separate decision.

## Why this version

{PLATFORM_VERSION} is the last Airbyte that runs a sync under Docker Compose.
From the 0.63 line onward every connector job goes through the workload
launcher, which resolves `kubernetes.default.svc` and has no Docker mode —
verified against the real images, not from documentation:

| Version | In Compose |
|---|---|
| 1.8.5 | bootloader needs a Kubernetes namespace, twice; the second has no opt-out |
| 0.64.7 | control plane runs, every connector job fails in the workload launcher |
| {PLATFORM_VERSION} | predates the workload launcher; the worker starts connectors on the Docker daemon |

## Getting the source

```bash
git clone --depth 1 --branch v{PLATFORM_VERSION} \\
    https://github.com/airbytehq/airbyte.git vendor/engine/src
```

It is a Gradle monorepo in Java and Kotlin. Building the platform images:

```bash
cd vendor/engine/src
./gradlew :oss:airbyte-server:assemble
./gradlew :oss:airbyte-container-orchestrator:assemble
```

Expect a long first build and a large Gradle cache.

## Before forking

Airbyte is licensed **ELv2**. Running it is one thing; modifying and
distributing it inside a commercial product is a different question, and it is
the same question `LIC-001` in `compatibility.yaml` is already open on. Get that
answered before shipping a modified build to a customer.

A local patch that is *not* a fork — an environment variable, a config change,
a sidecar — avoids the question entirely and is worth trying first.
""", encoding="utf-8")


def main() -> int:
    force_utf8()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("lock", "resolve every image to the digest on this machine"),
        ("save", "write the image bytes into vendor/engine/"),
        ("restore", "load them on a machine that has never pulled them"),
        ("verify", "check the running images against the lock"),
        ("connectors", "pin the engine's connector definitions to our lock"),
    ):
        sub.add_parser(name, help=help_text)
    args = parser.parse_args()
    return {"lock": cmd_lock, "save": cmd_save, "restore": cmd_restore,
            "verify": cmd_verify, "connectors": cmd_connectors}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
