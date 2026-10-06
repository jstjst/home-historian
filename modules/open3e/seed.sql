-- =====================================================================
-- open3e module: master data
--   Devices: one per control unit (ECU), device_key = "open3e/<name>" with
--   <name> as in config/devices.json. Without these rows the ingester drops
--   the module's messages.
--   Metrics: curated definitions for a few data points, as an example. The
--   ingester creates all others as stubs; curating them is manual work.
--
--   Loaded automatically on an empty database volume. Later, by hand:
--     docker compose exec -T timescaledb sh -c \
--       'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < modules/open3e/seed.sql
--   Running it again does no harm. Metrics curated by hand are left alone;
--   only stubs are overwritten.
-- =====================================================================

INSERT INTO devices (type, device_key, name) VALUES
  ('vitocal',    'open3e/vitocal',    'Vitocal 250-A'),
  ('vitocharge', 'open3e/vitocharge', 'Vitocharge VX3')
ON CONFLICT (device_key) DO NOTHING;

-- native_key = remainder of the topic after the device_key ('/' -> '.').
-- open3e flattens complex data points itself: one topic per leaf, nested
-- sub-values included.
INSERT INTO metrics (type, native_key, display_name, display_subname, unit, category, value_type, aggregation) VALUES
  ('vitocal', '268_FlowTemperatureSensor.Actual',            'HC1 flow temperature', 'actual',  'celsius', 'hc1', 'num', 'gauge'),
  ('vitocal', '268_FlowTemperatureSensor.Minimum',           'HC1 flow temperature', 'min',     'celsius', 'hc1', 'num', 'gauge'),
  ('vitocal', '268_FlowTemperatureSensor.Maximum',           'HC1 flow temperature', 'max',     'celsius', 'hc1', 'num', 'gauge'),
  ('vitocal', '268_FlowTemperatureSensor.Average',           'HC1 flow temperature', 'average', 'celsius', 'hc1', 'num', 'gauge'),
  ('vitocal', '268_FlowTemperatureSensor.SensorStatus.ID',   'HC1 flow temperature', 'state',   NULL,      'hc1', 'num', 'gauge'),
  ('vitocal', '268_FlowTemperatureSensor.SensorStatus.Text', 'HC1 flow temperature', 'state',   NULL,      'hc1', 'str', 'gauge'),
  ('vitocal', '396_DomesticHotWaterTemperatureSetpoint',     'DHW temperature', 'setpoint', 'celsius', 'dhw', 'num', 'gauge'),
  ('vitocharge', '1603_PointOfCommonCouplingPower.ActivePower',   'Grid connection active power',   'actual', 'watt', 'grid', 'num', 'gauge'),
  ('vitocharge', '1603_PointOfCommonCouplingPower.ReactivePower', 'Grid connection reactive power', 'actual', 'var',  'grid', 'num', 'gauge')
ON CONFLICT (type, native_key) DO UPDATE SET
    display_name    = EXCLUDED.display_name,
    display_subname = EXCLUDED.display_subname,
    unit            = EXCLUDED.unit,
    category        = EXCLUDED.category,
    value_type      = EXCLUDED.value_type,
    aggregation     = EXCLUDED.aggregation
WHERE metrics.display_name = metrics.native_key;   -- overwrite stubs only
