"""A Transform project that turns the four landed domains into reporting tables.

This is the half of the product the Overview used to say nothing about. The
models are deliberately ordinary -- staging views over what the pipelines
landed, then three marts a person would actually open -- because the point is
to give the health page real Transform state to report, not to show off SQL.

Idempotent by name.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _api import connect  # noqa: E402

api = connect()
NAME = 'Warehouse marts'

MODELS = {
    'models/staging/stg_orders.sql': """
{{ config(materialized='table') }}

-- One row per order, straight from what the Shop pipeline lands.
select
    id                                as order_id,
    customer_id,
    status,
    total_amount,
    currency,
    placed_at,
    updated_at
from {{ source('shop', 'orders') }}
""",
    'models/staging/stg_order_items.sql': """
{{ config(materialized='table') }}

select
    id                                as order_item_id,
    order_id,
    product_id,
    quantity,
    unit_price,
    discount,
    (quantity * unit_price) - discount as line_total,
    updated_at
from {{ source('shop', 'order_items') }}
""",
    'models/staging/stg_customers.sql': """
{{ config(materialized='table') }}

select
    id            as customer_id,
    full_name,
    email,
    country,
    signed_up_at
from {{ source('shop', 'customers') }}
""",
    'models/staging/stg_invoices.sql': """
{{ config(materialized='table') }}

select
    id          as invoice_id,
    order_id,
    invoice_no,
    currency,
    amount,
    tax_amount,
    status,
    issued_at,
    due_at
from {{ source('finance', 'invoices') }}
""",
    'models/staging/stg_payments.sql': """
{{ config(materialized='table') }}

select
    id          as payment_id,
    invoice_id,
    method,
    amount,
    paid_at
from {{ source('finance', 'payments') }}
""",
    'models/staging/stg_web_events.sql': """
{{ config(materialized='table') }}

select
    id          as event_id,
    session_id,
    customer_id,
    event_type,
    page_path,
    device,
    country,
    duration_ms,
    occurred_at
from {{ source('ops', 'web_events') }}
""",
    'models/staging/stg_leads.sql': """
{{ config(materialized='table') }}

select
    id           as lead_id,
    company,
    contact_name,
    industry,
    source       as lead_source,
    stage,
    score,
    owner,
    annual_value,
    created_at
from {{ source('crm', 'leads') }}
""",
    'models/marts/mart_daily_revenue.sql': """
{{ config(materialized='table') }}

-- Revenue by day, from the order book rather than from the ledger: the
-- ledger lags by a day and this is the number the morning report opens on.
with items as (
    select oi.order_id, sum(oi.line_total) as order_total
    from {{ ref('stg_order_items') }} oi
    group by 1
)
select
    date_trunc('day', o.placed_at)::date as revenue_date,
    o.currency,
    count(distinct o.order_id)           as orders,
    count(distinct o.customer_id)        as customers,
    sum(coalesce(i.order_total, 0))      as gross_revenue,
    avg(coalesce(i.order_total, 0))      as avg_order_value
from {{ ref('stg_orders') }} o
left join items i on i.order_id = o.order_id
where o.status <> 'CANCELLED'
group by 1, 2
""",
    'models/marts/mart_customer_360.sql': """
{{ config(materialized='table') }}

-- What one customer looks like across all four domains at once. This is the
-- model that makes any of the pipelines being late visible to a human.
with orders as (
    select customer_id,
           count(*)             as order_count,
           sum(total_amount)    as lifetime_value,
           max(placed_at)       as last_order_at
    from {{ ref('stg_orders') }}
    group by 1
),
visits as (
    select customer_id,
           count(*)                                       as events,
           count(distinct session_id)                     as sessions,
           max(occurred_at)                               as last_seen_at,
           sum(case when event_type = 'purchase' then 1 else 0 end) as purchase_events
    from {{ ref('stg_web_events') }}
    where customer_id is not null
    group by 1
)
select
    c.customer_id,
    c.full_name,
    c.country,
    c.signed_up_at,
    coalesce(o.order_count, 0)     as order_count,
    coalesce(o.lifetime_value, 0)  as lifetime_value,
    o.last_order_at,
    coalesce(v.events, 0)          as events,
    coalesce(v.sessions, 0)        as sessions,
    coalesce(v.purchase_events, 0) as purchase_events,
    v.last_seen_at
from {{ ref('stg_customers') }} c
left join orders o on o.customer_id = c.customer_id
left join visits v on v.customer_id = c.customer_id
""",
    'models/marts/mart_invoice_collection.sql': """
{{ config(materialized='table') }}

-- How much has been invoiced and how much has actually arrived. Finance reads
-- this; it is also the model that goes wrong first when the ledger is stale.
with paid as (
    select invoice_id, sum(amount) as paid_amount, max(paid_at) as last_paid_at
    from {{ ref('stg_payments') }}
    group by 1
)
select
    i.invoice_id,
    i.invoice_no,
    i.order_id,
    i.status,
    i.currency,
    i.amount                                    as invoiced_amount,
    coalesce(p.paid_amount, 0)                  as paid_amount,
    i.amount - coalesce(p.paid_amount, 0)       as outstanding_amount,
    i.issued_at,
    i.due_at,
    p.last_paid_at,
    case
        when coalesce(p.paid_amount, 0) >= i.amount then 'SETTLED'
        when i.due_at < now() then 'OVERDUE'
        else 'OPEN'
    end                                         as collection_state
from {{ ref('stg_invoices') }} i
left join paid p on p.invoice_id = i.invoice_id
""",
    'models/sources.yml': """
version: 2

sources:
  - name: shop
    description: Landed by the "Shop orders to warehouse" pipeline, hourly.
    schema: shop
    tables:
      - name: orders
      - name: order_items
      - name: customers
      - name: products
  - name: finance
    description: Landed by the "Finance invoices to warehouse" pipeline, nightly.
    schema: finance
    tables:
      - name: invoices
      - name: payments
  - name: ops
    description: Landed by the "Web events to warehouse" pipeline, every 30 minutes.
    schema: ops
    tables:
      - name: web_events
  - name: crm
    description: Landed by the "CRM leads to warehouse" pipeline, daily.
    schema: crm
    tables:
      - name: leads
      - name: activities
""",
    'models/marts/schema.yml': """
version: 2

models:
  - name: mart_daily_revenue
    description: Revenue by day and currency, from the order book.
    columns:
      - name: revenue_date
        tests: [not_null]
      - name: gross_revenue
        tests: [not_null]
  - name: mart_customer_360
    description: One row per customer, across orders and site behaviour.
    columns:
      - name: customer_id
        tests: [unique, not_null]
  - name: mart_invoice_collection
    description: Invoiced against collected, with an ageing state.
    columns:
      - name: invoice_id
        tests: [unique, not_null]
""",
}


def projects() -> dict[str, dict]:
    raw = api.get('/transforms')
    items = raw['items'] if isinstance(raw, dict) and 'items' in raw else raw
    return {p['name']: p for p in items if isinstance(p, dict)}


def connection_id(name: str) -> str:
    for c in api.get('/transforms/connections'):
        if c['name'] == name:
            return c['id']
    raise SystemExit(f'no transform connection called {name!r}')


if __name__ == '__main__':
    have = projects()
    if NAME in have:
        project = have[NAME]
        print(f'  project exists  {NAME} ({project["id"]})')
    else:
        project = api.post('/transforms', json={
            'name': NAME,
            'description': 'Staging views over the four landed domains, and the three '
                           'marts the business actually opens.',
            'connection_id': connection_id('Analytics warehouse'),
            'dbt_project_name': 'warehouse_marts',
            'development_schema': 'dbt_dev',
            'production_schema': 'marts',
            'source_schema': 'shop',
            'with_examples': False,
        })
        print(f'  project created {NAME} ({project["id"]})')

    pid = project['id']
    written = api.post(f'/transforms/{pid}/files/batch', json={
        'changes': [{'path': path, 'content': body.lstrip('\n')}
                    for path, body in MODELS.items()],
    })
    print(f'  wrote {len(MODELS)} files')
    print('  ->', str(written)[:200])
