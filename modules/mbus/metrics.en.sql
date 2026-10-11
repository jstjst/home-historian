-- =====================================================================
-- mbus module: English display names
--
--   Sets display_name and display_subname of the rows below, also where
--   they were changed by hand. Covers every row of
--   metrics.de.sql.
--   Apply it after metrics.de.sql:
--     docker compose exec -T timescaledb sh -c \
--       'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < modules/mbus/metrics.en.sql
-- =====================================================================

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
    display_subname = EXCLUDED.display_subname;
