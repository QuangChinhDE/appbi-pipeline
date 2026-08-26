"""Verification pass over the running platform.

Covers the UAT cases that need a live system: incremental state resume, cancel
idempotency, retry lineage, dependency-blocked delete, tenant isolation, RBAC
and secret leakage.
"""
from __future__ import annotations

import http.cookiejar
import os
import json
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("APPBI_API", "http://localhost:8010") + "/api/v1"


class Client:
    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            BASE + path, data=data, method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        try:
            with self.opener.open(request, timeout=600) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as error:
            raw = error.read()
            return error.code, (json.loads(raw) if raw else None)

    def login(self, email, password="Admin@12345"):
        status, payload = self.call("POST", "/auth/login", {"email": email, "password": password})
        assert status == 200, (status, payload)
        return payload


# `bool | None`: None means inconclusive -- the scenario did not run.
RESULTS: list[tuple[str, bool | None, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""), flush=True)


def inconclusive(name: str, detail: str) -> None:
    """The scenario did not run, so it neither passed nor failed.

    Recorded as a distinct outcome because the alternative is what UAT-007 did:
    it accepted `SUCCEEDED` after a cancel as a pass, so a sync that finished
    before the cancel landed was reported as proof that cancellation works.
    That is worse than a failure -- a failure gets investigated.

    An inconclusive result is not a pass. The summary refuses to claim the
    suite passed while any remain.
    """
    RESULTS.append((name, None, detail))
    print(f"[----] {name} -- INCONCLUSIVE: {detail}", flush=True)


def wait_terminal(client: Client, run_id: str, timeout: int = 600):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, run = client.call("GET", f"/runs/{run_id}")
        if run["status"] in ("SUCCEEDED", "FAILED", "FAILED_TO_START", "CANCELLED", "TIMED_OUT"):
            return run
        time.sleep(4)
    return run


def main() -> None:
    admin = Client()
    me = admin.login("admin@appbi.local")
    ws = me["workspace"]["id"]

    # ---- find the postgres pipeline created by the e2e run -----------------
    _, pipelines = admin.call("GET", "/pipelines?limit=50")
    target = next((p for p in pipelines["items"]
                   if p["source"]["connector_key"] == "source-postgres"), None)
    if target is None:
        print("no postgres pipeline found; run e2e.py --source postgres first")
        sys.exit(1)
    pipeline_id = target["id"]
    print(f"\nusing pipeline {target['name']} ({pipeline_id})\n")

    # ---- UAT: incremental state resumes ------------------------------------
    status, run = admin.call("POST", f"/pipelines/{pipeline_id}/runs")
    check("trigger returns 202", status == 202, f"status={status}")
    if status != 202:
        print(json.dumps(run, ensure_ascii=False, indent=2))
    else:
        final = wait_terminal(admin, run["id"])
        check("second run succeeds", final["status"] == "SUCCEEDED", final["status"])
        # Airbyte re-emits records sitting exactly on the cursor boundary, so
        # "resumed" means a couple of rows, not the full 2500 again.
        reread = final["records_synced"] or 0
        check(
            "incremental state resumed (near-zero re-read)",
            reread < 50,
            f"re-read {reread} of 2500 rows",
        )

    # ---- UAT-006: retry is refused on a successful run ---------------------
    status, refused = admin.call("POST", f"/runs/{run['id']}/retry")
    check("retry refused for a successful run", status == 422
          and refused["error"]["code"] == "RUN_NOT_RETRYABLE",
          f"status={status}")

    # ---- UAT-007: cancel a live run, twice ---------------------------------
    status, live = admin.call("POST", f"/pipelines/{pipeline_id}/runs")
    if status == 202:
        # Wait until the worker has actually claimed it, so we are cancelling a
        # running sync rather than a queued row.
        for _ in range(30):
            _, current = admin.call("GET", f"/runs/{live['id']}")
            if current["status"] in ("RUNNING", "STARTING"):
                break
            time.sleep(2)
        status1, cancelled = admin.call("POST", f"/runs/{live['id']}/cancel")
        status2, again = admin.call("POST", f"/runs/{live['id']}/cancel")
        check("cancel accepted", status1 == 200, f"-> {cancelled['status']}")
        check("double cancel is idempotent (no error)", status2 == 200,
              f"-> {again['status']}")
        terminal = wait_terminal(admin, live["id"], timeout=300)
        if terminal["status"] == "CANCELLED":
            # BA UAT-007: CANCEL_REQUESTED -> CANCELLED after the engine
            # confirms. Only this proves cancellation.
            check("UAT-007 cancel: run reaches CANCELLED", True, terminal["status"])
        elif terminal["status"] == "SUCCEEDED":
            # The sync finished before the cancel took effect. Nothing about
            # cancellation was exercised, so nothing can be claimed. Use a
            # dataset large enough that the sync is still running when the
            # cancel is issued.
            inconclusive("UAT-007 cancel: run reaches CANCELLED",
                         "the sync completed before the cancel took effect; "
                         "cancellation was never exercised. Re-run against a "
                         "dataset that is still syncing when cancel is issued.")
        else:
            check("UAT-007 cancel: run reaches CANCELLED", False,
                  f"ended {terminal['status']}, expected CANCELLED")

        # ---- UAT-006 proper: retry the cancelled run -----------------------
        if terminal["status"] in ("CANCELLED", "FAILED"):
            status, retried = admin.call("POST", f"/runs/{terminal['id']}/retry")
            check("retry creates a new run", status == 202 and retried["id"] != terminal["id"],
                  f"status={status}")
            if status == 202:
                check("retry links back to the original",
                      retried.get("retry_of_run_id") == terminal["id"])
                check("retry trigger type is RETRY", retried["trigger_type"] == "RETRY")
                _, original = admin.call("GET", f"/runs/{terminal['id']}")
                check("original run history is unchanged",
                      original["status"] == terminal["status"], original["status"])
                wait_terminal(admin, retried["id"], timeout=300)

    # ---- UAT-009: delete a source that has pipelines -----------------------
    source_id = target["source"]["id"]
    status, payload = admin.call("DELETE", f"/sources/{source_id}")
    blocked = status == 409 and payload["error"]["code"] == "RESOURCE_IN_USE"
    check("delete source with dependencies is blocked with 409", blocked,
          f"status={status} code={(payload or {}).get('error', {}).get('code')}")
    if blocked:
        constraints = payload["error"].get("constraints") or []
        check("409 lists the blocking pipelines", len(constraints) > 0,
              json.dumps(constraints, ensure_ascii=False)[:160])

    # ---- UAT-011: no secret anywhere in the payloads -----------------------
    _, source_detail = admin.call("GET", f"/sources/{source_id}")
    blob = json.dumps(source_detail, ensure_ascii=False)
    check("no plaintext credential in source detail", "demo_reader_pw" not in blob)
    check("credential is reported as masked",
          source_detail["credentials"]["fields"].get("password") == "********")
    check("no engine identifier leaks into the payload",
          "embedded://" not in blob and "engine_source_ref" not in blob)

    # ---- UAT-010: tenant isolation -----------------------------------------
    _, workspaces = admin.call("GET", "/auth/me")
    other = next((w for w in workspaces["workspaces"] if w["id"] != ws), None)
    if other:
        admin.call("POST", f"/auth/switch-workspace/{other['id']}")
        status, payload = admin.call("GET", f"/pipelines/{pipeline_id}")
        check("cross-workspace read returns 404 (policy-safe)", status == 404,
              f"status={status}")
        admin.call("POST", f"/auth/switch-workspace/{ws}")

    # ---- UAT-014: RBAC is enforced server-side -----------------------------
    analyst = Client()
    analyst_me = analyst.login("analyst@appbi.local")
    check("analyst has no pipeline create permission in the payload",
          "create" not in analyst_me["permissions"].get("pipelines", []))
    status, payload = analyst.call("POST", f"/pipelines/{pipeline_id}/runs")
    check("analyst run request is rejected 403", status == 403,
          f"status={status} code={(payload or {}).get('error', {}).get('code')}")
    status, payload = analyst.call("DELETE", f"/sources/{source_id}")
    check("analyst delete is rejected 403", status == 403, f"status={status}")
    status, _ = analyst.call("GET", "/pipelines")
    check("analyst can still read pipelines", status == 200, f"status={status}")

    # ---- audit trail --------------------------------------------------------
    # Ask for each action directly. Scanning the newest N events made this
    # depend on how much unrelated traffic the workspace happened to see while
    # the suite ran, which is a property of the environment, not of auditing.
    for expected in ("source.created", "destination.created", "pipeline.created",
                     "pipeline.run.triggered", "source.schema.discovered"):
        _, page = admin.call("GET", f"/audit?action={expected}&limit=1")
        recorded = [e for e in page["items"] if e["action"] == expected]
        check(f"audit records {expected}", bool(recorded))
    # Secret leakage is a property of the whole log, so this one does want a
    # broad sweep rather than a targeted lookup.
    _, recent = admin.call("GET", "/audit?limit=200")
    leaked = [e for e in recent["items"]
              if "demo_reader_pw" in json.dumps(e, ensure_ascii=False)]
    check("no credential in the audit log", not leaked)

    # ---- alerts / monitoring ------------------------------------------------
    status, monitoring = admin.call("GET", "/monitoring")
    check("monitoring endpoint responds", status == 200)
    if status == 200:
        check("engine reports operational", monitoring["engine"]["operational"] is True,
              str(monitoring["engine"].get("version")))

    status, overview = admin.call("GET", "/overview")
    check("overview reports a successful run",
          overview["onboarding"]["has_successful_run"] is True)

    # ---- schedule preview ---------------------------------------------------
    status, preview = admin.call("POST", "/pipelines/schedule/preview",
                                 {"type": "DAILY", "time_of_day": "02:00",
                                  "timezone": "Asia/Bangkok"})
    check("schedule preview returns 3 upcoming runs",
          status == 200 and len(preview["next_runs"]) == 3,
          str(preview.get("next_runs"))[:90] if status == 200 else str(status))

    status, payload = admin.call("POST", "/pipelines/schedule/preview",
                                 {"type": "INTERVAL", "interval_seconds": 30,
                                  "timezone": "Asia/Bangkok"})
    check("schedule below the minimum interval is rejected", status == 422,
          f"status={status}")

    # ---- summary ------------------------------------------------------------
    # Three outcomes, and the third is why: `ok is None` means the scenario
    # did not run. Counting those as passes is what let UAT-007 report that
    # cancellation worked when the sync had simply finished first.
    passed = [name for name, ok, _ in RESULTS if ok is True]
    failed = [name for name, ok, _ in RESULTS if ok is False]
    skipped = [name for name, ok, _ in RESULTS if ok is None]

    print(f"\n{len(passed)}/{len(RESULTS)} checks passed, "
          f"{len(failed)} failed, {len(skipped)} inconclusive")
    if failed:
        print("failing:", ", ".join(failed))
    if skipped:
        print("inconclusive:", ", ".join(skipped))
        print("An inconclusive scenario has no evidence, so this run cannot "
              "be cited as UAT coverage for it.")
    if failed or skipped:
        sys.exit(1)


if __name__ == "__main__":
    main()
