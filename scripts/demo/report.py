"""Read the Overview the way the page reads it, and print what it says.

Codes rather than sentences, deliberately: this is the contract the browser
renders, and reading it raw is the fastest way to see whether the server did
the thinking or quietly left it to the client.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _api import Api, connect, workspace_id  # noqa: E402

api = Api(sys.argv[1]) if len(sys.argv) > 1 else connect()
h = api.get('/overview')['health']


def metric(m: dict) -> str:
    delta = '' if m['delta'] is None else f" ({m['delta']:+})"
    return f"{m['key']}={m['value']}{delta}[{m['tone']}]"


print(f"STATUS   {h['status']}  |  {h['headline_code']} {h['headline_vars']}")
print('METRICS  ' + '  '.join(metric(m) for m in h['metrics']))

print(f"\nISSUES   {len(h['issues'])}")
for i in h['issues']:
    print(f"  [{i['severity']:8}] {i['kind']:20} {i['title_code']} {i['title_vars']}")
    for field in ('cause', 'impact', 'evidence'):
        if i.get(f'{field}_code'):
            print(f"             {field+':':10}{i[f'{field}_code']} {i[f'{field}_vars']}")
    if i.get('root'):
        print(f"             root:     {i['root']}")
    if i['affected']:
        print(f"             affected: {[a['name'] for a in i['affected']]} "
              f"of {i['affected_total']}")
    print(f"             action:   {i.get('action_code')} -> {i.get('action_href')}")

stages = [(s['stage'], f"{s['healthy']}/{s['total']}", s['problem']) for s in h['stages']]
print(f"\nFRESHNESS  {[(f['name'][:26], f['state']) for f in h['freshness']]}")
print(f"RELIABILITY {[(d['date'][5:], d['succeeded'], d['failed']) for d in h['reliability']]}")
print(f"CAUSES     {[(c['cause'], round(c['share'] * 100)) for c in h['failure_causes']]}")
print(f"STAGES     {stages}")
print(f"VOLUME     {[(v['name'][:24], v['current'], v['baseline']) for v in h['volume']]}")
print(f"DURATION   {[(v['name'][:24], v['current'], v['baseline']) for v in h['duration']]}")
print(f"TRANSFORMS {[(t['name'][:26], t['state'], t.get('detail_code')) for t in h['transforms']]}")
print(f"PLATFORM   {[(p['key'], p['state']) for p in h['platform']]}")
