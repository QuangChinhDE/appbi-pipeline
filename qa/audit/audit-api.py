"""Adversarial API audit.

Probes the Product API for contract, security and robustness problems rather
than confirming the happy path: malformed input, hostile input, boundary values,
missing auth, idempotency, pagination, and error-envelope consistency.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("APPBI_API", "http://localhost:8080") + "/api/v1"

FINDINGS: list[tuple[str, str, str]] = []


def finding(severity: str, area: str, detail: str) -> None:
    FINDINGS.append((severity, area, detail))
    print(f"  [{severity}] {area}: {detail}", flush=True)


class Client:
    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method, path, body=None, headers=None, raw_body=None):
        data = raw_body if raw_body is not None else (
            json.dumps(body).encode() if body is not None else None)
        request = urllib.request.Request(
            BASE + path, data=data, method=method,
            headers={"Content-Type": "application/json", **(headers or {})} if data else (headers or {}),
        )
        try:
            with self.opener.open(request, timeout=300) as response:
                payload = response.read()
                return response.status, (json.loads(payload) if payload else None)
        except urllib.error.HTTPError as error:
            payload = error.read()
            try:
                return error.code, (json.loads(payload) if payload else None)
            except json.JSONDecodeError:
                return error.code, {"_raw": payload.decode(errors="replace")[:300]}
        except Exception as exc:  # noqa: BLE001
            return 0, {"_transport": str(exc)}

    def login(self, email, password="Admin@12345"):
        return self.call("POST", "/auth/login", {"email": email, "password": password})


def envelope_ok(payload) -> tuple[bool, str]:
    """Every error must carry the section 23.2 envelope."""
    if not isinstance(payload, dict) or "error" not in payload:
        return False, f"no error envelope: {str(payload)[:120]}"
    error = payload["error"]
    for field in ("code", "message", "category", "trace_id"):
        if field not in error:
            return False, f"envelope missing '{field}'"
    if not error["trace_id"]:
        return False, "trace_id is empty"
    return True, ""


print("\n=== A. unauthenticated access ===")
anon = Client()
PROTECTED = [
    ("GET", "/overview"), ("GET", "/sources"), ("GET", "/destinations"),
    ("GET", "/pipelines"), ("GET", "/runs"), ("GET", "/monitoring"),
    ("GET", "/connectors"), ("GET", "/audit"), ("GET", "/alerts/rules"),
    ("GET", "/workspace/settings"), ("GET", "/workspace/members"),
    ("GET", "/engine/status"), ("GET", "/admin/compatibility"),
    ("POST", "/sources/test"), ("POST", "/pipelines"),
]
for method, path in PROTECTED:
    status, payload = anon.call(method, path, {} if method == "POST" else None)
    if status != 401:
        finding("blocker", "auth", f"{method} {path} without a session returned {status}")
    else:
        ok, why = envelope_ok(payload)
        if not ok:
            finding("bug", "auth", f"{method} {path} 401 {why}")

print("\n=== B. admin session ===")
admin = Client()
status, me = admin.login("admin@appbi.local")
if status != 200:
    print("cannot log in; aborting", me)
    sys.exit(1)
workspace = me["workspace"]["id"]
print(f"  logged in, workspace {workspace}")

print("\n=== C. malformed and hostile input ===")
# Non-JSON body.
status, payload = admin.call("POST", "/sources", raw_body=b"this is not json")
if status not in (400, 422):
    finding("bug", "input", f"non-JSON body returned {status}, expected 400/422")
else:
    ok, why = envelope_ok(payload)
    if not ok:
        finding("bug", "input", f"non-JSON body {why}")

# Wrong types where a string is expected.
status, payload = admin.call("POST", "/sources", {
    "name": {"nested": "object"}, "connector_key": ["array"], "configuration": "string",
})
if status != 422:
    finding("bug", "input", f"wrong-typed fields returned {status}, expected 422")

# Oversized name.
status, _ = admin.call("POST", "/sources", {
    "name": "x" * 5000, "connector_key": "source-faker", "configuration": {}, "credentials": {},
})
if status not in (422, 400):
    finding("bug", "input", f"5000-char name returned {status}, expected 422")

# Unknown connector.
status, payload = admin.call("POST", "/sources", {
    "name": "probe", "connector_key": "source-does-not-exist",
    "configuration": {}, "credentials": {},
})
if status != 404:
    finding("bug", "input", f"unknown connector returned {status}, expected 404")

# SQL-injection-shaped input must be handled as data, never break the query.
for payload_str in ["'; DROP TABLE sources; --", "1 OR 1=1", "%", "_"]:
    status, _ = admin.call("GET", f"/sources?q={urllib.parse.quote(payload_str)}")
    if status != 200:
        finding("blocker", "injection", f"search q={payload_str!r} returned {status}")
status, _ = admin.call("GET", "/sources")
if status != 200:
    finding("blocker", "injection", "sources list broken after injection probes")

# XSS-shaped name should round-trip as inert data.
status, created = admin.call("POST", "/sources", {
    "name": "<script>alert(1)</script>", "connector_key": "source-faker",
    "configuration": {"count": 10, "seed": 1}, "credentials": {}, "test_before_save": False,
})
if status == 201:
    if created["name"] != "<script>alert(1)</script>":
        finding("info", "xss", "name was altered on save (escaping at rest)")
    admin.call("DELETE", f"/sources/{created['id']}")
elif status not in (400, 422):
    finding("info", "xss", f"script-shaped name returned {status}")

print("\n=== D. pagination and filter boundaries ===")
for query, expect in [
    ("?limit=0", 422), ("?limit=-1", 422), ("?limit=99999", 422),
    ("?offset=-5", 422), ("?limit=abc", 422),
]:
    status, _ = admin.call("GET", f"/runs{query}")
    if status != expect:
        finding("bug", "pagination", f"/runs{query} returned {status}, expected {expect}")

status, page = admin.call("GET", "/runs?limit=1")
if status == 200:
    if page["page"]["total"] is None:
        finding("bug", "pagination", "list response has no total")
    if len(page["items"]) > 1:
        finding("bug", "pagination", "limit=1 returned more than one item")

# Invalid enum values in filters.
for query in ["?status=NOT_A_STATUS", "?trigger_type=NOPE", "?error_category=NOPE"]:
    status, payload = admin.call("GET", f"/runs{query}")
    if status not in (200, 422):
        finding("bug", "filters", f"/runs{query} returned {status}")
    if status == 500:
        finding("blocker", "filters", f"/runs{query} crashed")

print("\n=== E. tenant isolation ===")
status, other_ws = admin.call("GET", "/auth/me")
others = [w for w in other_ws["workspaces"] if w["id"] != workspace]
status, pipelines = admin.call("GET", "/pipelines?limit=1")
target = pipelines["items"][0]["id"] if pipelines["items"] else None
if others and target:
    admin.call("POST", f"/auth/switch-workspace/{others[0]['id']}")
    for method, path in [
        ("GET", f"/pipelines/{target}"),
        ("PATCH", f"/pipelines/{target}"),
        ("DELETE", f"/pipelines/{target}"),
        ("POST", f"/pipelines/{target}/runs"),
    ]:
        status, _ = admin.call(method, path, {} if method in ("PATCH", "POST") else None)
        if status not in (404, 405):
            finding("blocker", "tenant", f"{method} {path} across tenants returned {status}")
    # X-Workspace-Id must not let a user reach a workspace they switched away from
    # unless they are a member (they are here) — but a workspace they are NOT a
    # member of must be refused.
    status, _ = admin.call("GET", "/pipelines",
                           headers={"X-Workspace-Id": str(uuid.uuid4())})
    if status == 200:
        # Falling back to a legitimate membership is acceptable; leaking is not.
        pass
    admin.call("POST", f"/auth/switch-workspace/{workspace}")

print("\n=== F. RBAC enforced server-side ===")
analyst = Client()
analyst.login("analyst@appbi.local")
status, pipelines = admin.call("GET", "/pipelines?limit=1")
target = pipelines["items"][0]["id"] if pipelines["items"] else None
MUTATIONS = [
    ("POST", "/sources", {"name": "x", "connector_key": "source-faker",
                          "configuration": {}, "credentials": {}}),
    ("POST", "/destinations", {"name": "x", "connector_key": "destination-postgres",
                               "configuration": {}, "credentials": {}}),
    ("POST", "/pipelines", {"name": "x", "source_id": str(uuid.uuid4()),
                            "destination_id": str(uuid.uuid4())}),
    ("POST", "/alerts/rules", {"name": "x", "event_type": "RUN_FAILED"}),
    ("POST", "/admin/connectors/refresh", None),
    ("PATCH", "/workspace/settings", {"name": "hacked"}),
    ("POST", "/workspace/members", {"email": "x@y.z", "full_name": "x",
                                    "role": "OWNER", "password": "Password1"}),
]
if target:
    MUTATIONS += [
        ("POST", f"/pipelines/{target}/runs", None),
        ("POST", f"/pipelines/{target}/pause", None),
        ("DELETE", f"/pipelines/{target}", None),
    ]
for method, path, body in MUTATIONS:
    status, payload = analyst.call(method, path, body)
    if status not in (403, 422):
        finding("blocker", "rbac", f"analyst {method} {path} returned {status}, expected 403")
    elif status == 422:
        finding("info", "rbac",
                f"analyst {method} {path} returned 422 (validation ran before the permission check)")

print("\n=== G. idempotency ===")
if target:
    key = f"audit-{uuid.uuid4().hex[:8]}"
    s1, r1 = admin.call("POST", f"/pipelines/{target}/runs", None, {"Idempotency-Key": key})
    s2, r2 = admin.call("POST", f"/pipelines/{target}/runs", None, {"Idempotency-Key": key})
    if s1 == 202 and s2 == 202 and r1 and r2:
        if r1["id"] != r2["id"]:
            finding("bug", "idempotency",
                    "same Idempotency-Key produced two different runs")
    elif s1 == 202 and s2 == 409:
        finding("info", "idempotency",
                "replay with the same key hit ALREADY_RUNNING instead of returning the first run")
    # Cancel whatever we started so the audit leaves nothing running.
    if r1 and r1.get("id"):
        admin.call("POST", f"/runs/{r1['id']}/cancel")

print("\n=== H. error envelope consistency ===")
PROBES = [
    ("GET", f"/sources/{uuid.uuid4()}", 404),
    ("GET", f"/pipelines/{uuid.uuid4()}", 404),
    ("GET", f"/runs/{uuid.uuid4()}", 404),
    ("GET", "/connectors/nope", 404),
    ("GET", "/sources/not-a-uuid", 422),
    ("POST", "/sources/test", 422),   # missing connector_key
]
for method, path, expect in PROBES:
    status, payload = admin.call(method, path, {} if method == "POST" else None)
    if status != expect:
        finding("bug", "errors", f"{method} {path} returned {status}, expected {expect}")
    ok, why = envelope_ok(payload)
    if not ok:
        finding("bug", "errors", f"{method} {path} {why}")

print("\n=== I. secret leakage across the whole surface ===")
SECRETS = ["demo_reader_pw", "demo_writer_pw", "Admin@12345"]
SURFACE = ["/sources", "/destinations", "/pipelines", "/runs", "/audit?limit=200",
           "/overview", "/monitoring", "/connectors", "/workspace/members",
           "/alerts/notifications"]
for path in SURFACE:
    status, payload = admin.call("GET", path)
    blob = json.dumps(payload, ensure_ascii=False) if payload else ""
    for secret in SECRETS:
        if secret in blob:
            finding("blocker", "secrets", f"{path} leaks {secret!r}")
    for token in ["embedded://", "engine_source_ref", "engine_job_ref", "secret_ref",
                  "wrapped_data_key", "ciphertext"]:
        if token in blob:
            finding("blocker", "leak", f"{path} exposes internal field {token!r}")

# Detail payloads too.
status, sources = admin.call("GET", "/sources")
for item in (sources or {}).get("items", [])[:5]:
    status, detail = admin.call("GET", f"/sources/{item['id']}")
    blob = json.dumps(detail, ensure_ascii=False)
    for secret in SECRETS:
        if secret in blob:
            finding("blocker", "secrets", f"source detail {item['name']} leaks {secret!r}")

print("\n=== J. logs endpoint bounds ===")
status, runs = admin.call("GET", "/runs?limit=1")
if runs and runs["items"]:
    run_id = runs["items"][0]["id"]
    for query, expect in [("?limit=0", 422), ("?limit=999999", 422), ("?cursor=-1", 422)]:
        status, _ = admin.call("GET", f"/runs/{run_id}/logs{query}")
        if status != expect:
            finding("bug", "logs", f"logs{query} returned {status}, expected {expect}")
    status, page = admin.call("GET", f"/runs/{run_id}/logs?limit=5")
    if status == 200 and len(page["lines"]) > 5:
        finding("bug", "logs", "limit=5 returned more than five lines")

print("\n=== K. schedule validation ===")
BAD_SCHEDULES = [
    ({"type": "INTERVAL", "interval_seconds": 1, "timezone": "UTC"}, "below minimum"),
    ({"type": "CRON", "cron_expression": "* * * * *", "timezone": "UTC"}, "cron every minute"),
    ({"type": "CRON", "cron_expression": "garbage", "timezone": "UTC"}, "invalid cron"),
    ({"type": "DAILY", "time_of_day": "99:99", "timezone": "UTC"}, "invalid time"),
    ({"type": "DAILY", "time_of_day": "02:00", "timezone": "Mars/Olympus"}, "invalid timezone"),
    ({"type": "NOPE", "timezone": "UTC"}, "invalid type"),
]
for body, label in BAD_SCHEDULES:
    status, payload = admin.call("POST", "/pipelines/schedule/preview", body)
    if status not in (400, 422):
        finding("bug", "schedule", f"{label} accepted with {status}: {str(payload)[:120]}")

print("\n=== L. concurrency / double submit ===")
if target:
    s1, _ = admin.call("POST", f"/pipelines/{target}/runs")
    s2, payload = admin.call("POST", f"/pipelines/{target}/runs")
    if s1 == 202 and s2 == 202:
        finding("bug", "concurrency",
                "two overlapping runs were accepted for one pipeline")
    elif s2 == 409:
        code = (payload or {}).get("error", {}).get("code")
        if code != "PIPELINE_ALREADY_RUNNING":
            finding("info", "concurrency", f"second trigger 409 with code {code}")
    status, runs = admin.call("GET", f"/runs?pipeline_id={target}&limit=1")
    if runs and runs["items"]:
        admin.call("POST", f"/runs/{runs['items'][0]['id']}/cancel")

print("\n=== L2. duplicate names ===")
dup_name = f"audit-dup-{uuid.uuid4().hex[:6]}"
dup_body = {"name": dup_name, "connector_key": "source-faker",
            "configuration": {"count": 5, "seed": 1}, "credentials": {},
            "test_before_save": False}
s1, first = admin.call("POST", "/sources", dup_body)
if s1 == 201:
    s2, _ = admin.call("POST", "/sources", dup_body)
    if s2 == 500:
        finding("blocker", "duplicate", "creating a duplicate name returns 500")
    elif s2 not in (409, 422):
        finding("bug", "duplicate", f"duplicate name returned {s2}, expected 409/422")
    # Deleting must release the name, not reserve it forever.
    admin.call("DELETE", f"/sources/{first['id']}")
    s3, third = admin.call("POST", "/sources", dup_body)
    if s3 != 201:
        finding("bug", "duplicate",
                f"name still reserved after delete: re-create returned {s3}")
    elif third:
        admin.call("DELETE", f"/sources/{third['id']}")
else:
    finding("info", "duplicate", f"could not seed the duplicate probe ({s1})")


print("\n=== M. HTTP semantics ===")
for method, path, expect in [
    ("PUT", "/sources", 405),
    ("DELETE", "/pipelines", 405),
    ("GET", "/auth/login", 405),
]:
    status, payload = admin.call(method, path, {} if method == "PUT" else None)
    if status != expect:
        finding("info", "http", f"{method} {path} returned {status}, expected {expect}")

# Sanity: OpenAPI must describe the surface.
status, spec = admin.call("GET", "/../../openapi.json")
if status != 200:
    status, spec = Client().call("GET", "/../../openapi.json")
if status == 200 and isinstance(spec, dict):
    paths = spec.get("paths", {})
    for required in ["/api/v1/sources", "/api/v1/pipelines", "/api/v1/runs"]:
        if required not in paths:
            finding("bug", "openapi", f"{required} missing from the OpenAPI document")
else:
    finding("info", "openapi", f"openapi.json unreachable ({status})")

# ── report ─────────────────────────────────────────────────────────────────
print(f"\n=== {len(FINDINGS)} findings ===")
order = {"blocker": 0, "bug": 1, "info": 2}
for severity, area, detail in sorted(FINDINGS, key=lambda f: order.get(f[0], 9)):
    print(f"[{severity:8}] {area:14} {detail}")
if not FINDINGS:
    print("none")
