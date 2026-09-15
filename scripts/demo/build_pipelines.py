"""Four pipelines that differ in the ways the Overview actually reports on.

A report about pipelines is only worth reading when the pipelines are not all
the same, so these vary along every axis the health page looks at: schedule
type and interval (which drives freshness deadlines), sync mode (which drives
whether row counts grow or replace), and volume (which drives the baseline the
anomaly check compares against).

Idempotent by name.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _api import connect  # noqa: E402

api = connect()

CAT = json.loads((Path(__file__).parent / 'catalogue.json').read_text(encoding='utf-8'))
WAREHOUSE = CAT['warehouse_id']


def stream(name: str, namespace: str, cursor: str | None, pk: list[str],
           mode: str = 'incremental', dest: str = 'append_dedup') -> dict:
    s = {
        'name': name,
        'namespace': namespace,
        'selected': True,
        'sync_mode': mode,
        'destination_sync_mode': dest,
        # A primary key is a list of paths, not a list of columns: a key can be
        # composite and a path can be nested. Discovery returns [['id']].
        'primary_key_fields': [[column] for column in pk],
    }
    if mode == 'incremental' and cursor:
        s['cursor_fields'] = [cursor]
    return s


PIPELINES = [
    {
        'name': 'Shop orders to warehouse',
        'description': 'The order book, hourly. Incremental on updated_at so a '
                       'restated order arrives as an update rather than a duplicate.',
        'source': 'Shop database',
        'streams': [
            stream('orders', 'shop', 'updated_at', ['id']),
            stream('order_items', 'shop', 'updated_at', ['id']),
            stream('customers', 'shop', 'updated_at', ['id']),
            stream('products', 'shop', 'updated_at', ['sku']),
        ],
        'schedule': {'type': 'INTERVAL', 'interval_seconds': 3600, 'timezone': 'Asia/Ho_Chi_Minh'},
    },
    {
        'name': 'Finance invoices to warehouse',
        'description': 'Invoices and payments, overnight. Finance closes the day '
                       'before anyone reads the revenue report in the morning.',
        'source': 'Finance ledger',
        'streams': [
            stream('invoices', 'finance', 'updated_at', ['id']),
            stream('payments', 'finance', 'updated_at', ['id']),
        ],
        'schedule': {'type': 'DAILY', 'time_of_day': '02:00', 'timezone': 'Asia/Ho_Chi_Minh'},
    },
    {
        'name': 'Web events to warehouse',
        'description': 'Clickstream every half hour. Append-only: events are facts '
                       'that happened, never corrections of earlier ones.',
        'source': 'Web analytics',
        'streams': [
            stream('web_events', 'ops', 'occurred_at', ['id'], dest='append'),
        ],
        'schedule': {'type': 'INTERVAL', 'interval_seconds': 1800, 'timezone': 'Asia/Ho_Chi_Minh'},
    },
    {
        'name': 'CRM leads to warehouse',
        'description': 'Leads and activities, once a day, replaced whole. The table '
                       'is small and salespeople edit history, so a full refresh is '
                       'cheaper than reasoning about what changed.',
        'source': 'CRM',
        'streams': [
            stream('leads', 'crm', None, ['id'], mode='full_refresh', dest='overwrite'),
            stream('activities', 'crm', None, ['id'], mode='full_refresh', dest='overwrite'),
        ],
        'schedule': {'type': 'DAILY', 'time_of_day': '05:30', 'timezone': 'Asia/Ho_Chi_Minh'},
    },
]


def existing() -> dict[str, dict]:
    payload = api.get('/pipelines')
    items = payload['items'] if isinstance(payload, dict) and 'items' in payload else payload
    return {p['name']: p for p in items}


if __name__ == '__main__':
    have = existing()
    for spec in PIPELINES:
        if spec['name'] in have:
            print(f'  exists   {spec["name"]}')
            continue
        source = CAT['sources'][spec['source']]
        created = api.post('/pipelines', json={
            'name': spec['name'],
            'description': spec['description'],
            'source_id': source['source_id'],
            'destination_id': WAREHOUSE,
            'schema_snapshot_id': source['snapshot_id'],
            'streams': spec['streams'],
            'schedule': spec['schedule'],
            'overlap_policy': 'SKIP_IF_RUNNING',
            'run_first_sync': False,
        })
        print(f'  created  {spec["name"]}  ({created["id"]})')

    print('\n── pipelines now ──')
    for name, p in existing().items():
        sched = p.get('schedule') or {}
        print(f'  {name[:36]:38} {str(sched.get("type")):9} '
              f'{str(sched.get("interval_seconds") or sched.get("time_of_day") or ""):8} '
              f'{p.get("status")}')
