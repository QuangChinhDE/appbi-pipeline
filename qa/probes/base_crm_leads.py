"""Measure the Base CRM Leads API, and re-check what the connector claims.

`crm_leads.py` documents its response side as measured rather than assumed, and
this is what measured it. Re-run it whenever Base changes something, or before
promoting the connector after an edit: every assertion in the module docstring
has a line here that produces the number.

What a screenshot of the request contract cannot say, and this asks:

* the JSON key each collection sits under, and the fields on a record
* whether `page` really pages, where it starts, and whether `limit` does
  anything
* whether `start_time` / `end_time` filter, and what `time_filter_key` switches
  between -- the answer that decides whether an edited lead ever comes back
* whether feeds carry a timestamp, so the substream can have a real cursor
* the request rate the token is allowed

Usage:

    python qa/probes/base_crm_leads.py secrets/base-crm-leads.json [--quota]

where the file holds `{"access_token": "...", "password": "..."}`.

`--quota` measures the request cap, and there is only one way to measure it:
send until Base refuses. That leaves the token throttled for the rest of the
minute, so it is off by default -- running the probe should not break whatever
uses the token next.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://apis.basecrm.vn/leads/"
FUTURE, LATER = "1900000000", "1910000000"


def post(creds: dict, path: str, form: dict | None = None) -> dict:
    body = urllib.parse.urlencode(
        {"access_token": creds["access_token"], "password": creds["password"],
         **(form or {})}).encode()
    request = urllib.request.Request(
        BASE + path, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as refusal:
        # The refusal *is* the answer here. Base says "Quota exceeded" with
        # HTTP 400 and the reason in the body, so raising on the status line
        # throws away the only part that identifies what happened -- which is
        # exactly the mistake that made a quota look like a broken request.
        raw = refusal.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except ValueError:
        return {"_khong_phai_json": raw[:300]}


def collection(payload: dict) -> tuple[str, list]:
    """The key holding the records, whatever Base decided to call it.

    Not guessed from the entity name: `lead/services` answers under `services`,
    where every other Base collection would have said `lead_services`. A
    connector built on that guess reads zero records and reports success.
    """
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
    if key != "services":
        print(f"  !! connector dang doc khoa 'services'; API tra ve {key!r}")
    print(f"  vai truong      : {sorted(services[0])[:12]}")

    # The service with the most leads, so pagination has something to page.
    counts = []
    for service in services:
        rows = post(creds, "lead/list",
                    {"service_id": str(service["id"]), "page": "1"}).get("leads") or []
        counts.append((len(rows), str(service["id"]), service.get("name")))
    counts.sort(reverse=True)
    biggest = counts[0][1]
    print(f"\n== lead/list (service_id={biggest}, dich vu dong nhat) ==")

    page, seen, pages = 1, set(), []
    while page <= 60:
        rows = post(creds, "lead/list",
                    {"service_id": biggest, "page": str(page)}).get("leads") or []
        fresh = {r["id"] for r in rows} - seen
        pages.append(len(rows))
        if not rows:
            break
        if not fresh:
            print("  !! phan trang khong tien: trang sau lap lai trang truoc")
            break
        seen |= {r["id"] for r in rows}
        page += 1
    print(f"  khoa collection : 'leads'")
    print(f"  lat trang       : {pages} -> {len(seen)} id khac nhau")

    first = post(creds, "lead/list", {"service_id": biggest, "page": "1"}).get("leads") or []
    zero = post(creds, "lead/list", {"service_id": biggest, "page": "0"}).get("leads") or []
    same = {r["id"] for r in zero} == {r["id"] for r in first}
    print(f"  page=0          : {'lap lai trang 1 -> dem tu 1' if same else 'khac trang 1'}")
    for limit in ("5", "500", "1000"):
        n = len(post(creds, "lead/list",
                     {"service_id": biggest, "page": "1", "limit": limit}).get("leads") or [])
        print(f"  limit={limit:<5}     : {n} ban ghi"
              f"{'   (bi bo qua)' if n == len(first) else ''}")

    print(f"\n== time_filter_key: cot nao duoc so sanh ==")
    threshold = max(r["since"] for r in first) + 1   # cao hon moi 'since'
    print(f"  nguong {threshold} (cao hon moi 'since' cua {len(first)} lead)")
    for filter_key in ("since", "last_update", "last_update_stage", "last_update_status"):
        served = len(post(creds, "lead/list",
                          {"service_id": biggest, "page": "1",
                           "start_time": str(threshold), "end_time": FUTURE,
                           "time_filter_key": filter_key}).get("leads") or [])
        counted = sum(1 for r in first if (r.get(filter_key) or 0) >= threshold)
        print(f"    key={filter_key:<20} server={served:<4} dem tay={counted:<4} "
              f"{'khop' if served == counted else 'LECH'}")
    default = len(post(creds, "lead/list",
                       {"service_id": biggest, "page": "1",
                        "start_time": str(threshold), "end_time": FUTURE}).get("leads") or [])
    print(f"    khong gui key        server={default}  -> mac dinh la "
          f"{'since (nen phai ghim last_update)' if default == 0 else '?'}")

    print(f"\n== lead/feed/list ==")
    chosen, feeds = None, []
    for lead in first[:60]:
        found = post(creds, "lead/feed/list", {"lead_id": str(lead["id"])}).get("feeds") or []
        if len(found) > len(feeds):
            chosen, feeds = lead, found
        if len(feeds) >= 10:
            break
    if not chosen:
        print("  khong lead nao trong 60 lead dau co feed")
    else:
        print(f"  lead {chosen['id']}: {len(feeds)} feed, khoa collection 'feeds'")
        page_two = post(creds, "lead/feed/list",
                        {"lead_id": str(chosen["id"]), "page": "2"}).get("feeds") or []
        print(f"  page=2          : {len(page_two)} "
              f"({'bang trang 1 -> khong phan trang' if len(page_two) == len(feeds) else 'co phan trang'})")
        for tag, extra in (("start/end", {"start_time": FUTURE, "end_time": LATER}),
                           ("stime/etime", {"stime": FUTURE, "etime": LATER}),
                           ("cap + time_filter_key", {"start_time": FUTURE, "end_time": LATER,
                                                      "time_filter_key": "last_update"})):
            n = post(creds, "lead/feed/list",
                     {"lead_id": str(chosen["id"]), **extra}).get("feeds")
            n = len(n) if isinstance(n, list) else 0
            print(f"    {tag:<22} -> {n}/{len(feeds)} "
                  f"{'CO LOC' if n < len(feeds) else 'khong loc'}")
        have = sum(1 for f in feeds if f.get("last_update"))
        print(f"  last_update co tren {have}/{len(feeds)} feed"
              f"{'  -> cursor phia client la that' if have == len(feeds) else ''}")

    if "--quota" not in sys.argv:
        print()
        print("== han muc goi: bo qua (them --quota de do) ==")
        print("  Cach do duy nhat la ban lien tuc cho den khi bi tu choi,"
              " nen no lam token bi chan het phut do.")
        return 0

    print(f"\n== han muc goi ==")
    # `lead_feed` issues one request per lead, so the cap is the thing that
    # decides whether the stream finishes. Found the hard way: HTTP 400 with
    # `{"code": 0, "message": "Quota exceeded: 100 req/min"}`.
    started, refused, sent = time.monotonic(), None, 0
    while time.monotonic() - started < 75 and sent < 140:
        answer = post(creds, "lead/services")
        sent += 1
        if answer.get("code") == 0 and "quota" in str(answer.get("message", "")).lower():
            refused = (sent, answer["message"])
            break
    if refused:
        print(f"  bi tu choi sau {refused[0]} request trong "
              f"{time.monotonic() - started:.0f}s: {refused[1]!r}")
    else:
        print(f"  {sent} request trong {time.monotonic() - started:.0f}s, chua bi tu choi")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    raise SystemExit(main(sys.argv[1]))
