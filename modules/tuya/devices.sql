-- =====================================================================
-- tuya module: device type
--
--   No devices: names and device ids belong to the installation (README,
--   "Devices and metrics").
--
--   Loaded on the first start of the database.
-- =====================================================================

ALTER TYPE device_type ADD VALUE IF NOT EXISTS 'tuya';
