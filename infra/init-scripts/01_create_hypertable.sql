CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS readings (
    id BIGSERIAL,
    time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sensor_id TEXT NOT NULL,
    location TEXT,
    metric TEXT NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    unit TEXT,
    raw_value DOUBLE PRECISION,
    metadata JSONB,
    PRIMARY KEY (id, time)
);

SELECT create_hypertable('readings', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_readings_sensor_time ON readings (sensor_id, metric, time DESC);
