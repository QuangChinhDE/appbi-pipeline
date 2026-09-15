"""Trigger the demo pipelines and wait for them, reporting what each one did.

    python run.py                     # every demo pipeline, once
    python run.py "Web events"        # just the ones whose name contains this

Runs are real: a connector container starts, reads Postgres and writes into
the warehouse. That is the point -- the row counts the Overview reasons about
have to come from somewhere, and a fabricated number would make the report a
picture of a report.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _api import connect  # noqa: E402

api = connect()

DEMO = ['Shop orders to warehouse', 'Finance invoices to warehouse',
        'Web events to warehouse', 'CRM leads to warehouse']
TERMINAL = {'SUCCEEDED', 'FAILED', 'CANCELLED', 'PARTIAL_SUCCESS'}


def pipelines() -> dict[str, dict]:
    payload = api.get('/pipelines')
    items = payload['items'] if isinstance(payload, dict) and 'items' in payload else payload
    return {p['name']: p for p in items}


def wait(run_id: str, label: str, timeout: int = 900) -> dict:
    started = time.time()
    last = None
    while time.time() - started < timeout:
        run = api.get(f'/runs/{run_id}')
        status = run.get('status')
        if status != last:
            print(f'    {label}: {status}', flush=True)
            last = status
        if status in TERMINAL:
            return run
        time.sleep(4)
    print(f'    {label}: still running after {timeout}s, moving on')
    return api.get(f'/runs/{run_id}')


if __name__ == '__main__':
    wanted = sys.argv[1] if len(sys.argv) > 1 else None
    names = [n for n in DEMO if not wanted or wanted.lower() in n.lower()]
    have = pipelines()

    for name in names:
        pipeline = have.get(name)
        if pipeline is None:
            print(f'  ?  {name} does not exist')
            continue
        print(f'  ▸ {name}')
        run = api.post(f'/pipelines/{pipeline["id"]}/runs', json={})
        final = wait(run['id'], name)
        print(f'    -> {final.get("status")}  '
              f'rows={final.get("records_synced") or final.get("rows_synced")}  '
              f'bytes={final.get("bytes_synced")}  '
              f'error={(final.get("error") or {}).get("code") if isinstance(final.get("error"), dict) else final.get("error_code")}')
