-- Optional: run once on your own PostgreSQL/Supabase database.
-- No existing business tables are changed. Do not grant anonymous access.
CREATE SCHEMA IF NOT EXISTS po_testing;
CREATE TABLE IF NOT EXISTS po_testing.runs (
  run_id text PRIMARY KEY,
  saved_at timestamptz NOT NULL DEFAULT now(),
  payload jsonb NOT NULL,
  CHECK (payload->>'schema_version' = 'po-test.v2'),
  CHECK (payload->>'status' = 'passed'),
  CHECK (payload->>'inventory_is_simulated' = 'true'),
  CHECK (payload->>'submission_allowed' = 'false'),
  CHECK (payload->>'run_id' = run_id)
);
CREATE OR REPLACE VIEW po_testing.forecasts AS
SELECT run_id, payload->>'store' AS store, payload->>'as_of_date' AS as_of_date,
       f.value AS forecast
FROM po_testing.runs CROSS JOIN LATERAL jsonb_array_elements(payload->'forecasts') AS f(value);
CREATE OR REPLACE VIEW po_testing.recommendations AS
SELECT run_id, payload->>'store' AS store, payload->>'as_of_date' AS as_of_date,
       p.value AS recommendation
FROM po_testing.runs CROSS JOIN LATERAL jsonb_array_elements(payload->'recommendations') AS p(value);
