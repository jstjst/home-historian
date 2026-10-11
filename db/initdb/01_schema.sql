-- =====================================================================
-- home-historian  ->  TimescaleDB (PostgreSQL)
-- Time-series schema for energy devices (heat pump, PV, battery, meters)
--
-- Three levels:
--   devices       one physical device
--   metrics       what a measured quantity means for a device TYPE
--   measurements  the narrow time series (hypertable): metric + device + value
--
-- The reasoning behind each choice is in docs/architecture.md. In short:
--   * A metric belongs to (type, native_key), not to a device. Identical
--     devices share ONE set of definitions and are told apart by device_id.
--   * measurements carries the integer metric_id, not the text native_key.
--     The ingester resolves (type, native_key) -> metric_id through a cache
--     and creates unknown keys on the fly.
--   * native_key is the full raw remainder of the topic ('/' -> '.'). It is
--     the ONLY raw column and exists for the ingester's lookup alone.
--   * display_name + display_subname are the curated, unified view used for
--     analysis (sensor + role), identical across devices.
--   * category may sit on the metric OR on the device; a view merges the two
--     with COALESCE.
--   * Values are split by type (num/str/bool), so mixed value types need no
--     workaround.
--   * No foreign key and no unique constraint on measurements: a new data
--     point never fails on the schema and nothing slows down the insert.
-- =====================================================================


CREATE EXTENSION IF NOT EXISTS timescaledb;


-- ---------------------------------------------------------------------
-- Enums (guard against typos instead of loose strings)
-- ---------------------------------------------------------------------

-- A device type is a product line whose units deliver the same metrics.
-- The schema defines none. Every module adds its own in its devices.sql:
--   ALTER TYPE device_type ADD VALUE IF NOT EXISTS '<type>';
CREATE TYPE device_type AS ENUM ();

-- The part of the installation a value belongs to.
CREATE TYPE category AS ENUM (
    'primary',      -- refrigerant circuit (compressor, inverter, evaporator, ...)
    'secondary',    -- buffer charging circuit, heat pump -> buffer tank
    'hc1', 'hc2', 'hc3',  -- heating circuits behind the buffer tank
    'dhw',          -- domestic hot water
    'pv',           -- photovoltaics (generation, inverter)
    'battery',      -- battery storage
    'grid',         -- grid connection point (import / export)
    'config'        -- configuration / setpoints
);

CREATE TYPE agg_kind AS ENUM ('gauge', 'counter');
CREATE TYPE val_kind AS ENUM ('num', 'str', 'bool');


-- ---------------------------------------------------------------------
-- devices: one physical device
--   device_key is the part of the MQTT topic that identifies the device. The
--   ingester picks the device with the longest matching key; everything after
--   it is the native_key. A key may span several segments ('open3e/vitocal').
--   category is only set when the DEVICE determines the part of the
--   installation (a heat meter in one heating circuit). For a heat pump it
--   stays NULL, because there the metric decides.
-- ---------------------------------------------------------------------
CREATE TABLE devices (
    device_id   integer      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type        device_type  NOT NULL,
    device_key  text         NOT NULL UNIQUE,   -- topic prefix, anchor of the longest-prefix match
    name        text         NOT NULL,          -- 'Heat pump', 'Heat meter HC1'
    category    category,                        -- NULL unless the device determines it
    serial      text,
    location    text                             -- where it is installed, NOT the heating circuit
);

COMMENT ON COLUMN devices.device_key IS 'Topic prefix (longest-prefix match in the ingester). NOT the serial number.';
COMMENT ON COLUMN devices.category   IS 'Part of the installation when the device determines it (a meter in one circuit). NULL otherwise.';


-- ---------------------------------------------------------------------
-- metrics: definition of a measured quantity, bound to the device TYPE
--   metric_id is a surrogate key, so measurements only stores an integer.
--   (type, native_key) is the natural key the ingester looks up.
--   category is only set when the METRIC determines the part of the
--   installation (heat pump data points are specific to one circuit). For
--   registers shared by several meters it stays NULL.
-- ---------------------------------------------------------------------
CREATE TABLE metrics (
    metric_id     integer      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type          device_type  NOT NULL,
    native_key    text         NOT NULL,        -- raw remainder of the topic, e.g. '268_FlowTemperatureSensor.SensorStatus.ID'
    display_name  text         NOT NULL,        -- the sensor, e.g. 'HC1 flow temperature'
    display_subname text,                        -- role of the value (actual|min|max|average|setpoint|state); NULL if none
    unit          text,                          -- 'celsius', 'watt', 'kwh', ... (NULL for str/bool)
    category      category,                      -- NULL for shared meter registers
    value_type    val_kind     NOT NULL DEFAULT 'num',
    aggregation   agg_kind     NOT NULL DEFAULT 'gauge',  -- counter for cumulative meter readings
    deprecated    boolean      NOT NULL DEFAULT false,
    UNIQUE (type, native_key)                    -- natural key for the ingester's lookup
);

COMMENT ON COLUMN metrics.native_key      IS 'Complete lookup key per type (raw remainder of the topic). The ingester resolves (type, native_key) -> metric_id.';
COMMENT ON COLUMN metrics.display_subname IS 'Unified role of the value (actual|min|max|...) for analysis across devices. NULL if none.';
COMMENT ON COLUMN metrics.category         IS 'Part of the installation when the metric determines it. NULL for shared registers.';
COMMENT ON COLUMN metrics.aggregation      IS 'gauge = instantaneous value, counter = cumulative meter reading (take the difference!).';


-- ---------------------------------------------------------------------
-- measurements: the narrow time series (hypertable)
--   One row per value: which metric, which device, and exactly one of the
--   three value columns. No foreign key: the metric always exists because the
--   ingester creates it first, but no constraint check slows the insert.
-- ---------------------------------------------------------------------
CREATE TABLE measurements (
    time        timestamptz  NOT NULL,
    metric_id   integer      NOT NULL,          -- -> metrics.metric_id (joined, no FK)
    device_id   integer      NOT NULL,          -- -> devices.device_id (which unit)
    value_num   double precision,
    value_str   text,
    value_bool  boolean
);

SELECT create_hypertable('measurements', 'time', chunk_time_interval => INTERVAL '7 days');
CREATE INDEX idx_measurements_lookup ON measurements (device_id, metric_id, time DESC);

-- Compression: chunks older than 7 days are stored column by column, one
-- segment per device and metric (the way they are queried). It is lossless,
-- and compressed chunks can still be written to, only more slowly. On a
-- database created before this was added: db/enable_compression.sql
ALTER TABLE measurements SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id, metric_id',
    timescaledb.compress_orderby   = 'time DESC'
);
SELECT add_compression_policy('measurements', INTERVAL '7 days');

COMMENT ON TABLE measurements IS 'Time series. metric_id -> metrics, device_id -> devices (joined, no FK).';


-- =====================================================================
-- Master data
--   This schema creates NO devices and NO metrics.
--   * devices have to be entered: without a matching device_key the ingester
--     drops a device's messages. A module ships its device type, and its
--     devices where their keys are fixed, as modules/<name>/devices.sql.
--   * metrics are created by the ingester as stubs the first time a key
--     appears (display_name = native_key, no category, value_type 'str').
--     Giving them a name, unit, category and type is manual work. A module
--     ships the definitions it has as modules/<name>/metrics.de.sql.
--
--   Stubs that still need curating:
--     SELECT metric_id, type, native_key FROM metrics WHERE display_name = native_key;
--
--   After changing value_type the ingester keeps writing to the old column
--   until its metric cache expires (INGEST_METRIC_CACHE_SECONDS, one hour by
--   default); `docker compose restart ingest` applies it at once. Values
--   already stored stay where they are until db/backfill_value_types.sql
--   moves them.
-- =====================================================================


-- =====================================================================
-- v_measurements: values joined with their master data
--   category comes from the metric if it has one, otherwise from the device.
--   A single condition (category = 'hc1') then finds the heat pump's flow
--   temperature AND the heat meter of that circuit.
--
--   Example (Grafana; $__* are Grafana macros):
--     SELECT $__timeGroup(time, $__interval) AS "time",
--            display_name || ' ' || display_subname AS metric,
--            avg(value_num) AS value
--     FROM v_measurements
--     WHERE $__timeFilter(time) AND category = 'hc1'
--     GROUP BY 1, 2 ORDER BY 1;
--
--   Group by display_name AND display_subname: the name alone would merge
--   actual, min and max of one sensor into a single series.
-- =====================================================================
CREATE VIEW v_measurements AS
SELECT
    m.time,
    d.device_id,
    d.name                              AS device_name,
    d.type,
    met.display_name,
    met.display_subname,
    COALESCE(met.category, d.category)  AS category,   -- metric first, device as fallback
    met.unit,
    met.aggregation,
    m.value_num,
    m.value_str,
    m.value_bool
FROM measurements m
JOIN devices d        ON d.device_id = m.device_id
LEFT JOIN metrics met ON met.metric_id = m.metric_id;


-- =====================================================================
-- Real-time continuous aggregates for fast long-range charts
--   IMPORTANT: since TimescaleDB 2.13 materialized_only = TRUE is the
--   default, which silently hides the most recent values (not materialised
--   yet) from a dashboard. Real-time has to be switched on explicitly, so
--   that a query unions in the fresh raw data at the edge.
--   The raw data stays untouched next to the aggregates.
-- =====================================================================

-- Gauges: mean/min/max per hour and metric.
CREATE MATERIALIZED VIEW measurements_1h
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    device_id,
    metric_id,
    avg(value_num) AS avg_val,
    max(value_num) AS max_val,
    min(value_num) AS min_val
FROM measurements
WHERE value_num IS NOT NULL
GROUP BY bucket, device_id, metric_id
WITH NO DATA;

ALTER MATERIALIZED VIEW measurements_1h SET (timescaledb.materialized_only = false);

SELECT add_continuous_aggregate_policy('measurements_1h',
    start_offset      => INTERVAL '3 days',
    end_offset        => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes');


-- Counters: consumption per day and meter metric.
--   A cumulative meter only counts up, so the consumption of a day is
--   max - min within its bucket. This ignores a meter overflow or reset.
CREATE MATERIALIZED VIEW consumption_1d
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', time) AS bucket,
    device_id,
    metric_id,
    max(value_num) - min(value_num) AS consumption,
    max(value_num) AS meter_reading_end
FROM measurements
WHERE value_num IS NOT NULL
GROUP BY bucket, device_id, metric_id
WITH NO DATA;

ALTER MATERIALIZED VIEW consumption_1d SET (timescaledb.materialized_only = false);

SELECT add_continuous_aggregate_policy('consumption_1d',
    start_offset      => INTERVAL '7 days',
    end_offset        => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour');
