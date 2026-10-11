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
--   Devices: none here. Names and addresses belong to the installation; the
--   example below shows the rows, see the README.
--
--   Display names are German, as in the installation they come from.
--   seed.en.sql puts English names over them.
--
--   Loaded automatically on an empty database volume. Later, by hand:
--     docker compose exec -T timescaledb sh -c \
--       'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < modules/mbus/seed.de.sql
--   Running it again does no harm. Metrics curated by hand are left alone;
--   only stubs are overwritten.
-- =====================================================================

-- Example of an installation's devices: device_key = 'mbus/<name from
-- MBUS_METERS>'. The category sits on the device because all meters share
-- their metrics.
-- INSERT INTO devices (type, device_key, name, category) VALUES
--   ('multical303', 'mbus/hc1',       'WMZ HK1',           'hc1'),
--   ('multical303', 'mbus/hc2',       'WMZ HK2',           'hc2'),
--   ('multical303', 'mbus/hc3',       'WMZ HK3',           'hc3'),
--   ('multical303', 'mbus/secondary', 'WMZ Sekundärkreis', 'secondary')
-- ON CONFLICT (device_key) DO NOTHING;

-- DIF: 0x = current value, 1x = maximum, 3x = value during error state,
--      4x/5x = stored at the yearly target date (storage number 1).
INSERT INTO metrics (type, native_key, display_name, display_subname, unit, category, value_type, aggregation) VALUES
  ('multical303', '04_05',   'Wärmemenge',          'Ist',      'wh',         NULL, 'num', 'counter'),
  ('multical303', '04_06',   'Wärmemenge',          'Ist',      'wh',         NULL, 'num', 'counter'),
  ('multical303', '04_863C', 'Kältemenge',          'Ist',      'wh',         NULL, 'num', 'counter'),
  ('multical303', '04_13',   'Volumen',             'Ist',      'm3',         NULL, 'num', 'counter'),
  ('multical303', '04_14',   'Volumen',             'Ist',      'm3',         NULL, 'num', 'counter'),
  ('multical303', '04_FF07', 'Energie E8 Vorlauf',  'Ist',      'm3*celsius', NULL, 'num', 'counter'),
  ('multical303', '04_FF08', 'Energie E9 Rücklauf', 'Ist',      'm3*celsius', NULL, 'num', 'counter'),
  ('multical303', '03_22',   'Betriebszeit',        'Ist',      'second',     NULL, 'num', 'counter'),
  ('multical303', '33_22',   'Fehlerzeit',          'Ist',      'second',     NULL, 'num', 'counter'),
  ('multical303', '02_59',   'Vorlauftemperatur',   'Ist',      'celsius',    NULL, 'num', 'gauge'),
  ('multical303', '02_5D',   'Rücklauftemperatur',  'Ist',      'celsius',    NULL, 'num', 'gauge'),
  ('multical303', '02_61',   'Spreizung',           'Ist',      'kelvin',     NULL, 'num', 'gauge'),
  ('multical303', '02_2D',   'Wärmeleistung',       'Ist',      'watt',       NULL, 'num', 'gauge'),
  ('multical303', '12_2D',   'Wärmeleistung',       'Max',      'watt',       NULL, 'num', 'gauge'),
  ('multical303', '02_3B',   'Durchfluss',          'Ist',      'm3/hour',    NULL, 'num', 'gauge'),
  ('multical303', '12_3B',   'Durchfluss',          'Max',      'm3/hour',    NULL, 'num', 'gauge'),
  ('multical303', '02_FF22', 'Infocode',            'Status',   NULL,         NULL, 'num', 'gauge'),
  ('multical303', '44_05',   'Wärmemenge',          'Stichtag', 'wh',         NULL, 'num', 'gauge'),
  ('multical303', '44_06',   'Wärmemenge',          'Stichtag', 'wh',         NULL, 'num', 'gauge'),
  ('multical303', '44_863C', 'Kältemenge',          'Stichtag', 'wh',         NULL, 'num', 'gauge'),
  ('multical303', '44_13',   'Volumen',             'Stichtag', 'm3',         NULL, 'num', 'gauge'),
  ('multical303', '44_14',   'Volumen',             'Stichtag', 'm3',         NULL, 'num', 'gauge'),
  ('multical303', '44_FF07', 'Energie E8 Vorlauf',  'Stichtag', 'm3*celsius', NULL, 'num', 'gauge'),
  ('multical303', '44_FF08', 'Energie E9 Rücklauf', 'Stichtag', 'm3*celsius', NULL, 'num', 'gauge'),
  ('multical303', '52_2D',   'Wärmeleistung',       'Max Jahr', 'watt',       NULL, 'num', 'gauge'),
  ('multical303', '52_3B',   'Durchfluss',          'Max Jahr', 'm3/hour',    NULL, 'num', 'gauge'),
  ('multical303', '42_6C',   'Stichtag',            'Status',   NULL,         NULL, 'str', 'gauge')
ON CONFLICT (type, native_key) DO UPDATE SET
    display_name    = EXCLUDED.display_name,
    display_subname = EXCLUDED.display_subname,
    unit            = EXCLUDED.unit,
    category        = EXCLUDED.category,
    value_type      = EXCLUDED.value_type,
    aggregation     = EXCLUDED.aggregation
WHERE metrics.display_name = metrics.native_key;   -- overwrite stubs only
