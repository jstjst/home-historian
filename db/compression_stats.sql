-- =====================================================================
-- Chunk sizes of `measurements` and what compression saved
--
--   Reads only. `size_now` is what a chunk takes on disk. In a chunk that
--   is not compressed that is `data_now` plus `index_now`; a compressed
--   chunk keeps most of its data outside both (TOAST). `before` and `after`
--   exist for compressed chunks. A chunk that holds little compresses
--   badly: its ratio says nothing about a full one.
--
--     docker compose exec -T timescaledb sh -c \
--       'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < db/compression_stats.sql
-- =====================================================================

\set ON_ERROR_STOP on

-- Per chunk.
SELECT c.chunk_name,
       c.range_start::date AS from_day,
       c.range_end::date   AS to_day,
       c.is_compressed     AS compressed,
       pg_size_pretty(d.table_bytes) AS data_now,
       pg_size_pretty(d.index_bytes) AS index_now,
       pg_size_pretty(d.total_bytes) AS size_now,
       pg_size_pretty(s.before_compression_total_bytes) AS before,
       pg_size_pretty(s.after_compression_total_bytes)  AS after,
       round(s.before_compression_total_bytes::numeric
             / nullif(s.after_compression_total_bytes, 0), 1) AS ratio
FROM timescaledb_information.chunks c
JOIN chunks_detailed_size('measurements') d
  ON d.chunk_schema = c.chunk_schema AND d.chunk_name = c.chunk_name
LEFT JOIN chunk_compression_stats('measurements') s
  ON s.chunk_schema = c.chunk_schema AND s.chunk_name = c.chunk_name
WHERE c.hypertable_name = 'measurements'
ORDER BY c.range_start;

-- All compressed chunks together.
SELECT total_chunks,
       number_compressed_chunks AS compressed,
       pg_size_pretty(before_compression_total_bytes) AS before,
       pg_size_pretty(after_compression_total_bytes)  AS after,
       round(before_compression_total_bytes::numeric
             / nullif(after_compression_total_bytes, 0), 1) AS ratio
FROM hypertable_compression_stats('measurements');

-- The whole table, the aggregates and the database.
SELECT pg_size_pretty(hypertable_size('measurements'))    AS measurements,
       pg_size_pretty(hypertable_size('measurements_1h')) AS measurements_1h,
       pg_size_pretty(hypertable_size('consumption_1d'))  AS consumption_1d,
       pg_size_pretty(pg_database_size(current_database())) AS database;
