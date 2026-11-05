-- 002_views.sql — 10-minute view, device seeds, read-only role.
-- Idempotent: safe to re-run against an existing database.
-- Target: managed PostgreSQL (Supabase).

-- 1. Ten-minute bucketed view.
-- One row per device per 10-minute window, latest value per metric.
-- Dashboard readers query this instead of the raw table.
CREATE OR REPLACE VIEW sensor_readings_10min AS
SELECT
    date_trunc('hour', timestamp)
        + INTERVAL '10 minutes' * FLOOR(EXTRACT(MINUTE FROM timestamp) / 10)
        AS bucket,
    device_eui,
    device_alias,
    MAX(value) FILTER (WHERE metric_name = 'Soil_Temperature')   AS soil_temperature,
    MAX(value) FILTER (WHERE metric_name = 'Soil_Moisture')      AS soil_moisture,
    MAX(value) FILTER (WHERE metric_name = 'Soil_Conductivity')  AS soil_conductivity
FROM sensor_telemetry
GROUP BY bucket, device_eui, device_alias
ORDER BY bucket DESC;

-- 2. Repoint sensor_telemetry_wide to use the same 10-minute buckets.
-- Replaces the per-timestamp wide view from 001_init.sql so downstream
-- consumers see the cleaner bucketed data.
CREATE OR REPLACE VIEW sensor_telemetry_wide AS
SELECT
    bucket        AS timestamp,
    device_eui,
    device_alias,
    soil_temperature,
    soil_moisture,
    soil_conductivity
FROM sensor_readings_10min;

-- 3. Seed devices.
-- ON CONFLICT keeps this idempotent: re-running updates the alias/active flag.
INSERT INTO sensor_devices (device_eui, device_alias, is_active)
VALUES
    ('2CF7F1C063700148', 'Alpha', TRUE),
    ('2CF7F1C060800087', 'Beta',  FALSE)
ON CONFLICT (device_eui) DO UPDATE SET
    device_alias = EXCLUDED.device_alias,
    is_active    = EXCLUDED.is_active,
    updated_at   = now();

-- 4. Read-only role for dashboard consumers.
-- Supabase does not allow CREATE ROLE from the SQL editor with the default
-- postgres user. Use the Supabase dashboard (Authentication > Roles) to create
-- the role manually if this statement fails, then re-run the GRANTs below.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'telemetry_reader') THEN
        CREATE ROLE telemetry_reader WITH LOGIN PASSWORD 'CHANGE_ME_BEFORE_USE';
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO telemetry_reader;
GRANT SELECT ON sensor_devices        TO telemetry_reader;
GRANT SELECT ON sensor_telemetry      TO telemetry_reader;
GRANT SELECT ON sensor_readings_10min TO telemetry_reader;
GRANT SELECT ON sensor_telemetry_wide TO telemetry_reader;

-- Future tables/views in public also get SELECT for this role.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO telemetry_reader;
