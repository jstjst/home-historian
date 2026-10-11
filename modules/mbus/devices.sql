-- =====================================================================
-- mbus module: device type
--
--   No devices: names and addresses of the meters belong to the
--   installation (README, "Devices and metrics").
--
--   Loaded on the first start of the database.
-- =====================================================================

ALTER TYPE device_type ADD VALUE IF NOT EXISTS 'multical303';
