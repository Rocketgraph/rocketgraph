CREATE DATABASE IF NOT EXISTS demo;

CREATE TABLE IF NOT EXISTS demo.logs
(
    timestamp DateTime64(3, 'UTC'),
    service   LowCardinality(String),
    level     LowCardinality(String),
    message   String
)
ENGINE = MergeTree
ORDER BY (service, timestamp);
