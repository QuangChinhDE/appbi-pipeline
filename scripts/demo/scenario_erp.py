"""The most common failure in this product, staged honestly.

    python scenario_erp.py build     # source + three pipelines, all working
    python scenario_erp.py break     # rotate the password, as the DBA would
    python scenario_erp.py fail      # run them, and watch all three fail alike
    python scenario_erp.py fix       # put the right password back

A credential is rotated at the source system and nobody updates it here. Three
pipelines stop, for one reason. No run row is invented: the source really is
given a password that really does not work, and the connector really does fail
to authenticate -- which is the only way to be sure the Overview's grouping is
reading real signal rather than a fixture shaped to please it.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _api import _env, connect  # noqa: E402

api = connect()

WRONG_PASSWORD = 'rotated-on-2026-09-15-nobody-told-us'
TERMINAL = {'SUCCEEDED', 'FAILED', 'CANCELLED', 'PARTIAL_SUCCESS'}

PIPELINES = [
    ('ERP purchase orders to warehouse', 'purchase_orders', 'updated_at', 1800,
     'Purchase orders every half hour: procurement watches this one all morning.'),
    ('ERP stock movements to warehouse', 'stock_movements', 'updated_at', 1800,
     'Stock in and out, every half hour. The largest of the three.'),
    ('ERP suppliers to warehouse', 'suppliers', 'updated_at', 3600,
     'The supplier master, hourly. Small, and everything else joins to it.'),
]


def listing(kind: str) -> dict[str, dict]:
    return {i['name']: i for i in api.items(f'/{kind}')}


def source_config(password: str) -> dict:
    return {
        'host': 'postgres', 'port': 5432, 'username': 'appbi',
        'database': 'demo_source', 'schemas': ['erp'],
        'ssl_mode': {'mode': 'disable'},
        'tunnel_method': {'tunnel_method': 'NO_TUNNEL'},
        'replication_method': {'method': 'Xmin'},
    }


def build() -> None:
    sources = listing('sources')
    if SOURCE in sources:
        source = sources[SOURCE]
        print(f'  source exists  {SOURCE}')
    else:
        source = api.post('/sources', json={
            'name': SOURCE,
            'description': 'The old ERP. Three teams read from it and none of them own it.',
            'connector_key': 'source-postgres',
            'configuration': source_config(_env('POSTGRES_PASSWORD')),
            'credentials': {'password': _env('POSTGRES_PASSWORD')},
            'test_before_save': True,
        })
        print(f'  source created {SOURCE}')

    snap = api.post(f'/sources/{source["id"]}/discover')
    print(f'  discovered     {[s["name"] for s in snap["streams"]]}')

    warehouse = listing('destinations')['Analytics warehouse']
    have = listing('pipelines')
    for name, table, cursor, interval, description in PIPELINES:
        if name in have:
            print(f'  exists         {name}')
            continue
        api.post('/pipelines', json={
            'name': name,
            'description': description,
            'source_id': source['id'],
            'destination_id': warehouse['id'],
            'schema_snapshot_id': snap['id'],
            'streams': [{
                'name': table, 'namespace': 'erp', 'selected': True,
                'sync_mode': 'incremental', 'destination_sync_mode': 'append_dedup',
                'cursor_fields': [cursor], 'primary_key_fields': [['id']],
            }],
            'schedule': {'type': 'INTERVAL', 'interval_seconds': interval,
                         'timezone': 'Asia/Ho_Chi_Minh'},
            'overlap_policy': 'SKIP_IF_RUNNING',
            'run_first_sync': False,
        })
        print(f'  created        {name}')


def set_password(password: str, label: str) -> None:
    source = listing('sources')[SOURCE]
    api.patch(f'/sources/{source["id"]}', json={
        'configuration': source_config(password),
        'credentials': {'password': password},
        'test_before_save': False,
    })
    print(f'  {label}')


def run_all(wait_each: bool = True) -> None:
    have = listing('pipelines')
    runs = []
    for name, *_ in PIPELINES:
        pipeline = have[name]
        run = api.post(f'/pipelines/{pipeline["id"]}/runs', json={})
        runs.append((name, run['id']))
        print(f'  triggered      {name}')
    if not wait_each:
        return
    for name, run_id in runs:
        started = time.time()
        while time.time() - started < 600:
            run = api.get(f'/runs/{run_id}')
            if run.get('status') in TERMINAL:
                break
            time.sleep(4)
        error = run.get('error') or {}
        print(f'  {run.get("status"):10} {name}  '
              f'category={error.get("category") if isinstance(error, dict) else None} '
              f'code={error.get("code") if isinstance(error, dict) else None}')


if __name__ == '__main__':
    action = sys.argv[1] if len(sys.argv) > 1 else 'build'
    if action == 'build':
        build()
        print('\n── first run, with the right password ──')
        run_all()
    elif action == 'break':
        set_password(WRONG_PASSWORD, 'password rotated at the source; nobody updated it here')
    elif action == 'fail':
        run_all()
    elif action == 'fix':
        set_password(_env('POSTGRES_PASSWORD'), 'credentials restored')
    else:
        raise SystemExit(f'unknown action {action!r}')
