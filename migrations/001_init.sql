-- 001_init.sql — base schema for the SenseCAP telemetry pipeline.
-- Idempotent: safe to re-run against an existing database.
-- Target: managed PostgreSQL (Supabase). Plain Postgres by design -- at the
-- projected volume (~630k rows/year at 5-minute sampling) a standard indexed
-- table is sufficient; TimescaleDB was dropped. See BATCH_PIPELINE_ARCHITECTURE.md §3.

CREATE TABLE IF NOT EXISTS sensor_devices (
    device_eui TEXT PRIMARY KEY,
    device_alias TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sensor_telemetry (
    timestamp TIMESTAMPTZ NOT NULL,
    device_eui TEXT NOT NULL REFERENCES sensor_devices(device_eui),
    device_alias TEXT NOT NULL,
    metric_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    value DOUBLE PRECISION,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (timestamp, device_eui, metric_id)
);

CREATE INDEX IF NOT EXISTS ix_sensor_telemetry_device_time
    ON sensor_telemetry (device_eui, timestamp DESC);

CREATE INDEX IF NOT EXISTS ix_sensor_telemetry_metric_time
    ON sensor_telemetry (metric_name, timestamp DESC);

CREATE TABLE IF NOT EXISTS ingest_runs (
    run_id UUID PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    trigger_source TEXT,
    devices_requested INTEGER NOT NULL DEFAULT 0,
    api_calls INTEGER NOT NULL DEFAULT 0,
    rows_fetched INTEGER NOT NULL DEFAULT 0,
    rows_loaded INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS ix_ingest_runs_started_at
    ON ingest_runs (started_at DESC);

-- Wide view for dashboard consumption (§3.4). The ETL never writes here.
CREATE OR REPLACE VIEW sensor_telemetry_wide AS
SELECT
    timestamp,
    device_eui,
    device_alias,
    MAX(value) FILTER (WHERE metric_name = 'Soil_Temperature') AS soil_temperature,
    MAX(value) FILTER (WHERE metric_name = 'Soil_Moisture') AS soil_moisture,
    MAX(value) FILTER (WHERE metric_name = 'Soil_Conductivity') AS soil_conductivity
FROM sensor_telemetry
GROUP BY timestamp, device_eui, device_alias;
