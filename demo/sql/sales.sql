-- Sample data for the Sales database.
--
-- Run by demo/serve-multi-db.sh through the DuckDB CLI, which is what creates
-- the file:  duckdb sales.duckdb < sql/sales.sql

CREATE OR REPLACE TABLE Orders (
    OrderId   BIGINT,
    CustomerId BIGINT,
    Placed    TIMESTAMP,
    Total     DOUBLE,
    Status    VARCHAR
);

INSERT INTO Orders
SELECT
    n                                              AS OrderId,
    1 + (n * 7) % 40                               AS CustomerId,
    TIMESTAMP '2026-03-01 00:00:00' + INTERVAL (n * 37) MINUTE AS Placed,
    round(15 + (n * 13) % 400 + (n % 7) / 4.0, 2)  AS Total,
    CASE WHEN n % 17 = 0 THEN 'cancelled'
         WHEN n % 5 = 0  THEN 'pending'
         ELSE 'shipped' END                        AS Status
FROM range(1, 501) t(n);
