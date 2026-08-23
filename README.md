<!--
  The wordmark stands in for the `# duckdb-kql` heading this file used to open
  with. Two things about the markup are load-bearing, both verified rather than
  assumed — see tests/test_readme_logo.py:

  * **Absolute raw.githubusercontent URLs, not relative paths.** PyPI renders
    this file on its own domain and does not resolve repository-relative paths,
    so `docs/assets/...` would be a broken image there. GitHub resolves absolute
    raw URLs perfectly well, so one form serves both.
  * **`<picture>` for dark mode.** GitHub honours `prefers-color-scheme` inside
    it; PyPI's sanitizer (readme_renderer + bleach) drops `<source>` and keeps
    the `<img>`, so PyPI simply gets the light wordmark. The `alt` text is the
    project name so the heading survives even if the image does not.
-->
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/mmaitre314/duckdb-kql/main/docs/assets/logo-horizontal-dark.svg">
    <img src="https://raw.githubusercontent.com/mmaitre314/duckdb-kql/main/docs/assets/logo-horizontal-light.svg" alt="duckdb-kql" width="343">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/mmaitre314/duckdb-kql/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/mmaitre314/duckdb-kql/actions/workflows/ci.yml/badge.svg?branch=main"></a>
  <a href="https://pypi.org/project/duckdb-kql/"><img alt="PyPI" src="https://img.shields.io/pypi/v/duckdb-kql.svg"></a>
  <a href="https://pypi.org/project/duckdb-kql/"><img alt="Python versions" src="https://img.shields.io/pypi/pyversions/duckdb-kql.svg"></a>
</p>

Run [Kusto Query Language](https://learn.microsoft.com/kusto/query/) (KQL) queries on
[DuckDB](https://duckdb.org) in Python. Develop KQL queries locally, run them in unit tests, as part of CI builds, etc. without the need for network.

```python
import duckdb_kql

con = duckdb_kql.connect()
con.sql("CREATE TABLE Logs AS SELECT * FROM 'logs.parquet'")

duckdb_kql.kql(con, """
    Logs
    | where Timestamp > ago(1d) and Level == "Error"
    | summarize Count = count() by bin(Timestamp, 1h), Component
    | sort by Timestamp asc
""")
```

Several statements at once — the shape Azure Data Explorer calls a
[database script](https://learn.microsoft.com/azure/data-explorer/database-script),
for setting a database up in KQL alone. Blank lines separate the statements:

```python
duckdb_kql.script(con, """
    .set-or-replace Errors <| Logs | where Level == "Error"

    .set-or-replace ByComponent <| Errors | summarize n = count() by Component
""")
```

See the [demo](https://github.com/mmaitre314/duckdb-kql/blob/main/demo/demo.ipynb) notebook.

## Install

APIs are split into 3 layers to control features and dependencies. Install based on needs.

Layer | Install | Scenario | Dependencies
--|--|--|--
0 | `pip install duckdb-kql` | Translate KQL queries to SQL | antlr4 only
1 | `pip install duckdb-kql[duckdb]` | Run KQL queries | adds duckdb
2 | `pip install duckdb-kql[kusto]` | Run KQL queries via Kusto SDK APIs | adds pandas

See [Getting started](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/getting-started.md).

To reduce runtime dependencies, translate KQL queries to SQL at build time using the CLI.

```bash
duckdb-kql translate -o query.sql query.kql
```

See [Build-time translation](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/cli.md).

## KustoClient query

Run queries using APIs compatible with Kusto client SDK ([azure-kusto-data](https://github.com/Azure/azure-kusto-python)). 

```python
from duckdb_kql.kusto import KustoClient
from duckdb_kql.kusto.helpers import dataframe_from_result_table

client = KustoClient(con)

response = client.execute("NetDefaultDB", """
    Requests
    | where Status >= 500
    | summarize Errors = count() by Service
    | sort by Errors desc
""")

dataframe_from_result_table(table)
```

See [Kusto client](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/kusto-client.md).

## Local HTTP server

Start a local KQL HTTP server using the CLI, then open <https://dataexplorer.azure.com>, choose 'Add connection', and enter `http://127.0.0.1:31415`.

```bash
duckdb-kql serve
```
See [Kusto server](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/kusto-server.md).

## Query validation and translation

Use `validate()` and `to_sql()` to respectively validate the KQL query and translate it to SQL. 

```python
>>> duckdb_kql.to_sql("print x = 1 + 1")
'SELECT (CAST(1 AS BIGINT) + CAST(1 AS BIGINT)) AS "x"'

>>> duckdb_kql.validate("Logs | where Level ==")
[Diagnostic(span=SourceSpan(line=1, column=21), message="mismatched input '<EOF>' ...")]
```

## Query parameters

Declare parameters and pass values to defend against query injections.

```python
duckdb_kql.kql(con, """
    declare query_parameters(state:string);
    StormEvents | where State == state
""", {"state": user_input})
```

## Coverage

Correctness measured against the real KQL engine (the [Kusto Emulator](https://learn.microsoft.com/en-us/azure/data-explorer/kusto-emulator-overview)).

| | |
|---|---|
| Doc-corpus cases matching ground truth | **285** of 1036 (0 mismatches) |
| [Azure Monitor's published KQL subset](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/azure-monitor-profile.md) | **115 / 119 (96%)** |
| Tabular operators | **22 / 42** |
| Scalar functions / aggregates / binary operators | **111 / 19 / 33** |

Supported operators: `where`, `project`, `project-away`, `project-rename`,
`extend`, `summarize`, `join`, `mv-expand`, `distinct`, `count`,
`sort` / `order by`, `top`, `take` / `limit`, `union`, `macro-expand`, `lookup`,
`parse`, `parse-where`, `render`; sources `print`, `datatable`, `range`, and
tables; plus `let` and
`declare query_parameters`.

[The support matrix](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/kql-support.md)
lists every operator, function and type, supported or not, with the known
limitations and Kusto discrepancies for each.

## Documentation

| Document | Topics |
|---|---|
| [Getting started](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/getting-started.md) | Install, first query, the three layers |
| [KQL support matrix](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/kql-support.md) | Every operator and function, supported or not, each with its gotchas |
| [Build-time CLI](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/cli.md) | Translating `.kql` to `.sql` in CI, to avoid a runtime dependency |
| [Local Kusto endpoint](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/kusto-server.md) | `duckdb-kql serve` — query a DuckDB file from the Azure Data Explorer UI |
| [API reference](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/api.md) | Every public function and type |
| [Kusto SDK compatibility](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/kusto-client.md) | What Layer 2 implements, no-ops, and refuses |
| [Azure Monitor profile](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/azure-monitor-profile.md) | Coverage against a published KQL subset |
| [`docs/TRANSLATION.md`](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/TRANSLATION.md) | Normative KQL→DuckDB mapping spec (R1–R16) |
| [`docs/create-database.md`](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/create-database.md) | Reference notes for `.create database`, reconstructed from the KQL parser |
| [`docs/implementation-plan.md`](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/implementation-plan.md) | Architecture and milestones |
| [`docs/test-plan.md`](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/test-plan.md) | Corpus harvesting, oracle, divergence catalog |
| [`docs/kql-on-duckdb-landscape.md`](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/kql-on-duckdb-landscape.md) | Survey of existing KQL-on-DuckDB work |
| [`docs/implementation-options.md`](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/implementation-options.md) | Six approaches considered, with the chosen one |
| [`docs/m0-grammar-spike.md`](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/m0-grammar-spike.md) | Grammar viability result |
| [`docs/frequency-scan-results.md`](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/frequency-scan-results.md) | What KQL constructs actually get used |
| [`docs/licensing.md`](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/licensing.md) | Third-party licensing review |
| [demo/](https://github.com/mmaitre314/duckdb-kql/blob/main/demo/demo.ipynb) | Notebook tour of all three layers, with outputs |
| [CONTRIBUTING.md](https://github.com/mmaitre314/duckdb-kql/blob/main/CONTRIBUTING.md) | How to add a mapping, and when not to |
| [SECURITY.md](https://github.com/mmaitre314/duckdb-kql/blob/main/SECURITY.md) | Reporting vulnerabilities; what is in scope |
| [Releases](https://github.com/mmaitre314/duckdb-kql/releases) | What changed, and when |

## Design

The KQL parser is generated by ANTLR from Microsoft's [KQL grammar](https://github.com/microsoft/Kusto-Query-Language/tree/master/grammar).
Translation targets DuckDB SQL as a chain of Common Table Expressions (CTEs), one per KQL operator. DuckDB handles query optimization and execution.

## Development

```bash
pip install -e ".[dev]"
pytest

tools/regen_parser.sh        # regenerate the parser (maintainers; needs Java)
```

The acceptance suite compares against the Kusto Emulator, which runs in Docker;
see [`docs/oracle-harness.md`](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/oracle-harness.md).

## License

MIT. See [`LICENSE`](https://github.com/mmaitre314/duckdb-kql/blob/main/LICENSE), [`THIRD-PARTY-NOTICES.md`](https://github.com/mmaitre314/duckdb-kql/blob/main/THIRD-PARTY-NOTICES.md), and [`licenses/`](https://github.com/mmaitre314/duckdb-kql/tree/main/licenses).

## Trademarks

"DuckDB" is a trademark of the DuckDB Foundation. "Kusto", "Azure", and
"Azure Data Explorer" are trademarks of Microsoft Corporation. This project
is independent and is not affiliated with, endorsed by, or sponsored by
either the DuckDB Foundation or Microsoft. Product names are used
descriptively, to indicate compatibility.
