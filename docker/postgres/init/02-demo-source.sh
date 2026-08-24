#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname demo_source <<'EOSQL'
CREATE SCHEMA IF NOT EXISTS shop;

CREATE TABLE shop.customers (
    id           SERIAL PRIMARY KEY,
    full_name    TEXT        NOT NULL,
    email        TEXT        NOT NULL UNIQUE,
    country      TEXT        NOT NULL,
    signed_up_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE shop.orders (
    id           SERIAL PRIMARY KEY,
    customer_id  INTEGER     NOT NULL REFERENCES shop.customers(id),
    status       TEXT        NOT NULL,
    total_amount NUMERIC(12,2) NOT NULL,
    currency     TEXT        NOT NULL DEFAULT 'USD',
    placed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE shop.products (
    sku        TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    category   TEXT NOT NULL,
    unit_price NUMERIC(10,2) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO shop.customers (full_name, email, country, signed_up_at, updated_at)
SELECT
    'Customer ' || g,
    'customer' || g || '@example.com',
    (ARRAY['VN','SG','US','JP','DE'])[1 + (g % 5)],
    now() - (g || ' hours')::interval,
    now() - (g || ' hours')::interval
FROM generate_series(1, 500) g;

INSERT INTO shop.products (sku, name, category, unit_price, updated_at)
SELECT
    'SKU-' || lpad(g::text, 5, '0'),
    'Product ' || g,
    (ARRAY['Hardware','Software','Service','Accessory'])[1 + (g % 4)],
    round((random() * 900 + 10)::numeric, 2),
    now() - (g || ' minutes')::interval
FROM generate_series(1, 200) g;

INSERT INTO shop.orders (customer_id, status, total_amount, currency, placed_at, updated_at)
SELECT
    1 + (g % 500),
    (ARRAY['PLACED','PAID','SHIPPED','CANCELLED'])[1 + (g % 4)],
    round((random() * 2000 + 5)::numeric, 2),
    'USD',
    now() - (g || ' minutes')::interval,
    now() - (g || ' minutes')::interval
FROM generate_series(1, 2000) g;

-- Airbyte's postgres source needs SELECT on the tables it reads.
GRANT USAGE ON SCHEMA shop TO demo_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA shop TO demo_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA shop GRANT SELECT ON TABLES TO demo_reader;
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname demo_warehouse <<'EOSQL'
-- The destination connector creates its own schema/tables; it just needs
-- the rights to do so.
GRANT ALL ON DATABASE demo_warehouse TO demo_writer;
GRANT ALL ON SCHEMA public TO demo_writer;
ALTER DATABASE demo_warehouse OWNER TO demo_writer;
EOSQL

echo "[init] demo_source + demo_warehouse ready"
