-- =====================================================================
-- senec module: English display names
--   seed.de.sql carries German display names. This file puts the English ones
--   over them: apply it after seed.de.sql, and again whenever seed.de.sql was
--   applied again. It changes display_name and display_subname only, also
--   on metrics curated by hand.
--
--     docker compose exec -T timescaledb sh -c \
--       'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < modules/senec/seed.en.sql
-- =====================================================================

-- ENERGY: the figures of the unit's overview page. PM1OBJ1: the meter at the
-- grid connection point. PV1: the PV inputs. TEMPMEASURE: the unit itself.
INSERT INTO metrics (type, native_key, display_name, display_subname, unit, category, value_type, aggregation) VALUES
  ('senec_v3', 'ENERGY.GUI_HOUSE_POW',                 'House consumption',       'actual', 'watt',      NULL,      'num', 'gauge'),
  ('senec_v3', 'ENERGY.GUI_GRID_POW',                  'Grid power',              'actual', 'watt',      'grid',    'num', 'gauge'),
  ('senec_v3', 'ENERGY.GUI_INVERTER_POWER',            'PV power',                'actual', 'watt',      'pv',      'num', 'gauge'),
  ('senec_v3', 'ENERGY.GUI_BAT_DATA_POWER',            'Battery power',           'actual', 'watt',      'battery', 'num', 'gauge'),
  ('senec_v3', 'ENERGY.GUI_BAT_DATA_VOLTAGE',          'Battery voltage',         'actual', 'volt',      'battery', 'num', 'gauge'),
  ('senec_v3', 'ENERGY.GUI_BAT_DATA_CURRENT',          'Battery current',         'actual', 'ampere',    'battery', 'num', 'gauge'),
  ('senec_v3', 'ENERGY.GUI_BAT_DATA_FUEL_CHARGE',      'Battery state of charge', 'actual', 'percent',   'battery', 'num', 'gauge'),
  ('senec_v3', 'ENERGY.GUI_BAT_DATA_MAX_CELL_VOLTAGE', 'Battery cell voltage',    'max',    'millivolt', 'battery', 'num', 'gauge'),
  ('senec_v3', 'ENERGY.GUI_BAT_DATA_MIN_CELL_VOLTAGE', 'Battery cell voltage',    'min',    'millivolt', 'battery', 'num', 'gauge'),
  ('senec_v3', 'ENERGY.STAT_STATE',                    'System state',            'state',  NULL,        NULL,      'num', 'gauge'),
  ('senec_v3', 'ENERGY.STAT_HOURS_OF_OPERATION',       'Operating time',          'actual', 'hour',      NULL,      'num', 'counter'),
  ('senec_v3', 'PM1OBJ1.FREQ',                         'Grid frequency',          'actual', 'hertz',     'grid',    'num', 'gauge'),
  ('senec_v3', 'PM1OBJ1.P_TOTAL',                      'Grid meter power',        'total',  'watt',      'grid',    'num', 'gauge'),
  ('senec_v3', 'PM1OBJ1.P_AC.0',                       'Grid meter power',        'L1',     'watt',      'grid',    'num', 'gauge'),
  ('senec_v3', 'PM1OBJ1.P_AC.1',                       'Grid meter power',        'L2',     'watt',      'grid',    'num', 'gauge'),
  ('senec_v3', 'PM1OBJ1.P_AC.2',                       'Grid meter power',        'L3',     'watt',      'grid',    'num', 'gauge'),
  ('senec_v3', 'PM1OBJ1.U_AC.0',                       'Grid voltage',            'L1',     'volt',      'grid',    'num', 'gauge'),
  ('senec_v3', 'PM1OBJ1.U_AC.1',                       'Grid voltage',            'L2',     'volt',      'grid',    'num', 'gauge'),
  ('senec_v3', 'PM1OBJ1.U_AC.2',                       'Grid voltage',            'L3',     'volt',      'grid',    'num', 'gauge'),
  ('senec_v3', 'PM1OBJ1.I_AC.0',                       'Grid current',            'L1',     'ampere',    'grid',    'num', 'gauge'),
  ('senec_v3', 'PM1OBJ1.I_AC.1',                       'Grid current',            'L2',     'ampere',    'grid',    'num', 'gauge'),
  ('senec_v3', 'PM1OBJ1.I_AC.2',                       'Grid current',            'L3',     'ampere',    'grid',    'num', 'gauge'),
  ('senec_v3', 'PV1.MPP_POWER.0',                      'PV string power',         'MPP 1',  'watt',      'pv',      'num', 'gauge'),
  ('senec_v3', 'PV1.MPP_POWER.1',                      'PV string power',         'MPP 2',  'watt',      'pv',      'num', 'gauge'),
  ('senec_v3', 'PV1.MPP_POWER.2',                      'PV string power',         'MPP 3',  'watt',      'pv',      'num', 'gauge'),
  ('senec_v3', 'PV1.MPP_VOL.0',                        'PV string voltage',       'MPP 1',  'volt',      'pv',      'num', 'gauge'),
  ('senec_v3', 'PV1.MPP_VOL.1',                        'PV string voltage',       'MPP 2',  'volt',      'pv',      'num', 'gauge'),
  ('senec_v3', 'PV1.MPP_VOL.2',                        'PV string voltage',       'MPP 3',  'volt',      'pv',      'num', 'gauge'),
  ('senec_v3', 'PV1.MPP_CUR.0',                        'PV string current',       'MPP 1',  'ampere',    'pv',      'num', 'gauge'),
  ('senec_v3', 'PV1.MPP_CUR.1',                        'PV string current',       'MPP 2',  'ampere',    'pv',      'num', 'gauge'),
  ('senec_v3', 'PV1.MPP_CUR.2',                        'PV string current',       'MPP 3',  'ampere',    'pv',      'num', 'gauge'),
  ('senec_v3', 'TEMPMEASURE.BATTERY_TEMP',             'Battery temperature',     'actual', 'celsius',   'battery', 'num', 'gauge'),
  ('senec_v3', 'TEMPMEASURE.CASE_TEMP',                'Case temperature',        'actual', 'celsius',   NULL,      'num', 'gauge'),
  ('senec_v3', 'TEMPMEASURE.MCU_TEMP',                 'Controller temperature',  'actual', 'celsius',   NULL,      'num', 'gauge')
ON CONFLICT (type, native_key) DO UPDATE SET
    display_name    = EXCLUDED.display_name,
    display_subname = EXCLUDED.display_subname;

-- BMS: the battery modules. The unit has four slots, A to D; index 0 of a
-- per-module array is module A. Every slot gets its rows, fitted or not: per
-- module, per cell (16 voltages) and per temperature sensor (3).
INSERT INTO metrics (type, native_key, display_name, display_subname, unit, category, value_type, aggregation)
SELECT 'senec_v3'::device_type, format('BMS.%s.%s', f.field, m), f.name, format(f.subname, chr(65 + m)),
       f.unit, 'battery'::category, 'num'::val_kind, f.aggregation::agg_kind
FROM generate_series(0, 3) AS m,
     (VALUES
        ('VOLTAGE',          'Battery module voltage',         'module %s',     'volt',      'gauge'),
        ('CURRENT',          'Battery module current',         'module %s',     'ampere',    'gauge'),
        ('SOC',              'Battery module state of charge', 'module %s',     'percent',   'gauge'),
        ('SOH',              'Battery module state of health', 'module %s',     'percent',   'gauge'),
        ('CYCLES',           'Battery module cycles',          'module %s',     NULL,        'counter'),
        ('MAX_CELL_VOLTAGE', 'Battery module cell voltage',    'module %s max', 'millivolt', 'gauge'),
        ('MIN_CELL_VOLTAGE', 'Battery module cell voltage',    'module %s min', 'millivolt', 'gauge'),
        ('TEMP_MAX',         'Battery module temperature',     'module %s max', 'celsius',   'gauge'),
        ('TEMP_MIN',         'Battery module temperature',     'module %s min', 'celsius',   'gauge')
     ) AS f(field, name, subname, unit, aggregation)
UNION ALL
SELECT 'senec_v3'::device_type, format('BMS.CELL_VOLTAGES_MODULE_%s.%s', chr(65 + m), c),
       'Battery cell voltage', format('module %s cell %s', chr(65 + m), c + 1),
       'millivolt', 'battery'::category, 'num'::val_kind, 'gauge'::agg_kind
FROM generate_series(0, 3) AS m, generate_series(0, 15) AS c
UNION ALL
SELECT 'senec_v3'::device_type, format('BMS.CELL_TEMPERATURES_MODULE_%s.%s', chr(65 + m), c),
       'Battery cell temperature', format('module %s sensor %s', chr(65 + m), c + 1),
       'celsius', 'battery'::category, 'num'::val_kind, 'gauge'::agg_kind
FROM generate_series(0, 3) AS m, generate_series(0, 2) AS c
ON CONFLICT (type, native_key) DO UPDATE SET
    display_name    = EXCLUDED.display_name,
    display_subname = EXCLUDED.display_subname;
