"""Create the Postgres sources and the warehouse, then discover what they hold.

Everything goes through the product's own endpoints, so whatever this builds
is something a customer could have built the same way -- and anything the
product would reject, this cannot sneak past.

Idempotent by name: run it twice and it adopts what it made the first time.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _api import _env, connect  # noqa: E402

api = connect()

PG = {
    'host': 'postgres',
    'port': 5432,
    'username': 'appbi',
    'password': _env('POSTGRES_PASSWORD'),
    'ssl_mode': {'mode': 'disable'},
    'tunnel_method': {'tunnel_method': 'NO_TUNNEL'},
}

SOURCES = [
    ('Shop database', 'The order book: customers, products, orders and their line items.', ['shop']),
    ('Finance ledger', 'Invoices and the payments against them.', ['finance']),
    ('Web analytics', 'Clickstream from the storefront. Append-only and by far the largest.', ['ops']),
    ('CRM', 'Leads and the activity logged against them.', ['crm']),
]

DESTINATION = (
    'Analytics warehouse',
    'Where every pipeline lands before Transform builds the reporting tables.',
    'raw',
)


def by_name(kind: str) -> dict[str, dict]:
    payload = api.get(f'/{kind}')
    items = payload['items'] if isinstance(payload, dict) and 'items' in payload else payload
    return {item['name']: item for item in items}


def ensure_source(name: str, description: str, schemas: list[str]) -> dict:
    existing = by_name('sources')
    if name in existing:
        print(f'  source exists   {name}')
        return existing[name]
    created = api.post('/sources', json={
        'name': name,
        'description': description,
        'connector_key': 'source-postgres',
        'configuration': {
            **{k: v for k, v in PG.items() if k != 'password'},
            'database': 'demo_source',
            'schemas': schemas,
            'replication_method': {'method': 'Xmin'},
        },
        'credentials': {'password': PG['password']},
        'test_before_save': True,
    })
    print(f'  source created  {name}  ({created["id"]})')
    return created


def ensure_destination(name: str, description: str, schema: str) -> dict:
    existing = by_name('destinations')
    if name in existing:
        print(f'  dest exists     {name}')
        return existing[name]
    created = api.post('/destinations', json={
        'name': name,
        'description': description,
        'connector_key': 'destination-postgres',
        'configuration': {
            **{k: v for k, v in PG.items() if k != 'password'},
            'database': 'demo_warehouse',
            'schema': schema,
        },
        'credentials': {'password': PG['password']},
        'test_before_save': True,
    })
    print(f'  dest created    {name}  ({created["id"]})')
    return created


if __name__ == '__main__':
    print('── actors ──')
    sources = {name: ensure_source(name, desc, schemas) for name, desc, schemas in SOURCES}
    warehouse = ensure_destination(*DESTINATION)

    print('\n── discovery ──')
    catalogue = {}
    for name, source in sources.items():
        snap = api.post(f'/sources/{source["id"]}/discover')
        streams = snap.get('streams') or []
        catalogue[name] = {
            'source_id': source['id'],
            'snapshot_id': snap.get('id'),
            'streams': [
                {
                    'name': s.get('name'),
                    'namespace': s.get('namespace'),
                    'sync_modes': s.get('supported_sync_modes'),
                    'cursors': s.get('default_cursor_field') or s.get('cursor_fields'),
                    'pk': s.get('source_defined_primary_key') or s.get('primary_key_fields'),
                }
                for s in streams
            ],
        }
        print(f'  {name}: {len(streams)} streams -> '
              f'{[s.get("name") for s in streams]}')

    out = Path(__file__).parent / 'catalogue.json'
    out.write_text(json.dumps(
        {'warehouse_id': warehouse['id'], 'sources': catalogue}, indent=2), encoding='utf-8')
    print(f'\nwrote {out.name}')
