-- =====================================================================
-- open3e module: English display names
--
--   Sets display_name and display_subname of the rows below, also where
--   they were changed by hand. Covers the data points of the
--   default selection only (268, 396, 1603).
--   Apply it after metrics.de.sql:
--     docker compose exec -T timescaledb sh -c \
--       'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < modules/open3e/metrics.en.sql
-- =====================================================================

INSERT INTO metrics (type, native_key, display_name, display_subname, unit, category, value_type, aggregation) VALUES
  ('vitocharge',  '1603_PointOfCommonCouplingPower.ActivePower',   'Grid connection point active power',   'actual',            'watt', 'grid', 'num', 'gauge'),
  ('vitocharge',  '1603_PointOfCommonCouplingPower.ReactivePower', 'Grid connection point reactive power', 'actual',            'var', 'grid', 'num', 'gauge'),
  ('vitocal250a', '268_FlowTemperatureSensor.Actual',              'Heating water flow temperature',       'actual',            'celsius', 'secondary', 'num', 'gauge'),
  ('vitocal250a', '268_FlowTemperatureSensor.Average',             'Heating water flow temperature',       'average',           'celsius', 'secondary', 'num', 'gauge'),
  ('vitocal250a', '268_FlowTemperatureSensor.Maximum',             'Heating water flow temperature',       'max',               'celsius', 'secondary', 'num', 'gauge'),
  ('vitocal250a', '268_FlowTemperatureSensor.Minimum',             'Heating water flow temperature',       'min',               'celsius', 'secondary', 'num', 'gauge'),
  ('vitocal250a', '268_FlowTemperatureSensor.SensorStatus.ID',     'Heating water flow temperature',       'sensor state ID',   NULL, 'secondary', 'num', 'gauge'),
  ('vitocal250a', '268_FlowTemperatureSensor.SensorStatus.Text',   'Heating water flow temperature',       'sensor state text', NULL, 'secondary', 'str', 'gauge'),
  ('vitocal250a', '396_DomesticHotWaterTemperatureSetpoint',       'DHW temperature setpoint',             NULL,                'celsius', 'dhw', 'num', 'gauge')
ON CONFLICT (type, native_key) DO UPDATE SET
    display_name    = EXCLUDED.display_name,
    display_subname = EXCLUDED.display_subname;
