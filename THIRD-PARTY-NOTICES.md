# Third-Party Notices

`duckdb-kql` is licensed under the MIT License (see `LICENSE`). It vendors and
builds on the third-party material below, each under its own license.

## Microsoft — Kusto Query Language grammar (Apache-2.0)

`grammar/Kql.g4` and `grammar/KqlTokens.g4` are vendored from
[`microsoft/Kusto-Query-Language`](https://github.com/microsoft/Kusto-Query-Language)
at commit `6ad55002f78cc6a99870a524bb3b5c796b170b23`, licensed under the
**Apache License 2.0**.

**These files have been modified.** Local patches are marked in-file with
`PATCH duckdb-kql/NNN` and documented in `grammar/UPSTREAM.md`, as required by
Apache-2.0 §4(b).

The contents of `src/duckdb_kql/_antlr/` are generated from these grammar files
by the ANTLR tool and are therefore also derived from Apache-2.0 material.

## Microsoft — Kusto documentation (test corpus)

Test cases harvested from
[`MicrosoftDocs/dataexplorer-docs`](https://github.com/MicrosoftDocs/dataexplorer-docs)
carry per-case provenance (`source`, `source_commit`).

That repository is dual-licensed: **code samples under MIT** (`LICENSE-CODE`) and
**documentation prose under CC-BY-4.0** (`LICENSE`). This project harvests
**only the code samples** — the ` ```kusto ` query blocks. Documentation prose,
including the rendered example *output tables*, is **not** copied; expected
results are generated independently by executing the queries. See
`docs/licensing.md`.

## ANTLR (BSD-3-Clause)

The generated parser requires the `antlr4-python3-runtime` package, licensed
under the **BSD 3-Clause License**. It is a normal runtime dependency and is not
vendored here.

## DuckDB (MIT)

`duckdb` is a runtime dependency, licensed under the **MIT License**. Not
vendored here.
