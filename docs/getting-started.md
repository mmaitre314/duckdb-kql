# Getting started

Run KQL against local data — Parquet, CSV, a DuckDB file — with no cluster, no
server, and no Java.

- [Install](#install)
- [Your first query](#your-first-query)
- [Loading data](#loading-data)
- [Query parameters](#query-parameters-and-user-input)
- [Translating without running](#translating-without-running)
- [Using the Kusto SDK interface](#using-the-kusto-sdk-interface)
- [Build-time translation](cli.md)
- [When something is not supported](#when-something-is-not-supported)
- [Two things that will bite you](#two-things-that-will-bite-you)

## Install

Pick the layer you need. Each one adds exactly one dependency, and you can move
up later without changing the code you have already written.

```bash
pip install duckdb-kql              # Layer 0 — KQL text to DuckDB SQL
pip install 'duckdb-kql[duckdb]'    # Layer 1 — and run it
pip install 'duckdb-kql[kusto]'     # Layer 2 — via the azure-kusto-data API
pip install 'duckdb-kql[all]'       # everything, plus Arrow output
```

Python 3.10 or newer.

## Your first query

```python
import duckdb_kql

con = duckdb_kql.connect()
con.sql("""
    CREATE TABLE Events AS SELECT * FROM (VALUES
        ('web-01', 'Error',   TIMESTAMP '2024-03-01 09:15:00'),
        ('web-01', 'Info',    TIMESTAMP '2024-03-01 09:17:00'),
        ('web-01', 'Error',   TIMESTAMP '2024-03-01 09:41:00'),
        ('web-02', 'Error',   TIMESTAMP '2024-03-01 10:02:00')
    ) t(Host, Level, Timestamp)
""")

rel = duckdb_kql.sql(con, """
    Events
    | where Level == "Error"
    | summarize Errors = count() by Host
    | sort by Errors desc
""")

print(rel.fetchall())
# [('web-01', 2), ('web-02', 1)]
```

`duckdb_kql.sql()` returns a DuckDB *relation*, so you can keep composing with
DuckDB's own API — `.fetchall()`, `.df()`, `.arrow()`, `.limit(10)`, or feeding
it into another query.

Two shortcuts for the common cases:

```python
duckdb_kql.df(con, "Events | count")      # pandas DataFrame
duckdb_kql.arrow(con, "Events | count")   # pyarrow Table
```

### Use `duckdb_kql.connect()`, not `duckdb.connect()`

An existing connection works fine, but `connect()` also sets
`TimeZone='UTC'`, which KQL semantics require. Without it, a machine in a
non-UTC zone returns *shifted datetimes* rather than an error — see
[Two things that will bite you](#two-things-that-will-bite-you).

## Loading data

There is no ingestion step: a KQL table name is a DuckDB table, view, or any
relation DuckDB can name.

```python
con = duckdb_kql.connect("analytics.duckdb")

# Parquet, CSV, JSON — DuckDB reads them directly.
con.sql("CREATE VIEW Logs AS SELECT * FROM 'logs/*.parquet'")
con.sql("CREATE VIEW Signins AS SELECT * FROM read_csv('signins.csv')")

duckdb_kql.sql(con, "Logs | where Level == 'Error' | take 100")
```

A pandas DataFrame works too — register it under the name your KQL uses:

```python
import pandas as pd

frame = pd.read_parquet("events.parquet")
con.register("Events", frame)
duckdb_kql.sql(con, "Events | summarize n = count() by Level")
```

Table and column names are **case-sensitive**, matching KQL. `Events` and
`events` are different tables.

## Query parameters and user input

Do not build queries with f-strings. Declare parameters and pass values:

```python
rel = duckdb_kql.sql(con, """
    declare query_parameters(host:string, since:datetime);
    Events
    | where Host == host and Timestamp > since
""", {"host": user_input, "since": "2024-03-01T00:00:00Z"})
```

The values are bound by DuckDB, never spliced into the SQL. The generated
statement contains a placeholder where the value goes — not even the parameter's
*name* reaches the SQL text — so a value like `' OR 1=1 --` is matched as the
string it is:

```python
>>> str(duckdb_kql.to_sql(
...     "declare query_parameters(host:string); Events | where Host == host"))
'WITH _s0 AS (SELECT * FROM "Events"),\n     _s1 AS (SELECT * FROM _s0 WHERE ("Host" = CAST($kqlp0 AS VARCHAR)))\nSELECT * FROM _s1'
```

Values are checked against the declared type rather than coerced to it. Passing
`5` where the query says `string` is an error, not a silent `"5"`.

A parameter may have a default:

```python
"declare query_parameters(limit:long = 100); Events | take limit"
```

Ask a query what it expects before supplying anything:

```python
>>> [(p.name, p.type, p.required) for p in duckdb_kql.query_parameters(kql)]
[('host', 'string', True), ('since', 'datetime', True)]
```

## Translating without running

Layer 0 needs no database at all — useful for linting, for CI checks, or for
handing the SQL to something else.

```python
import duckdb_kql

duckdb_kql.to_sql("Events | where Level == 'Error' | take 10")
duckdb_kql.parse(kql).ok           # True / False
duckdb_kql.validate(kql)           # list of diagnostics, empty if valid
```

`to_sql()` returns a `str` subclass that also carries `.parameters` (values for
the placeholders) and `.unbound` (declared parameters still without a value).
Anything expecting a plain string keeps working.

`join` is the one operator that needs to know the input columns, to reproduce
KQL's column-renaming rules. `duckdb_kql.sql()` reads them from the connection;
if you call `to_sql()` directly, pass them:

```python
duckdb_kql.to_sql(kql, schema={"Events": ["Host", "Level", "Timestamp"]})
```

### Or translate at build time, and drop the dependency

There is a command for exactly this. Translate your `.kql` files to `.sql` in
CI, ship the SQL, and nothing at runtime needs this package:

```bash
duckdb-kql queries/ -o build/sql/ --check
```

See [Build-time translation](cli.md).

## Using the Kusto SDK interface

If you already have code written against `azure-kusto-data`, Layer 2 lets it run
against local data with a two-line change:

```diff
-from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
-from azure.kusto.data.helpers import dataframe_from_result_table
+from duckdb_kql.kusto import KustoClient, KustoConnectionStringBuilder
+from duckdb_kql.kusto.helpers import dataframe_from_result_table

-client = KustoClient(KustoConnectionStringBuilder.with_az_cli_authentication(CLUSTER))
+client = KustoClient("analytics.duckdb")

 response = client.execute("StormDb", "StormEvents | take 10")
 df = dataframe_from_result_table(response.primary_results[0])
```

The response object, the row access, and the DataFrame dtypes all match the
SDK's. What differs — and what it refuses rather than silently ignores — is in
[Kusto SDK compatibility](kusto-client.md).

## When something is not supported

Coverage is partial and says so. An unsupported construct raises
`KqlUnsupportedError`; it never returns an approximate answer.

```python
>>> duckdb_kql.to_sql("Events | parse Message with * 'user=' User ' ' *")
KqlUnsupportedError: unsupported KQL construct 'ParseOperator' at 1:9
(not implemented in this wave; near "parseMessagewith*'user='User' '*")
```

The error taxonomy:

| Exception | Means |
|---|---|
| `KqlSyntaxError` | The query does not parse. `.diagnostics` holds every error, not just the first. |
| `KqlUnsupportedError` | It parses, but uses something this version does not translate. |
| `KqlSchemaError` | An unknown table or column, a name collision, or a query parameter problem. |

All three derive from `KqlError`, so `except duckdb_kql.KqlError` catches
anything the library raises.

## Two things that will bite you

**Datetimes are UTC.** KQL datetimes carry no time zone and are always UTC.
DuckDB reads the *session* `TimeZone` when casting a string without an offset,
so on a machine in, say, `Europe/Paris`, `datetime(2024-01-01)` becomes
`2023-12-31T23:00:00Z` — quietly. `duckdb_kql.connect()` and `duckdb_kql.sql()`
set `TimeZone='UTC'` for you. If you run the SQL from `to_sql()` yourself, set
it yourself:

```python
con.execute("SET TimeZone='UTC'")
```

**Identifiers are case-sensitive.** KQL distinguishes `Level` from `level`;
DuckDB folds case. Generated SQL always quotes identifiers to preserve the
distinction, which means the table you reference in KQL must match the DuckDB
table's name exactly. Two KQL columns that differ only in case are a
`KqlSchemaError` rather than an arbitrary winner.

## Next

- [API reference](api.md) — every public function and type
- [Kusto SDK compatibility](kusto-client.md) — the Layer 2 contract in full
- [`TRANSLATION.md`](TRANSLATION.md) — the normative mapping spec, including the
  twelve places KQL and SQL look alike and behave differently
