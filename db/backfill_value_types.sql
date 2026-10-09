-- =====================================================================
-- Move stored values into the column their metric's value_type names
--
--   A stub is typed as text, so its values sit in value_str even when they
--   are numbers. Setting value_type to 'num' or 'bool' only changes where
--   NEW values go. This script moves the ones already stored.
--
--   It reads what to do from the database: every metric typed 'num' or
--   'bool' whose rows still hold a matching text in value_str. Nothing is
--   listed by hand, and running it again does no harm.
--
--   A text that does not fit the type (a 'num' metric that once reported
--   'n/a') stays in value_str, as it would in the ingester.
--
--   The other direction (a metric changed back to 'str') is not handled: a
--   number turned into text would not match what the device sent.
--
--   Both aggregates only read value_num, and their refresh policies look
--   back 3 and 7 days. The script therefore refreshes them over their whole
--   history; TimescaleDB recomputes only the ranges that changed.
--
--   Run as the database owner, AFTER the ingester has picked up the new
--   types (`docker compose restart ingest`, or wait for the metric cache to
--   expire). Until then it keeps writing text, which the next run moves.
--
--     docker compose exec -T timescaledb sh -c \
--       'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < db/backfill_value_types.sql
--
--   Do not run it inside a transaction (psql -1): refreshing an aggregate
--   needs its own.
--
--   Needs PostgreSQL 16 (pg_input_is_valid).
-- =====================================================================

\set ON_ERROR_STOP on

CREATE TEMP TABLE moved (
    metric_id  integer,
    target     text,
    row_count  bigint,
    oldest     timestamptz
);

-- Text -> number.
WITH updated AS (
    UPDATE measurements m
    SET value_num = m.value_str::double precision,
        value_str = NULL
    FROM metrics met
    WHERE met.metric_id = m.metric_id
      AND met.value_type = 'num'
      AND m.value_str IS NOT NULL
      AND pg_input_is_valid(m.value_str, 'double precision')
    RETURNING m.metric_id, m.time
)
INSERT INTO moved
SELECT metric_id, 'num', count(*), min(time) FROM updated GROUP BY metric_id;

-- Text -> boolean, with the words the ingester accepts (_parse_bool).
WITH updated AS (
    UPDATE measurements m
    SET value_bool = lower(btrim(m.value_str, E' \t\r\n')) IN ('true', '1', 'on', 'yes'),
        value_str  = NULL
    FROM metrics met
    WHERE met.metric_id = m.metric_id
      AND met.value_type = 'bool'
      AND lower(btrim(m.value_str, E' \t\r\n'))
          IN ('true', '1', 'on', 'yes', 'false', '0', 'off', 'no')
    RETURNING m.metric_id, m.time
)
INSERT INTO moved
SELECT metric_id, 'bool', count(*), min(time) FROM updated GROUP BY metric_id;

-- What was moved.
SELECT met.type, met.native_key, met.display_name, moved.target,
       moved.row_count, moved.oldest
FROM moved
JOIN metrics met USING (metric_id)
ORDER BY met.type, met.native_key;

-- What is left behind: text that does not fit its metric's type.
SELECT met.type, met.native_key, met.value_type,
       count(*) AS rows_left, min(m.value_str) AS example
FROM measurements m
JOIN metrics met USING (metric_id)
WHERE met.value_type IN ('num', 'bool')
  AND m.value_str IS NOT NULL
GROUP BY 1, 2, 3
ORDER BY 1, 2;

-- Refresh both aggregates over their whole history, up to the last complete
-- bucket. Only the ranges the UPDATE touched are recomputed, so this costs
-- nothing when there was nothing to move. The open bucket stays with the
-- refresh policy, so the real-time edge is not materialised early.
CALL refresh_continuous_aggregate('measurements_1h', NULL, time_bucket('1 hour', now()));
CALL refresh_continuous_aggregate('consumption_1d',  NULL, time_bucket('1 day',  now()));
