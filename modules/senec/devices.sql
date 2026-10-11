-- =====================================================================
-- senec module: device type and device
--
--   device_key = 'senec/<SENEC_DEVICE>'; the row is the default name. Another
--   name needs a row of its own (README, "Devices and metrics").
--
--   Loaded on the first start of the database. Run it as it is, not inside
--   a transaction: a new enum value can only be used once it is committed.
-- =====================================================================

ALTER TYPE device_type ADD VALUE IF NOT EXISTS 'senec_v3';

INSERT INTO devices (type, device_key, name) VALUES
  ('senec_v3', 'senec/home', 'SENEC.Home V3 hybrid duo LFP')
ON CONFLICT (device_key) DO NOTHING;
