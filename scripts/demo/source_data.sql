-- A small business that is coherent across four domains.
--
-- The point is not volume for its own sake: the Overview reports on pipelines,
-- and pipelines are only interesting when they differ from each other. So the
-- domains differ in the ways that matter to the report -- one is small and
-- slow-moving, one is append-only and large, one has a natural cursor for
-- incremental sync, one is wide and mostly static.
--
-- Idempotent: the existing shop tables are left alone, everything else is
-- rebuilt from scratch each run.

CREATE SCHEMA IF NOT EXISTS finance;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS crm;

-- shop: line items, the detail under the orders that already exist
DROP TABLE IF EXISTS shop.order_items;
CREATE TABLE shop.order_items (
    id           bigserial PRIMARY KEY,
    order_id     integer       NOT NULL,
    product_id   integer       NOT NULL,
    quantity     integer       NOT NULL,
    unit_price   numeric(12,2) NOT NULL,
    discount     numeric(5,2)  NOT NULL DEFAULT 0,
    updated_at   timestamptz   NOT NULL DEFAULT now()
);
INSERT INTO shop.order_items (order_id, product_id, quantity, unit_price, discount, updated_at)
SELECT o.id,
       1 + (random() * 199)::int,
       1 + (random() * 4)::int,
       round((5 + random() * 495)::numeric, 2),
       round((random() * 25)::numeric, 2),
       now() - (random() * interval '45 days')
FROM shop.orders o, generate_series(1, 3) g
WHERE random() < 0.8;
CREATE INDEX ON shop.order_items (updated_at);

-- finance: invoices and payments, the money view of the same orders
DROP TABLE IF EXISTS finance.payments;
DROP TABLE IF EXISTS finance.invoices;
CREATE TABLE finance.invoices (
    id            bigserial PRIMARY KEY,
    order_id      integer       NOT NULL,
    invoice_no    text          NOT NULL,
    currency      text          NOT NULL DEFAULT 'VND',
    amount        numeric(14,2) NOT NULL,
    tax_amount    numeric(14,2) NOT NULL,
    status        text          NOT NULL,
    issued_at     timestamptz   NOT NULL,
    due_at        timestamptz   NOT NULL,
    updated_at    timestamptz   NOT NULL DEFAULT now()
);
INSERT INTO finance.invoices (order_id, invoice_no, amount, tax_amount, status, issued_at, due_at, updated_at)
SELECT o.id,
       'INV-' || to_char(now() - (random() * interval '60 days'), 'YYYYMM') || '-' || lpad(o.id::text, 6, '0'),
       round((100000 + random() * 9900000)::numeric, 2),
       round((random() * 990000)::numeric, 2),
       (ARRAY['DRAFT','ISSUED','PAID','OVERDUE','VOID'])[1 + (random() * 4)::int],
       now() - (random() * interval '60 days'),
       now() + (random() * interval '30 days'),
       now() - (random() * interval '30 days')
FROM shop.orders o;
CREATE INDEX ON finance.invoices (updated_at);

CREATE TABLE finance.payments (
    id           bigserial PRIMARY KEY,
    invoice_id   bigint        NOT NULL,
    method       text          NOT NULL,
    amount       numeric(14,2) NOT NULL,
    paid_at      timestamptz   NOT NULL,
    reference    text,
    updated_at   timestamptz   NOT NULL DEFAULT now()
);
INSERT INTO finance.payments (invoice_id, method, amount, paid_at, reference, updated_at)
SELECT i.id,
       (ARRAY['BANK_TRANSFER','CARD','CASH','E_WALLET'])[1 + (random() * 3)::int],
       round((i.amount * (0.4 + random() * 0.6))::numeric, 2),
       i.issued_at + (random() * interval '20 days'),
       'PAY-' || substr(md5(random()::text), 1, 10),
       now() - (random() * interval '20 days')
FROM finance.invoices i
WHERE i.status IN ('PAID', 'OVERDUE');
CREATE INDEX ON finance.payments (updated_at);

-- ops: the append-only stream, an order of magnitude bigger than the rest
DROP TABLE IF EXISTS ops.web_events;
CREATE TABLE ops.web_events (
    id          bigserial PRIMARY KEY,
    session_id  text        NOT NULL,
    customer_id integer,
    event_type  text        NOT NULL,
    page_path   text        NOT NULL,
    device      text        NOT NULL,
    country     text        NOT NULL,
    duration_ms integer     NOT NULL,
    occurred_at timestamptz NOT NULL
);
INSERT INTO ops.web_events (session_id, customer_id, event_type, page_path, device, country, duration_ms, occurred_at)
SELECT substr(md5((g / 7)::text), 1, 16),
       CASE WHEN random() < 0.6 THEN 1 + (random() * 499)::int END,
       (ARRAY['page_view','add_to_cart','checkout_start','purchase','search','signup'])[1 + (random() * 5)::int],
       (ARRAY['/','/products','/products/detail','/cart','/checkout','/account','/search'])[1 + (random() * 6)::int],
       (ARRAY['desktop','mobile','tablet'])[1 + (random() * 2)::int],
       (ARRAY['VN','SG','TH','MY','ID','PH'])[1 + (random() * 5)::int],
       (50 + random() * 30000)::int,
       now() - (random() * interval '30 days')
FROM generate_series(1, 25000) g;
CREATE INDEX ON ops.web_events (occurred_at);

-- crm: wide, slow-moving, the kind of table that is synced whole
DROP TABLE IF EXISTS crm.activities;
DROP TABLE IF EXISTS crm.leads;
CREATE TABLE crm.leads (
    id            bigserial PRIMARY KEY,
    company       text          NOT NULL,
    contact_name  text          NOT NULL,
    email         text          NOT NULL,
    phone         text,
    industry      text,
    source        text          NOT NULL,
    stage         text          NOT NULL,
    score         integer       NOT NULL,
    owner         text          NOT NULL,
    annual_value  numeric(14,2),
    created_at    timestamptz   NOT NULL,
    updated_at    timestamptz   NOT NULL DEFAULT now()
);
INSERT INTO crm.leads (company, contact_name, email, phone, industry, source, stage, score, owner, annual_value, created_at, updated_at)
SELECT 'Company ' || g,
       'Contact ' || g,
       'contact' || g || '@example.test',
       '+84' || lpad((random() * 999999999)::bigint::text, 9, '0'),
       (ARRAY['Retail','Manufacturing','Logistics','Education','Healthcare','Finance'])[1 + (random() * 5)::int],
       (ARRAY['WEBSITE','REFERRAL','EVENT','OUTBOUND','PARTNER'])[1 + (random() * 4)::int],
       (ARRAY['NEW','QUALIFIED','PROPOSAL','NEGOTIATION','WON','LOST'])[1 + (random() * 5)::int],
       (random() * 100)::int,
       (ARRAY['an.nguyen','binh.tran','chi.le','dung.pham'])[1 + (random() * 3)::int],
       round((10000000 + random() * 990000000)::numeric, 2),
       now() - (random() * interval '180 days'),
       now() - (random() * interval '40 days')
FROM generate_series(1, 1200) g;
CREATE INDEX ON crm.leads (updated_at);

CREATE TABLE crm.activities (
    id          bigserial PRIMARY KEY,
    lead_id     bigint      NOT NULL,
    kind        text        NOT NULL,
    subject     text        NOT NULL,
    outcome     text,
    happened_at timestamptz NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO crm.activities (lead_id, kind, subject, outcome, happened_at, updated_at)
SELECT l.id,
       (ARRAY['CALL','EMAIL','MEETING','DEMO'])[1 + (random() * 3)::int],
       'Touchpoint ' || g,
       (ARRAY['CONNECTED','NO_ANSWER','FOLLOW_UP','CLOSED'])[1 + (random() * 3)::int],
       now() - (random() * interval '90 days'),
       now() - (random() * interval '30 days')
FROM crm.leads l, generate_series(1, 4) g
WHERE random() < 0.7;
CREATE INDEX ON crm.activities (updated_at);

SELECT table_schema, table_name,
       (xpath('/row/c/text()', query_to_xml(format('select count(*) c from %I.%I', table_schema, table_name), false, true, '')))[1]::text::int AS rows
FROM information_schema.tables
WHERE table_schema IN ('shop','finance','ops','crm') AND table_type = 'BASE TABLE'
ORDER BY 1, 2;
