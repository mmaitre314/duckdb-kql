# Implementation Plan — `duckdb-kql`

> Status: draft (2026-08-02). Builds on
> [`kql-on-duckdb-landscape.md`](./kql-on-duckdb-landscape.md) and
> [`implementation-options.md`](./implementation-options.md). Direction is
> settled: **pure‑Python KQL → DuckDB‑SQL transpiler**, parser bootstrapped from
> Microsoft's official Apache‑2.0 `Kql.g4` via ANTLR's Python target, with
> **DuckDB Python UDFs** as the escape hatch for functions that don't map to SQL.
> The sqlglot‑as‑emitter question is intentionally **deferred** (see §11).

> Related: [`lessons-from-bun-rewrite.md`](./lessons-from-bun-rewrite.md) reviews
> Bun's Zig→Rust rewrite for transferable practice. Its main proposals for this
> plan: write a normative `TRANSLATION.md` **before** the translator, keep the
> function mapping table as **data rather than code**, and adopt "fix the rule,
> not the query."

## 1. Goal & non‑goals

**Goal.** A pip‑installable Python library that runs Kusto KQL queries against
data already resident in a DuckDB connection, returning Arrow/pandas results:

```python
import duckdb, duckdb_kql

con = duckdb.connect()
con.execute("CREATE TABLE Logs AS SELECT * FROM 'logs.parquet'")

df = duckdb_kql.df(con, """
    Logs
    | where Timestamp > ago(1d) and Level == "Error"
    | summarize Count = count() by bin(Timestamp, 1h), Component
    | sort by Timestamp asc
""")
```

**Non‑goals (v1).**
- Not a full ADX server, ingestion, or management‑command surface.
- Not 100% KQL coverage on day one — we ship a useful subset and grow it.
- No network/remote Kusto; execution is always local DuckDB.
- No compiled/native artifacts shipped by us (ANTLR runtime and DuckDB are the
  only non‑trivial deps; both are pure‑Python wheels for the user).

## 2. Architecture overview

A five‑stage pipeline, each stage independently testable:

```
KQL text
  │  (1) Lex + parse         ANTLR-generated parser from Kql.g4  → parse tree (CST)
  ▼
CST
  │  (2) Lower to IR          visitor over CST  → small, stable KQL AST/IR
  ▼
KQL IR  ── + schema (table→columns/types, pulled from the DuckDB connection)
  │  (3) Translate            visitor over IR  → DuckDB SQL (CTE-chained)
  ▼                           + records which UDFs the SQL needs
DuckDB SQL (+ required UDF set)
  │  (4) Ensure UDFs          register any needed Python UDFs on the connection (idempotent)
  ▼
  │  (5) Execute              con.sql(sql)  → DuckDB relation → Arrow/pandas
  ▼
Result
```

**Why a separate IR (stage 2) rather than translating the raw ANTLR tree?**
- Decouples us from ANTLR's generated tree shape, so we can later swap or
  hand‑write the parser without rewriting the translator.
- Gives a clean, documented, testable surface (each operator = one IR node).
- Makes the translator a straightforward visitor with no ANTLR imports.

**Translation model (stage 3).** KQL is a linear pipeline of *tabular operators*,
each consuming and producing a relation. We render the pipeline as a **chain of
CTEs** (readable, and DuckDB optimizes straight through them):

```
T | where P | project a, b | summarize c=count() by a
```
→
```sql
WITH _s0 AS (SELECT * FROM T),
     _s1 AS (SELECT * FROM _s0 WHERE <P>),
     _s2 AS (SELECT a, b FROM _s1),
     _s3 AS (SELECT a, count(*) AS c FROM _s2 GROUP BY a)
SELECT * FROM _s3
```

Scalar expressions are translated by a separate expression visitor backed by a
**function/operator mapping table** (§5). DuckDB performs all query optimization;
we never run a source‑level optimizer over the SQL.

## 3. Package layout (proposed)

```
duckdb-kql/
├─ pyproject.toml
├─ README.md
├─ LICENSE                      # MIT (project)
├─ NOTICE                       # attribution for vendored Apache-2.0 grammar
├─ docs/                        # these design docs
├─ grammar/
│  ├─ Kql.g4                    # vendored from microsoft/Kusto-Query-Language @ pinned commit
│  ├─ KqlTokens.g4
│  └─ UPSTREAM.md               # source commit + local patches log
├─ src/duckdb_kql/
│  ├─ __init__.py               # public API (df/sql/to_sql/register)
│  ├─ _antlr/                   # generated parser (vendored, committed)
│  │  ├─ KqlLexer.py
│  │  ├─ KqlParser.py
│  │  └─ KqlVisitor.py
│  ├─ ir.py                     # IR node dataclasses
│  ├─ lower.py                  # CST → IR
│  ├─ translate/
│  │  ├─ operators.py           # tabular-operator translators
│  │  ├─ expressions.py         # scalar-expression translator
│  │  ├─ functions.py           # KQL→DuckDB function/operator mapping table
│  │  └─ types.py               # KQL↔DuckDB type mapping, datetime/timespan/dynamic
│  ├─ udf/
│  │  ├─ registry.py            # idempotent UDF registration on a connection
│  │  └─ builtins.py            # Python UDF implementations (only where needed)
│  ├─ schema.py                 # introspect DuckDB tables (DESCRIBE / information_schema)
│  ├─ session.py               # optional connection wrapper
│  └─ errors.py                 # KqlSyntaxError / KqlUnsupportedError / KqlSchemaError
├─ tools/
│  └─ regen_parser.(sh|py)      # runs the ANTLR tool to regenerate _antlr/
└─ tests/
   ├─ test_parse.py             # parse-only / fuzz
   ├─ test_translate_sql.py     # KQL → expected SQL (golden strings)
   ├─ test_behavior.py          # KQL run on DuckDB fixtures → expected results
   └─ fixtures/
```

## 4. Parser (stages 1–2)

**Approach:** vendor `Kql.g4` + `KqlTokens.g4` from `microsoft/Kusto-Query-Language`
at a **pinned commit**, generate the Python lexer/parser/visitor with the ANTLR
tool at *dev* time, and **commit the generated Python** so end users need only
`antlr4-python3-runtime` (no Java/ANTLR toolchain to install).

**Runtime dep:** `antlr4-python3-runtime`, version matched to the ANTLR tool used
to generate (the existing PyPI `kusto-query-language-parser` pins `4.8.0`; we'll
target a current 4.13.x unless the grammar forces otherwise).

**M0 spike / risk (do first):** confirm `Kql.g4` compiles cleanly to the **Python
target**. Microsoft's grammar may contain target‑specific options, actions, or
semantic predicates (it's maintained as a *reference* alongside a hand‑written C#
parser). Tasks:
- Run the ANTLR tool with `-Dlanguage=Python3`; catch target‑specific constructs.
- Strip/port any embedded actions or `options { }` that don't apply to Python.
- Record every local edit in `grammar/UPSTREAM.md` so re‑syncing upstream is
  mechanical.
- If the grammar proves too divergent, fall back to a hand‑written
  recursive‑descent parser for the MVP subset (still pure Python) — but only if
  the spike shows this is cheaper.

**Lowering (`lower.py`):** a visitor turning the CST into IR dataclasses
(`Query`, `Let`, `TabularExpr`, and one node per operator: `Where`, `Project`,
`Extend`, `Summarize`, `Sort`, `Take`, `Top`, `Join`, `Union`, `Distinct`, …;
plus expression nodes). Unsupported constructs raise `KqlUnsupportedError` with
the operator name and source span — so partial coverage fails loudly and clearly.

## 5. Translation (stage 3)

### 5.1 Tabular operators → SQL
Each operator is a function `(prev_cte, node, ctx) -> new_cte_sql`. Context
carries the current output column list (for `project-away`, default `summarize`
names, `join` disambiguation, etc.).

### 5.2 Scalar expressions & the function mapping table
`functions.py` holds a declarative registry keyed by KQL function/operator name.
Each entry resolves to one of:
- **native** — a DuckDB builtin (`strlen → length`, `toupper → upper`);
- **template** — a SQL expansion (`ago(x) → (now() - <x>)`,
  `bin(v, g) → time_bucket(<g>, <v>)`, `iff(c,a,b) → CASE WHEN <c> THEN <a> ELSE <b> END`);
- **udf** — a registered Python UDF (last resort, §6).

This table *is* the coverage surface; growing coverage = adding rows + tests.

### 5.3 Type & semantic mapping (`types.py`) — the genuinely hard part
- **datetime** → DuckDB `TIMESTAMP`; datetime literals via `TIMESTAMP '…'`.
- **timespan** (`1d`, `5m`, `100ms`) → `INTERVAL`; arithmetic preserved.
- **dynamic / JSON** → DuckDB `JSON` (and/or native `LIST`/`STRUCT`); property
  access `a.b` / `a["b"]` → `json_extract`; `parse_json`/`todynamic` mapping.
- **string semantics** — KQL `==` is case‑sensitive, `=~` case‑insensitive;
  `has`/`contains`/`startswith` are case‑*insensitive* by default → map to the
  right DuckDB `ILIKE`/`lower()`/`contains` form. This is a common correctness
  trap; cover it with explicit tests.
- **null/empty** — `isempty`/`isnotempty`/`isnull` semantics.
- **numeric coercions** — `toint`/`tolong`/`todouble`/`tobool`/`tostring`.

Type‑sensitive translations need table schemas; `schema.py` introspects the
DuckDB connection (`DESCRIBE`/`information_schema`) lazily. MVP can be
schema‑light (bind table names only) and pull schema on demand for the operators
that need it.

## 6. UDF strategy (stage 4)

- **SQL first.** Always prefer native DuckDB SQL or a template expansion. Reserve
  Python UDFs for functions with no clean, correct SQL form (candidates: some
  `parse`/regex extraction, IPv4/CIDR ops, certain `dynamic` manipulations).
- **Registration.** `udf/registry.py` registers UDFs on the connection via
  `con.create_function(...)`, namespaced (e.g. `kql_parse_ipv4`), **idempotently**
  (track what's registered per connection; never double‑register).
- **Wiring.** The translator records the set of UDFs a query needs; stage 4
  ensures exactly those are present before execution — so a query that needs no
  UDF touches no UDF.
- **Performance guardrail.** Per‑row Python UDFs are slow; a mapping may use a UDF
  only if a native/vectorized form is genuinely impractical, and each UDF gets a
  perf note in its docstring. (DuckDB supports Arrow‑vectorized UDFs; prefer that
  signature where a UDF is unavoidable.)

## 7. Public API (stage 5)

Small, functional, pandas‑friendly (mirrors what ADX users expect):

```python
duckdb_kql.sql(con, kql: str) -> duckdb.DuckDBPyRelation   # execute, lazy relation
duckdb_kql.df(con, kql: str) -> pandas.DataFrame            # execute → pandas
duckdb_kql.arrow(con, kql: str) -> pyarrow.Table            # execute → Arrow
duckdb_kql.to_sql(kql: str, schema=None) -> str            # translate only (no exec)
```

- `to_sql` needs no connection → cheap inspection/debugging and the core of
  golden translation tests.
- Optional `session.KqlSession(con)` wrapper for repeated queries.
- `pandas`/`pyarrow` are **optional** deps; default return is a DuckDB relation
  so the core has no heavy deps.
- Errors: `KqlSyntaxError` (parse), `KqlUnsupportedError` (unmapped
  operator/function, with name + span), `KqlSchemaError` (unknown table/column).

## 8. Milestones

**M0 — Scaffolding & parser (de‑risk).**
- Package skeleton, `pyproject.toml`, CI, licensing/NOTICE.
- Vendor + pin grammar; **prove `Kql.g4` → Python target compiles**; `tools/regen_parser`.
- `parse(kql)` returns a CST; smoke test on a corpus of real KQL queries.
- IR dataclasses + `lower.py` for the MVP operator set.

**M1 — MVP transpiler (first useful release).**
- Operators: `where`, `project`, `project-away`, `project-rename`, `extend`,
  `take`/`limit`, `top`, `sort`/`order`, `distinct`, `count`, `summarize`
  (`count`/`sum`/`avg`/`min`/`max`/`make_list`/`make_set`/`dcount`≈approx, `by`),
  `join` (inner/left/right, `kind=`), `union`, `let` (scalar + tabular).
- Scalars: arithmetic/comparison/logical; string (`strlen`, `substring`,
  `toupper`/`tolower`, `strcat`, `replace`, `split`, `has`/`contains`/`startswith`/
  `endswith`, `=~`); datetime (`now`, `ago`, `bin`, `todatetime`, datetime/timespan
  literals); conversions; `iff`/`case`; `isnull`/`isnotnull`/`isempty`/`coalesce`.
- Public API (`sql`/`df`/`arrow`/`to_sql`), error types.
- Golden SQL tests + behavioral tests on DuckDB fixtures.

**M2 — dynamic/JSON & analytics depth.**
- `dynamic`/JSON access, `parse_json`/`todynamic`, arrays; `mv-expand`, `parse`
  operator, `make-series`, percentiles, richer datetime/regex; first Python UDFs.

**M3 — completeness & edge cases.**
- `join` kinds/edge cases, `union` wildcards, `toscalar`/subqueries, user function
  definitions (`let f=(...){...}`), `materialize`, more management‑free operators.

**M4 — conformance, performance, docs.**
- Conformance harness (§9), perf pass on CTE chains/UDFs, user docs & examples,
  coverage matrix published.

## 9. Testing & conformance

> Expanded into a dedicated [`test-plan.md`](./test-plan.md), which covers corpus
> harvesting from Microsoft's docs, differential testing against reference
> engines, the KQL↔DuckDB behavioral-divergence catalog, and a usage-driven
> implementation prioritization. Summary below.

- **Parse tests** — parse a corpus of real‑world KQL; assert no crashes
  (fuzz‑friendly). Guards grammar regressions.
- **Golden translation tests** — `to_sql(kql)` vs expected DuckDB SQL (normalized
  whitespace). Fast regression net; brittle, so kept minimal and intentional.
- **Behavioral tests (primary)** — load fixture tables into DuckDB, run KQL, assert
  the resulting DataFrame. This is the real correctness signal.
- **Conformance oracle (CI‑only, deferred to M4)** — compare our results against an
  external reference (e.g. `saoc90/kql-to-sql` translations, or a KustoLoco/real
  ADX run) to catch semantic drift. **Never a runtime dependency** — CI only.
- **Coverage matrix** — a generated table of supported operators/functions,
  published in docs so users know what works.

## 10. Tooling, packaging, licensing

- **Deps:** runtime = `duckdb`, `antlr4-python3-runtime`; optional = `pandas`,
  `pyarrow`. Dev = the ANTLR tool (Java) for regen only, `pytest`, `ruff`, `nox`.
- **Generated code committed** so users never need Java/ANTLR.
- **Grammar provenance:** `Kql.g4`/`KqlTokens.g4` are Apache‑2.0 → keep upstream
  headers, add `NOTICE` and `grammar/UPSTREAM.md` (source commit + local patches).
  Project code stays MIT.
- **CI:** matrix over supported Python (3.9+); run parse/translate/behavior tests;
  a scheduled job that re‑runs the regen from pinned grammar to detect drift.

## 11. Deferred / open decisions

- **sqlglot as the SQL emitter (deferred, per decision).** Stage 3 will first emit
  DuckDB SQL via simple, well‑tested string builders. We keep the emitter behind a
  narrow internal interface so a sqlglot‑AST backend can be dropped in later
  without touching operators/expressions logic. Revisit after M1 with a real spike
  (quoting robustness vs. added surface). DuckDB owns optimization regardless.
- **Schema‑light vs schema‑aware default** — how much type inference to require up
  front vs. pull lazily. Start schema‑light; tighten as type‑sensitive operators
  land.
- **Grammar fallback** — if the ANTLR Python‑target spike (M0) is costly, decide
  between patching the grammar vs. a hand‑written subset parser.
- **UDF boundary** — finalize the list of functions allowed to be UDFs vs. required
  to be native SQL, informed by M2 findings.

## 12. Risks & mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| `Kql.g4` doesn't cleanly target Python (C#-isms) | Blocks parser | M0 spike first; log patches; hand‑written subset fallback |
| Grammar lags the real (hand‑written) KQL parser | Edge‑case parse gaps | Pin commit; parse‑corpus tests; sync procedure documented |
| KQL↔SQL semantic drift (strings/dynamic/datetime) | Wrong results | Behavioral + (later) conformance tests; explicit trap tests |
| Per‑row Python UDF performance | Slow queries | SQL‑first policy; Arrow‑vectorized UDFs; perf notes |
| Scope creep toward "all of KQL" | Never ships | Strict MVP (M1); coverage matrix; `KqlUnsupportedError` is fine |

## 13. Immediate next steps

1. **M0 spike:** vendor+pin `Kql.g4`/`KqlTokens.g4`, run the ANTLR Python‑target
   generation, and report whether it compiles clean or needs patching. *(This is
   the single biggest unknown and gates everything else.)*
2. Stand up the package skeleton, CI, and licensing/NOTICE.
3. Define the IR dataclasses for the M1 operator set.

I'll hold here for a green light on this plan (and specifically on the M0 spike)
before writing code.
