-- =====================================================================
-- Add metrics.kind to a database created before the schema had it
--
--   kind says where a value comes from: measured (sensor, meter), setting
--   (configured by a person or the installer) or state (determined by the
--   device). It starts as NULL for every metric; the dashboard
--   "home-historian - Metrics" sets it. The view v_measurements gets the
--   column at its end, and the role grafana the right to write it.
--
--   Run as the database owner. Running it again does no harm.
--
--     docker compose exec -T timescaledb sh -c \
--       'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < db/add_metric_kind.sql
-- =====================================================================

\set ON_ERROR_STOP on

DO $$
BEGIN
    CREATE TYPE metric_kind AS ENUM ('measured', 'setting', 'state');
EXCEPTION WHEN duplicate_object THEN
    NULL;
END
$$;

ALTER TABLE metrics ADD COLUMN IF NOT EXISTS kind metric_kind;

COMMENT ON COLUMN metrics.kind IS 'Where the value comes from: measured (sensor, meter), setting (configured), state (determined by the device). NULL until curated.';

-- A view can only gain columns at its end; the others stay as they are.
CREATE OR REPLACE VIEW v_measurements AS
SELECT
    m.time,
    d.device_id,
    d.name                              AS device_name,
    d.type,
    met.display_name,
    met.display_subname,
    COALESCE(met.category, d.category)  AS category,
    met.unit,
    met.aggregation,
    m.value_num,
    m.value_str,
    m.value_bool,
    met.kind
FROM measurements m
JOIN devices d        ON d.device_id = m.device_id
LEFT JOIN metrics met ON met.metric_id = m.metric_id;

GRANT UPDATE (kind) ON metrics TO grafana;
