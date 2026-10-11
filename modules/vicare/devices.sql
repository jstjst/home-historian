-- =====================================================================
-- vicare module: device type
--
--   No devices: gateway and device id belong to the installation (README,
--   "Devices and metrics").
--
--   Loaded on the first start of the database.
-- =====================================================================

ALTER TYPE device_type ADD VALUE IF NOT EXISTS 'vitocal250a';
