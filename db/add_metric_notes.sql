-- =====================================================================
-- Add the table of metric notes to a database created before the schema
-- had it
--
--   The dashboard "home-historian - Metrics" stores one free-text note per
--   metric in `metric_notes`. The role grafana gets the right to write that
--   table and keeps SELECT only on everything else.
--
--   Run as the database owner. Running it again does no harm.
--
--     docker compose exec -T timescaledb sh -c \
--       'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < db/add_metric_notes.sql
-- =====================================================================

\set ON_ERROR_STOP on

CREATE TABLE IF NOT EXISTS metric_notes (
    metric_id   integer      PRIMARY KEY REFERENCES metrics ON DELETE CASCADE,
    note        text         NOT NULL,
    updated     timestamptz  NOT NULL DEFAULT now()
);

GRANT SELECT, INSERT, UPDATE, DELETE ON metric_notes TO grafana;
