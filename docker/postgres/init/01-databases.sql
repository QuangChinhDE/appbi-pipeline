-- Product control-plane DB is created by POSTGRES_DB. These two extra
-- databases give the platform a real source to read from and a real
-- warehouse to write into, so a first sync moves genuine rows.
CREATE DATABASE demo_source;
CREATE DATABASE demo_warehouse;

-- Dedicated least-privilege engine users (what a real deployment would do).
CREATE USER demo_reader WITH PASSWORD 'demo_reader_pw';
CREATE USER demo_writer WITH PASSWORD 'demo_writer_pw';

-- Airbyte's own metadata database.
--
-- Separate from the product's, and never read by product code (guardrail 2):
-- Airbyte owns execution truth, the product owns business truth. Sharing a
-- database would make that boundary a matter of discipline instead of access.
CREATE DATABASE airbyte OWNER appbi;
