# Third-Party Notices

`duckdb-kql` is licensed under the MIT License (see `LICENSE`). It vendors and
builds on the third-party material below, each under its own license.

Full license texts live in [`licenses/`](licenses/) and are shipped in both the
wheel and the sdist, so an installed copy carries them too.

| Material | Where | License | Text |
|---|---|---|---|
| Kusto Query Language grammar | `grammar/*.g4`, `src/duckdb_kql/_antlr/` | Apache-2.0 | [`licenses/Apache-2.0-Kusto-Query-Language.txt`](licenses/Apache-2.0-Kusto-Query-Language.txt) |
| Kusto documentation code samples | `tests/cases/` (not shipped) | MIT | — |
| `azure-kusto-data` (interface, and some helper bodies) | `src/duckdb_kql/kusto/` | MIT | [`licenses/MIT-azure-kusto-python.txt`](licenses/MIT-azure-kusto-python.txt) |
| DejaVu Sans Mono (outlined into the logo) | `docs/assets/*.svg` | Bitstream Vera | [`licenses/Bitstream-Vera-DejaVu.txt`](licenses/Bitstream-Vera-DejaVu.txt) |
| `antlr4-python3-runtime` | dependency, not vendored | BSD-3-Clause | — |
| `duckdb`, `pandas`, `pyarrow` | optional dependencies, not vendored | MIT / BSD-3-Clause / Apache-2.0 | — |

## Microsoft — Kusto Query Language grammar (Apache-2.0)

Copyright 2019 Microsoft Corporation.

`grammar/Kql.g4` and `grammar/KqlTokens.g4` are vendored from
[`microsoft/Kusto-Query-Language`](https://github.com/microsoft/Kusto-Query-Language)
at commit `6ad55002f78cc6a99870a524bb3b5c796b170b23`, path `grammar/`, licensed
under the **Apache License 2.0**. The full license text is
[`licenses/Apache-2.0-Kusto-Query-Language.txt`](licenses/Apache-2.0-Kusto-Query-Language.txt),
copied verbatim from that commit — Apache-2.0 §4(a) requires that recipients get
a copy, and referring to the license by name is not the same as providing it.

Upstream has **no `NOTICE` file** at that commit, so there is nothing to
propagate under §4(d). (Checked directly: `LICENSE` is present, `NOTICE`,
`NOTICE.txt`, `LICENSE.txt` and `LICENSE.md` are not.)

**These files have been modified**, as §4(b) requires be stated. Local patches
are marked in-file with `PATCH duckdb-kql/NNN` and documented in
`grammar/UPSTREAM.md`, which also records the SHA-256 of each file *as vendored,
before patching* — so the claim above can be checked against upstream rather
than taken on trust.

The contents of `src/duckdb_kql/_antlr/` are generated from these grammar files
by the ANTLR tool and are therefore also derived from Apache-2.0 material. That
directory **is** shipped in the wheel, which is why the license text is shipped
with it.

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

The corpus is excluded from both the wheel and the sdist.

## Microsoft — `azure-kusto-data` (MIT)

Copyright (c) Microsoft Corporation. All rights reserved. Full text:
[`licenses/MIT-azure-kusto-python.txt`](licenses/MIT-azure-kusto-python.txt).

`src/duckdb_kql/kusto/` is a compatibility layer for
[`azure-kusto-data`](https://github.com/Azure/azure-kusto-python). Reproducing
its *interface* — class names, method signatures, attribute names, the
`WellKnownDataSet` string values that travel on the wire — is the entire point
of a drop-in and is not copying in any meaningful sense.

A line-level comparison against `azure-kusto-data` 6.0.4 found a small number of
places where the *implementation* also matches closely, the largest being the
KQL-type-to-pandas-dtype dispatch table in `helpers.py`. Those exist because
matching the SDK's exact dtype behaviour is the requirement, not an incidental
resemblance — the same table written differently would be a different answer.
Rather than paraphrase working code to obscure where it came from, the notice is
given here. The audit and its findings are in
[`docs/kusto-client.md`](docs/kusto-client.md#provenance).

`azure-kusto-data` is **not** a dependency of this package, at runtime or
otherwise; nothing from it is installed alongside `duckdb-kql`.

## DejaVu Sans Mono — logo wordmark (Bitstream Vera license)

The wordmark in `docs/assets/logo-horizontal-*.svg` and `social-preview.svg` was
outlined from **DejaVu Sans Mono**, whose glyph outlines are copyright
Bitstream, Inc. under the permissive **Bitstream Vera Fonts** license, with
DejaVu's own changes placed in the public domain. Full text:
[`licenses/Bitstream-Vera-DejaVu.txt`](licenses/Bitstream-Vera-DejaVu.txt).

The glyphs are converted to paths at generation time, so the SVGs carry no font
and there is no runtime font dependency — but the outlines are still derived
from the typeface, so the notice belongs here. `docs/assets/generate_logo.py`
takes the font as `--font` and defaults to the DejaVu path; regenerating with
that default reproduces the committed SVGs byte for byte, which is what pins
this attribution to the specific face rather than to a recollection of it.

The license permits redistribution and modification. Its one substantive
condition is that modified versions must not be distributed under a name
containing "Bitstream" or "Vera" — nothing here is distributed as a font at all.

## ANTLR (BSD-3-Clause)

The generated parser requires the `antlr4-python3-runtime` package, licensed
under the **BSD 3-Clause License**. It is a normal runtime dependency and is not
vendored here.

## DuckDB (MIT)

`duckdb` is an optional dependency, licensed under the **MIT License**. Not
vendored here.

## pandas (BSD-3-Clause) and PyArrow (Apache-2.0)

Optional dependencies of the `pandas` / `kusto` / `arrow` extras. Not vendored
here.

## Kusto Emulator (development only)

The Kusto Emulator container is a **development and CI dependency only**. It is
never redistributed, is never a runtime dependency, and no performance figure of
any kind is taken from it or published. Its terms are reviewed clause by clause
in `docs/licensing.md` §5, and the scope of use is stated in
`docs/oracle-harness.md`.
