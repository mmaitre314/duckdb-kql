-- Sample data for the Customers database.
--
-- A separate DuckDB file on purpose: the point of the demo is a KQL query that
-- joins across two of them.

CREATE OR REPLACE TABLE Customers (
    CustomerId BIGINT,
    Name       VARCHAR,
    Region     VARCHAR,
    Tier       VARCHAR
);

INSERT INTO Customers
SELECT
    n                                                     AS CustomerId,
    'customer-' || printf('%03d', n)                      AS Name,
    ['us-west-2', 'us-east-1', 'eu-west-1'][1 + n % 3]    AS Region,
    CASE WHEN n % 10 = 0 THEN 'enterprise'
         WHEN n % 3 = 0  THEN 'business'
         ELSE 'free' END                                  AS Tier
FROM range(1, 41) t(n);
