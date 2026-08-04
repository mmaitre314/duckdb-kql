# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the version stays below `1.0`, the public API may change between minor
versions. What will not change without a note here is the project's one
promise: a construct either translates correctly or raises.

## [Unreleased]

### Added

- **Three-layer API.** `duckdb_kql` (translation, `antlr4` only),
  `duckdb_kql.engine` (execution, `+ duckdb`), and `duckdb_kql.kusto` (an
  `azure-kusto-data`-shaped client, `+ pandas`). Each layer adds exactly one
  dependency; importing `duckdb_kql` does not import `duckdb`.
- **`declare query_parameters`.** Values are bound through DuckDB's parameter
  API, never spliced into the SQL — the generated statement contains no
  caller-supplied bytes, not even the parameter's name. `to_sql()` now also
  accepts `parameters=` and reports `.parameters` / `.unbound`.
- **`duckdb_kql.query_parameters(kql)`** to inspect what a query expects before
  supplying it.
- **`duckdb_kql.connect()`** — `duckdb.connect()` plus the `TimeZone='UTC'` that
  KQL datetime semantics require.
- **`duckdb_kql.kusto`**: `KustoClient`, `KustoConnectionStringBuilder`,
  `ClientRequestProperties`, `KustoResponseDataSet` and
  `dataframe_from_result_table`, matching the SDK's shapes and dtypes. `raw_rows`
  carries Kusto's wire format so the SDK's own converters work; with
  `azure-kusto-data` installed, its `dataframe_from_result_table` accepts these
  tables directly.
- **`servertimeout`** enforced by interrupting the running query, rather than
  accepted and ignored.
- **[`docs/kql-support.md`](docs/kql-support.md)** — every operator, function
  and type, supported or not, each with its known limitations and the places it
  diverges from Kusto. Generated from the translator's registries and probed at
  generation time, so it cannot claim support that does not exist.
- User documentation: [getting started](docs/getting-started.md),
  [API reference](docs/api.md),
  [Kusto SDK compatibility](docs/kusto-client.md).
- Coverage tracking against
  [Azure Monitor's published KQL subset](docs/azure-monitor-profile.md):
  114 / 119 probes.
- **A `duckdb-kql` command** for translating `.kql` files to `.sql` at build
  time, so the queries can run with no dependency on this package at all —
  see [Build-time translation](docs/cli.md). `--check` fails a build when a
  generated `.sql` is stale, the header carries the `SET TimeZone='UTC'`
  requirement that nothing else would tell a consumer about, and a
  parameterized query gets its `$slot` placeholders mapped back to their
  declared names and types. Layer 0 only: it never imports `duckdb`.
- **Full type annotations, and `py.typed`.** A caller's type checker now sees
  real types across all three layers — `DuckDBPyConnection`, `DuckDBPyRelation`,
  `pandas.DataFrame`, `TranslationResult` — and catches misuse that previously
  passed silently. `duckdb` and `pandas` are typed through `TYPE_CHECKING`-only
  imports, so annotations arrive without their imports. `tests/test_typing.py`
  runs a checker over a sample consumer and asserts the revealed types are real:
  `mypy src/` passing says nothing about what a caller sees.

### Changed

- **Python 3.10 is now the minimum** (was 3.9).
- **`duckdb` is no longer a hard dependency.** It was declared as one but never
  imported by any module in `src/`. It is now the `duckdb` extra; install
  `duckdb-kql[duckdb]` to execute, or `duckdb-kql[all]` for everything.
- `duckdb_kql.sql` / `df` / `arrow` moved to `duckdb_kql.engine`. They remain
  importable from the top level, resolved lazily.
- `_connection_schema()` is now the public `duckdb_kql.engine.schema()`.
- `KustoServiceError` takes `semantic=` at construction; it was patched on
  afterwards, so a typo in the attribute name would have made every error look
  like an execution failure.

### Notes

- Every `ClientRequestProperties` option is classified as implemented, a no-op,
  or refused, with a reason for each — see
  [the option table](docs/kusto-client.md#request-options). `set_option` raises
  rather than silently ignoring anything it cannot honour.
- Async and streaming clients are deliberately absent: both exist to manage a
  network round trip that does not happen here.

## [0.0.1.dev0]

Initial development version. Parser, IR, translator and the acceptance harness
against the Kusto Emulator.

[Unreleased]: https://github.com/mmaitre314/duckdb-kql/compare/v0.0.1.dev0...HEAD
[0.0.1.dev0]: https://github.com/mmaitre314/duckdb-kql/releases/tag/v0.0.1.dev0
