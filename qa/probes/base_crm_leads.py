"""Measure the Base CRM Leads API before a connector is written against it.

Three endpoints are documented (`lead/services`, `lead/list`, `lead/feed/list`)
and the documentation gives their request fields but not their responses. What a
connector needs and a screenshot cannot say:

* the JSON key each collection lives under, and the fields on a record
* whether `page` really pages, and where it starts
* whether `start_time` / `end_time` filter at all, and what `time_filter_key`
  actually switches between
* whether feeds carry a timestamp, so the substream can have a real cursor
* whether any two endpoints return the same rows, which is the check that kept
  three duplicate streams out of Base CRM Sales

Run once a Leads token exists:

    python qa/probes/base_crm_leads.py secrets/base-crm-leads.json

where the file holds `{"access_token": "...", "password": "..."}`.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request

BASE = "https://apis.basecrm.vn/leads/"


def post(creds: dict, path: str, form: dict | None = None):
    body = urllib.parse.urlencode(
        {"access_token": creds["access_token"], "password": creds["password"],
         **(form or {})}).encode()
    request = urllib.request.Request(
        BASE + path, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=180) as response:
        raw = response.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except ValueError:
        return {"_khong_phai_json": raw[:300]}


def collection(payload: dict) -> tuple[str, list]:
    """The key holding the records, whatever Base decided to call it."""
    for key, value in payload.items():
        if isinstance(value, list) and value:
            return key, value
    return "", []


def main(path_to_creds: str) -> int:
    creds = json.load(open(path_to_creds, encoding="utf-8"))

    print("== lead/services ==")
    payload = post(creds, "lead/services")
    if payload.get("code") == 0:
        print(f"  tu choi: {payload.get('message')!r}")
        return 1
    key, services = collection(payload)
    print(f"  khoa collection : {key!r}   so ban ghi: {len(services)}")
    print(f"  truong          : {sorted(services[0])}" if services else "  rong")
    print(f"  mau             : {json.dumps(services[0], ensure_ascii=False)[:300]}")

    service_id = str(services[0]["id"])
    print(f"\n== lead/list (service_id={service_id}) ==")
    first = post(creds, "lead/list", {"service_id": service_id, "page": "1"})
    key_leads, leads = collection(first)
    print(f"  khoa collection : {key_leads!r}   trang 1: {len(leads)} ban ghi")
    if leads:
        print(f"  truong          : {sorted(leads[0])}")
        for stamp in ("last_update", "since", "last_update_stage", "last_update_status"):
            have = sum(1 for r in leads if r.get(stamp) is not None)
            print(f"    {stamp:<20} co tren {have}/{len(leads)} ban ghi")

    print("  -- phan trang --")
    ids_page = {}
    for page in ("1", "2"):
        rows = collection(post(creds, "lead/list",
                               {"service_id": service_id, "page": page}))[1]
        ids_page[page] = {r["id"] for r in rows}
        print(f"    page={page}: {len(rows)} ban ghi")
    if ids_page.get("1") and ids_page.get("2"):
        overlap = len(ids_page["1"] & ids_page["2"])
        print(f"    trung nhau giua 2 trang: {overlap} "
              f"({'PHAN TRANG HONG' if overlap else 'ok'})")
    page0 = collection(post(creds, "lead/list",
                            {"service_id": service_id, "page": "0"}))[1]
    print(f"    page=0: {len(page0)} ban ghi "
          f"({'giong page 1 -> dem tu 1' if {r['id'] for r in page0} == ids_page.get('1') else 'khac page 1'})")
    print(f"    khong gui page: {len(collection(post(creds, 'lead/list', {'service_id': service_id}))[1])} ban ghi")
    print(f"    gui limit=5   : {len(collection(post(creds, 'lead/list', {'service_id': service_id, 'page': '1', 'limit': '5'}))[1])} ban ghi "
          f"(bang trang 1 nghia la limit bi bo qua)")

    print("  -- bo loc thoi gian --")
    n = len(ids_page.get("1", ()))
    FUTURE, LATER = "1900000000", "1910000000"
    for label, form in (
        ("chi start_time=2030", {"start_time": FUTURE}),
        ("cap start+end=2030", {"start_time": FUTURE, "end_time": LATER}),
        ("cap + key=last_update", {"start_time": FUTURE, "end_time": LATER,
                                   "time_filter_key": "last_update"}),
        ("cap + key=since", {"start_time": FUTURE, "end_time": LATER,
                             "time_filter_key": "since"}),
    ):
        rows = collection(post(creds, "lead/list",
                               {"service_id": service_id, "page": "1", **form}))[1]
        print(f"    {label:<24} -> {len(rows)}/{n} "
              f"({'CO LOC' if len(rows) < n else 'khong loc'})")

    print("\n== lead/feed/list ==")
    found = None
    for lead in leads[:40]:
        payload = post(creds, "lead/feed/list", {"lead_id": str(lead["id"])})
        key_feed, feeds = collection(payload)
        if feeds:
            found = (lead, key_feed, feeds)
            break
    if not found:
        print("  khong lead nao trong 40 lead dau co feed")
    else:
        lead, key_feed, feeds = found
        print(f"  lead {lead['id']}: khoa collection {key_feed!r}, {len(feeds)} ban ghi")
        print(f"  truong          : {sorted(feeds[0])}")
        for stamp in ("last_update", "since", "created_at"):
            have = sum(1 for r in feeds if r.get(stamp) is not None)
            print(f"    {stamp:<20} co tren {have}/{len(feeds)} ban ghi")
        for label, form in (("start/end=2030", {"start_time": FUTURE, "end_time": LATER}),
                            ("stime/etime=2030", {"stime": FUTURE, "etime": LATER})):
            rows = collection(post(creds, "lead/feed/list",
                                   {"lead_id": str(lead["id"]), **form}))[1]
            print(f"    {label:<20} -> {len(rows)}/{len(feeds)} "
                  f"({'CO LOC' if len(rows) < len(feeds) else 'khong loc'})")

    print("\n== trung lap output ==")
    lead_ids = {r["id"] for r in leads}
    print(f"  lead/list tra ve {len(lead_ids)} id; "
          f"lead/services tra ve {len(services)} dich vu -- khong giao nhau ve thuc the")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    raise SystemExit(main(sys.argv[1]))
