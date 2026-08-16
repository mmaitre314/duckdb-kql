-- Init script for `duckdb-kql serve --init`.
--
-- The server starts on an empty in-memory database and this attaches the two
-- files built by demo/serve-multi-db.sh. Each attached database is a Kusto
-- database to a client, reachable as database("Sales").Orders.
--
-- ATTACH is a DuckDB statement with no KQL spelling, which is why the init
-- script is SQL even though every query afterwards is KQL.
--
-- READ_ONLY because nothing served here should be able to write, and saying so
-- at the ATTACH is stronger than trusting the translated surface to stay
-- read-only by accident.

ATTACH 'sales.duckdb'     AS Sales     (READ_ONLY);
ATTACH 'customers.duckdb' AS Customers (READ_ONLY);
