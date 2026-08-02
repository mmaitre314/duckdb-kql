# Running KQL on DuckDB — Landscape & Literature Review

> Status: research notes (2026-08-02). This document surveys the existing
> ecosystem for running **Kusto Query Language (KQL)** against **DuckDB**, plus
> the surrounding tooling (KQL parsers, transpilers, and in‑process engines)
> that we could build on. A companion document,
> [`implementation-options.md`](./implementation-options.md), turns this survey
> into concrete implementation options with pros/cons.

## 1. Problem framing

KQL (a.k.a. the *Kusto Query Language*) is Microsoft's pipe‑oriented query
language used by Azure Data Explorer (ADX), Azure Monitor / Log Analytics,
Microsoft Sentinel, and Microsoft Fabric Real‑Time Intelligence. It is
optimized for append‑only telemetry / log analytics and has a very different
surface from SQL:

- **Pipe (`|`) data‑flow model** rather than nested `SELECT … FROM`.
- A large built‑in library: **70+ tabular operators** (`where`, `summarize`,
  `join`, `extend`, `project`, `parse`, `mv-expand`, `make-series`, …),
  **200+ scalar functions**, **40+ aggregation functions**.
- KQL‑specific semantics: a `dynamic` (JSON‑like) type, `datetime`/`timespan`
  types and arithmetic, case‑insensitive `has`/`contains` string search,
  `bin()`/time‑bucketing, `todynamic`, and so on.

DuckDB is an in‑process, columnar **SQL** analytics engine with excellent
Python bindings, Arrow/Parquet integration, and an extension mechanism. It does
**not** natively understand KQL. So "run KQL on DuckDB" reduces to one of a few
strategies (detailed in §2), and the ecosystem already contains partial
building blocks for each (§3–§6).

There is genuine demand: DuckDB is a natural local/offline substitute for an
ADX cluster (unit tests, local log triage, notebooks, CI), and KQL is the
language security/observability engineers already know.

## 2. Strategy taxonomy

There are four fundamentally different ways to get KQL results out of
DuckDB‑resident data:

| # | Strategy | How it works | Who executes the query |
|---|----------|--------------|------------------------|
| A | **Transpile KQL → SQL**, run on DuckDB | Parse KQL, emit equivalent DuckDB SQL, execute via DuckDB | DuckDB engine |
| B | **In‑process KQL engine**, DuckDB as storage | A real KQL engine executes; DuckDB only stores/loads data (Parquet/Arrow) | Separate KQL engine |
| C | **DuckDB extension** exposing KQL | Native/embedded extension adds `kql_*` functions or a KQL parser to DuckDB | DuckDB (+ embedded translator) |
| D | **Remote to a real Kusto cluster** | Send KQL to ADX/Fabric over the wire | Azure (not local, not DuckDB) |

Strategy **A** and **C** are the same idea (translation) at different
integration layers; **B** trades away DuckDB's engine to get exact KQL
semantics; **D** is out of scope for "on DuckDB" but is the *reference behavior*
we must match, and its client libraries define the Python API shape users
expect.

The rest of this document inventories the concrete projects available for each.

## 3. Direct KQL‑on‑DuckDB projects (Strategies A/C)

### 3.1 `saoc90/kql-to-sql` — the closest existing work ⭐ most relevant

A **KQL → SQL converter written in C#/.NET**, *built on the official Microsoft
Kusto language parser* (`Microsoft.Azure.Kusto.Language`). It explicitly targets
**DuckDB** and **PostgreSQL (PGlite)** SQL dialects.

- **Coverage** (per its `KqlOperatorsChecklist.md` / `KqlCommandsChecklist.md`):
  70+ tabular operators, 200+ scalar functions, 40+ aggregates, 50+ management
  commands mapped to DuckDB/Postgres SQL.
- **Consumption surfaces** (this is the important part):
  - a **NuGet library** — `new KqlToSqlConverter(new DuckDbDialect())`;
  - a **DuckDB extension** exposing SQL functions **`kql_to_sql()`** and
    **`kql_explain()`** (headless, in‑database translation);
  - a **Blazor WASM** demo that runs queries entirely client‑side against
    **DuckDB‑WASM** (live demo: https://saoc90.github.io/kql-to-sql/).
- **License:** MIT. **Maturity:** low adoption (~4 stars) but ~355 commits and
  fuzzing tests — actively built, not abandoned, but effectively a one‑author
  project with no released stability guarantees.
- **Python story:** none today. It is C#/.NET; a Python user would have to (a)
  load its DuckDB extension, (b) call it via .NET interop, or (c) run it as a
  service. This is the single most reusable asset for our goal, and the gap it
  leaves — a Python entry point — is essentially our project.

### 3.2 DuckDB "piped‑SQL" extensions (KQL‑*adjacent*, not KQL)

- **`ywelsch/duckdb-psql`** — a DuckDB extension adding a **pipe (`|`) SQL**
  syntax ("PSQL"), explicitly described as a lightweight cousin of piped
  languages such as **PRQL and Kusto**. Not KQL, but proves the pattern of
  bolting a pipe language onto DuckDB via the extension API and is a useful
  precedent for the ergonomics and for how a parser can live inside an
  extension.
- **PRQL** has a DuckDB integration as well. Again a different (pipelined)
  language, relevant only as prior art for "non‑SQL front‑end → DuckDB."

These show the extension route is viable, but neither speaks KQL.

## 4. Full KQL engines (Strategy B)

If we want *exact* KQL semantics without writing a transpiler, we can embed an
engine that already implements KQL and use DuckDB purely as a data store.

- **`NeilMacMullen/kusto-loco` (KustoLoco)** — a **C# in‑process KQL query
  engine** with flexible I/O (CSV, JSON, **Parquet**), in‑memory POCO querying,
  and Vega‑Lite chart rendering. Packaged on NuGet (`KustoLoco.Core`). It is a
  maintained **fork of BabyKusto**. It does **not** use DuckDB — it is its own
  execution engine — so pairing it with DuckDB means DuckDB is only the storage
  / Parquet layer.
- **`davidnx/baby-kusto-csharp` (BabyKusto)** — the original self‑contained C#
  KQL execution engine (from a Microsoft hackathon by David Nissimoff, Vicky Li,
  et al.). Upstream of KustoLoco; less actively developed now.

Both are .NET, so from Python they'd need `pythonnet` (see §5.3). Their appeal
is fidelity; their drawback is they bypass DuckDB's engine entirely.

## 5. KQL parsers (building blocks for a transpiler)

Any home‑grown transpiler (Strategy A) needs a parser. Options, best fidelity
first:

### 5.1 `microsoft/Kusto-Query-Language` — the official parser (gold standard)

- The authoritative **C# parser + semantic analyzer**, published as NuGet
  **`Microsoft.Azure.Kusto.Language`**. Crucially, the repo *also* contains a
  **translator project that emits the same libraries in JavaScript**, so the
  official parser is available in **both .NET and JS** environments.
- Handles the entire real‑world KQL grammar (this is what ADX, Sentinel, and the
  ADX web UI use). Provides `KustoCode.Parse`, diagnostics, and a canonical
  formatter.
- **License:** Apache‑2.0. It **parses/analyzes only** — it does *not* translate
  KQL to SQL. (kql‑to‑sql in §3.1 is what builds translation on top of it.)

### 5.2 `Kustology` — Python wrapper over the official parser

- An open‑source Python library that talks to `Microsoft.Azure.Kusto.Language`
  **through `pythonnet`**, adding a small Python layer and a **Pydantic IR**.
- API: `parse(query)` (walkable `KustoQuery` object), `format_query(query)`
  (canonical ADX formatting), `validate(query)` (structured diagnostics with
  severity/offset/message). Because pythonnet keeps Python and the CLR in one
  process, you can drop into raw .NET tree‑walking on the same `KustoCode`.
- **Parsing/validation only — no execution, no SQL generation.** But it is the
  most direct proof that we can drive Microsoft's parser from Python, and its IR
  could seed our translator's input.

### 5.3 pythonnet + `Microsoft.Azure.Kusto.Language` directly

- Documented pattern (e.g., Optyx Security's "Parsing KQL with Python"): `pip
  install pythonnet`, load the `Kusto.Language` DLL, call `KustoCode.Parse` /
  `getDiagnostics` for local syntax validation. This is the low‑level version of
  what Kustology packages.

### 5.4 Official ANTLR grammar in the Microsoft repo ⭐ (correcting an earlier assumption)

An earlier draft repeated the common claim that "there is no official Microsoft
ANTLR grammar." **That is wrong.** The `microsoft/Kusto-Query-Language` repo ships
an **ANTLR4 grammar** under
[`grammar/`](https://github.com/microsoft/Kusto-Query-Language/tree/master/grammar):

- **`Kql.g4`** — the parser grammar: **~1,550 lines / ~41 KB, ~200+ parser
  rules**. It covers the full tabular‑operator set (`where`, `summarize`, `join`,
  `lookup`, `project`/`project-away`, `extend`, `sort`/`order`, `take`/`limit`,
  `union`, `mv-expand`, `make-series`, `distinct`, `top`, `sample`, graph ops),
  the full scalar‑expression hierarchy, and function‑call forms
  (`toscalar`, `totable`, dotted/scoped functions).
- **`KqlTokens.g4`** — the lexer/token grammar it imports.
- a **`.antlr`** subdirectory.
- **License: Apache‑2.0** (the repo license), i.e. safe to reuse/derive from.

**Important caveat on provenance:** the *shipping* C# engine is a hand‑written
recursive‑descent parser (see `src/Kusto.Language/Parser/*.cs`, e.g.
`QueryGrammar.cs`, `CommandGrammar.cs`), **not** generated from these `.g4`
files. The `.g4` grammar therefore reads as an **official reference grammar**
that may occasionally lag the hand‑written parser and carries no semantic
actions. Still, for our purposes it's a **massive head start**: an
Apache‑2.0, Microsoft‑authored, comprehensive KQL grammar we can feed straight
into ANTLR's **Python target** to generate a pure‑Python parser.

### 5.5 Pure‑Python / other‑language parsers built on ANTLR

- **`tedyeates/kusto-query-language-parser`** (PyPI:
  `kusto-query-language-parser`) — a **pure‑Python ANTLR4** KQL parser
  producing a parse tree / JSON, with search helpers. **Apache‑2.0**, Python
  ≥3.6, depends on `antlr4-python3-runtime==4.8.0`. Given it is ANTLR4‑based and
  Apache‑2.0, it very likely derives from (or aligns with) Microsoft's `Kql.g4`.
  **Very early (v0.0.2, Apr 2025), parse‑tree only, no SQL**, coverage unproven —
  but it's a working example of exactly the pipeline we'd use (`Kql.g4` → ANTLR
  Python runtime → parse tree).
- **`CraftedSignal/kql-parser`** — a **Go** ANTLR4 KQL parser (extracts
  conditions/fields/tables). Not Python, but confirms the same grammar route
  works across targets.

> Note on naming collisions: "KQL" also refers to the **Kibana Query Language**
> (e.g. `Aloshi/kql-parser`). Those projects are unrelated to Kusto and are not
> relevant here.

## 6. Transpiler frameworks

- **`sqlglot`** — the de‑facto Python SQL parser/transpiler, and it already has
  a **DuckDB output dialect** plus a huge function‑mapping infrastructure.
  However, it has **no KQL/Kusto read dialect today** (confirmed: "Kusto KQL is
  SQL‑like, but it's not supported by sqlglot"). Adding one is a real but
  bounded contribution, and sqlglot has been growing pipe‑syntax support.
- **ClickHouse's native KQL dialect** — ClickHouse implemented a **read‑only KQL
  dialect** in C++ (`SET dialect = 'kusto'`, `ParserKQLStatement`), rolled out
  across "phase 1/2" PRs (#37961, #42510). It parses KQL into ClickHouse's AST.
  Not reusable for DuckDB (C++, tightly coupled to ClickHouse internals), but it
  is the best existing **reference design** for "translate KQL to another
  engine's execution model," including which operators/functions a realistic MVP
  covers (filter, project, sort, limit, string ops, `avg/count/min/max/sum`).
  Worth reading for scoping and for edge cases (it has had parser crash bugs,
  e.g. #59036 — a caution about KQL parsing complexity).

## 7. Remote Kusto clients (Strategy D — reference only, not on DuckDB)

These send KQL to a **real ADX/Fabric cluster**; they do **not** execute locally
and cannot run on DuckDB. They matter to us as (a) the compatibility oracle and
(b) the **Python API shape users already expect** (KQL string in → pandas
DataFrame out):

- **`azure-kusto-python`** (`azure-kusto-data`) — official Microsoft SDK; run
  KQL against a cluster, get results as pandas.
- **`Kqlmagic`** and **`ipython-kusto`** — Jupyter `%kql` / `%%kql` magics
  returning pandas DataFrames.
- **`kqlalchemy`** / **`sqlalchemy-kusto`** — SQLAlchemy dialects for Kusto.
  Note these mostly ride Kusto's **SQL‑over‑ODBC (MSSQL emulation)** surface —
  i.e. they are *SQL → Kusto*, not *KQL → anything*. `kqlalchemy` is early
  (0 stars, MIT); `sqlalchemy-kusto`'s KQL dialect is "in progress."

The takeaway: the ecosystem's Python KQL story is entirely "talk to Azure."
There is **no existing pip‑installable way to run KQL locally against DuckDB** —
which is exactly the gap this repository targets.

## 8. Summary of findings

1. **Nobody ships a Python library that runs KQL on DuckDB today.** The space is
   open.
2. **`saoc90/kql-to-sql` is the closest prior art** and the most reusable asset:
   it already translates KQL → **DuckDB** SQL using Microsoft's official parser
   and even ships a DuckDB extension — but it is C#/.NET with **no Python entry
   point** and low maturity.
3. **The official parser (`Microsoft.Azure.Kusto.Language`) is the fidelity
   ceiling**, reachable from Python via **pythonnet** (proven by **Kustology**)
   or from **JavaScript** (Microsoft ships a JS transpile of it).
4. **There IS an official, Apache‑2.0 ANTLR grammar** in the Microsoft repo
   (`grammar/Kql.g4` + `KqlTokens.g4`, ~1,550 lines, ~200+ rules) — it can be
   compiled with ANTLR's **Python target** to bootstrap a pure‑Python parser.
   (Caveat: it's a reference grammar; the shipping C# parser is hand‑written, so
   the grammar may lag slightly.) The existing pure‑Python parser
   (`kusto-query-language-parser`, v0.0.2, parse‑tree only) demonstrates this
   exact pipeline but is immature.
5. **`sqlglot` is the natural transpiler backbone** (already outputs DuckDB SQL)
   but **lacks a KQL front‑end** — a bounded piece of net‑new work.
6. **ClickHouse's KQL dialect** is the best **reference implementation** for
   scoping an MVP and understanding KQL‑parsing pitfalls.
7. **Full engines (KustoLoco/BabyKusto)** give exact semantics but **bypass
   DuckDB's engine** and pull in a .NET runtime.

These findings feed directly into the option set in
[`implementation-options.md`](./implementation-options.md).

## 9. Sources

- kql-to-sql (KQL→SQL, DuckDB dialect + DuckDB extension): https://github.com/saoc90/kql-to-sql · demo: https://saoc90.github.io/kql-to-sql/
- Microsoft official parser (`Microsoft.Azure.Kusto.Language`, C#+JS): https://github.com/microsoft/Kusto-Query-Language · https://www.nuget.org/packages/Microsoft.Azure.Kusto.Language/
- Kustology (Python wrapper over the official parser via pythonnet): https://detect.fyi/deep-kql-analysis-with-kustology-f07de9b02829
- kusto-query-language-parser (pure‑Python ANTLR parser): https://pypi.org/project/kusto-query-language-parser/ · https://github.com/tedyeates/kusto-query-language-python-parser
- Parsing KQL with Python (pythonnet + Kusto.Language): https://optyx.io/posts/kql-python/
- CraftedSignal/kql-parser (Go ANTLR KQL parser): https://github.com/CraftedSignal/kql-parser
- KustoLoco (C# in‑process KQL engine): https://github.com/NeilMacMullen/kusto-loco · https://www.nuget.org/packages/KustoLoco.Core/
- BabyKusto (self‑contained C# KQL engine): https://github.com/davidnx/baby-kusto-csharp
- duckdb-psql (pipe SQL for DuckDB; cites Kusto/PRQL): https://github.com/ywelsch/duckdb-psql
- ClickHouse KQL dialect (reference impl): https://github.com/ClickHouse/ClickHouse/pull/37961 · https://github.com/ClickHouse/ClickHouse/pull/42510 · https://clickhouse.com/docs/guides/developer/alternative-query-languages
- sqlglot (transpiler; DuckDB dialect, no KQL): https://github.com/tobymao/sqlglot
- azure-kusto-python (official remote SDK, reference behavior): https://github.com/Azure/azure-kusto-python
- Kqlmagic / ipython-kusto (Jupyter magics): https://learn.microsoft.com/en-us/azure/data-explorer/kqlmagic · https://pypi.org/project/ipython-kusto/
- kqlalchemy / sqlalchemy-kusto (SQLAlchemy dialects): https://github.com/alexkyllo/kqlalchemy · https://pypi.org/project/sqlalchemy-kusto/
