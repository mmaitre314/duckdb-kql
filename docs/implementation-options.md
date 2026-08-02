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
| 1 | Pure‑Python KQL→SQL transpiler | Own parser+emitter → DuckDB SQL | none | med (grows) | High | ✅ |
| 2 | `sqlglot` KQL front‑end | Add KQL read‑dialect, transpile to DuckDB | none | med→high | High | ✅ |
| 3 | pythonnet + official parser | Microsoft parser (AST) → our SQL emitter | .NET runtime + pythonnet | high | Med‑High | ❌ |
| 4 | Reuse `kql-to-sql` (.NET / extension) | Call existing translator, run SQL on DuckDB | .NET or the DuckDB extension | high | Low‑Med | ❌ |
| 5 | JS official parser / kql-to-sql WASM | Drive JS/WASM translator from Python | JS/WASM runtime | high | Med | ⚠️ |
| 6 | Embed KustoLoco engine | Real KQL engine executes; DuckDB = storage | .NET runtime + pythonnet | highest | Med | ❌ |

"Fidelity" = how faithfully real KQL semantics are reproduced. "Effort" is
relative to reaching a *useful* subset, not 100% coverage.

---

## Option 1 — Pure‑Python KQL → SQL transpiler

Parse KQL in Python (own hand‑written parser, or a Python ANTLR grammar such as
`kusto-query-language-parser`) and emit **DuckDB SQL**, then execute on DuckDB.

**Pros**
- **Zero non‑Python runtime.** `pip install duckdb-kql` and go — no .NET, no
  Node, no WASM. Best possible packaging/distribution story and matches user
  expectations for a Python library.
- **DuckDB does the heavy lifting** — we inherit its speed, Arrow/Parquet, and
  scale; we only translate.
- **Full control** over dialect mapping, error messages, and incremental scope.
- Easiest to embed, test, and ship on PyPI; smallest dependency surface.

**Cons**
- **We own the parser.** KQL's grammar is large and only *unofficially*
  documented as a grammar; no official `.g4`. Keeping up with KQL syntax is an
  ongoing tax.
- **Semantic gaps are the hard part**, not parsing: `dynamic`/JSON,
  `datetime`/`timespan` arithmetic, `has`/`contains` tokenization semantics,
  `mv-expand`, `make-series`, `parse`, percentiles, etc. Each needs a correct
  DuckDB expansion.
- Existing pure‑Python parser (`kusto-query-language-parser`) is **v0.0.2,
  parse‑tree only, unproven coverage** — we may end up writing our own parser
  anyway.
- Risk of subtle **divergence from real ADX behavior** that's hard to detect
  without a conformance suite.

**Best when:** we want a clean, dependency‑free Python package and are willing to
grow coverage operator‑by‑operator from an MVP.

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

## Preliminary framing for the discussion (not a decision)

Two coherent directions emerge:

- **Pure‑Python transpiler** (Option 1 or **2**) — best long‑term fit for a
  Python DuckDB library: no runtime baggage, DuckDB does the work, pip‑installable.
  Cost is owning KQL parsing + semantic mapping. **Option 2 (sqlglot front‑end)**
  is the most leveraged version of this.
- **Reuse existing translation/engine** (Option **4**, later 3/6) — fastest to
  broad coverage and highest fidelity, at the price of a .NET/WASM dependency and
  dependence on a small upstream.

A plausible hybrid: **start on Option 4/3 to get coverage and an oracle, and to
learn the mappings, then converge on a pure‑Python Option 1/2** for the shippable
library — using the official‑parser path as the conformance reference.

Let's discuss and pick a direction before writing an implementation plan.

## Sources

See the consolidated source list in
[`kql-on-duckdb-landscape.md` §9](./kql-on-duckdb-landscape.md#9-sources). Key
anchors for these options: `saoc90/kql-to-sql`, `microsoft/Kusto-Query-Language`
(+ its JS transpile), `Kustology`, `kusto-query-language-parser`, `sqlglot`,
`NeilMacMullen/kusto-loco`, and ClickHouse's KQL dialect PRs.
