"""Build the Transform project and wait, then say what dbt actually did.

    python run_transform.py [command]      # default: build

A Transform project that has never been invoked has no health to report, so
this is not optional decoration -- the Overview's Transform card reads the
result of runs like these.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _api import connect  # noqa: E402

api = connect()
NAME = 'Warehouse marts'
TERMINAL = {'SUCCEEDED', 'FAILED', 'CANCELLED', 'ERROR', 'PARTIAL_SUCCESS'}


def project_id(name: str) -> str:
    raw = api.get('/transforms')
    items = raw['items'] if isinstance(raw, dict) else raw
    for p in items:
        if isinstance(p, dict) and p['name'] == name:
            return p['id']
    raise SystemExit(f'no transform project called {name!r}')


if __name__ == '__main__':
    command = sys.argv[1] if len(sys.argv) > 1 else 'build'
    pid = project_id(NAME)
    inv = api.post(f'/transforms/{pid}/invocations', json={'command': command})
    print(f'  {command} -> invocation {inv["id"]}')

    last = None
    started = time.time()
    while time.time() - started < 1200:
        cur = api.get(f'/transform-invocations/{inv["id"]}')
        status = cur.get('status')
        if status != last:
            print(f'    {status}', flush=True)
            last = status
        if status in TERMINAL:
            break
        time.sleep(4)

    print(f'  final: {cur.get("status")}  '
          f'models={cur.get("models_built") or cur.get("model_count")}  '
          f'tests={cur.get("tests_passed")}/{cur.get("tests_run")}  '
          f'duration={cur.get("duration_seconds")}s')
    if cur.get('status') not in ('SUCCEEDED',):
        logs = api.get(f'/transform-invocations/{inv["id"]}/logs')
        text = logs if isinstance(logs, str) else str(logs)
        print('  ── tail of logs ──')
        print('\n'.join(text.splitlines()[-40:]))
