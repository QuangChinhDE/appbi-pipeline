-- A legacy ERP, which is where the most common failure in this product lives.
--
-- Three tables from one system, each feeding its own pipeline. That shape is
-- the point: when the ERP's password is rotated and nobody updates it here,
-- three pipelines fail for one reason, and a health page that says "3 failed
-- runs" has told the reader nothing they can act on.

CREATE SCHEMA IF NOT EXISTS erp;

DROP TABLE IF EXISTS erp.stock_movements;
DROP TABLE IF EXISTS erp.purchase_orders;
DROP TABLE IF EXISTS erp.suppliers;

CREATE TABLE erp.suppliers (
    id          bigserial PRIMARY KEY,
    code        text        NOT NULL,
    name        text        NOT NULL,
    country     text        NOT NULL,
    lead_days   integer     NOT NULL,
    rating      numeric(3,1),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO erp.suppliers (code, name, country, lead_days, rating, updated_at)
SELECT 'SUP-' || lpad(g::text, 4, '0'),
       'Supplier ' || g,
       (ARRAY['VN','CN','KR','JP','TH'])[1 + (random() * 4)::int],
       3 + (random() * 40)::int,
       round((2 + random() * 3)::numeric, 1),
       now() - (random() * interval '30 days')
FROM generate_series(1, 180) g;
CREATE INDEX ON erp.suppliers (updated_at);

CREATE TABLE erp.purchase_orders (
    id           bigserial PRIMARY KEY,
    po_number    text          NOT NULL,
    supplier_id  bigint        NOT NULL,
    status       text          NOT NULL,
    total_amount numeric(14,2) NOT NULL,
    ordered_at   timestamptz   NOT NULL,
    expected_at  timestamptz   NOT NULL,
    updated_at   timestamptz   NOT NULL DEFAULT now()
);
INSERT INTO erp.purchase_orders (po_number, supplier_id, status, total_amount, ordered_at, expected_at, updated_at)
SELECT 'PO-' || lpad(g::text, 6, '0'),
       1 + (random() * 179)::int,
       (ARRAY['DRAFT','APPROVED','SENT','RECEIVED','CANCELLED'])[1 + (random() * 4)::int],
       round((500000 + random() * 49500000)::numeric, 2),
       now() - (random() * interval '90 days'),
       now() + (random() * interval '45 days'),
       now() - (random() * interval '25 days')
FROM generate_series(1, 1400) g;
CREATE INDEX ON erp.purchase_orders (updated_at);

CREATE TABLE erp.stock_movements (
    id          bigserial PRIMARY KEY,
    sku         text        NOT NULL,
    warehouse   text        NOT NULL,
    direction   text        NOT NULL,
    quantity    integer     NOT NULL,
    reason      text,
    moved_at    timestamptz NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO erp.stock_movements (sku, warehouse, direction, quantity, reason, moved_at, updated_at)
SELECT 'SKU-' || lpad((1 + (random() * 199)::int)::text, 4, '0'),
       (ARRAY['HN-01','HCM-01','DN-01'])[1 + (random() * 2)::int],
       (ARRAY['IN','OUT'])[1 + (random() * 1)::int],
       1 + (random() * 500)::int,
       (ARRAY['PURCHASE','SALE','RETURN','ADJUSTMENT','TRANSFER'])[1 + (random() * 4)::int],
       now() - (random() * interval '60 days'),
       now() - (random() * interval '20 days')
FROM generate_series(1, 9000) g;
CREATE INDEX ON erp.stock_movements (updated_at);

SELECT table_name,
       (xpath('/row/c/text()', query_to_xml(format('select count(*) c from erp.%I', table_name), false, true, '')))[1]::text::int AS rows
FROM information_schema.tables
WHERE table_schema = 'erp' AND table_type = 'BASE TABLE'
ORDER BY 1;
