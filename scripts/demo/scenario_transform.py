"""A Transform project that breaks the way Transform projects really break.

    python scenario_transform.py build   # a marketing project that works
    python scenario_transform.py break   # upstream drops a column it depends on
    python scenario_transform.py fix     # put the column back

Nobody writes a broken model. What happens is that a model written against a
column stops compiling months later because the column went away upstream --
and the dashboard it feeds keeps serving yesterday's numbers without saying so.
That is the case the Transform card on the Overview exists to catch, so that is
the case staged here: the model is correct when written and the world changes
underneath it.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _api import connect  # noqa: E402

api = connect()
NAME = 'Marketing attribution'
TERMINAL = {'SUCCEEDED', 'FAILED', 'CANCELLED', 'ERROR', 'PARTIAL_SUCCESS'}

FILES = {
    'models/sources.yml': """
version: 2

sources:
  - name: ops
    schema: ops
    tables:
      - name: web_events
""",
    'models/attribution/stg_sessions.sql': """
{{ config(materialized='table') }}

-- One row per session, with the campaign that brought it in.
select
    session_id,
    min(occurred_at)                                   as started_at,
    max(occurred_at)                                   as ended_at,
    count(*)                                           as events,
    max(utm_source)                                    as utm_source,
    max(device)                                        as device,
    max(country)                                       as country,
    sum(case when event_type = 'purchase' then 1 else 0 end) as purchases
from {{ source('ops', 'web_events') }}
group by session_id
""",
    'models/attribution/mart_channel_performance.sql': """
{{ config(materialized='table') }}

-- Sessions and conversions by acquisition channel. This is the model behind
-- the marketing dashboard, which is why it being quietly stale matters.
select
    coalesce(utm_source, 'direct') as channel,
    device,
    count(*)                       as sessions,
    sum(events)                    as events,
    sum(purchases)                 as purchases,
    round(
        100.0 * sum(case when purchases > 0 then 1 else 0 end) / nullif(count(*), 0),
        2
    )                              as conversion_rate
from {{ ref('stg_sessions') }}
group by 1, 2
""",
}

def psql(database: str) -> list[str]:
    return ['docker', 'compose', '-p', 'appbi-pipeline', 'exec', '-T', 'postgres',
            'psql', '-U', 'appbi', '-d', database, '-v', 'ON_ERROR_STOP=1', '-c']


def sql(statement: str, database: str = 'demo_source') -> None:
    result = subprocess.run(psql(database) + [statement], capture_output=True, text=True,
                            cwd=str(Path(__file__).resolve().parents[2]))
    if result.returncode:
        raise SystemExit(result.stderr.strip()[:400])
    print('  db:', result.stdout.strip().splitlines()[-1] if result.stdout.strip() else 'ok')


def projects() -> dict[str, dict]:
    raw = api.get('/transforms')
    items = raw['items'] if isinstance(raw, dict) else raw
    return {p['name']: p for p in items if isinstance(p, dict)}


def connection_id(name: str) -> str:
    for c in api.get('/transforms/connections'):
        if c['name'] == name:
            return c['id']
    raise SystemExit(f'no transform connection called {name!r}')


def invoke(pid: str, command: str = 'build') -> dict:
    inv = api.post(f'/transforms/{pid}/invocations', json={'command': command})
    started = time.time()
    while time.time() - started < 900:
        cur = api.get(f'/transform-invocations/{inv["id"]}')
        if cur.get('status') in TERMINAL:
            return cur
        time.sleep(4)
    return cur


if __name__ == '__main__':
    action = sys.argv[1] if len(sys.argv) > 1 else 'build'

    if action == 'build':
        # The column exists when the models are written. That is the point.
        sql("ALTER TABLE ops.web_events ADD COLUMN IF NOT EXISTS utm_source text")
        sql("UPDATE ops.web_events SET utm_source = "
            "(ARRAY['google','facebook','tiktok','email','direct'])[1 + (random() * 4)::int] "
            "WHERE utm_source IS NULL")

        have = projects()
        if NAME in have:
            project = have[NAME]
            print(f'  project exists  {NAME}')
        else:
            project = api.post('/transforms', json={
                'name': NAME,
                'description': 'Sessions and channel performance for the marketing dashboard.',
                'connection_id': connection_id('Analytics warehouse'),
                'dbt_project_name': 'marketing_attribution',
                'development_schema': 'dbt_marketing',
                'production_schema': 'marketing',
                'source_schema': 'ops',
                'with_examples': False,
            })
            print(f'  project created {NAME}')
        api.post(f'/transforms/{project["id"]}/files/batch', json={
            'changes': [{'path': p, 'content': b.lstrip('\n')} for p, b in FILES.items()],
        })
        print(f'  wrote {len(FILES)} files')
        print('  NOTE: re-run the "Web events to warehouse" pipeline so the new '
              'column reaches the warehouse, then build.')

    elif action == 'buildrun':
        result = invoke(projects()[NAME]['id'])
        print(f'  build -> {result.get("status")}')

    elif action == 'break':
        # Marketing retires the parameter; nobody tells the analytics team.
        #
        # Dropped in the warehouse as well as the source, because that is where
        # the model reads. Removing it only at the source changes nothing for
        # dbt until the removal is approved and lands -- which is exactly what
        # this stands in for.
        sql("ALTER TABLE ops.web_events DROP COLUMN IF EXISTS utm_source")
        sql("ALTER TABLE ops.web_events DROP COLUMN IF EXISTS utm_source", 'demo_warehouse')
        print('  utm_source is gone from both the source and the warehouse')

    elif action == 'fix':
        sql("ALTER TABLE ops.web_events ADD COLUMN IF NOT EXISTS utm_source text",
            'demo_warehouse')
        sql("ALTER TABLE ops.web_events ADD COLUMN IF NOT EXISTS utm_source text")
        sql("UPDATE ops.web_events SET utm_source = "
            "(ARRAY['google','facebook','tiktok','email','direct'])[1 + (random() * 4)::int] "
            "WHERE utm_source IS NULL")
        print('  column restored')

    else:
        raise SystemExit(f'unknown action {action!r}')
