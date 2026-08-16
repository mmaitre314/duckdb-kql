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
[DuckDB](https://duckdb.org) in Python.

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

**New here? Start with [Getting started](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/getting-started.md).**

## Install

Install only the layer you need — each adds one dependency.

```bash
pip install duckdb-kql              # translate KQL to SQL         (antlr4 only)
pip install 'duckdb-kql[duckdb]'    # ... and run it               (+ duckdb)
pip install 'duckdb-kql[kusto]'     # ... via the Kusto SDK API    (+ pandas)
pip install 'duckdb-kql[all]'       # everything
```

Python 3.10 or newer. Fully typed — the package ships `py.typed`, so your type
checker sees real types across all three layers ([details][typing]).

[typing]: https://github.com/mmaitre314/duckdb-kql/blob/main/docs/api.md#typing

## No-runtime-dependency option

Translate at build time and the output has no dependency on this package at all
— not even Python. Only your CI machine installs it.

```bash
duckdb-kql translate queries/ -o build/sql/ --check   # fails the build if a .sql is stale
```

See [Build-time translation](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/cli.md).

## Query it from the Azure Data Explorer UI

`duckdb-kql serve` puts a local Kusto REST endpoint in front of a DuckDB file,
so Kusto's own tools can query it. Standard library only — no new dependency.

```bash
duckdb-kql serve analytics.duckdb     # http://127.0.0.1:31415
```

Open <https://dataexplorer.azure.com>, choose **Add connection**, and paste that
URL. It listens on loopback only and cannot be told otherwise, because it
answers unauthenticated queries: see
[A local Kusto endpoint](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/kusto-server.md).

## Three layers

| Layer | Import | Needs | For |
|---|---|---|---|
| **0** | `duckdb_kql` | `antlr4-python3-runtime` | KQL text in, DuckDB SQL out. No database involved. |
| **1** | `duckdb_kql.engine` | `+ duckdb` | Running the translated SQL. |
| **2** | `duckdb_kql.kusto` | `+ pandas` | A drop-in for `azure-kusto-data`'s `KustoClient`. |

Importing `duckdb_kql` never imports `duckdb`, so Layer 0 genuinely installs and
runs without a database.

### Layer 0 — translate

```python
>>> import duckdb_kql
>>> duckdb_kql.to_sql("print x = 1 + 1")
'SELECT (CAST(1 AS BIGINT) + CAST(1 AS BIGINT)) AS "x"'

>>> duckdb_kql.validate("Logs | where Level ==")
[Diagnostic(span=SourceSpan(line=1, column=21), message="mismatched input '<EOF>' ...")]
```

### Layer 1 — execute

```python
import duckdb_kql

con = duckdb_kql.connect("analytics.duckdb")   # duckdb.connect + TimeZone=UTC
rel = duckdb_kql.kql(con, "StormEvents | summarize n = count() by State")
rel.fetchall()
```

### Layer 2 — the Kusto SDK interface

For code already written against `azure-kusto-data`: change the import and the
connection string, leave the queries alone.

```python
from duckdb_kql.kusto import KustoClient, ClientRequestProperties
from duckdb_kql.kusto.helpers import dataframe_from_result_table

client = KustoClient("analytics.duckdb")
props = ClientRequestProperties()
props.set_parameter("state", user_input)

response = client.execute("Storm", """
    declare query_parameters(state:string);
    StormEvents | where State == state | take 10
""", props)

df = dataframe_from_result_table(response.primary_results[0])
```

Details, including what it refuses and why:
[`docs/kusto-client.md`](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/kusto-client.md).

## Query parameters

Never build a query by concatenating strings. Declare parameters and pass
values; they are bound as values, so the generated SQL contains no
caller-controlled text at all.

```python
duckdb_kql.kql(con, """
    declare query_parameters(state:string);
    StormEvents | where State == state
""", {"state": user_input})     # safe whatever user_input contains
```

## Coverage

Measured against the real KQL engine (the Kusto Emulator), not asserted.

| | |
|---|---|
| Doc-corpus cases matching ground truth | **253** of 1036 (0 mismatches) |
| [Azure Monitor's published KQL subset](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/azure-monitor-profile.md) | **114 / 119 (96%)** |
| Tabular operators | **16 / 41** |
| Scalar functions / aggregates / binary operators | **110 / 19 / 33** |

Supported operators: `where`, `project`, `project-away`, `project-rename`,
`extend`, `summarize`, `join`, `mv-expand`, `distinct`, `count`,
`sort` / `order by`, `take` / `limit`, `render`; sources `print`, `datatable`,
`range`, and tables; plus `let` and `declare query_parameters`.

**[The support matrix](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/kql-support.md)
lists every operator, function and type — supported or not — with the known
limitations and Kusto discrepancies for each.** It is generated from the
translator's own registries and probed at build time, so it cannot claim support
that does not exist.

## Why refusal matters

The failure mode this project is built to avoid is not a crash — it is a query
that runs and returns a *different* answer than Kusto would. KQL and SQL look
alike in places where they behave differently: `%` is a mathematical modulo in
KQL and takes the dividend's sign in DuckDB; `extract`'s arguments are in the
opposite order; KQL weeks start on Sunday; `make_datetime` truncates where
`make_timestamp` rounds. Every mapping is verified against the emulator rather
than inferred from documentation, and where an honest mapping does not exist —
`hash_xxhash64`, `datetime_part('nanosecond')` — the answer is an error, not an
approximation.

## Documentation

| Document | What it covers |
|---|---|
| [Getting started](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/getting-started.md) | Install, first query, the three layers |
| [**KQL support matrix**](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/kql-support.md) | Every operator and function, supported or not, each with its gotchas |
| [Build-time CLI](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/cli.md) | Translating `.kql` to `.sql` in CI, to avoid a runtime dependency |
| [Local Kusto endpoint](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/kusto-server.md) | `duckdb-kql serve` — query a DuckDB file from the Azure Data Explorer UI |
| [API reference](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/api.md) | Every public function and type |
| [Kusto SDK compatibility](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/kusto-client.md) | What Layer 2 implements, no-ops, and refuses |
| [Azure Monitor profile](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/azure-monitor-profile.md) | Coverage against a published KQL subset |
| [`docs/TRANSLATION.md`](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/TRANSLATION.md) | **Normative** KQL→DuckDB mapping spec (R1–R12) |
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
see [`docs/oracle-harness.md`](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/oracle-harness.md). It is a development and
CI tool only, neither a runtime dependency nor redistributed.

## License

MIT. See [`LICENSE`](https://github.com/mmaitre314/duckdb-kql/blob/main/LICENSE) and [`THIRD-PARTY-NOTICES.md`](https://github.com/mmaitre314/duckdb-kql/blob/main/THIRD-PARTY-NOTICES.md).

## Trademarks

"DuckDB" is a trademark of the DuckDB Foundation. "Kusto", "Azure", and
"Azure Data Explorer" are trademarks of Microsoft Corporation. This project
is independent and is not affiliated with, endorsed by, or sponsored by
either the DuckDB Foundation or Microsoft. Product names are used
descriptively, to indicate compatibility.
