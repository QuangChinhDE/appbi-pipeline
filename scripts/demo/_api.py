"""A logged-in session against a running AppBI API.

Everything in this directory goes through the product's own endpoints rather
than through SQL, so whatever it builds is something a customer could have
built the same way -- and anything the product would reject, these cannot
sneak past. That is the whole reason the demo data is worth having: a health
page is only tested by signal it did not help produce.

Credentials come from `.env` and are never printed.

    APPBI_API        override the base URL (default http://127.0.0.1:8011/api/v1)
    APPBI_WORKSPACE  work against this workspace id (default: the first one)

THE DEMO, END TO END

Build it once, in this order. Every step is idempotent, so a second run adopts
what the first one made rather than duplicating it.

    psql -f source_data.sql   into demo_source   four business domains
    psql -f erp_data.sql      into demo_source   a fifth, for the failure below
    python build_actors.py                       Postgres sources + warehouse
    python build_pipelines.py                    four pipelines, four shapes
    python run.py                                sync them all, for real
    python build_transform.py                    staging + three marts
    python run_transform.py                      dbt build
    python report.py                             what the Overview now says

THE SCENARIOS

Each stages a real fault -- no run row is ever invented -- and each can be put
back. They exist because the Overview's cards cannot be trusted until they have
been seen answering signal they did not help produce.

    scenario_erp.py build | break | fail | fix
        A credential rotated at the source and not updated here. Three
        pipelines stop for one reason, which is the case the issue grouping
        exists for: the page should show one card naming the credential, not
        three cards naming three pipelines. Left broken long enough, the three
        also miss their freshness deadlines -- and that must *not* become a
        fourth card, because it is the same incident.

    scenario_transform.py build | break | buildrun | fix
        A model written against a column that later goes away upstream. The
        dashboard keeps serving yesterday's numbers without saying so, which is
        what the Transform card is for.

    A volume anomaly needs no script: run one full-refresh pipeline five times,
    delete most of the rows at the source, and run it once more. The run
    succeeds, and the Overview should still object -- a successful run is not
    the same as correct data.

Clearing everything: `scenario_erp.py fix`, `scenario_transform.py fix`, then
`run.py` to let the pipelines recover. The page follows within a minute; it
reads live state rather than stored verdicts.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get('APPBI_API', 'http://127.0.0.1:8011/api/v1')


def _env(name: str) -> str:
    """Read one value out of .env without echoing it anywhere."""
    env_file = ROOT / '.env'
    if not env_file.exists():
        raise SystemExit(f'no .env at {env_file}')
    for line in env_file.read_text(encoding='utf-8').splitlines():
        if line.startswith(f'{name}='):
            return line.split('=', 1)[1].strip()
    raise SystemExit(f'{name} is not in .env')


class Api:
    def __init__(self, workspace_id: str | None = None):
        self.s = requests.Session()
        self.workspace_id = workspace_id
        r = self.s.post(f'{BASE}/auth/login', json={
            'email': _env('SEED_ADMIN_EMAIL'), 'password': _env('SEED_ADMIN_PASSWORD'),
        })
        if r.status_code >= 400:
            raise SystemExit(f'login failed: {r.status_code} {r.text[:200]}')
        self.me = r.json()

    def call(self, method: str, path: str, **kw):
        headers = {'X-Workspace-Id': self.workspace_id} if self.workspace_id else {}
        r = self.s.request(method, f'{BASE}{path}', headers=headers, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f'{method} {path} -> {r.status_code}\n{r.text[:600]}')
        return r.json() if r.text else None

    def get(self, path, **kw): return self.call('GET', path, **kw)
    def post(self, path, **kw): return self.call('POST', path, **kw)
    def patch(self, path, **kw): return self.call('PATCH', path, **kw)

    def items(self, path: str) -> list:
        """List endpoints return either a bare list or a paged envelope."""
        payload = self.get(path)
        return payload['items'] if isinstance(payload, dict) and 'items' in payload else payload


def workspace_id() -> str:
    """Which workspace to build into: $APPBI_WORKSPACE, or the first one."""
    chosen = os.environ.get('APPBI_WORKSPACE')
    if chosen:
        return chosen
    probe = Api()
    workspaces = probe.me.get('workspaces') or []
    if not workspaces:
        raise SystemExit('this account has no workspace')
    return workspaces[0]['id']


def connect() -> Api:
    """The session every script in this directory starts with."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from _console import force_utf8
    force_utf8()
    return Api(workspace_id())
