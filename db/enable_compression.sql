-- =====================================================================
-- Bring an existing database to the schema's chunk and compression settings
--
--   For a database created before the schema compressed `measurements`, or
--   while it used chunks of 7 days that were compressed after 7 days.
--
--   `measurements` gets chunks of 1 day, and chunks older than 1 day are
--   stored column by column, one segment per device and metric. Nothing is
--   lost: a compressed chunk returns the same rows, and it can still be
--   written to (late values, the backfill of value types), only more slowly.
--
--   The chunk interval applies to chunks created from now on. The existing
--   ones keep their span and are compressed once their end lies 1 day back.
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

SELECT set_chunk_time_interval('measurements', INTERVAL '1 day');

-- add_compression_policy leaves an existing policy as it is.
SELECT remove_compression_policy('measurements', if_exists => true);
SELECT add_compression_policy('measurements', INTERVAL '1 day');

-- Chunks and whether they are compressed yet.
SELECT chunk_name, range_start, range_end, is_compressed
FROM timescaledb_information.chunks
WHERE hypertable_name = 'measurements'
ORDER BY range_start;
