-- =====================================================================
-- Switch on compression for a database created before the schema had it
--
--   Chunks of `measurements` older than 7 days are stored column by column,
--   one segment per device and metric. Nothing is lost: a compressed chunk
--   returns the same rows, and it can still be written to (late values, the
--   backfill of value types), only more slowly.
--
--   The script only sets this up. The chunks that are old enough already are
--   compressed by the policy's background job, which starts right away; the
--   last query shows how far it is.
--
--   Run as the database owner. Running it again does no harm.
--
--     docker compose exec -T timescaledb sh -c \
--       'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < db/enable_compression.sql
--
--   Needs a TimescaleDB with the community licence
--   (SHOW timescaledb.license -> timescale).
-- =====================================================================

\set ON_ERROR_STOP on

ALTER TABLE measurements SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id, metric_id',
    timescaledb.compress_orderby   = 'time DESC'
);

SELECT add_compression_policy('measurements', INTERVAL '7 days', if_not_exists => true);

-- Chunks and whether they are compressed yet.
SELECT chunk_name, range_start, range_end, is_compressed
FROM timescaledb_information.chunks
WHERE hypertable_name = 'measurements'
ORDER BY range_start;
