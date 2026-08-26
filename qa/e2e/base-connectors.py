#!/usr/bin/env python3
"""Run the Base.vn connectors against the real Base.vn API.

    python qa/e2e/base-connectors.py                       # every connector
    python qa/e2e/base-connectors.py --app workflow        # one
    python qa/e2e/base-connectors.py --read --records 50   # pull until one large stream
    python qa/e2e/base-connectors.py --read --all-streams  # exercise every stream
    python qa/e2e/base-connectors.py --read --stream hiring.candidate
    python qa/e2e/base-connectors.py --incremental         # two syncs, compare

Tokens come from a JSON file of `{"<app>": "<token>"}`, `secrets/base-tokens.json`
by default. Never committed: `secrets/` is ignored, and the app refuses to read
one from anywhere the repository tracks.

What it checks
--------------

    spec         the manifest loads and declares `access_token_v2`
    check        the token is accepted, and a bad one *fails* rather than
                 returning nothing
    discover     every stream appears, with its primary key and cursor
    read         records come back, pagination advances, no duplicate ids
    incremental  a second sync reads less than the first and loses nothing

The `check` test runs twice on purpose: once with the real token and once with
a deliberately corrupted one. Base answers both with `HTTP 200`, so a connector
that only looks at the status line passes both — which is precisely the defect
this connector exists to fix, and the reason a negative control belongs in the
happy-path suite.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from _console import force_utf8  # noqa: E402

from app.connectors.base_vn import (  # noqa: E402
    BY_KEY, CONNECTORS, RUNNER_REPOSITORY, RUNNER_VERSION, TOKEN_FIELD,
    compile_manifest,
)

IMAGE = f"{RUNNER_REPOSITORY}:{RUNNER_VERSION}"

PASS, FAIL, SKIP = "ok  ", "FAIL", "--  "


def run(job: Path, command: list[str], timeout: int = 600) -> list[dict]:
    """One protocol command, returning the messages it emitted."""
    # `docker run --rm` removes the container only when the Docker client exits
    # normally. Killing the client on timeout leaves the connector running in
    # the background, which is exactly how a single pagination bug consumed
    # CPU and 120 MB of logs after the test had already reported a timeout.
    container_name = f"appbi-base-test-{__import__('uuid').uuid4().hex[:12]}"
    argv = ["docker", "run", "--rm", "--name", container_name,
            "-v", f"{job}:/s:ro", IMAGE, *command]
    try:
        result = subprocess.run(
            argv, capture_output=True, timeout=timeout,
            # Bytes, decoded explicitly as UTF-8. `text=True` decodes with the
            # locale codec, which on Windows is cp1252 — so the first Vietnamese
            # character in a Base record killed the reader thread and handed
            # back empty output. Every `read` then reported "0 records" for
            # streams that were returning thousands, and reported it as a pass.
            env={"MSYS_NO_PATHCONV": "1", **__import__("os").environ})
    except subprocess.TimeoutExpired:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30, check=False,
        )
        return [{"type": "TRACE", "error": {"message": f"timed out after {timeout}s"}}]
    stdout = (result.stdout or b"").decode("utf-8", "replace")
    messages = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                messages.append(json.loads(line))
            except ValueError:
                continue
    return messages


def first(messages: list[dict], kind: str) -> dict | None:
    return next((m for m in messages if m.get("type") == kind), None)


def failure_text(messages: list[dict]) -> str:
    for message in messages:
        if message.get("type") == "TRACE":
            error = message.get("trace", message).get("error", {})
            return str(error.get("message") or error.get("internal_message") or "")[:200]
    return ""


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []

    def add(self, app: str, phase: str, outcome: str, detail: str = "") -> None:
        self.rows.append((app, phase, outcome, detail))
        print(f"  {outcome} {app:10} {phase:12} {detail}"[:150])

    @property
    def failed(self) -> int:
        return sum(1 for _, _, outcome, _ in self.rows if outcome == FAIL)

    @property
    def skipped(self) -> int:
        return sum(1 for _, _, outcome, _ in self.rows if outcome == SKIP)


def exercise(app: str, token: str, report: Report, *, domain: str, do_read: bool,
             records: int, incremental: bool, all_streams: bool,
             stream_filter: set[str]) -> None:
    connector = BY_KEY[f"source-base-{app}"]
    manifest = compile_manifest(connector)
    job = Path(tempfile.mkdtemp(prefix=f"base-{app}-"))
    try:
        def write(name: str, payload: dict) -> None:
            (job / name).write_text(json.dumps(payload), encoding="utf-8")

        config = {TOKEN_FIELD: token, "domain": domain, "updated_from": "0",
                  "__injected_declarative_manifest": manifest}
        write("config.json", config)
        write("bad.json", {**config, TOKEN_FIELD: token[:-8] + "XXXXXXXX"})

        # ── spec ────────────────────────────────────────────────────────────
        # Asked of the *manifest*, not of `docker run … spec`. The runner is
        # generic: without a config it has no idea which connector it is, and
        # answers with its own one-field spec describing the manifest slot. The
        # connector's spec is the one inside the manifest, and it is the one
        # the product renders a form from.
        properties = manifest["spec"]["connection_specification"]["properties"]
        required = manifest["spec"]["connection_specification"]["required"]
        ok = TOKEN_FIELD in properties and TOKEN_FIELD in required
        report.add(app, "spec", PASS if ok else FAIL,
                   f"{len(properties)} field(s): {', '.join(properties)}")

        # ── check, with a good token ────────────────────────────────────────
        messages = run(job, ["check", "--config", "/s/config.json"])
        status = ((first(messages, "CONNECTION_STATUS") or {})
                  .get("connectionStatus") or {})
        good = status.get("status") == "SUCCEEDED"
        report.add(app, "check", PASS if good else FAIL,
                   status.get("message", "")[:110] or failure_text(messages))

        # ── check, with a corrupted token ───────────────────────────────────
        # Base returns HTTP 200 either way. A connector that passes this is
        # broken in the most dangerous way available: it reports success and
        # writes nothing.
        messages = run(job, ["check", "--config", "/s/bad.json"])
        status = ((first(messages, "CONNECTION_STATUS") or {})
                  .get("connectionStatus") or {})
        rejected = status.get("status") != "SUCCEEDED"
        report.add(app, "bad-token", PASS if rejected else FAIL,
                   "correctly refused" if rejected
                   else "ACCEPTED A BAD TOKEN — a sync would silently write nothing")

        # ── discover ────────────────────────────────────────────────────────
        messages = run(job, ["discover", "--config", "/s/config.json"])
        catalog = (first(messages, "CATALOG") or {}).get("catalog") or {}
        streams = catalog.get("streams", [])
        expected = {s.name for s in connector.streams}
        found = {s["name"] for s in streams}
        no_key = [s["name"] for s in streams if not s.get("source_defined_primary_key")]
        ok = found == expected and not no_key
        report.add(app, "discover", PASS if ok else FAIL,
                   f"{len(streams)}/{len(expected)} streams"
                   + (f", missing {sorted(expected - found)}" if expected - found else "")
                   + (f", no primary key: {no_key}" if no_key else ""))

        if not (do_read and good):
            return

        # ── read ────────────────────────────────────────────────────────────
        for stream in connector.streams:
            qualified_name = f"{app}.{stream.name}"
            if stream_filter and qualified_name not in stream_filter:
                continue
            entry = next((s for s in streams if s["name"] == stream.name), None)
            if entry is None:
                continue
            write("catalog.json", {"streams": [{
                "stream": {"name": stream.name,
                           "json_schema": entry.get("json_schema", {}),
                           "supported_sync_modes": entry["supported_sync_modes"]},
                "sync_mode": "full_refresh",
                "destination_sync_mode": "overwrite",
            }]})
            started = time.time()
            messages = run(job, ["read", "--config", "/s/config.json",
                                 "--catalog", "/s/catalog.json"])
            rows = [m["record"]["data"] for m in messages if m.get("type") == "RECORD"]
            ids = [r.get("id") for r in rows if r.get("id") is not None]
            duplicates = len(ids) - len(set(ids))
            if duplicates or failure_text(messages):
                outcome = FAIL
            elif not rows:
                # Nothing came back. That is a legitimate answer for an empty
                # table and a symptom of a broken harness, and the two look
                # identical from here -- so it is neither a pass nor a failure.
                outcome = SKIP
            else:
                outcome = PASS
            report.add(app, f"read:{stream.name}", outcome,
                       f"{len(rows)} record(s) in {time.time()-started:.0f}s"
                       + (f", {duplicates} DUPLICATE ids" if duplicates else "")
                       + (" — empty, or nothing was read" if not rows else "")
                       + (f" — {failure_text(messages)}" if failure_text(messages) else ""))
            if len(rows) >= records and not all_streams:
                break

        # ── incremental ─────────────────────────────────────────────────────
        if incremental:
            target = next((s for s in connector.streams
                           if s.incremental and not s.parent), None)
            if target is None:
                report.add(app, "incremental", SKIP, "no incremental root stream")
                return
            entry = next((s for s in streams if s["name"] == target.name), None)
            write("catalog.json", {"streams": [{
                "stream": {"name": target.name,
                           "json_schema": (entry or {}).get("json_schema", {}),
                           "supported_sync_modes": ["full_refresh", "incremental"]},
                "sync_mode": "incremental",
                "destination_sync_mode": "append_dedup",
                "cursor_field": [target.incremental.field],
            }]})
            messages = run(job, ["read", "--config", "/s/config.json",
                                 "--catalog", "/s/catalog.json"])
            first_rows = sum(1 for m in messages if m.get("type") == "RECORD")
            states = [m for m in messages if m.get("type") == "STATE"]
            if not first_rows:
                # You cannot test a cursor with no records to carry it.
                report.add(app, "incremental", SKIP,
                           f"{target.name} is empty; nothing to advance a cursor over")
                return
            if not states:
                report.add(app, "incremental", FAIL, "no STATE emitted, so a "
                                                     "second sync would re-read everything")
                return
            write("state.json", [states[-1].get("state", states[-1])])
            messages = run(job, ["read", "--config", "/s/config.json",
                                 "--catalog", "/s/catalog.json",
                                 "--state", "/s/state.json"])
            second_rows = sum(1 for m in messages if m.get("type") == "RECORD")
            report.add(app, "incremental",
                       PASS if second_rows <= first_rows else FAIL,
                       f"{target.name}: {first_rows} then {second_rows} record(s)")
    finally:
        shutil.rmtree(job, ignore_errors=True)


def main() -> int:
    force_utf8()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tokens", default="secrets/base-tokens.json")
    parser.add_argument("--domain", default="base.com.vn",
                        help="which Base installation to test against; "
                             "base.com.vn is a separate one with separate accounts")
    parser.add_argument("--app", action="append", default=[],
                        help="only these; repeatable")
    parser.add_argument("--read", action="store_true", help="also pull records")
    parser.add_argument("--records", type=int, default=200,
                        help="stop reading a connector after this many records")
    parser.add_argument("--all-streams", action="store_true",
                        help="read every selected stream even after a large one")
    parser.add_argument("--stream", action="append", default=[],
                        help="only read this app.stream; repeatable")
    parser.add_argument("--incremental", action="store_true",
                        help="sync twice and compare")
    args = parser.parse_args()

    tokens_path = Path(args.tokens)
    if not tokens_path.exists():
        print(f"no token file at {tokens_path}.\n\n"
              f'Write {{"workflow": "...", "hrm": "..."}} there. Keep it under '
              "secrets/, which is git-ignored: these are live credentials for "
              "somebody's Base account.", file=sys.stderr)
        return 2
    tokens = json.loads(tokens_path.read_text(encoding="utf-8"))

    if shutil.which("docker") is None:
        print("docker is not on PATH; the connector runs as a container",
              file=sys.stderr)
        return 2

    apps = args.app or [c.app for c in CONNECTORS]
    stream_filter = set(args.stream)
    known_streams = {
        f"{connector.app}.{stream.name}"
        for connector in CONNECTORS for stream in connector.streams
    }
    unknown_streams = sorted(stream_filter - known_streams)
    if unknown_streams:
        print(f"unknown stream(s): {', '.join(unknown_streams)}", file=sys.stderr)
        return 2
    if stream_filter and not args.app:
        apps = sorted({name.split(".", 1)[0] for name in stream_filter})
    missing = [a for a in apps if a not in tokens]
    if missing:
        print(f"no token for: {', '.join(missing)}", file=sys.stderr)
        return 2

    print(f"runner {IMAGE}\n")
    report = Report()
    for app in apps:
        print(f"── {app}")
        exercise(app, tokens[app], report, domain=args.domain,
                 do_read=args.read, records=args.records,
                 incremental=args.incremental, all_streams=args.all_streams,
                 stream_filter=stream_filter)
        print()

    total = len(report.rows)
    passed = total - report.failed - report.skipped
    print(f"{passed} passed, {report.skipped} skipped, "
          f"{report.failed} failed ({total} checks)")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
