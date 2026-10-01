# Vendored Grammar — Provenance and Local Patches

## Upstream

| | |
|---|---|
| Repository | [`microsoft/Kusto-Query-Language`](https://github.com/microsoft/Kusto-Query-Language) |
| Path | `grammar/` |
| Pinned commit | **`6ad55002f78cc6a99870a524bb3b5c796b170b23`** |
| License | **Apache-2.0** (see `../THIRD-PARTY-NOTICES.md`) |

Upstream file hashes as vendored (before local patches):

```
a912b0f1e24d46f3f1639c97a9ea5f0d30fd0241bfa11f6ccbbb5989fc069666  Kql.g4
06a76feecd1eaedca4a86051abc130183a8594c4781294200497c905af3d2a21  KqlTokens.g4
```

## Why these files are patched at all

`Kql.g4` is a **reference grammar**. The parser Microsoft actually ships is
hand-written recursive descent (`src/Kusto.Language/Parser/*.cs`), so the grammar
occasionally lags the real language. The M0 spike
([`../docs/m0-grammar-spike.md`](../docs/m0-grammar-spike.md)) measured this
against 1,427 real queries from the product documentation: **~97.8% of in-scope
queries parse unpatched**, and the gaps are a short, enumerable list.

Each local patch below is one of two kinds, and the distinction matters when
re-syncing. `001` fixes a documented, valid KQL construct that upstream rejects
— a short, mechanical re-apply. `002` and `003` **restructure** rules that
upstream writes with shared prefixes, for parsing speed; they change no
accepted language and no emitted SQL, but they are a standing rewrite rather
than an addition, so an upstream bump means redoing them against the new text
rather than pasting a block back. Patches are marked in-file with
`PATCH duckdb-kql/NNN`.

## Local patches

### `001` — tabular subquery on the right of `in`

**File:** `Kql.g4`, rule `listEqualityExpression`

**Problem.** Upstream restricts the right-hand side to a comma-separated list of
`invocationExpression`, so a tabular subquery is rejected:

```kusto
StormEvents | where State in (PopulationData | project State)
```

This form is valid and documented (`in-operator.md`, `in-cs-operator.md`,
`not-in-operator.md`).

**Fix.** Add a first alternative accepting a `pipeExpression` subquery, leaving
the original list form as the second alternative.

**Effect.** ANTLR generation stays clean (exit 0, no warnings); corpus parse
1,281 → **1,285** blocks, no regressions.

**Why first.** It was the only genuine gap touching Wave 1.

### `002` — left-factor `equalityExpression`

**File:** `Kql.g4`, rules `equalityExpression`, `equalityExpressionTail`

**Problem.** Four of the five alternatives begin with `Left=relationalExpression`,
so ALL(*) parses an entire expression before the operator token can tell it
which alternative it is in — and builds a large DFA doing so. Measured over the
frozen corpus, this one decision was **42% of all prediction time** and the
largest single contributor to a cold first parse. A tester reported 10.8s for a
first parse on Windows; ~81% of it was this rule and `003`.

**Fix.** Parse `relationalExpression` once and hang the operator and its
right-hand side off a tail rule. The tail's **alternative labels are the names
upstream gives the rules**, so the generated context classes — `Equals…`,
`List…`, `Between…EqualityExpression` — are unchanged and the lowerer's dispatch
still matches. PATCH `001` lives inside it as the `Subquery=pipeExpression`
alternative.

**Effect.** Generation clean (exit 0, no warnings). Corpus parse stays at
**1,285** blocks and `tools/sql_snapshot.py` is **byte-identical**. First parse
0.808s → 0.138s; corpus warm-up ~16.6s → 1.81s.

**Cost.** `Left` now sits on the parent, so a tail handler reads it from there
— see `_lower_equality` in `lower.py`, and `tests/test_grammar_left_factoring.py`
for why losing it is silent rather than loud.

### `003` — left-factor `functionCallOrPathExpression`

**File:** `Kql.g4`, rule `functionCallOrPathExpression`

**Problem.** The same shape: upstream spells the first two alternatives as
`functionCallOrPathRoot` and `functionCallOrPathRoot (operation)+`. **18% of
prediction time.**

**Fix.** One alternative, `root (operation)*`, which subsumes the bare root.
Labelled so the context-class name survives.

**Effect.** No lowerer change at all. With zero operations the context holds a
single child, and `_collapse` steps through it exactly as it stepped through the
bare alternative.

### `004` — graph patterns, graph operator clauses, and `1..3`

**Files:** `Kql.g4` (`graphMatchPattern`, `graphMatchPatternEdge`,
`graphMatchPatternNode`, `graphMatchPatternNamedEdge`,
`makeGraphTablesAndKeysClause`, `makeGraphTableAndKey`,
`makeGraphPartitionedByClause`, `relaxedQueryOperatorParameter`) and
`KqlTokens.g4` (`NonIntegerNumber`).

**Problem.** Six documented constructs fail to parse: a graph pattern is one
element per comma, so `(a)-->(b)` fails at the arrow; `()` and `-[*1..3]->`
need names; `make-graph` takes one node table, not two; `partitioned-by`
wants a dotted path; `with_node_id=` and `output=` are keywords that
`relaxedQueryOperatorParameter` does not list; and `1..3` lexes as the real `1.`
then `.3`, which breaks every graph range and `between (1..3)` alike.

**Fix.** The rules as in the report, which is written up for upstream in
[`upstream-report-graph-and-range.md`](upstream-report-graph-and-range.md).
`NonIntegerNumber` gains a **lexer predicate** — `{self._input.LA(1) != 46}?`,
a trailing-dot real may not be followed by a dot — which is Python and so ties
the lexer to the one target this repo generates. ANTLR has no target-neutral
negative lookahead.

**Effect.** Generation clean. The 1,285 corpus blocks translate
**byte-identically** (`tools/sql_snapshot.py`). Re-harvesting the same pinned
docs commit then parses **1,366** blocks, 81 more. Most are graph examples this
patch unlocked, 24 of which the emulator answers; 29 begin with a management
command, which the parser has accepted since the control-command work but no
re-harvest had picked up.

## Known gaps *not* yet patched

Deliberately left failing — they raise `KqlUnsupportedError` until their wave:

| Construct | Wave |
|---|---|
| bare `serialize` (no column assignment) | 2 |
| `parse-kv` | 2 |
| `parse kind=regex … with * <re>` | 2 |
| `table('Name')` as a source | 3 |
| `project-by-names` | deferred (kql-to-sql defers it too) |

Out of scope entirely, and therefore never to be patched: management commands
(`.create`, `.ingest` — a *separate* upstream grammar). Graph semantics were
listed here until `004`; see `docs/graph-proposal.md`.

## Re-syncing with upstream

1. Fetch the new `Kql.g4` / `KqlTokens.g4`; record the new commit and hashes above.
2. Re-apply each `PATCH duckdb-kql/NNN` block (search for that marker).
3. Run `tools/regen_parser.sh`.
4. Run the L1 corpus test — the parsed-block count must not go **down**.
5. Run `tools/sql_snapshot.py --compare` against the pre-sync snapshot. For a
   re-sync that only re-applies these patches it must come back
   **byte-identical**; `002` moves the left operand onto the parent rule, and a
   mis-applied version answers the right operand alone rather than failing.
6. Note any patch that upstream has since fixed, and delete it.
