# API reference

Three layers, each adding one dependency. Nothing in a lower layer imports
anything from a higher one, and importing `duckdb_kql` never imports `duckdb`.

- [Layer 0 — `duckdb_kql`](#layer-0--duckdb_kql)
- [Layer 1 — `duckdb_kql.engine`](#layer-1--duckdb_kqlengine)
- [Layer 2 — `duckdb_kql.kusto`](#layer-2--duckdb_kqlkusto)
- [Errors](#errors)
- [Command line](#command-line)

---

## Layer 0 — `duckdb_kql`

Requires `antlr4-python3-runtime`. KQL text in, DuckDB SQL out; no database.

### `to_sql(kql, schema=None, parameters=None) -> TranslationResult`

Translate *kql* to DuckDB SQL.

| Argument | Meaning |
|---|---|
| `kql` | The query text. |
| `schema` | `{table_name: [column_name, ...]}`. Only `join` consults it; everything else translates schema-free. |
| `parameters` | `{name: value}` for the query's `declare query_parameters` declarations. |

Returns a `str` subclass — anything expecting a string keeps working — carrying:

| Attribute | Meaning |
|---|---|
| `.parameters` | `{slot: value}` to pass to DuckDB alongside the SQL. `{}` when the query declares none. |
| `.unbound` | Names of declared parameters with neither a value nor a default. The SQL is valid text but cannot run until they are supplied. |
| `.udfs` | User-defined functions the SQL needs to have registered. Empty today. |

Raises `KqlSyntaxError`, `KqlUnsupportedError`, or `KqlSchemaError` (a value of
the wrong type, or a value for a name the query does not declare).

> The returned SQL assumes `TimeZone='UTC'`. If you execute it yourself rather
> than through Layer 1, run `SET TimeZone='UTC'` first — otherwise datetimes are
> silently shifted rather than rejected.

```python
>>> kql = "declare query_parameters(n:long); range i from 1 to n step 1"
>>> duckdb_kql.to_sql(kql).unbound
('n',)
>>> duckdb_kql.to_sql(kql, parameters={"n": 3}).parameters
{'kqlp0': 3}
```

### `query_parameters(kql) -> list[ParameterDeclaration]`

What a query expects, in declaration order. Cheaper than translating.

```python
>>> [(p.name, p.type, p.required) for p in duckdb_kql.query_parameters(
...     "declare query_parameters(state:string, n:long = 10); StormEvents | take n")]
[('state', 'string', True), ('n', 'long', False)]
```

### `ParameterDeclaration`

| Field | Meaning |
|---|---|
| `name` | The KQL name, with any `['...']` escaping removed. |
| `type` | Canonical KQL scalar type: `bool`, `int`, `long`, `real`, `decimal`, `string`, `datetime`, `timespan`, `guid`, `dynamic`. |
| `default` | The declared default, already coerced, or `None`. |
| `required` | `True` when there is no default. |
| `slot` | The generated placeholder name used in the SQL. Never derived from `name`. |

Accepted Python types per declared type:

| KQL type | Accepts |
|---|---|
| `string` | `str` |
| `bool` | `bool` |
| `int`, `long` | `int` (not `bool`) |
| `real` | `int`, `float` |
| `decimal` | `Decimal`, `int`, `str` |
| `datetime` | `datetime` (naive is UTC, aware is converted), `date`, ISO-8601 `str` |
| `timespan` | `timedelta`, or a KQL timespan string (`"90m"`, `"1.02:03:04"`) |
| `guid` | `UUID`, or a `str` a `UUID` accepts |
| `dynamic` | anything JSON-serializable, or a JSON `str` |

Values are checked, not coerced: `1.5` for a `long` is a `KqlSchemaError`, never
a silent `1`.

### `parse(kql) -> ParseResult`

Parse and return the syntax tree. Raises `KqlSyntaxError` if it does not parse.
`ParseResult` has `.ok`, `.tree`, and `.diagnostics`.

### `validate(kql) -> list[Diagnostic]`

Every syntax diagnostic, empty when the query is valid. Does not raise.

```python
>>> duckdb_kql.validate("Logs | where Level ==")
[Diagnostic(span=SourceSpan(line=1, column=21), message="mismatched input '<EOF>' ...")]
```

`Diagnostic` has `.span` (a `SourceSpan` with 1-based `line`, 0-based `column`)
and `.message`.

### `to_sql(kql, schema=None, parameters=None, database=None, allow_write=True, clusters=None)`

`database` gives unqualified table names a database: `T` renders as
`"sales"."T"`. It needs no connection, so Layer 0 can target a database too.

---

## Layer 1 — `duckdb_kql.engine`

Requires `duckdb`. Also re-exported from the top level (`duckdb_kql.kql`, …),
resolved lazily so that importing `duckdb_kql` does not import `duckdb`.

Every function here sets `TimeZone='UTC'` on the connection and reads its schema,
so `join` works without you supplying one.

### `connect(database=":memory:", **kwargs) -> DuckDBPyConnection`

`duckdb.connect(...)` plus `SET TimeZone='UTC'`. Use it unless you are setting
the zone yourself. Raises `ImportError` with an install hint if `duckdb` is not
installed.

### `kql(con, query, parameters=None, database=None, allow_write=True, clusters=None) -> DuckDBPyRelation`

Execute and return a relation, so you can keep composing with DuckDB's API.

`allow_write` (default `True`) gates the ingestion commands `.set`,
`.append`, `.set-or-append` and `.set-or-replace`. It defaults to allowing them
because the caller wrote the query and owns the connection; `duckdb-kql serve`
defaults the other way, since it answers unauthenticated requests.

`clusters` says which local database stands in for each Kusto cluster, so a
query written for a real service runs against test data unchanged:

```python
duckdb_kql.kql(con, query, clusters={
    ("mycluster.eastus.kusto.windows.net", "mydb"): "database1",
})
```

`{"cluster": {"database": "name"}}` is accepted too — it is what a JSON config
file looks like, and `duckdb-kql serve --cluster-map` reads that shape.

Omitted, `cluster(...)` is **refused**: reading it as local would answer a
question about somewhere else with data from here. Spellings are normalized —
one entry covers `mycluster.example.net`, `https://mycluster.example.net` and a
trailing slash — but a short name is *not* expanded to a domain, because Kusto
does not expand it either.

`database` selects which attached database unqualified table names belong to.
It is applied by **qualifying the names during translation**, not by switching
the connection — so nothing about `con` changes, and the relation cannot drift
between being built and being fetched. An explicit `database("other").T` in the
query wins, and a name bound by a tabular `let` is left alone (it is a CTE).
A database that is not attached raises `KqlSchemaError` naming it, and listing
the ones that are.

> **Concurrency.** A DuckDB connection is not safe for concurrent use, and this
> predates `database=`: reading the schema is an `execute` / `fetchall` pair
> that a second thread's `execute` invalidates. Serialize access to a shared
> connection, or give each thread its own. `duckdb-kql serve` holds a lock for
> exactly this reason.

Also accepts the control commands `.show version`, `.show databases` and
`.show tables` — a separate Kusto dialect, with the column shapes Kusto returns
— and pipelines built on them, such as
`.show tables | where TableName startswith "Storm"`.
See [Control commands](kusto-client.md#control-commands).

### `execute(con, kql, parameters=None, database=None) -> DuckDBPyConnection`

Mirrors `con.execute` — for the cursor, or the side effect.

### `df(con, kql, parameters=None, database=None)` · `arrow(con, kql, parameters=None, database=None)`

`kql(...).df()` and `kql(...).arrow()`. Need `pandas` and `pyarrow` respectively.

### `schema(con) -> dict[str, list[str]]`

Table name to column names, read from `information_schema`. Returns `{}` rather
than raising on a connection with no schema.

```python
import duckdb_kql

con = duckdb_kql.connect("analytics.duckdb")
rel = duckdb_kql.kql(con, """
    declare query_parameters(since:datetime);
    Logs | where Timestamp > since | summarize n = count() by Level
""", {"since": "2024-01-01T00:00:00Z"})
```

Unbound parameters are a `KqlSchemaError` naming the KQL parameter, not a DuckDB
complaint about a generated slot.

---

## Layer 2 — `duckdb_kql.kusto`

Requires `duckdb`; `pandas` for the DataFrame helper. A drop-in for
`azure-kusto-data`. The compatibility contract in full — including every request
option and why it is implemented, a no-op, or refused — is in
[Kusto SDK compatibility](kusto-client.md); this is the surface.

### `KustoClient(kcsb, database=None)`

*kcsb* may be a DuckDB database path, a `KustoConnectionStringBuilder`, or an
existing `duckdb` connection. A connection the client opened is closed with the
client; one you passed in is left alone.

| Method | Behaviour |
|---|---|
| `execute(database, query, properties=None)` | Dispatches on the leading `.` to `execute_mgmt` or `execute_query`. |
| `execute_query(database, query, properties=None)` | Translates and runs KQL. Returns `KustoResponseDataSet`. |
| `execute_mgmt(database, command, properties=None)` | `.show version`, `.show databases`, `.show tables`. Everything else raises `KustoUnsupportedError`. |
| `close()` / context manager | Closes an owned connection. Idempotent. |

`database` selects an ATTACHed DuckDB catalog when one matches. A name that
matches nothing and conflicts with the client's configured database raises
rather than silently answering from the wrong one.

### `KustoConnectionStringBuilder(connection_string)`

Accepts a bare path (`"analytics.duckdb"`, `":memory:"`) or a keyword string
(`"Data Source=analytics.duckdb;Initial Catalog=Logs"`). Exposes `.data_source`,
`.database_name`, and `.ignored_credentials`.

The SDK's `with_*_authentication` constructors exist and **discard their
credentials** — there is no service to present them to. That is safe only
because a cluster URL (`https://…`, `net.tcp://…`) is refused outright rather
than reinterpreted as a local file.

### `ClientRequestProperties()`

Same shape as the SDK's: `set_parameter` / `has_parameter` / `get_parameter`,
`set_option` / `has_option` / `get_option`, `to_json`,
`get_tracing_attributes`, and the `client_request_id` / `application` / `user`
attributes.

Two differences, both deliberate:

- `set_parameter` accepts any Python value, not only `str`. The declared KQL type
  decides what is valid.
- `set_option` **raises** `KustoUnsupportedError` for an option this client
  cannot honour, at the line that sets it. An unrecognised option is refused
  too. The classification is `duckdb_kql.kusto.OPTION_SUPPORT`.

```python
props = ClientRequestProperties()
props.set_parameter("state", user_input)
props.set_option(props.request_timeout_option_name, timedelta(seconds=30))
```

### `KustoResponseDataSet`

Iterable over tables; indexable by position or table name. `.primary_results`
gives the query's own output. `.tables`, `.tables_count`, `.tables_names`,
`.errors_count` (always 0 — failures raise), `.get_exceptions()`.

A query response carries three tables, as real Kusto's does: `PrimaryResult`,
`@ExtendedProperties`, and `QueryCompletionInformation`.

### `KustoResultTable` · `KustoResultRow` · `KustoResultColumn`

| Member | Meaning |
|---|---|
| `table.columns` | `KustoResultColumn` objects with `.column_name`, `.column_type` (a *Kusto* type name), `.ordinal`. |
| `table.raw_rows` | Values in Kusto's **wire** form — ISO-8601 for datetime, `d.hh:mm:ss.fffffff` for timespan, parsed JSON for dynamic. |
| `table.rows` / iteration | `KustoResultRow`, with wire values converted to `datetime`, `timedelta`, `Decimal`. |
| `row[0]` / `row["Name"]` | By position or by column name. Also `.to_dict()`, `.to_list()`. |

### `duckdb_kql.kusto.helpers.dataframe_from_result_table(table, ...)`

Same signature and the same dtypes as the SDK's: `Int64Dtype` for a long,
`Float64Dtype` for a real, UTC-aware `datetime64` for a datetime,
`timedelta64` for a timespan. `nullable_bools`, `converters_by_type` and
`converters_by_column_name` all behave as they do there.

If `azure-kusto-data` is installed, **its own** helper accepts these tables too —
they are registered with its ABC — so an existing
`from azure.kusto.data.helpers import dataframe_from_result_table` keeps working.

### Not provided

Streaming (`execute_streaming_query`, `KustoStreamingResponseDataSet`) and the
async client. Both exist to manage a network round trip that does not happen
here; wrapping a local list in either would be ceremony rather than capability.

---

## Errors

```
KqlError                      duckdb_kql.errors — Layer 0 and 1
├── KqlSyntaxError            does not parse; .diagnostics has all of them
├── KqlUnsupportedError       parses, but outside the supported surface
└── KqlSchemaError            unknown table/column, name collision, parameter problem

KustoError                    duckdb_kql.kusto.exceptions — Layer 2
├── KustoServiceError         the query failed; .is_semantic_error() distinguishes
│                             a bad query from a failed run
└── KustoClientError
    ├── KustoClosedError      the client has been closed
    └── KustoUnsupportedError a request option, command, or data source we refuse
```

Layer 2 wraps Layer 0 and 1 failures in `KustoServiceError`, so SDK-shaped code
catching that keeps working.

---

## Command line

`duckdb-kql` (also `python -m duckdb_kql`) is Layer 0 as a command: it
translates `.kql` files to `.sql` so the output can be run without this package
installed at all. `--check` makes a stale generated file fail CI.

```bash
duckdb-kql translate queries/ -o build/sql/ --check
```

Full reference, including the generated header and how to bind the placeholders
a parameterized query produces: [Build-time translation](cli.md).

---

## Typing

The package ships `py.typed` ([PEP 561][pep561]), so your type checker uses
these annotations. What you get:

```python
>>> reveal_type(duckdb_kql.connect())
_duckdb.DuckDBPyConnection
>>> reveal_type(duckdb_kql.kql(con, "T | count"))
_duckdb.DuckDBPyRelation
>>> reveal_type(duckdb_kql.df(con, "T | count"))
pandas.core.frame.DataFrame
>>> duckdb_kql.kql("not a connection", "T | count")
error: Argument 1 has incompatible type "str"; expected "DuckDBPyConnection"
```

`duckdb` and `pandas` are *optional* dependencies, imported under
`TYPE_CHECKING` only. Their types reach you without their imports reaching your
runtime — `import duckdb_kql` still does not import `duckdb`.

`tests/test_typing.py` runs a checker over a sample consumer and asserts the
revealed types are real. That is a different question from `mypy src/` passing,
which says nothing about what a caller sees: a function returning `Any` type
checks perfectly from the inside while telling the caller nothing.

### What is `Any`, and why

Four things genuinely are, and they are declared that way rather than left to
inference:

| | Why |
|---|---|
| `ParseResult.tree` | The ANTLR runtime ships no type information. Naming a class here would resolve to `Any` anyway while implying otherwise. Work from `duckdb_kql.ir`, which is typed; `tree` is the escape hatch. |
| Query parameter values | What is acceptable depends on the *declared KQL type*, checked at bind time. A signature cannot express "whatever this query said". |
| `dynamic` column values | A JSON document is `Any` by construction. |
| `arrow()`'s return | `pyarrow` ships neither `py.typed` nor stubs, so `pa.Table` is `Any` for everyone. A test asserts this so the claim gets revisited if pyarrow changes. |

If you use pandas and want `df()` fully typed, add `pandas-stubs` — pandas
itself ships no type information either.

[pep561]: https://peps.python.org/pep-0561/
