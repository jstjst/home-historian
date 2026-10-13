-- =====================================================================
-- Let Grafana edit metric definitions, on a database created before
-- 90_grafana_role.sh granted it
--
--   The dashboard "home-historian - Metrics" edits display_name,
--   display_subname, unit, category and disabled of a metric. The role
--   grafana gets UPDATE on these five columns of `metrics`. It keeps SELECT
--   only on the other columns (type, native_key, value_type, aggregation)
--   and on every other table except metric_notes.
--
--   Needs db/add_metric_notes.sql first: the same form saves the note.
--
--   Run as the database owner. Running it again does no harm.
--
--     docker compose exec -T timescaledb sh -c \
--       'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < db/grant_metric_edit.sql
--
--   To take it back:
--     REVOKE UPDATE (display_name, display_subname, unit, category, disabled)
--         ON metrics FROM grafana;
-- =====================================================================

\set ON_ERROR_STOP on

GRANT UPDATE (display_name, display_subname, unit, category, disabled)
    ON metrics TO grafana;
