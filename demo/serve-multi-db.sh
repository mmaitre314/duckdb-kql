#!/usr/bin/env bash
#
# Serve two DuckDB files as two Kusto databases, and query across them.
#
#   ./serve-multi-db.sh          # build the files, start the server
#   ./serve-multi-db.sh --check  # build, prove the queries work, exit
#
# What it shows: `duckdb-kql serve` starts on an empty in-memory database and
# runs an init script that ATTACHes the two files. Each one is a Kusto database
# to any client, so `database("Sales").Orders | join database("Customers")...`
# reaches across both — the thing a single DuckDB file cannot do on its own.
#
# Requires the DuckDB CLI (https://duckdb.org/docs/installation) to build the
# sample files, and `pip install 'duckdb-kql[duckdb]'` to serve them.

set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-31415}"

if ! command -v duckdb >/dev/null 2>&1; then
    echo "This script builds the sample databases with the DuckDB CLI, which is" >&2
    echo "not on PATH. Install it from https://duckdb.org/docs/installation" >&2
    echo "(the Python 'duckdb' package is a library, not this command)." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 1. Build two database files, each from its own .sql script
# ---------------------------------------------------------------------------

rm -f sales.duckdb customers.duckdb

echo "==> building sales.duckdb"
duckdb sales.duckdb < sql/sales.sql

echo "==> building customers.duckdb"
duckdb customers.duckdb < sql/customers.sql

duckdb sales.duckdb -c "SELECT 'Sales.Orders' AS tbl, count(*) AS rows FROM Orders"
duckdb customers.duckdb -c "SELECT 'Customers.Customers' AS tbl, count(*) AS rows FROM Customers"

# ---------------------------------------------------------------------------
# 2. Serve them, attached by the init script
# ---------------------------------------------------------------------------

# A cross-database KQL query: the join reaches into the other attached file.
# Note the unqualified pipeline still works — `database()` only qualifies the
# source it is attached to.
read -r -d '' QUERY <<'KQL' || true
database("Sales").Orders
| where Status == "shipped"
| join kind=inner (database("Customers").Customers) on CustomerId
| summarize Revenue = sum(Total), Orders = count() by Region, Tier
| sort by Revenue desc
| take 5
KQL

if [[ "${1:-}" == "--check" ]]; then
    # Non-interactive: start the server, run the query over HTTP, stop. This is
    # the path CI takes, so the demo cannot rot without something noticing.
    echo "==> checking the server answers a cross-database query"
    duckdb-kql serve --init sql/attach.sql --port "$PORT" &
    SERVER=$!
    trap 'kill "$SERVER" 2>/dev/null || true' EXIT

    for _ in $(seq 1 50); do
        curl -sf "http://127.0.0.1:${PORT}/" >/dev/null 2>&1 && break
        sleep 0.2
    done

    curl -sf -X POST "http://127.0.0.1:${PORT}/v1/rest/query" \
        -H 'Content-Type: application/json' \
        --data "$(QUERY="$QUERY" python3 -c 'import json,os; print(json.dumps({"csl": os.environ["QUERY"]}))')" \
    | python3 -c '
import json, sys
table = json.load(sys.stdin)["Tables"][0]
print(" | ".join(c["ColumnName"] for c in table["Columns"]))
for row in table["Rows"]:
    print(" | ".join(str(v) for v in row))
'
    echo "==> ok"
    exit 0
fi

cat <<EOF

Starting the server. Two databases are attached by sql/attach.sql, so a client
sees three: memory (empty, the one it connects to), Sales, and Customers.

Try it from another terminal:

  curl -s -X POST http://127.0.0.1:${PORT}/v1/rest/mgmt \\
      -H 'Content-Type: application/json' \\
      -d '{"csl":".show databases entities"}'

  curl -s -X POST http://127.0.0.1:${PORT}/v1/rest/query \\
      -H 'Content-Type: application/json' \\
      -d '{"csl":"database(\\"Sales\\").Orders | count"}'

Or open https://dataexplorer.azure.com -> Add connection -> http://127.0.0.1:${PORT}

EOF

exec duckdb-kql serve --init sql/attach.sql --port "$PORT"
