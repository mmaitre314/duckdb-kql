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

`OPTION_SUPPORT` is the source of truth, and `tests/test_docs.py` holds this
table to it. It covers every option on the product's request-properties page.

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
| `query_now` | **Implemented** | Pins the query clock: `now()` and `ago()` resolve against the supplied instant instead of the wall clock, through one binding shared by the whole statement. Request-scoped, so two requests on one client may pin different instants and a request that sets nothing gets the real clock. See [Deterministic tests](#deterministic-tests). |
| `deferpartialqueryfailures` | No-op | This client never returns partial results: a query either completes or raises. There is no partial failure to defer or to surface. |
| `results_progressive_enabled` | No-op | Progressive framing is a streaming-transport concern. There is no transport here, and the full result is already materialised. |
| `request_readonly` | **Implemented** | A write — ingestion or a database command — is refused under it, as Kusto refuses one (measured). It was a no-op here on the grounds that translated KQL only reads, and the write went through. |
| `request_readonly_hardline` | **Implemented** | Same: a write is refused under it. The plugins it also disables are refused here anyway. |
| `request_app_name` | No-op | Recorded for tracing only. |
| `request_user` | No-op | Recorded for tracing only. |
| `request_description` | No-op | Recorded for tracing only. |
| `client_max_redirect_count` | No-op | There is no HTTP request to redirect. |
| `query_log_query_parameters` | No-op | There is no query journal to log parameters to: `.show queries` is refused here. |
| `query_weakconsistency_session_id` | No-op | Takes effect only under queryconsistency=weakconsistency_by_session_id, which is refused. On its own it selects nothing. |
| `results_error_reporting_placement` | No-op | Where errors go among partial results. There are none here: a query completes or raises. |
| `results_v2_fragment_primary_tables` | No-op | Response framing: the rows are the same rows, in one fragment or many. |
| `results_v2_newlines_between_frames` | No-op | Response framing, whitespace between frames. |
| `client_results_reader_allow_varying_row_widths` | No-op | A tolerance in the reader. Every row here has the result's width. |
| `query_results_progressive_row_count` | No-op | Tunes the progressive stream, which is a no-op above for the same reason. |
| `query_results_progressive_update_period` | No-op | Tunes the progressive stream, which is a no-op above for the same reason. |
| `push_selection_through_aggregation` | No-op | A plan hint for Kusto's engine. It cannot change a result, and DuckDB plans the query itself. |
| `query_optimize_fts_at_relop` | No-op | A plan hint for Kusto's free-text search. It cannot change a result. |
| `query_distribution_nodes_span` | No-op | Shapes the node hierarchy of a distributed query. There is one process here and no hierarchy; it cannot change a result. |
| `materialized_view_shuffle_query` | No-op | A shuffle-strategy hint for materialized views, which are refused here; a hint cannot change a result in any case. |
| `query_results_cache_force_refresh` | No-op | There is no results cache: every result is computed fresh, which is what a forced refresh asks for. |
| `request_callout_disabled` | No-op | Nothing here calls out: `evaluate` (http_request, sql_request and every other plugin) is refused. The restriction already holds. |
| `request_sandboxed_execution_disabled` | No-op | Nothing here runs in a sandbox: `evaluate python` and `evaluate r` are refused. The restriction already holds. |
| `request_external_data_disabled` | No-op | `externaldata` and `external_table()` are refused. The restriction already holds. |
| `request_external_table_disabled` | No-op | `external_table()` is refused. The restriction already holds. |
| `query_cursor_disabled` | No-op | The cursor functions are refused. The restriction already holds. |
| `queryconsistency` | Refused | A single local database has one consistency level. Accepting `weakconsistency` would suggest a choice that does not exist. |
| `truncationmaxrecords` | Refused | Kusto truncates a result and *tells you* it did, via `QueryCompletionInformation`. Silently returning fewer rows without that signal would look like a complete answer. |
| `truncationmaxsize` | Refused | Same: a truncated result that does not announce itself is indistinguishable from a short one. |
| `notruncation` | Refused | Nothing truncates here, so this is not the no-op it looks like — a caller setting it believes truncation was otherwise in play. |
| `query_datetimescope_column` | Refused | Datetime scoping rewrites the query's time filter server-side. Ignoring it would silently widen the window the caller asked for. |
| `query_datetimescope_from` | Refused | Half of a datetime scope; same reason. |
| `query_datetimescope_to` | Refused | The other half; same reason. |
| `query_language` | Refused | This client speaks KQL. Accepting `sql` or `csl` would promise a dialect it does not translate. |
| `query_bin_auto_size` | Refused | `bin_auto()` is not in the supported surface, so the setting would configure nothing. |
| `query_bin_auto_at` | Refused | The alignment point for `bin_auto()`; same reason. |
| `maxmemoryconsumptionperiterator` | Refused | DuckDB's memory limit is a connection setting with different units and different scope; mapping one to the other would be a guess. |
| `max_memory_consumption_per_query_per_node` | Refused | Same. |
| `query_fanout_nodes_percent` | Refused | Fanout spreads a query over a cluster's nodes. There is one process here. |
| `query_fanout_threads_percent` | Refused | DuckDB's threading is a connection setting, not a per-query one. |
| `query_results_cache_max_age` | No-op at zero | There is no results cache, so a zero age — return no cached result — is what already happens, and is accepted: `time(0s)`, `0s`, `timespan(0)`, `time(00:00:00)`, `'00:00:00'`, `'0'` and any other timespan literal or plain timespan string of zero ticks, or `datetime.timedelta(0)` through `set_option`. Any other age is refused, since it would govern a cache that does not exist; so is a spelling Kusto refuses (`0`, `time(null)`, `' 0s '`) or one this cannot read for certain (`'0 s'`). |
| `query_results_cache_per_shard` | Refused | Follows `query_results_cache_max_age`: there is no results cache, so there are no shards of one to enable it for. |
| `query_take_max_records` | Refused | Same as `truncationmaxrecords`: a result capped without saying so is indistinguishable from a short one. |
| `maxoutputcolumns` | Refused | A limit Kusto enforces by refusing the query (measured: SEM0004 past it). Ignoring it would answer a query Kusto refuses. |
| `query_max_entities_in_union` | Refused | A limit Kusto enforces by refusing the query; ignoring it would answer a query Kusto refuses. |
| `query_results_apply_getschema` | Refused | Replaces the result with its schema (measured). Ignoring it would return rows where the caller asked for columns; write `| getschema` instead. |
| `validatepermissions` | Refused | Returns a permissions verdict instead of running the query. There are no permissions here to validate, and running the query would answer a different question. |
| `best_effort` | Refused | Changes which tables a union resolves to. This translator refuses an unresolvable table rather than tolerating it, and that tolerance is what the option asks for. |
| `query_datascope` | Refused | 'hotcache' restricts a query to cached data, and nothing here is cached or not; answering from all of it would silently widen the scope. |
| `query_force_row_level_security` | Refused | Row level security policies are not modelled here, so there are no rules to enforce; ignoring the request would return rows a policy hides. |
| `request_block_row_level_security` | Refused | Row level security policies are not modelled here, so no table is known to have one to block. |
| `request_remote_entities_disabled` | Refused | `cluster()` and `database()` references are answered from local stand-ins. Ignoring this would answer a query Kusto refuses. |
| `request_impersonation_disabled` | Refused | It stops cross-cluster queries in Kusto, and `cluster()` is answered here from a local stand-in. Ignoring it would answer a query Kusto refuses. |
| `query_cursor_after_default` | Refused | Database cursors are not modelled, and the cursor functions are refused; the setting would configure nothing. |
| `query_cursor_before_or_at_default` | Refused | Database cursors are not modelled; see query_cursor_after_default. |
| `query_cursor_current` | Refused | Database cursors are not modelled; see query_cursor_after_default. |
| `query_cursor_scoped_tables` | Refused | Scopes tables to a cursor range, and there are no cursors here. Ignoring it would silently widen the rows the caller asked for. |
| `query_python_debug` | Refused | `evaluate python` is refused, so the setting would configure nothing. |

`servertimeout` is real, not advisory: the deadline interrupts the running
DuckDB query, and the connection stays usable afterwards.

```python
props = ClientRequestProperties()
props.set_option(props.request_timeout_option_name, timedelta(seconds=30))
# a timedelta, a number of seconds, or a KQL timespan string ("30s", "5m")
```

## Deterministic tests

A query written against `now()` or `ago()` cannot be asserted on while the clock
moves under it. Generating fixtures relative to wall-clock time works until a
window boundary lands on the wrong side of a second, and then it fails once a
week for reasons nobody can reproduce.

`query_now` pins the clock for one request, so the query under test runs
**unchanged**:

```python
from datetime import datetime, timedelta, timezone
from duckdb_kql.kusto import ClientRequestProperties, KustoClient

FIXED = datetime(2020, 1, 2, 12, tzinfo=timezone.utc)

properties = ClientRequestProperties()
properties.set_option("query_now", FIXED)

with KustoClient(connection) as client:
    response = client.execute(None, "Events | where Occurred > ago(1d) | count", properties)
```

Every `now()` and `ago()` in the request — in a `let`, in either branch of an
`iff`, on a join's right side, in a nested query — resolves against that one
instant, because they all render as the same bound placeholder. `now(1h)` and
`ago(-1h)` offset from it.

The same argument exists on the layers below, so a test does not have to reach
for the SDK shape to get a fixed clock:

```python
duckdb_kql.query(connection, "Events | where Occurred > ago(1d)", query_now=FIXED)
duckdb_kql.to_sql("print t = now()", query_now=FIXED)   # no connection needed
```

What the value may be, and what it becomes: a `datetime` (aware values are
converted to UTC and the zone dropped), a `date`, or an ISO-8601 string —
exactly what `set_parameter` accepts for a declared `datetime`, because it is the
same coercion. Anything else raises naming `query_now` rather than being
quietly ignored.

Three properties worth relying on in a test:

- **It is request-scoped.** Two requests on one client may pin different
  instants, and a request that sets nothing gets the real clock even if an
  earlier one pinned it. Nothing is stored on the client.
- **Omitting it changes nothing.** The generated SQL is byte-for-byte what it
  was, with DuckDB's own `now()`, and no parameter is bound.
- **Only the function is affected.** The override acts on the parsed query, not
  on its text, so a string literal `'now()'` or a column named `now` is left
  alone. Pinning the clock is not a find-and-replace.

For boundary tests, remember which comparisons are inclusive: with the clock
pinned to `FIXED`, a row at exactly `ago(1d)` passes `t >= ago(1d)` and fails
`t > ago(1d)`. Both endpoints are now exactly expressible, which is the point.

It also works as a `set` statement in the query text, which is Kusto's other
spelling for the same request option:

```kusto
set query_now = datetime(2020-01-02 12:00);
Events | where Occurred > ago(1d) | count
```

The value must be a **datetime literal** — measured, Kusto answers SEM0020 for a
string, a timespan, a number, a bare name or no value at all, and so does this.
A later `set` of the same option wins, and if a request also passes
`query_now=`, the statement in the text wins: a `set` statement *sets the
request property*, so the text is the later word.

`set` statements more generally are classified by the same table as the request
options, in `duckdb_kql.options` — one classification, two spellings. An option
that is a no-op here is accepted silently, because doing nothing is what it asks
for; one that would change an answer is refused with its reason; and
`servertimeout` / `norequesttimeout` are refused *as statements* and point at the
request option, because the deadline is enforced while the query runs and a
`set` statement is read when it is translated. `request_readonly` and
`request_readonly_hardline` are the reverse — implemented as request options,
no-ops as statements — because a `set` statement heads a query, and a query
does not write.

The product documentation lists thirteen options that "can't be set with a set
statement". Measured, that is not what it means: Kusto accepts every one of
them as a statement, and it *honours* `servertimeout` and `truncationmaxsize`
(`set servertimeout = 10ms` times the query out). So none of them is refused
here merely for being on that list; each is classified by what it does.

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
| `.set` / `.append` / `.set-or-append` / `.set-or-replace` | `ExtentId`, `OriginalSize`, `ExtentSize`, `CompressedSize`, `IndexSize`, `RowCount` | Ingests the command's KQL body into the table. Only `RowCount` and a generated `ExtentId` are real; the sizes describe a cluster's storage layout and are null. Gated by `allow_write`, which defaults to on here and off in `duckdb-kql serve`. |
| `.show entity_groups` | `Name`, `Entities` | The `entity_groups=` mapping this client was given, since a named group is cluster-side state and there is no cluster. `Entities` is a **string** holding a JSON array of the references, measured. No mapping means no rows. |
| `.show functions` | `Name`, `Parameters`, `Body`, `Folder`, `DocString` | The `functions=` registry for the database the request names, since a stored function is database-side state. `Parameters` and `Body` are as Kusto reports them — `(x:long=0)`, `{ Events }` — and an unset `Folder`/`DocString` is `''`, measured. `.show function F` gives one row, or Kusto's "not found" as a service error. |
| `.create database` / `.attach database` / `.detach database` | `DatabaseName`, `PersistentPath`, `Created`, `StoresMetadata`, `StoresData` (`Result` for detach) | Mapped onto DuckDB's `ATTACH` / `DETACH`: `volatile` is `:memory:`, `persist (<path>)` names a **file**. Gated by `allow_write`. See [`.create database`](create-database.md) for the grammar and what is refused. |
| everything else | — | `KustoUnsupportedError`, naming the ones that work. |

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
is **null** rather than filled with something plausible. Policy and
schema-management commands administer a cluster, and there is no cluster; a stub
returning an empty table would look like a command that worked.

Database lifecycle is the other exception, for the same reason: `.create`,
`.attach` and `.detach` describe *where the data lives*, which a DuckDB file
has an answer for. Kusto persists into folders — conventionally one for
metadata and one for data — where DuckDB uses a single file for both, so a
command giving several paths uses the first and reports it back in
`PersistentPath`. A remote blob URI is refused; there is nothing local behind
it.

Ingestion is the exception, because it describes *data* rather than a cluster:
the four commands above load a KQL body into a table, which is how a test suite
seeds fixtures. `KustoClient.execute()` and `duckdb_kql.kql()` run them
identically — the client used to refuse a command that `kql()` on the same
connection accepted.

## Notebook display

A response renders as a table in Jupyter: the query's own output inline, with
`@ExtendedProperties` and `QueryCompletionInformation` folded into a collapsed
`<details>` so the metadata does not bury the answer. Column types are shown
under the column names, `null` and `""` are drawn differently — `isnull` and
`isempty` are different questions (R4) — and long tables are cut short with a
count of what was left out.

This is an **addition**, not a fidelity claim: the real `azure-kusto-data`
renders as `<...KustoResponseDataSet object at 0x...>`, so a notebook that looks
good here will look plainer against a real cluster. `str()` on a result table is
untouched and still produces the SDK's JSON. The rendering needs no pandas.

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

## Provenance

This layer **reimplements the `azure-kusto-data` interface**; it does not vendor
the upstream package, and `azure-kusto-data` is not a dependency of
`duckdb-kql` at runtime or otherwise. Nothing from it is installed alongside
this package.

Reproducing the *interface* is the entire point of a drop-in, so class names,
method signatures, attribute names and the wire-protocol string constants
(`WellKnownDataSet.PrimaryResult` and friends) match upstream by intent. That is
not copying in any meaningful sense — a drop-in that renamed them would not be
one.

The *implementations* were written against the documented behaviour and pinned
by tests, not transcribed. A line-level audit against `azure-kusto-data` 6.0.4
found that mostly holds, with a bounded set of exceptions worth naming rather
than glossing:

| Where | What matches | Why |
|---|---|---|
| `helpers.py` | The KQL-type → pandas-dtype dispatch table (~19 lines), and the `parse_timedelta` numeric branch | Matching the SDK's *exact* dtype behaviour is the requirement — `dataframe_from_result_table` has to produce the dtypes downstream code already indexes on. The same table written differently would be a different answer, not a different phrasing. |
| `_models.py` | Short dunder bodies — `__len__`, `__iter__`, `__getitem__`, `columns_count` | Four- to six-line methods whose content follows from the signature. `__nonzero__ = __bool__` is upstream's Python-2 alias, kept so subclassing code behaves identically. |
| `response.py` | The index-or-name `__getitem__` lookup | Same shape, same reason. |

Everything else — the connection-string builder, the request-option policy, the
control-command handling, the timeout enforcement, the error types — is this
project's own, and diverges from upstream deliberately where the constraints
differ (see [The governing rule](#the-governing-rule)).

Because those exceptions exist, `azure-kusto-data`'s MIT notice is carried in
[`THIRD-PARTY-NOTICES.md`](../THIRD-PARTY-NOTICES.md) with its full license text
in [`licenses/MIT-azure-kusto-python.txt`](../licenses/MIT-azure-kusto-python.txt),
rather than paraphrasing working code to avoid the attribution. MIT asks for the
copyright line and the permission notice; both are there.

[sdk]: https://pypi.org/project/azure-kusto-data/
