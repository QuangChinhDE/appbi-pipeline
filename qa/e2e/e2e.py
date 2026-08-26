"""End-to-end smoke test against the running Product API.

Drives the exact journey from section 9.1: create source -> test -> create
destination -> test -> discover -> create pipeline -> run -> watch to terminal.
Nothing here touches the engine directly.
"""
from __future__ import annotations

import argparse
import os
import json
import sys
import time
import urllib.error
import urllib.request
import http.cookiejar
from pathlib import Path

# `_console` is shared tooling and lives with the operational scripts;
# only this import crosses from `qa/` into `scripts/`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from _console import force_utf8  # noqa: E402

BASE = os.environ.get("APPBI_API", "http://localhost:8010") + "/api/v1"
JAR = http.cookiejar.CookieJar()
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(JAR))


def call(method: str, path: str, body=None, expect=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with OPENER.open(request, timeout=600) as response:
            raw = response.read()
            payload = json.loads(raw) if raw else None
            status = response.status
    except urllib.error.HTTPError as error:
        raw = error.read()
        payload = json.loads(raw) if raw else None
        status = error.code
    if expect and status not in expect:
        print(f"!! {method} {path} -> {status}")
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:2500])
        sys.exit(1)
    return status, payload




# What this run actually proved. Written as each operation completes, never up
# front: the release gate reads this file instead of taking anyone's word, so a
# name appearing here has to mean the thing ran.
# ANSI escapes leaking through to the browser was a real defect; this is
# the guard. chr(27) rather than a literal, which would be an invisible
# control character sitting in the source.
ANSI_ESCAPE = chr(27)

EVIDENCE: dict[str, bool] = {}


RUN_IDS: set[str] = set()


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


STARTED_AT = _utc_now()


def remember_run(run_id: str) -> None:
    """Every run this evidence is about, so the gate can check they exist."""
    if run_id:
        RUN_IDS.add(str(run_id))


def proved(operation: str) -> None:
    EVIDENCE[operation] = True


def step(label: str) -> None:
    print(f"\n=== {label} ===", flush=True)


def main() -> None:
    force_utf8()
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["faker", "postgres"], default="faker")
    parser.add_argument("--demo-host", default=os.getenv("APPBI_DEMO_DB_HOST", "postgres"),
                        help="how the *connector* reaches the demo database. "
                             "`postgres` is right when connectors run on the "
                             "Compose network; a Kubernetes Airbyte launches "
                             "them in its own cluster, where that name does not "
                             "resolve, so certification against one passes an "
                             "address instead.")
    parser.add_argument("--suffix", default=str(int(time.time()))[-5:])
    parser.add_argument("--evidence", default=None,
                        help="write a JSON record of what this run proved, for "
                             "scripts/release-gate.py")
    parser.add_argument("--skip-builder", action="store_true",
                        help="skip the Connector Builder checks (they need "
                             "outbound network for the sample API)")
    parser.add_argument(
        "--engine", choices=["embedded", "airbyte-api"], default=None,
        help="assert the deployment is running this engine before testing it; "
             "without it the run passes against whatever happens to be up, "
             "which is how an AIRBYTE_API lane silently certifies the embedded "
             "executor instead",
    )
    args = parser.parse_args()
    sfx = args.suffix

    step("login")
    _, me = call("POST", "/auth/login",
                 {"email": "admin@appbi.local", "password": "Admin@12345"}, expect={200})
    print("user:", me["email"], "| workspace:", me["workspace"]["name"], "| role:", me["role"])

    if args.engine:
        step("engine identity")
        expected = {"embedded": "AIRBYTE_EMBEDDED", "airbyte-api": "AIRBYTE_API"}[args.engine]
        _, matrix = call("GET", "/admin/compatibility", expect={200})
        actual = matrix["engine"]["type"]
        print("engine:", actual, "| version:", matrix["engine"].get("version"),
              "| reachable:", matrix["engine"].get("reachable"))
        if actual != expected:
            print(f"!! asked to certify {expected}, but this deployment runs {actual}")
            sys.exit(1)
        if not matrix["engine"].get("reachable"):
            print("!! the engine is not reachable; nothing below would mean anything")
            sys.exit(1)

    step("create source")
    if args.source == "faker":
        source_body = {
            "name": f"Sample data {sfx}",
            "connector_key": "source-faker",
            "configuration": {"count": 300, "seed": 42, "records_per_slice": 100,
                              "parallelism": 1, "always_updated": True},
            "credentials": {},
            "test_before_save": True,
        }
    else:
        source_body = {
            "name": f"Demo Postgres {sfx}",
            "connector_key": "source-postgres",
            "configuration": {
                "host": args.demo_host, "port": 5432, "database": "demo_source",
                "schemas": ["shop"], "username": "demo_reader",
                "ssl_mode": {"mode": "disable"},
                "replication_method": {"method": "Standard"},
                "tunnel_method": {"tunnel_method": "NO_TUNNEL"},
            },
            "credentials": {"password": "demo_reader_pw"},
            "test_before_save": True,
        }
    status, source = call("POST", "/sources", source_body, expect={201})
    print("source:", source["id"], "| health:", source["health"]["label"])
    proved("source_create_and_check")
    print("credentials returned:", json.dumps(source["credentials"], ensure_ascii=False))
    assert "demo_reader_pw" not in json.dumps(source), "SECRET LEAKED IN RESPONSE"

    step("create destination")
    _, destination = call("POST", "/destinations", {
        "name": f"Demo Warehouse {sfx}",
        "connector_key": "destination-postgres",
        "configuration": {
            "host": args.demo_host, "port": 5432, "database": "demo_warehouse",
            "schema": f"synced_{sfx}", "username": "demo_writer",
            "ssl_mode": {"mode": "disable"},
            "tunnel_method": {"tunnel_method": "NO_TUNNEL"},
        },
        "credentials": {"password": "demo_writer_pw"},
        "test_before_save": True,
    }, expect={201})
    print("destination:", destination["id"], "| health:", destination["health"]["label"])
    proved("destination_create_and_check")

    step("discover schema")
    _, snapshot = call("POST", f"/sources/{source['id']}/discover", None, expect={200})
    print("snapshot:", snapshot["id"], "| streams:", snapshot["stream_count"])
    proved("discover")
    for stream in snapshot["streams"][:8]:
        print(f"   - {stream['namespace'] or '-'}.{stream['name']} "
              f"modes={stream['supported_sync_modes']} pk={stream['source_defined_primary_key']} "
              f"fields={len(stream['fields'])}")

    chosen = snapshot["streams"][:2]
    selections = []
    for stream in chosen:
        incremental = "incremental" in stream["supported_sync_modes"]
        cursor = stream["default_cursor_field"]
        if incremental and not cursor and not stream["source_defined_cursor"]:
            candidates = [f["name"] for f in stream["fields"]
                          if "date" in f["type"] or f["name"].endswith("_at")]
            cursor = candidates[:1]
        use_incremental = incremental and (bool(cursor) or stream["source_defined_cursor"])
        pk = stream["source_defined_primary_key"]
        selections.append({
            "name": stream["name"],
            "namespace": stream["namespace"],
            "selected": True,
            "sync_mode": "incremental" if use_incremental else "full_refresh",
            "destination_sync_mode": "append_dedup" if (use_incremental and pk) else "overwrite",
            "cursor_fields": cursor if use_incremental else [],
            "primary_key_fields": pk if (use_incremental and pk) else [],
        })
    print("selected:", json.dumps(selections, ensure_ascii=False))

    step("create pipeline + first sync")
    _, pipeline = call("POST", "/pipelines", {
        "name": f"Demo pipeline {sfx}",
        "source_id": source["id"],
        "destination_id": destination["id"],
        "schema_snapshot_id": snapshot["id"],
        "streams": selections,
        "schedule": {"type": "DAILY", "time_of_day": "02:00", "timezone": "Asia/Bangkok"},
        "run_first_sync": True,
    }, expect={201})
    print("pipeline:", pipeline["id"], "| status:", pipeline["status"],
          "| next run:", pipeline["next_run_at"])
    proved("connection_create")

    step("watch run")
    deadline = time.time() + 900
    run = None
    while time.time() < deadline:
        _, runs = call("GET", f"/runs?pipeline_id={pipeline['id']}&limit=1", expect={200})
        if runs["items"]:
            run = runs["items"][0]
            remember_run(run["id"])
            print(f"  [{int(time.time() % 10000)}] {run['status']:<18} "
                  f"records={run['records_synced']} bytes={run['bytes_synced']}", flush=True)
            if run["status"] in ("SUCCEEDED", "FAILED", "FAILED_TO_START",
                                 "CANCELLED", "TIMED_OUT"):
                break
        time.sleep(5)

    if run is None:
        print("!! no run was created")
        sys.exit(1)

    step("run detail")
    _, detail = call("GET", f"/runs/{run['id']}", expect={200})
    print("status      :", detail["status"])
    print("duration    :", detail["duration_seconds"], "s")
    print("records     :", detail["records_synced"])
    print("bytes       :", detail["bytes_synced"])
    print("attempts    :", [(a["attempt_number"], a["status"]) for a in detail["attempts"]])
    print("stream stats:", [(s["stream_name"], s["records_emitted"]) for s in detail["stream_stats"]])
    if detail["records_synced"] and detail["stream_stats"]:
        proved("job_status_and_stats")
        proved("sync_full_refresh")
    if detail["error"]:
        print("error       :", json.dumps(detail["error"], ensure_ascii=False, indent=2))

    step("logs tail")
    _, logs = call("GET", f"/runs/{run['id']}/logs?limit=2000", expect={200})
    for line in logs["lines"][-25:]:
        print("   ", line[:220])
    if logs["lines"]:
        proved("job_logs")
        # ANSI leaking to the browser was a real defect; this is the guard.
        leaked = [line for line in logs["lines"] if ANSI_ESCAPE in line]
        if leaked:
            print(f"!! {len(leaked)} log line(s) still carry terminal escapes: "
                  f"{leaked[0][:120]}")
            sys.exit(2)

    if detail["status"] != "SUCCEEDED":
        step("result")
        print(f"FAILED: {detail['status']}")
        sys.exit(2)

    step("second sync — does the cursor hold?")
    # An incremental stream that re-reads everything still succeeds, and still
    # writes the right rows. The only way to see the difference is to sync a
    # second time with nothing new and look at what moved.
    incremental = [s_["name"] for s_ in selections if s_["sync_mode"] == "incremental"]
    if not incremental:
        print("no incremental stream was selected; nothing to prove here")
    else:
        _, second = call("POST", f"/pipelines/{pipeline['id']}/runs",
                         {"trigger": "manual"}, expect={201, 202})
        remember_run(second["id"])
        deadline = time.time() + 900
        while time.time() < deadline:
            _, second = call("GET", f"/runs/{second['id']}", expect={200})
            print(f"  {second['status']:<18} records={second['records_synced']}", flush=True)
            if second["status"] in ("SUCCEEDED", "FAILED", "FAILED_TO_START",
                                    "CANCELLED", "TIMED_OUT"):
                break
            time.sleep(5)

        if second["status"] != "SUCCEEDED":
            print(f"!! second sync ended {second['status']}")
            sys.exit(2)

        by_stream = {s_["stream_name"]: s_["records_emitted"] for s_ in second["stream_stats"]}
        first_by_stream = {s_["stream_name"]: s_["records_emitted"]
                           for s_ in detail["stream_stats"]}
        for name in incremental:
            again, before = by_stream.get(name, 0), first_by_stream.get(name, 0)
            print(f"   {name}: {before} first, {again} second")
            if before and again >= before:
                print(f"!! '{name}' is configured incremental but re-read every row; "
                      "the cursor is not being persisted")
                sys.exit(2)
        proved("sync_incremental_second_pass")

    step("cancel — does a running sync stop when asked?")
    # Not inferable from a run list, which is why it has to be done here rather
    # than asserted later. Cancel early: a sync with nothing new to read
    # finishes in seconds and the request would land after it is already done.
    _, third = call("POST", f"/pipelines/{pipeline['id']}/runs",
                    {"trigger": "manual"}, expect={201, 202})
    remember_run(third["id"])
    time.sleep(12)
    _, cancelled = call("POST", f"/runs/{third['id']}/cancel", None,
                        expect={200, 202, 409})
    print("  cancel accepted ->", cancelled.get("status"))

    deadline = time.time() + 300
    while time.time() < deadline:
        _, third = call("GET", f"/runs/{third['id']}", expect={200})
        if third["status"] in ("CANCELLED", "SUCCEEDED", "FAILED", "TIMED_OUT"):
            break
        time.sleep(5)
    print("  final:", third["status"])

    if third["status"] == "CANCELLED":
        proved("cancel")
    elif third["status"] == "SUCCEEDED":
        # A race, not a defect: the run finished before the request landed.
        # Reported honestly and left unproved — the gate will refuse the
        # release rather than accept a maybe.
        print("  !! the run completed before the cancel took effect. "
              "Not counted as proof; re-run to exercise it.")
    else:
        print(f"!! cancel produced {third['status']}")
        sys.exit(2)

    if not args.skip_builder:
        step("connector builder — test and publish a custom connector")
        _, projects = call("GET", "/builder/projects", expect={200})
        candidates = projects if isinstance(projects, list) else projects.get("items", [])
        project = next((p_ for p_ in candidates if p_.get("last_test_ok")), None)

        if project is None:
            print("  no builder project to exercise; skipping (not proved)")
        else:
            _, tested = call("POST", f"/builder/projects/{project['id']}/test",
                             {"stream_name": None}, expect={200})
            preview = tested.get("record_preview_supported", True)
            print(f"  test ok={tested.get('ok')} preview_supported={preview} "
                  f"records={tested.get('record_count')}")
            if tested.get("ok"):
                proved("declarative_builder_test")
                _, published = call(
                    "POST", f"/builder/projects/{project['id']}/publish", None,
                    expect={200})
                print(f"  published {published.get('connector_key')} "
                      f"rev {published.get('published_version')}")
                if published.get("status") == "PUBLISHED":
                    proved("declarative_connector_publish")
            else:
                print("  builder test failed:",
                      json.dumps(tested.get("error"), ensure_ascii=False)[:200])

    step("result")
    print(f"OK: sync succeeded with {detail['records_synced']} records")

    if args.evidence:
        # Evidence v2. v1 recorded which operations passed and nothing about
        # *what* they passed against, so an evidence file proved a run had
        # happened somewhere, at some point, on some build. The release gate
        # could then bind a certification to a commit read from the release
        # manager's working tree, which is not the deployment.
        #
        # Everything here is read back from the deployment under test, not
        # asserted by this script about itself.
        deployment: dict = {}
        try:
            _, matrix = call("GET", "/admin/compatibility", expect={200})
            deployment = {
                "product_version": matrix.get("product_version"),
                "build": matrix.get("build") or {},
                "workspace_fingerprint": matrix.get("workspace_fingerprint"),
                "engine": matrix.get("engine") or {},
                "connectors": {
                    key: value.get("engine_image")
                    for key, value in (matrix.get("connectors") or {}).items()
                    if value.get("engine_image")
                },
            }
        except Exception as exc:  # noqa: BLE001
            print(f"!! could not read /admin/compatibility: {exc}")

        record = {
            "schema": 2,
            "produced_by": "qa/e2e/e2e.py",
            "engine": args.engine or "unspecified",
            "started_at": STARTED_AT,
            "finished_at": _utc_now(),
            # The run ids this evidence is actually about. The gate checks they
            # exist on the deployment being released, which is what stops an
            # artifact from being assembled out of somebody else's history.
            "run_ids": sorted(RUN_IDS),
            "deployment": deployment,
            "operations": EVIDENCE,
        }
        Path(args.evidence).write_text(
            json.dumps(record, indent=2) + chr(10), encoding="utf-8")
        print(f"evidence -> {args.evidence}")
        build = (deployment.get("build") or {}).get("sha") or "unknown"
        print(f"  build   {build}")
        print(f"  runs    {len(RUN_IDS)}")
        for name in sorted(EVIDENCE):
            print(f"  proved  {name}")


if __name__ == "__main__":
    main()
