-- =====================================================================
-- mbus module: metric definitions, German display names
--
--   Type 'multical303' (Kamstrup MULTICAL 303). native_key is the record
--   header in hex, <DIF>_<VIF>. The rows are the records of two telegrams:
--   the one in sim/fake_meter.py (a heat/cooling meter) and one read from
--   an installed heat meter.
--   DIF: 0x = current value, 1x = maximum, 3x = value during an error state,
--   4x/5x = stored at the yearly target date.
--
--   Units are those of the published payload, not of the meter's display.
--   The category is NULL: it sits on the device, because all meters share
--   these rows.
--
--   Loaded on the first start of the database, after devices.sql. On an
--   existing database (repeatable, only stubs are overwritten):
--     docker compose exec -T timescaledb sh -c \
--       'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < modules/mbus/metrics.de.sql
-- =====================================================================

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
