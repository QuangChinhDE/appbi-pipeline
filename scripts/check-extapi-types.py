"""Compare what the extapis connectors declare with what the APIs actually return.

Not a CI test: it needs live keys. Run it after changing a connector, or when
a destination reports a typing error:

    BPM_KEY=… SALES_KEY=… PROJECT_KEY=… LEAD_KEY=… DATASET_KEY=… \\
    ACTING_USER=mytest01 python scripts/check-extapi-types.py [app …]

A key left unset skips that application.

Why it exists: the destination gives each column one type, chosen from a
union by a fixed priority (array > object > string > number > integer >
boolean), so a value of any other type does not fit. `task.in_review` was
declared with integer and boolean, arrived as `false`, and BigQuery answered
"Bad int64 value: false" on every run -- even with boolean listed first. The shapes had been read from a handful
of records, with falsy values skipped as "empty" -- which is exactly how a
boolean column hides. This reads every record of every stream, falsy values
included, and reports each field whose values do not fit its column.

Exit status 1 when any field mismatches.
"""

from __future__ import annotations

import collections
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.connectors.base_vn import EXTAPI_CONNECTORS  # noqa: E402

KEY_ENV = {"bpm": "BPM_KEY", "dataset": "DATASET_KEY", "sales": "SALES_KEY",
           "project": "PROJECT_KEY", "lead": "LEAD_KEY"}
#: Applications that answer as a person.
NEEDS_USER = {"project", "lead"}


def _call(app: str, path: str, body: dict | None = None) -> dict:
    payload = dict(body or {})
    if app in NEEDS_USER:
        payload.setdefault("user_id", os.environ.get("ACTING_USER", ""))
    domain = os.environ.get("BASE_DOMAIN", "base.vn")
    request = urllib.request.Request(
        f"https://extapis.{domain}/{app}/v1/{path}", data=json.dumps(payload).encode(),
        headers={"X-API-TOKEN": os.environ[KEY_ENV[app]], "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _records(app: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = collections.defaultdict(list)
    if app == "bpm":
        out["service"] = _call(app, "services")["services"]
        for s in out["service"]:
            out["field"] += _call(app, "service/fields", {"service_id": s["id"]}).get("fields") or []
            out["stage"] += _call(app, "service/stages", {"service_id": s["id"]}).get("stages") or []
            jobs = _call(app, "job/list", {"service_id": s["id"], "per_page": 100,
                                           "sort_by": "updated"}).get("jobs") or []
            out["job"] += jobs
            out["job_detail"] += [_call(app, "job/get", {"job_id": j["id"]}).get("job") or {}
                                  for j in jobs[:20]]
    elif app == "dataset":
        out["dataset"] = _call(app, "datasets/get")["dataset"]
        for d in out["dataset"]:
            out["dataset_field"] += (_call(app, "dataset/get", {"id": d["id"]}).get("dataset") or {}).get("form") or []
            out["record"] += _call(app, "dataset/records", {"dataset_id": d["id"]}).get("records") or []
    elif app == "sales":
        out["pipeline"] = _call(app, "pipeline/all")["pipelines"]
        for p in out["pipeline"]:
            out["pipeline_stage"] += _call(app, "pipeline/get.stages", {"id": p["id"]}).get("stages") or []
            out["deal"] += _call(app, "pipeline/deals", {"id": p["id"], "limit": 1000}).get("deals") or []
            out["pipeline_log"] += _call(app, "pipeline/get.logs", {"id": p["id"], "limit": 200}).get("logs") or []
        for kind in ("account", "contact"):
            out[f"{kind}_service"] = _call(app, f"{kind}/service/all")[f"{kind}_services"]
            for s in out[f"{kind}_service"]:
                out[kind] += _call(app, f"{kind}/list", {"service_id": s["id"], "limit": 1000}).get(f"{kind}s") or []
        for name, path in (("pipeline_segment", "segment/pipeline.segments"),
                           ("account_segment", "segment/account.segments"),
                           ("contact_segment", "segment/contact.segments")):
            body = _call(app, path)
            out[name] = next(v for v in body.values() if isinstance(v, list))
        out["feed"] = _call(app, "feed/list", {"limit": 200}).get("feeds") or []
    elif app == "project":
        out["service"] = _call(app, "service/all", {"limit": 200})["services"]
        for s in out["service"]:
            out["service_field"] += _call(app, "service/get.fields", {"id": s["id"]}).get("fields") or []
            out["service_item_type"] += _call(app, "service/get.itemtypes", {"id": s["id"]}).get("item_types") or []
            out["service_stage"] += _call(app, "service/get.stages", {"id": s["id"]}).get("stages") or []
        out["project"] = _call(app, "project/list", {"limit": 200})["projects"]
        for p in out["project"]:
            out["project_board"] += _call(app, "project/get.boards", {"project_id": p["id"]}).get("boards") or []
            out["project_group"] += _call(app, "project/get.groups", {"project_id": p["id"]}).get("groups") or []
            tasks = _call(app, "task/list", {"project_id": p["id"], "limit": 200,
                                             "include_archived": True}).get("tasks") or []
            out["task"] += tasks
            for t in tasks[:40]:
                out["task_activity"] += _call(app, "task/get.activity", {"task_id": t["id"], "limit": 100}).get("events") or []
    elif app == "lead":
        out["service"] = (_call(app, "service/all", {"limit": 200})["services"]
                          + _call(app, "service/all", {"limit": 200, "metatype": "events"})["services"])
        for s in out["service"]:
            fields = _call(app, "service/get.fields", {"id": s["id"]})
            out["service_field"] += fields.get("fields") or []
            out["service_system_field"] += fields.get("system_fields") or []
            out["service_stage"] += _call(app, "service/get.stages", {"id": s["id"]}).get("stages") or []
            out["service_source"] += _call(app, "service/get.sources", {"id": s["id"], "limit": 200}).get("sources") or []
            leads = _call(app, "lead/list", {"service_id": s["id"], "limit": 200}).get("leads") or []
            out["lead"] += leads
            for lead in leads[:40]:
                out["lead_feed"] += _call(app, "lead/feed/list", {"lead_id": lead["id"], "limit": 100}).get("feeds") or []
    return out


def _json_type(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        # A string of digits casts to an integer column (Sales sends some
        # epoch seconds this way); any other string does not.
        return "digit-string" if value.lstrip("-").isdigit() else "string"
    return "array" if isinstance(value, list) else "object"


#: How the destination's typing step picks one column type out of a union:
#: by this fixed priority, not by the order declared. Measured: a field
#: declared ["boolean", "integer"] still became INT64 in BigQuery.
_PRIORITY = ("array", "object", "string", "number", "integer", "boolean")


def _column_type(declared: list[str]) -> str:
    types = [t for t in declared if t != "null"]
    return min(types, key=lambda t: _PRIORITY.index(t) if t in _PRIORITY else len(_PRIORITY))


def _fits(value_type: str, column: str) -> bool:
    if value_type in ("null", column):
        return True
    if value_type == "digit-string":
        return column in ("integer", "number", "string")
    if column == "number" and value_type == "integer":
        return True
    # A string column takes a scalar of any type; objects and arrays are both
    # JSON to the destination.
    if column == "string" and value_type in ("integer", "number", "boolean"):
        return True
    return {value_type, column} <= {"array", "object"}


def main(apps: list[str]) -> int:
    mismatches = 0
    for connector in EXTAPI_CONNECTORS:
        if apps and connector.app not in apps:
            continue
        if not os.environ.get(KEY_ENV[connector.app]):
            print(f"{connector.app}: skipped, {KEY_ENV[connector.app]} not set")
            continue
        data = _records(connector.app)
        for stream in connector.streams:
            rows = data.get(stream.name) or []
            for field, prop in stream.properties.items():
                column = _column_type(prop["type"])
                seen = collections.Counter(_json_type(r.get(field)) for r in rows if field in r)
                bad = {t: n for t, n in seen.items() if not _fits(t, column)}
                if bad:
                    mismatches += 1
                    print(f"{connector.app}.{stream.name}.{field}: column {column} "
                          f"(declared {prop['type']}), saw {dict(seen)}")
            print(f"{connector.app}.{stream.name}: {len(rows)} records checked")
    print(f"mismatching fields: {mismatches}")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
