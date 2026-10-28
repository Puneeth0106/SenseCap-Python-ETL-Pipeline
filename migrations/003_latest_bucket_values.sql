-- Select the newest reading for each metric in each ten-minute bucket.
CREATE OR REPLACE VIEW sensor_readings_10min AS
WITH ranked AS (
    SELECT date_trunc('hour', timestamp)
               + INTERVAL '10 minutes' * FLOOR(EXTRACT(MINUTE FROM timestamp) / 10) AS bucket,
           device_eui, device_alias, metric_name, value,
           ROW_NUMBER() OVER (
               PARTITION BY date_trunc('hour', timestamp)
                   + INTERVAL '10 minutes' * FLOOR(EXTRACT(MINUTE FROM timestamp) / 10),
                   device_eui, device_alias, metric_name
               ORDER BY timestamp DESC, metric_id
           ) AS position
    FROM sensor_telemetry
)
SELECT bucket, device_eui, device_alias,
       MAX(value) FILTER (WHERE metric_name = 'Soil_Temperature') AS soil_temperature,
       MAX(value) FILTER (WHERE metric_name = 'Soil_Moisture') AS soil_moisture,
       MAX(value) FILTER (WHERE metric_name = 'Soil_Conductivity') AS soil_conductivity
FROM ranked
WHERE position = 1
GROUP BY bucket, device_eui, device_alias;
