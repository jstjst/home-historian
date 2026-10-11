-- =====================================================================
-- open3e module: English display names
--   seed.de.sql carries German display names. This file puts the English ones
--   over them: apply it after seed.de.sql, and again whenever seed.de.sql was
--   applied again. It changes display_name and display_subname only, also
--   on metrics curated by hand.
--
--     docker compose exec -T timescaledb sh -c \
--       'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < modules/open3e/seed.en.sql
-- =====================================================================

-- native_key = remainder of the topic after the device_key ('/' -> '.').
-- open3e flattens complex data points itself: one topic per leaf, nested
-- sub-values included.
INSERT INTO metrics (type, native_key, display_name, display_subname, unit, category, value_type, aggregation) VALUES
  ('vitocal250a', '268_FlowTemperatureSensor.Actual',            'HC1 flow temperature', 'actual',  'celsius', 'hc1', 'num', 'gauge'),
  ('vitocal250a', '268_FlowTemperatureSensor.Minimum',           'HC1 flow temperature', 'min',     'celsius', 'hc1', 'num', 'gauge'),
  ('vitocal250a', '268_FlowTemperatureSensor.Maximum',           'HC1 flow temperature', 'max',     'celsius', 'hc1', 'num', 'gauge'),
  ('vitocal250a', '268_FlowTemperatureSensor.Average',           'HC1 flow temperature', 'average', 'celsius', 'hc1', 'num', 'gauge'),
  ('vitocal250a', '268_FlowTemperatureSensor.SensorStatus.ID',   'HC1 flow temperature', 'state',   NULL,      'hc1', 'num', 'gauge'),
  ('vitocal250a', '268_FlowTemperatureSensor.SensorStatus.Text', 'HC1 flow temperature', 'state',   NULL,      'hc1', 'str', 'gauge'),
  ('vitocal250a', '396_DomesticHotWaterTemperatureSetpoint',     'DHW temperature', 'setpoint', 'celsius', 'dhw', 'num', 'gauge'),
  ('vitocharge', '1603_PointOfCommonCouplingPower.ActivePower',   'Grid connection active power',   'actual', 'watt', 'grid', 'num', 'gauge'),
  ('vitocharge', '1603_PointOfCommonCouplingPower.ReactivePower', 'Grid connection reactive power', 'actual', 'var',  'grid', 'num', 'gauge')
ON CONFLICT (type, native_key) DO UPDATE SET
    display_name    = EXCLUDED.display_name,
    display_subname = EXCLUDED.display_subname;
