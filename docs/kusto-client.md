# Kusto SDK compatibility

`duckdb_kql.kusto` is a drop-in for [`azure-kusto-data`][sdk]'s `KustoClient`,
backed by a local DuckDB database.

```diff
-from azure.kusto.data import KustoClient, KustoConnectionStringBuilder, ClientRequestProperties
-from azure.kusto.data.helpers import dataframe_from_result_table
+from duckdb_kql.kusto import KustoClient, KustoConnectionStringBuilder, ClientRequestProperties
+from duckdb_kql.kusto.helpers import dataframe_from_result_table

-client = KustoClient(KustoConnectionStringBuilder.with_az_cli_authentication(CLUSTER))
+client = KustoClient("analytics.duckdb")

 response = client.execute("StormDb", "StormEvents | take 10")
 df = dataframe_from_result_table(response.primary_results[0])
```

The queries, the request properties, the response walking and the DataFrame
dtypes all stay as they were. This page is about the parts that cannot stay the
same, and what happens to them.

## The governing rule

**Nothing is accepted and then ignored.** A local client cannot honour every
Kusto request option, and the tempting shortcut — store them all, act on the
ones we know — turns a `truncationmaxrecords` or a `servertimeout` into a
promise the caller believes is being kept. So every option is one of three
things, and the classification is data (`duckdb_kql.kusto.OPTION_SUPPORT`) that
a test walks:

| | |
|---|---|
| **Implemented** | We act on it. |
| **No-op** | We accept it and do nothing, *because doing nothing is the behaviour it asks for here* — not because it was inconvenient. |
| **Refused** | `set_option` raises `KustoUnsupportedError`, at the line that sets it. |

An option nobody has classified is refused too. An unrecognised option is not a
safe one.

## Request options

Generated from `OPTION_SUPPORT`, which is the source of truth.

This table is the *Python API's* policy: `set_option` raises at the line that
asks for something impossible, which is the right answer for a caller who can
change that line. [`duckdb-kql serve`](kusto-server.md#request-options) judges
`queryconsistency` and `query_language` by their value instead, because there
the caller is a web UI that sends them on every request and cannot be edited.
Neither ever accepts an option and ignores it.

| Option | Support | Why |
|---|---|---|
| `servertimeout` | **Implemented** | Enforced by interrupting the DuckDB query when the deadline passes. |
| `norequesttimeout` | **Implemented** | Disables the timeout above. |
| `deferpartialqueryfailures` | No-op | This client never returns partial results: a query either completes or raises. There is no partial failure to defer or to surface. |
| `results_progressive_enabled` | No-op | Progressive framing is a streaming-transport concern. There is no transport here, and the full result is already materialised. |
| `request_readonly` | No-op | Translated KQL only ever reads: no operator in the supported surface writes. The guarantee the option asks for already holds. |
| `request_app_name` | No-op | Recorded for tracing only. |
| `request_user` | No-op | Recorded for tracing only. |
| `request_description` | No-op | Recorded for tracing only. |
| `client_max_redirect_count` | No-op | There is no HTTP request to redirect. |
| `query_now` | Refused | Overriding `now()` means threading a clock through every datetime function. Until that exists, a query using `now()` with this option set would silently use the real clock. |
| `queryconsistency` | Refused | A single local database has one consistency level. Accepting `weakconsistency` would suggest a choice that does not exist. |
| `truncationmaxrecords` | Refused | Kusto truncates a result and *tells you* it did, via `QueryCompletionInformation`. Silently returning fewer rows without that signal would look like a complete answer. |
| `truncationmaxsize` | Refused | Same: a truncated result that does not announce itself is indistinguishable from a short one. |
| `notruncation` | Refused | Nothing truncates here, so this is not the no-op it looks like — a caller setting it believes truncation was otherwise in play. |
| `query_datetime_scope_column` | Refused | Datetime scoping rewrites the query's time filter server-side. Ignoring it would silently widen the window the caller asked for. |
| `query_datetime_scope_from` | Refused | Half of a datetime scope; same reason. |
| `query_datetime_scope_to` | Refused | The other half; same reason. |
| `query_language` | Refused | This client speaks KQL. Accepting `sql` or `csl` would promise a dialect it does not translate. |
| `query_bin_auto_size` | Refused | `bin_auto()` is not in the supported surface, so the setting would configure nothing. |
| `query_bin_auto_at` | Refused | The alignment point for `bin_auto()`; same reason. |
| `maxmemoryconsumptionperiterator` | Refused | DuckDB's memory limit is a connection setting with different units and different scope; mapping one to the other would be a guess. |
| `max_memory_consumption_per_query_per_node` | Refused | Same. |
| `query_fanout_nodes_percent` | Refused | Fanout spreads a query over a cluster's nodes. There is one process here. |
| `query_fanout_threads_percent` | Refused | DuckDB's threading is a connection setting, not a per-query one. |
| `query_results_cache_max_age` | Refused | There is no results cache, so a max age would govern nothing. |

`servertimeout` is real, not advisory: the deadline interrupts the running
DuckDB query, and the connection stays usable afterwards.

```python
props = ClientRequestProperties()
props.set_option(props.request_timeout_option_name, timedelta(seconds=30))
# a timedelta, a number of seconds, or a KQL timespan string ("30s", "5m")
```

## Query parameters

`set_parameter` binds to the query's `declare query_parameters` declarations,
the same as against a real cluster — and, as there, the value never becomes
query text.

```python
props = ClientRequestProperties()
props.set_parameter("state", user_input)

client.execute("StormDb", """
    declare query_parameters(state:string);
    StormEvents | where State == state | count
""", props)
```

Two differences from the SDK, both deliberate:

- The value need not be a `str`. The declared KQL type decides what is accepted,
  so a `datetime` parameter takes a `datetime`.
- Values are **checked** against the declared type rather than coerced. `5` for a
  `string` parameter is an error, not `"5"`.

A declared parameter with no value and no default raises `KustoServiceError`
naming the parameter — not a DuckDB complaint about a generated placeholder.

## Authentication

`KustoConnectionStringBuilder`'s `with_*_authentication` constructors all exist,
and all **discard the credentials they are given**. There is no service to
present them to.

That is defensible only because of the other half of the rule: a cluster URL is
**refused**, never reinterpreted.

```python
>>> KustoClient("https://help.kusto.windows.net")
KustoUnsupportedError: unsupported by duckdb-kql: data source
'https://help.kusto.windows.net' (this client runs queries locally against
DuckDB and never contacts a cluster; give it a database path so it is obvious
which data is being queried)
```

If a URL silently became a local file, you would get confident answers computed
from data that has nothing to do with the cluster you named. Credentials being
ignored is only harmless when nothing can be sent anywhere.

Keywords in a connection string that this client does not use are recorded in
`.ignored_credentials`, so it is visible that they were dropped rather than
applied.

## Control commands

These are not Layer 2 only — `duckdb_kql.kql(con, ".show tables")` runs them
too, and both go through the same translation
([`duckdb_kql.control`](../src/duckdb_kql/control.py)).

| Command | Columns (as Kusto returns them) | Behaviour |
|---|---|---|
| `.show version` | `BuildVersion`, `BuildTime`, `ServiceType`, `ProductVersion`, `ServiceOffering` | This package's version, and DuckDB's in `ProductVersion`. `BuildTime` is null — there is no build timestamp to report. |
| `.show databases` | `DatabaseName`, `PersistentStorage`, `Version`, `IsCurrent`, `DatabaseAccessMode`, `PrettyName`, `ReservedSlot1`, `DatabaseId`, `InTransitionTo`, `SuspensionState` | The DuckDB catalogs attached to the connection. `Version` is DuckDB's; `DatabaseId` and the cluster-only columns are null. |
| `.show tables` | `TableName`, `DatabaseName`, `Folder`, `DocString` | Tables **and views** in the current database — a view is a queryable table as far as KQL is concerned. `Folder` and `DocString` are null. |
| everything else | — | `KustoUnsupportedError`, naming the three that work. |

A command's result can be **piped into query operators**, as it can in Kusto:

```kusto
.show tables | where TableName startswith "Storm" | project TableName
.show tables | count
.show databases | project DatabaseName, IsCurrent
```

The command half is a fixed set of literals and is case-insensitive; everything
after the first `|` is ordinary KQL, translated by the ordinary path, so
identifiers there are case-sensitive and any operator this package does not
support still raises.

The column names and order are measured against the Kusto Emulator, because
callers index into them by name and a plausible subset breaks at the point of
use rather than at the point of translation.

Where a column describes something a cluster has and a DuckDB file does not, it
is **null** rather than filled with something plausible. Ingestion, policy and
schema-management commands administer a cluster, and there is no cluster; a stub
returning an empty table would look like a command that worked.

## Databases

A DuckDB connection has one database unless others are attached. The `database`
argument to `execute` therefore:

- selects an **ATTACHed** catalog when the name matches one;
- is accepted when there is no conflicting default;
- **raises** when it names something else and the client has a different
  database configured.

The last case is the one worth having: code that queries several databases
through one client would otherwise get consistent-looking answers from whichever
happened to be open.

```python
client = KustoClient("Data Source=main.duckdb;Initial Catalog=Main")
client._connection.execute("ATTACH 'archive.duckdb' AS Archive")

client.execute("Archive", "OldEvents | count")   # selects Archive
client.execute("Elsewhere", "T | count")         # KustoUnsupportedError
```

## The response

A query response carries the three tables real Kusto returns:

| Table | Kind |
|---|---|
| `PrimaryResult` | the query's output |
| `@ExtendedProperties` | `QueryProperties` |
| `QueryCompletionInformation` | `QueryCompletionInformation`, carrying `client_request_id` |

`response.primary_results[0]` is the result. `errors_count` is always 0 — a
failed query raises rather than returning, so there is nothing to under-report.

### `raw_rows` holds the wire form

This is the detail most likely to matter and least likely to be noticed.
`raw_rows` holds what Kusto *sends*, not Python objects:

| Kusto type | In `raw_rows` |
|---|---|
| `datetime` | `"2020-01-02T03:04:05.000000Z"` |
| `timespan` | `"1.02:03:04"`, `"00:00:01.5000000"`, `"-02:00:00"` |
| `dynamic` | parsed JSON (`{"a": [1, 2]}`) |
| `decimal`, `guid` | strings |
| `real` | a float, or `"NaN"` / `"Infinity"` / `"-Infinity"` |

`dataframe_from_result_table` and `KustoResultRow` both parse *from* that form.
Storing live `datetime` and `timedelta` objects instead would skip their
converters and land `object` columns in the DataFrame — no error, just
arithmetic and comparisons quietly not working.

Iterating a table gives converted values, as in the SDK:

```python
row = response.primary_results[0][0]
row["StartTime"]     # datetime.datetime(..., tzinfo=timezone.utc)
row["Duration"]      # datetime.timedelta(...)
```

Kusto reports timespans to 100ns ticks and DuckDB stores microseconds, so the
seventh fractional digit is written as `0` rather than invented.

### DataFrame dtypes

`duckdb_kql.kusto.helpers.dataframe_from_result_table` uses the SDK's conversion
table, so the dtypes match: `Int64Dtype` for a long, `Float64Dtype` for a real,
UTC-aware `datetime64` for a datetime, `timedelta64` for a timespan.

If `azure-kusto-data` happens to be installed, **its** helper works on these
tables too — they are registered with its ABC — so an existing
`from azure.kusto.data.helpers import dataframe_from_result_table` import needs
no change at all.

## Errors

| Exception | Raised when |
|---|---|
| `KustoServiceError` | The query failed. `.is_semantic_error()` is `True` when the problem is the query (syntax, unsupported construct, unknown column) rather than the run. `.has_partial_results()` is always `False`. |
| `KustoUnsupportedError` | A refused request option, control command, or data source. |
| `KustoClosedError` | The client has been closed. |

## Not provided

**Streaming.** `execute_streaming_query` and `KustoStreamingResponseDataSet`
exist to avoid holding a large remote result in memory while it arrives over the
network. There is no round trip here and the result is already materialised, so
a streaming API would be ceremony around a list.

**Async.** An async client here would be a coroutine wrapping a synchronous
call — the `await` would suggest concurrency that nobody gets. If your calling
code is async, `asyncio.to_thread(client.execute, ...)` does the honest version
in one line.

Both are worth revisiting if a real need appears; neither is being faked in the
meantime.

[sdk]: https://pypi.org/project/azure-kusto-data/
