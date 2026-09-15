"""Pick up a schema change on a pipeline: rediscover, read the diff, approve.

    python rediscover.py "Web events to warehouse"

A column added upstream does not reach the warehouse on its own, and that is
deliberate -- a pipeline syncs the shape it was approved with, so nobody's
report changes underneath them without a person saying yes. This is the yes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _api import connect  # noqa: E402

api = connect()


def pipelines() -> dict[str, dict]:
    payload = api.get('/pipelines')
    items = payload['items'] if isinstance(payload, dict) and 'items' in payload else payload
    return {p['name']: p for p in items}


if __name__ == '__main__':
    name = sys.argv[1]
    pipeline = pipelines()[name]
    pid = pipeline['id']

    snap = api.post(f'/pipelines/{pid}/rediscover')
    print(f'  rediscovered: snapshot {snap["id"]}')

    diff = api.get(f'/pipelines/{pid}/schema-diff')
    print('  diff:', json.dumps(diff, ensure_ascii=False)[:700])

    approved = api.post(f'/pipelines/{pid}/schema-approve', json={
        'snapshot_id': snap['id'],
    })
    print('  approved:', json.dumps(approved, ensure_ascii=False)[:300])
