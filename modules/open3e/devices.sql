-- =====================================================================
-- open3e module: device types and devices
--
--   One device per control unit: device_key = 'open3e/<key in
--   config/devices.json>'. The two rows are those of the shipped
--   devices.json, which matches the simulator. Other keys need rows of their
--   own (README, "Devices and metrics").
--
--   Loaded on the first start of the database. Run it as it is, not inside
--   a transaction: a new enum value can only be used once it is committed.
-- =====================================================================

ALTER TYPE device_type ADD VALUE IF NOT EXISTS 'vitocal250a';
ALTER TYPE device_type ADD VALUE IF NOT EXISTS 'vitocharge';

INSERT INTO devices (type, device_key, name) VALUES
  ('vitocal250a', 'open3e/vitocal',    'Vitocal 250-A'),
  ('vitocharge',  'open3e/vitocharge', 'Vitocharge VX3')
ON CONFLICT (device_key) DO NOTHING;
