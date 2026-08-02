# M0 Spike Result — `Kql.g4` on ANTLR's Python Target

> Run 2026-08-02. **Result: PASS.** The gating risk for the whole implementation
> plan is resolved. Microsoft's grammar compiles to the Python target with zero
> diagnostics and parses **97.8%** of real-world doc queries once out-of-scope
> constructs are excluded.

## What was tested

| | |
|---|---|
| Grammar | `microsoft/Kusto-Query-Language` `grammar/Kql.g4` (1,550 lines) + `KqlTokens.g4` (485 lines) |
| Tool | ANTLR **4.13.2** (`antlr4-4.13.2-complete.jar`) |
| Command | `java -jar antlr.jar -Dlanguage=Python3 -visitor -o gen Kql.g4` |
| Runtime | `antlr4-python3-runtime==4.13.2`, Python 3.11 |
| Corpus | 1,427 ` ```kusto ` blocks from `dataexplorer-docs` @ `87fbc42` |

## 1. Generation — clean

**Exit code 0. Zero warnings, zero errors.** No target-specific actions, no
embedded C#, no semantic predicates needing translation. The grammar is
target-agnostic ANTLR4 as hoped.

Generated (committed at build time so users need no Java):

| File | Size |
|---|---|
| `KqlParser.py` | 1.0 MB |
| `KqlLexer.py` | 130 KB |
| `KqlListener.py` | 105 KB |
| `KqlVisitor.py` | 61 KB |

The 1 MB parser is worth noting for package size but is a single pure-Python
module — no build step, no native code.

## 2. Hand-written smoke tests — 11/11

Every Wave-1 shape parses, and a deliberately malformed control correctly fails:

`where` · full pipeline with `summarize`/`bin`/`sort` · `datatable` literal ·
`join kind=leftouter` · `let` + tabular · `dynamic` + `mv-expand` · the
`has`/`contains_cs`/`=~`/`!startswith` matrix · timespan literals ·
`make-series` · `render` · nested `iff`/`case`/`isnull`

## 3. Full corpus — 89.8% raw, 97.8% in-scope

Parsing all 1,427 real doc blocks:

| | Blocks | % |
|---|---:|---:|
| Parsed successfully | 1,281 | 89.8% |
| Failed | 146 | 10.2% |

Classifying the 146 failures is what matters:

| Category | Count | Verdict |
|---|---:|---|
| **Graph semantics** (`make-graph`, `graph-match`, `node-*`) | 50 | Out of scope — already deferred |
| **Management commands** (`.create`, `.set`, `.ingest`) | 30 | Out of scope — a *separate* grammar; `Kql.g4` is the query grammar |
| **Mislabeled blocks** — not KQL at all | 21 | Not a grammar issue (see below) |
| **Cross-cluster** (`cluster(...).database(...)`) | 13 | Out of scope — we're local-only |
| **Genuine grammar gaps** | **31** | **2.2% of corpus** |
| `evaluate` plugin | 1 | Wave 3 |

**Excluding out-of-scope constructs, the grammar parses ~97.8% of real queries.**

### The docs mislabel some blocks
21 blocks tagged ` ```kusto ` are not queries: bare expression lists from function
reference pages (13, e.g. `rand() rand(1000)`), JSON config blobs (3), rendered
**output tables** (2), prose (1), a **SQL** snippet (1), and a query fragment
starting with `|` (1).

**This is a harvester finding, not a grammar finding** — `tools/harvest_docs.py`
must filter these out, or the corpus will carry ~21 junk cases. Cheap filter: a
block that fails to parse *and* matches one of these shapes is dropped, not
xfailed.

## 4. The genuine gaps — a short, enumerable list

Probed individually. These are real, valid KQL that the reference grammar
rejects — consistent with `Kql.g4` being a reference artifact that lags the
hand-written C# parser:

| Construct | Example | Wave |
|---|---|---|
| **`in` / `in~` / `!in` with a tabular subquery** | `where State in (Pop \| project State)` | **1** ⚠️ |
| **bare `serialize`** (no assignment) | `T \| serialize` | 2 |
| `parse-kv` | `parse-kv str as (a:string)` | 2 |
| `parse kind=regex … with * <re>` | the `<regex>` pattern form | 2 |
| `table('Name')` as a source | `table('StormEvents') \| count` | 3 |
| `project-by-names` | — | deferred (kql-to-sql defers it too) |

Only **one** touches Wave 1: `in` with a tabular subquery. It's concentrated in
9 blocks of `parse-kv-operator.md` plus a handful elsewhere.

**Confirmed working** (I checked, expecting failures and not finding them):
multi-statement `let`, `toscalar(...)` with a tabular argument, `materialize(...)`
+ `union`, user-defined function definitions `let f = (a:string) { … }`,
`serialize` *with* a column assignment, and the `find` operator.

## 5. Decision

**Proceed with ANTLR-from-`Kql.g4`.** The fallback (hand-written recursive-descent
parser) is not needed. Rationale:

- clean generation, no toolchain friction
- 97.8% in-scope coverage on a 1,427-query real-world corpus
- the gap list is **short, enumerable, and mostly outside Wave 1**

**Grammar patch policy:** vendor at a pinned commit and keep local fixes as a
small, documented patch set in `grammar/UPSTREAM.md`, one file per gap. Start
with the `in`-subquery rule since it's the only Wave-1 blocker. Everything else
can raise `KqlUnsupportedError` until its wave.

This also **stands up test-plan layer L1** (parse corpus) as a side effect —
`corpus_parse.py` becomes the L1 regression test, with the current 1,281 passing
blocks as the baseline.

## 6. Follow-ups

- [ ] Vendor `Kql.g4` + `KqlTokens.g4` at pinned commit; add `grammar/UPSTREAM.md`
- [ ] Patch the `in`-with-subquery rule (Wave-1 blocker)
- [ ] Add block-shape filtering to `harvest_docs.py` for the 21 mislabeled blocks
- [ ] Wire `corpus_parse.py` in as the L1 test with a 1,281-block floor
- [ ] Decide whether to commit the 1 MB generated parser or generate at build time
      (leaning commit — it removes the Java dependency for users)

## Reproducing

```bash
curl -O https://raw.githubusercontent.com/microsoft/Kusto-Query-Language/master/grammar/Kql.g4
curl -O https://raw.githubusercontent.com/microsoft/Kusto-Query-Language/master/grammar/KqlTokens.g4
curl -O https://repo1.maven.org/maven2/org/antlr/antlr4/4.13.2/antlr4-4.13.2-complete.jar
java -jar antlr4-4.13.2-complete.jar -Dlanguage=Python3 -visitor -o gen Kql.g4
pip install antlr4-python3-runtime==4.13.2
```
