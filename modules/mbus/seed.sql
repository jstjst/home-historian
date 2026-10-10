-- =====================================================================
-- mbus module: master data
--   Metrics: curated definitions for the records of a Kamstrup MULTICAL 303
--   (type 'multical303'). One definition serves every meter of the type; which
--   circuit a meter sits in is the DEVICE's category.
--   native_key = "<DIF>_<VIF>", the record header in hex. The keys below are
--   those of two telegrams: the one in sim/fake_meter.py (a heat/cooling
--   meter) and one read from an installed heat meter. The VIF carries the
--   resolution, so energy and volume have a key per resolution (05/06 and
--   13/14); the payload is scaled to the same unit either way. A meter
--   configured differently sends other records; the ingester creates those
--   as stubs.
--   E8 and E9 (volume * inlet / outlet temperature) arrive in m^3 * degree
--   Celsius whatever the meter's resolution: the module scales them from the
--   volume record of the same telegram. Their increase divided by the
--   increase of the volume is the flow-weighted mean temperature.
--   Units are those of the payload (pyMeterBus scales to Wh, m^3, W, seconds),
--   not those on the meter's display.
--
--   Devices: none here. Names and addresses belong to the installation, see
--   the README for the rows to insert.
--
--   Loaded automatically on an empty database volume. Later, by hand:
--     docker compose exec -T timescaledb sh -c \
--       'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < modules/mbus/seed.sql
--   Running it again does no harm. Metrics curated by hand are left alone;
--   only stubs are overwritten.
-- =====================================================================

-- DIF: 0x = current value, 1x = maximum, 3x = value during error state,
--      4x/5x = stored at the yearly target date (storage number 1).
INSERT INTO metrics (type, native_key, display_name, display_subname, unit, category, value_type, aggregation) VALUES
  ('multical303', '04_05',   'Heat energy',             'actual',      'wh',         NULL, 'num', 'counter'),
  ('multical303', '04_06',   'Heat energy',             'actual',      'wh',         NULL, 'num', 'counter'),
  ('multical303', '04_863C', 'Cooling energy',          'actual',      'wh',         NULL, 'num', 'counter'),
  ('multical303', '04_13',   'Volume',                  'actual',      'm3',         NULL, 'num', 'counter'),
  ('multical303', '04_14',   'Volume',                  'actual',      'm3',         NULL, 'num', 'counter'),
  ('multical303', '04_FF07', 'Inlet energy E8',         'actual',      'm3*celsius', NULL, 'num', 'counter'),
  ('multical303', '04_FF08', 'Outlet energy E9',        'actual',      'm3*celsius', NULL, 'num', 'counter'),
  ('multical303', '03_22',   'Operating time',          'actual',      'second',     NULL, 'num', 'counter'),
  ('multical303', '33_22',   'Operating time in error', 'actual',      'second',     NULL, 'num', 'counter'),
  ('multical303', '02_59',   'Flow temperature',        'actual',      'celsius',    NULL, 'num', 'gauge'),
  ('multical303', '02_5D',   'Return temperature',      'actual',      'celsius',    NULL, 'num', 'gauge'),
  ('multical303', '02_61',   'Temperature difference',  'actual',      'kelvin',     NULL, 'num', 'gauge'),
  ('multical303', '02_2D',   'Thermal power',           'actual',      'watt',       NULL, 'num', 'gauge'),
  ('multical303', '12_2D',   'Thermal power',           'max',         'watt',       NULL, 'num', 'gauge'),
  ('multical303', '02_3B',   'Volume flow',             'actual',      'm3/hour',    NULL, 'num', 'gauge'),
  ('multical303', '12_3B',   'Volume flow',             'max',         'm3/hour',    NULL, 'num', 'gauge'),
  ('multical303', '02_FF22', 'Info code',               'state',       NULL,         NULL, 'num', 'gauge'),
  ('multical303', '44_05',   'Heat energy',             'target date', 'wh',         NULL, 'num', 'gauge'),
  ('multical303', '44_06',   'Heat energy',             'target date', 'wh',         NULL, 'num', 'gauge'),
  ('multical303', '44_863C', 'Cooling energy',          'target date', 'wh',         NULL, 'num', 'gauge'),
  ('multical303', '44_13',   'Volume',                  'target date', 'm3',         NULL, 'num', 'gauge'),
  ('multical303', '44_14',   'Volume',                  'target date', 'm3',         NULL, 'num', 'gauge'),
  ('multical303', '44_FF07', 'Inlet energy E8',         'target date', 'm3*celsius', NULL, 'num', 'gauge'),
  ('multical303', '44_FF08', 'Outlet energy E9',        'target date', 'm3*celsius', NULL, 'num', 'gauge'),
  ('multical303', '52_2D',   'Thermal power',           'max year',    'watt',       NULL, 'num', 'gauge'),
  ('multical303', '52_3B',   'Volume flow',             'max year',    'm3/hour',    NULL, 'num', 'gauge'),
  ('multical303', '42_6C',   'Target date',             'state',       NULL,         NULL, 'str', 'gauge')
ON CONFLICT (type, native_key) DO UPDATE SET
    display_name    = EXCLUDED.display_name,
    display_subname = EXCLUDED.display_subname,
    unit            = EXCLUDED.unit,
    category        = EXCLUDED.category,
    value_type      = EXCLUDED.value_type,
    aggregation     = EXCLUDED.aggregation
WHERE metrics.display_name = metrics.native_key;   -- overwrite stubs only
