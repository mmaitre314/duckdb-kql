# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the version stays below `1.0`, the public API may change between minor
versions. What will not change without a note here is the project's one
promise: a construct either translates correctly or raises.

## [Unreleased]

### Added

- `duckdb_kql.TranslationResult`, `Schema` and `Parameters` are importable at
  runtime. They appear in the public `to_sql` signature, so callers need them to
  annotate their own code.
- `tools/regen_expectations.py --only CASE_ID …` re-freezes named cases instead
  of the whole corpus.

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

- **The version comes from the git tag.** `pyproject.toml` declares
  `dynamic = ["version"]` and `hatch-vcs` reads the tag at build time, so
  releasing is one action — publish a GitHub Release for `vX.Y.Z` — with no file
  to bump and no way for the tag and the package to disagree. The job that
  compared the tag against two hard-coded version strings is gone; what replaces
  it is a check that the built distribution's version *is* the tag, catching the
  one failure that survives: a checkout without tags silently building a dev
  version under a release's name.
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

### Fixed

Findings from the first full code-review pass
([`docs/code-review/review-2026-08-04.md`](docs/code-review/review-2026-08-04.md)).
Each expectation below was measured on the Kusto Emulator, not inferred.

- **Negated operators no longer drop null rows.** `!contains`, `!has`,
  `!startswith`, `!endswith`, `!=`, `!~` and `!in` rendered as a naive
  `NOT (…)`; on a null operand SQL answers NULL and `where` discards the row,
  while KQL answers **true** and keeps it. `| where s !contains "x"` returned a
  smaller, entirely plausible result. The equality and membership families are
  total in KQL and are now emitted that way — except when *both* operands are
  null, which KQL leaves null, so the guard is conditional rather than a blanket
  `coalesce`.
- **`sort` put nulls at the wrong end.** KQL treats null as the *smallest*
  value: `sort by x asc` returns null first and `desc` returns it last. The
  emitter had this exactly inverted while its comments asserted the opposite as
  fact, and `TRANSLATION.md` §9 listed it as an open question. Now settled and
  pinned.
- **`extend` that overwrites a column keeps the column's position.** It moved to
  the end, so a three-column input came back ordered `b, c, a`. Column order is
  user-visible, and `schema.output_columns` computed the same wrong order, so a
  `join` after such an `extend` inherited the wrong names too.
- **A DuckDB integer wider than 64 bits no longer breaks the DataFrame path.**
  `UBIGINT`/`HUGEINT`/`UHUGEINT` values above `2**63-1` were reported as Kusto
  `long`; `dataframe_from_result_table` then raised a bare `OverflowError` from
  inside pandas. Such columns now report `string`, which is the one replacement
  that does not round the value. Columns that fit are unaffected.
- **A timespan parameter of `inf`, `nan` or `1e400` raises `KqlSchemaError`**
  rather than leaking `OverflowError` from the stdlib past the error taxonomy.
- **Table names are matched exactly.** The schema lookup also tried
  `name.lower()` and `name.upper()`, so `foo` could bind to a table named `Foo`
  and return another table's rows. KQL identifiers are case-sensitive (R7).
- **`KustoClosedError` derives from `KustoError`**, as it does in
  `azure-kusto-data`, not from `KustoClientError` — where a caller's
  `except KustoClientError` would have swallowed it.
- Removed the scalar `dcount` registry row, which mapped to
  `approx_count_distinct` and contradicted the aggregate row's measured decision
  to count exactly.
- `USE "<database>"` in the Kusto client now goes through the identifier-quoting
  helper like every other identifier. It was already gated by exact membership,
  so this is defence in depth rather than a fix.

### Notes

- Every `ClientRequestProperties` option is classified as implemented, a no-op,
  or refused, with a reason for each — see
  [the option table](docs/kusto-client.md#request-options). `set_option` raises
  rather than silently ignoring anything it cannot honour.
- Async and streaming clients are deliberately absent: both exist to manage a
  network round trip that does not happen here.

[Unreleased]: https://github.com/mmaitre314/duckdb-kql/commits/main
