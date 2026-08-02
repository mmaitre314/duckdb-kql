# Implementation Options — Running KQL on DuckDB from Python

> Status: draft for discussion (2026-08-02). Builds on the landscape survey in
> [`kql-on-duckdb-landscape.md`](./kql-on-duckdb-landscape.md). Goal: let Python
> code run **Kusto KQL** queries against data held in **DuckDB**, ideally with an
> API as simple as `run_kql(query, con) -> pandas.DataFrame`. No decision is made
> here — this is the menu we'll choose from.

## Target developer experience (what all options aim for)

```python
import duckdb, duckdb_kql

con = duckdb.connect()
con.execute("CREATE TABLE Logs AS SELECT * FROM 'logs.parquet'")

df = duckdb_kql.query(con, """
    Logs
    | where Timestamp > ago(1d) and Level == "Error"
    | summarize Count = count() by bin(Timestamp, 1h), Component
    | sort by Timestamp asc
""")
```

The options differ mainly in **how the KQL becomes something DuckDB can run**,
and what runtime baggage that requires.

## The six options at a glance

| # | Option | Core idea | Runtime deps beyond DuckDB+Python | Fidelity | Effort | Pure‑pip? |
|---|--------|-----------|-----------------------------------|----------|--------|-----------|
| 1 | **Pure‑Python KQL→SQL transpiler ✅** | ANTLR(`Kql.g4`) parser + emitter → DuckDB SQL (+UDFs) | none | med (grows) | Med‑High | ✅ |
| 2 | `sqlglot` KQL front‑end | Add KQL read‑dialect, transpile to DuckDB | none | med→high | High | ✅ |
| 3 | pythonnet + official parser | Microsoft parser (AST) → our SQL emitter | .NET runtime + pythonnet | high | Med‑High | ❌ |
| 4 | Reuse `kql-to-sql` (.NET / extension) | Call existing translator, run SQL on DuckDB | .NET or the DuckDB extension | high | Low‑Med | ❌ |
| 5 | JS official parser / kql-to-sql WASM | Drive JS/WASM translator from Python | JS/WASM runtime | high | Med | ⚠️ |
| 6 | Embed KustoLoco engine | Real KQL engine executes; DuckDB = storage | .NET runtime + pythonnet | highest | Med | ❌ |

"Fidelity" = how faithfully real KQL semantics are reproduced. "Effort" is
relative to reaching a *useful* subset, not 100% coverage.

---

## Option 1 — Pure‑Python KQL → SQL transpiler ✅ chosen direction

Parse KQL in Python — bootstrapping from Microsoft's **official Apache‑2.0
`grammar/Kql.g4`** compiled with **ANTLR's Python target** — and emit **DuckDB
SQL**, then execute on DuckDB. Where a KQL function has no clean DuckDB SQL
equivalent, fall back to a **DuckDB Python UDF** (`con.create_function(...)`),
keeping any non‑Python code to essentially zero.

> **Decision (2026‑08‑02):** we're aiming for this pure‑Python direction. See
> "Why pure Python wins here" below and the updated closing framing.

**Pros**
- **Zero non‑Python runtime.** `pip install duckdb-kql` and go — no .NET, no
  Node, no WASM. Best packaging/distribution story and matches what users expect
  from a Python library.
- **DuckDB does the heavy lifting** — we inherit its speed, Arrow/Parquet, and
  scale; we only translate.
- **We don't have to invent the grammar.** The official `Kql.g4` (~1,550 lines,
  ~200+ rules, Apache‑2.0) → ANTLR Python target gives us a real parser cheaply;
  `kusto-query-language-parser` already demonstrates this exact pipeline.
- **Full control** over dialect mapping, error messages, and incremental scope.
- **Clean UDF escape hatch:** functions that don't map to DuckDB SQL become small
  Python UDFs registered on the connection — pure Python, no compiled artifacts.

**Cons / risks to manage**
- **Semantic mapping is the real work**, not parsing: `dynamic`/JSON,
  `datetime`/`timespan` arithmetic, `has`/`contains` tokenization semantics,
  `mv-expand`, `make-series`, `parse`, percentiles, etc. Each needs a correct
  DuckDB expansion (SQL or UDF).
- **The `.g4` is a *reference* grammar** — the shipping C# parser is hand‑written,
  so the grammar may lag or mismatch in edge cases; we may need local grammar
  fixes. (Mitigation: pin a grammar commit; keep a conformance suite.)
- The existing pure‑Python parser is **v0.0.2, parse‑tree only, unproven
  coverage** — useful as a reference, but we likely generate our own from
  `Kql.g4` rather than depend on it.
- Risk of subtle **divergence from real ADX behavior**; needs a golden test
  suite (Option 4/3 can serve as the oracle — see below).
- **Per‑row Python UDFs can be slow**; prefer native SQL/vectorized expansions
  first and reserve UDFs for genuinely hard functions.

### Why pure Python wins here
The whole value proposition is a **pip‑installable, embeddable** library where
DuckDB is the engine. A .NET/WASM dependency (Options 3–6) undercuts that for
every user to solve a problem most queries don't have. The grammar find removes
the biggest objection to Option 1 (owning the parser), and DuckDB UDFs cover the
long tail of function mappings **without leaving Python**.

---

## Option 2 — Build a KQL front‑end for `sqlglot`

Add a **Kusto/KQL "read" dialect** to `sqlglot`, then use its existing **DuckDB
generator** to emit SQL. Effectively Option 1 but standing on sqlglot's mature
transpiler infrastructure.

**Pros**
- **Reuses battle‑tested infrastructure**: expression trees, an enormous
  function‑mapping/rewriting system, and an already‑correct **DuckDB output
  dialect**. We write a *front‑end*, not a whole transpiler.
- Pure Python, pip‑installable, no external runtime.
- Potentially **upstreamable** — a KQL dialect benefits (and is maintained by)
  the broader sqlglot community; sqlglot is already adding pipe‑syntax support.
- Gives cross‑dialect output almost for free (Postgres, etc.) if ever wanted.

**Cons**
- **No KQL dialect exists today** — this is real net‑new grammar work inside
  sqlglot's parser model, which is tokenizer+Pratt‑parser based and assumes a
  SQL‑ish shape; KQL's pipe/data‑flow model must be mapped onto sqlglot's AST.
- Same **semantic‑mapping burden** as Option 1 for KQL‑specific types/functions.
- Coupling to sqlglot's release cadence and internal APIs.
- Contributing/maintaining a dialect upstream adds process overhead.

**Best when:** we believe the transpiler route is right and want to minimize the
"SQL generation + function mapping" work by reusing sqlglot, accepting the cost
of teaching it KQL's shape.

---

## Option 3 — `pythonnet` + official Microsoft parser, custom SQL emitter

Use **`Microsoft.Azure.Kusto.Language`** (the authoritative parser) via
**`pythonnet`** to get a fully‑correct AST/IR (as **Kustology** already does),
then write the **KQL‑AST → DuckDB‑SQL** emitter ourselves (in Python or C#).

**Pros**
- **Best possible parse fidelity** — it's *the* parser ADX/Sentinel use; we never
  fight KQL syntax edge cases or maintain a grammar.
- Free **diagnostics, formatting, and semantic analysis** from the same library.
- **Kustology** proves the pythonnet bridge and even offers a Pydantic IR to
  start from; we skip straight to the interesting part (SQL emission).

**Cons**
- **Heavy runtime dependency**: requires a **.NET runtime + pythonnet** on every
  install → materially harder cross‑platform packaging (Linux/macOS/Windows,
  wheels, CI) than a pure‑Python package.
- We **still write the entire SQL‑generation layer** — the parser doesn't
  translate; it only parses. So the hard semantic work of Options 1/2 remains.
- CLR‑in‑process adds startup cost, memory, and a debugging surface unfamiliar to
  most Python users.

**Best when:** parse correctness is paramount and a .NET dependency is
acceptable; a good "phase‑2 fidelity upgrade" if a pure‑Python parser proves too
lossy.

---

## Option 4 — Reuse `saoc90/kql-to-sql` (existing KQL→DuckDB‑SQL translator)

The closest prior art already translates KQL → **DuckDB** SQL (built on the
official parser) and ships **both a NuGet library and a DuckDB extension**
(`kql_to_sql()` / `kql_explain()`). Wrap it from Python: either load its **DuckDB
extension** and call the SQL functions, or call the .NET translator to get SQL
strings, then execute on DuckDB.

**Pros**
- **Lowest effort to a broad working subset** — 70+ operators / 200+ functions
  are *already mapped to DuckDB SQL*. We could have something real quickly.
- Built on the **official parser** → high fidelity for what's covered.
- MIT‑licensed; we can vendor, fork, or contribute upstream.
- The **DuckDB‑extension** path means translation happens *inside* DuckDB —
  Python just loads an extension and runs `SELECT kql_to_sql(...)` /
  passes KQL through, minimal glue.

**Cons**
- **.NET coupling.** The library is C#/.NET; the DuckDB extension embeds a .NET
  translator. Either way we ship/require a .NET‑based artifact — not pure Python,
  and the extension must be built per‑platform.
- **Maturity/bus‑factor risk**: ~4 stars, effectively single‑author, no
  stability guarantees; we'd likely need to fork and maintain.
- **Distribution friction**: a non‑registry DuckDB **community/unsigned
  extension** can be awkward to load (signature flags, per‑platform binaries) and
  to publish on PyPI.
- We inherit its bugs and its mapping choices; auditing correctness is on us.

**Best when:** we want the fastest path to broad coverage and are comfortable
depending on / forking a .NET artifact, at least initially.

---

## Option 5 — Drive the JS/WASM translator from Python

Microsoft ships a **JavaScript build of the official parser**, and `kql-to-sql`
compiles to **WASM** (its Blazor demo runs against DuckDB‑WASM). Run that
translator from Python via a bundled JS runtime (Node subprocess) or a WASM
runtime (`wasmtime`/`wasmer`), get SQL back, execute on DuckDB.

**Pros**
- Reuses an **existing, official‑parser‑based** translator without a full .NET
  install (WASM route), keeping the fidelity of Options 3/4.
- WASM is **sandboxed and portable** across OSes — potentially simpler than
  shipping platform‑specific .NET binaries.

**Cons**
- **Awkward Python packaging**: bundling Node or a WASM runtime + glue, plus
  IPC/marshaling overhead per query.
- The WASM/JS artifact is designed for the browser demo, not as a headless
  library — likely needs adaptation.
- More moving parts to debug (Python ↔ JS/WASM boundary) than any pure‑Python
  option.

**Best when:** we want official‑parser fidelity, want to avoid a .NET runtime,
and consider a WASM sidecar acceptable. Generally a fallback to Options 3/4.

---

## Option 6 — Embed a full KQL engine (KustoLoco), DuckDB as storage

Don't transpile at all: run a **real in‑process KQL engine** (**KustoLoco**, or
BabyKusto) via `pythonnet`, and use DuckDB only to hold/load data (export
Parquet/Arrow from DuckDB into the engine).

**Pros**
- **Exact KQL semantics** with the least *translation* work — the engine already
  implements KQL operators/functions correctly.
- Mature‑ish, maintained engine (KustoLoco) with Parquet I/O and charting.

**Cons**
- **DuckDB stops being the query engine** — it's demoted to a storage/Parquet
  layer, which partly defeats "run KQL *on DuckDB*." We lose DuckDB's
  optimizer/scale for the actual query.
- **Data movement**: results must round‑trip DuckDB → engine → Python; large
  tables mean copying out of DuckDB.
- **.NET runtime + pythonnet** dependency and packaging cost (as Option 3).
- KustoLoco's coverage ≠ full ADX; we still hit gaps, just different ones.

**Best when:** semantic fidelity matters more than using DuckDB's engine, e.g.
validating other options' output against a KQL oracle, or a "correctness mode."

---

## Cross‑cutting considerations (apply to whichever we pick)

- **Table/schema binding.** KQL references tables by bare name (`Logs | …`); we
  must map those to DuckDB tables/views and know their schemas (needed for
  `dynamic`/type‑aware translation and for the official parser's semantic pass).
- **Type system.** DuckDB has `JSON`, `TIMESTAMP`, `INTERVAL`, lists/structs —
  good targets for KQL `dynamic`/`datetime`/`timespan`/arrays, but the mappings
  need care (e.g. KQL `dynamic` indexing, `todatetime`, `ago()`, `bin()`).
- **Correctness / conformance.** Whatever we build needs a **golden test suite**
  of KQL→expected‑results. Options 4/6 (official‑parser / real engine) can double
  as oracles to test Options 1/2 against.
- **Scope as a dial.** An MVP (à la ClickHouse's KQL phase 1) —
  `where`/`project`/`extend`/`summarize`/`sort`/`take`/`join`/`union` +
  common scalar/aggregate functions — likely covers most real queries and is a
  realistic first milestone for any option.
- **API surface.** Regardless of engine, expose the pandas‑returning shape users
  know from `azure-kusto-python`/Kqlmagic, so migration from ADX is trivial.

## Direction (decided) and open questions

**Decided:** go **pure Python** — **Option 1**, bootstrapping the parser from
Microsoft's official Apache‑2.0 `Kql.g4` via ANTLR's Python target, emitting
DuckDB SQL, with **DuckDB Python UDFs** as the escape hatch for functions that
don't map cleanly. Keep non‑Python code to ~zero.

**Still to settle before an implementation plan:**
- **Parser build:** generate from `Kql.g4` with ANTLR (ship generated Python,
  pin a grammar commit) vs. reuse/fork `kusto-query-language-parser` vs. a
  hand‑written recursive‑descent parser for a curated subset. Leaning
  ANTLR‑from‑`Kql.g4`.
- **`sqlglot` (Option 2) as the *emitter* backend?** Even with our own KQL
  front‑end, sqlglot could build/optimize the DuckDB SQL AST and handle
  quoting/dialect details. Worth a spike — it stays pure Python.
- **MVP scope:** which operators/functions ship first (suggest the ClickHouse
  phase‑1 set: `where`/`project`/`extend`/`summarize`/`sort`/`take`/`top`/
  `join`/`union`/`distinct` + common scalar/aggregate functions).
- **Conformance oracle:** use Option 4 (`kql-to-sql`) and/or a real ADX/KustoLoco
  instance **in CI only** (not as a runtime dependency) to generate golden
  expected results.
- **UDF policy:** guardrails for when a mapping may be a per‑row Python UDF vs.
  must be native SQL, given performance.

Next step: agree on the bullets above, then I'll write the implementation plan
(package layout, parser pipeline, translation architecture, test harness).

## Sources

See the consolidated source list in
[`kql-on-duckdb-landscape.md` §9](./kql-on-duckdb-landscape.md#9-sources). Key
anchors for these options: `saoc90/kql-to-sql`, `microsoft/Kusto-Query-Language`
(+ its JS transpile), `Kustology`, `kusto-query-language-parser`, `sqlglot`,
`NeilMacMullen/kusto-loco`, and ClickHouse's KQL dialect PRs.
