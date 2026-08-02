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

Each local patch below fixes one documented, valid KQL construct that upstream
rejects. Patches are marked in-file with `PATCH duckdb-kql/NNN`.

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

## Known gaps *not* yet patched

Deliberately left failing — they raise `KqlUnsupportedError` until their wave:

| Construct | Wave |
|---|---|
| bare `serialize` (no column assignment) | 2 |
| `parse-kv` | 2 |
| `parse kind=regex … with * <re>` | 2 |
| `table('Name')` as a source | 3 |
| `project-by-names` | deferred (kql-to-sql defers it too) |

Out of scope entirely, and therefore never to be patched: graph semantics
(`make-graph`, `graph-match`, …), management commands (`.create`, `.ingest` — a
*separate* upstream grammar), and cross-cluster references (`cluster(…)`).

## Re-syncing with upstream

1. Fetch the new `Kql.g4` / `KqlTokens.g4`; record the new commit and hashes above.
2. Re-apply each `PATCH duckdb-kql/NNN` block (search for that marker).
3. Run `tools/regen_parser.sh`.
4. Run the L1 corpus test — the parsed-block count must not go **down**.
5. Note any patch that upstream has since fixed, and delete it.
