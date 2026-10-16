-- =====================================================================
-- tuya module: metric definitions, German display names
--
--   Type 'tuya'. native_key is Tuya's data point code. The rows are the
--   codes a WiFi temperature and humidity sensor (Tuya category wsdcg,
--   product "T&H Sensor") delivered, plus "online", which the module derives
--   from Tuya's online/offline events. Codes of other sensors (battery_state,
--   temp_periodic_report, ...) are stored as stubs.
--
--   Units are those of the published payload: the module applies the scale
--   from the device's specification, so 185 with scale 1 arrives as 18.5.
--
--   The category is NULL: a room sensor belongs to no part of the
--   installation. Where it hangs goes into devices.location.
--
--   Loaded on the first start of the database, after devices.sql. On an
--   existing database (repeatable, only stubs are overwritten):
--     docker compose exec -T timescaledb sh -c \
--       'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < modules/tuya/metrics.de.sql
-- =====================================================================

INSERT INTO metrics (type, native_key, display_name, display_subname, unit, category, value_type, aggregation) VALUES
  ('tuya', 'va_temperature',       'Temperatur',                    'Ist',    'celsius', NULL, 'num',  'gauge'),
  ('tuya', 'va_humidity',          'Luftfeuchte',                   'Ist',    'percent', NULL, 'num',  'gauge'),
  ('tuya', 'battery_percentage',   'Batterie',                      'Ist',    'percent', NULL, 'num',  'gauge'),
  ('tuya', 'online',               'Online',                        'Status', NULL,      NULL, 'bool', 'gauge'),
  ('tuya', 'temp_alarm',           'Temperatur Alarm',              'Status', NULL,      NULL, 'str',  'gauge'),
  ('tuya', 'hum_alarm',            'Luftfeuchte Alarm',             'Status', NULL,      NULL, 'str',  'gauge'),
  ('tuya', 'maxtemp_set',          'Temperatur Alarmgrenze oben',   'Soll',   'celsius', NULL, 'num',  'gauge'),
  ('tuya', 'minitemp_set',         'Temperatur Alarmgrenze unten',  'Soll',   'celsius', NULL, 'num',  'gauge'),
  ('tuya', 'maxhum_set',           'Luftfeuchte Alarmgrenze oben',  'Soll',   'percent', NULL, 'num',  'gauge'),
  ('tuya', 'minihum_set',          'Luftfeuchte Alarmgrenze unten', 'Soll',   'percent', NULL, 'num',  'gauge'),
  ('tuya', 'temp_sensitivity',     'Temperatur Meldeschwelle',      'Soll',   'kelvin',  NULL, 'num',  'gauge'),
  ('tuya', 'hum_sensitivity',      'Luftfeuchte Meldeschwelle',     'Soll',   'percent', NULL, 'num',  'gauge'),
  ('tuya', 'temp_unit_convert',    'Temperatureinheit Anzeige',     'Soll',   NULL,      NULL, 'str',  'gauge')
ON CONFLICT (type, native_key) DO UPDATE SET
    display_name    = EXCLUDED.display_name,
    display_subname = EXCLUDED.display_subname,
    unit            = EXCLUDED.unit,
    category        = EXCLUDED.category,
    value_type      = EXCLUDED.value_type,
    aggregation     = EXCLUDED.aggregation
WHERE metrics.display_name = metrics.native_key;   -- overwrite stubs only
