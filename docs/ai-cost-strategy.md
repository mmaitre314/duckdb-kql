# AI Cost vs Quality Strategy

> Status: proposal (2026-08-02). How to structure the build of `duckdb-kql` to get
> good output per dollar. Companions:
> [`test-plan.md`](./test-plan.md) (the oracle that makes this possible),
> [`lessons-from-bun-rewrite.md`](./lessons-from-bun-rewrite.md) (where the
> method comes from), [`TRANSLATION.md`](./TRANSLATION.md) (the spec that gets
> cached).

## 0. The headline

Model tiering is real but it is **not the biggest lever here**. In rough order of
impact for this project:

| # | Lever | Why it dominates |
|---|-------|------------------|
| 1 | **Don't call a model at all** | Most of our bulk work is deterministic — scripts, not agents |
| 2 | **The oracle gate** | A mechanical pass/fail is what makes cheap models *safe*; without it you pay for expensive review |
| 3 | **Batch API (50% off)** | Our workload is async, parallel, latency-insensitive — a near-perfect fit |
| 4 | **Prompt caching (~0.1× reads)** | `TRANSLATION.md` + registry is a stable prefix read by every mapping task |
| 5 | **Effort level** | Per-request, often a bigger swing than model choice |
| 6 | **Model tiering** | Real (5× spread Haiku↔Opus), but applies to less of the work than you'd think |

The generic "Opus orchestrator + Haiku subagents" advice covers only #6 and part
of #4. Levers 1–3 are where this project's money actually is.

## 1. The decision rule

For each task, in order:

1. **Is it deterministic?** → write a script. No model. *(Cheapest by far.)*
2. **Is it mechanically verifiable?** (a test or the oracle says pass/fail) →
   cheap model + the gate, retry on failure, **escalate after N failures**.
3. **Is it irreversible or architectural?** → frontier model + adversarial review.

"Read-only" is the wrong criterion — **verifiability** is. A read-only task whose
output nobody can check still needs a good model; a *writing* task with a hard
test gate is safe on a cheap one.

## 2. Tier 0 — no model at all (the biggest saver)

These are pure code. An LLM writes each **once**; then they run free forever:

- `tools/harvest_docs.py` — scrape ` ```kusto ` blocks from pinned markdown
- `tools/regen_expectations.py` — boot the emulator, ingest fixtures, capture results
- `tools/gen_coverage.py` — case files → coverage matrix
- the **frequency scan** — count operator/function occurrences
- the ANTLR parser generation (`Kql.g4` → Python target)
- the comparison engine (§4.2 of the test plan) and the test runner

**This is the cost-shaped version of Bun's "fix the process that generates the
code, don't hand-fix the code."** Pay once for the generator, never per item.
Harvesting thousands of doc examples with an agent instead of `re` + `requests`
would be a pure waste.

**Rule: if the output is mechanically derivable from the input, it is a script.**

## 3. Tier 1 — cheap model (Haiku 4.5, $1/$5 per MTok)

High-volume, schema-constrained, mechanically checked:

- reformatting imported corpora (kql-to-sql, ClickHouse) into our case schema
- authoring/normalizing corpus case files
- repo search and "where is X" location tasks (in an **isolated subagent** so the
  raw file dumps never touch the main thread)
- coverage-matrix spot checks

Gate: schema validation + the case must parse. Failures are cheap and visible.

## 4. Tier 2 — mid model (Sonnet 5, $3/$15; intro $2/$10 through 2026-08-31)

The workhorse tier — this is where most of the ~350 mapping items live:

- individual **function/operator mappings**, gated by their trap test
- draining `xfail` cases from the ranked worklist
- **adversarial review passes** — the value is the *framing* (fresh context, diff
  only, "assume it's wrong"), not raw capability, so this does not need Opus
- routine translation once a family's pattern is established

## 5. Tier 3 — frontier model (Opus 5, $5/$25)

Reserve for work where a mistake propagates and is expensive to unwind:

- **the R-rules in `TRANSLATION.md`** — a wrong invariant silently poisons every
  one of ~350 mappings. This is the single highest-value place to spend.
- the IR design and the emitter interface
- the `has` tokenization strategy (subtle; wrong answers look right)
- **judging whether a passing test actually proves correctness** — the Bun
  19-regressions problem
- the M0 grammar spike (ambiguous, high-consequence, gates everything)

**The economics of "pay once at the top":** ~12 R-rules decided carefully on a
frontier model is trivial spend. Three hundred and fifty mappings each requiring
frontier-level reasoning is not. The whole architecture — declarative registry,
trap catalog, oracle — exists to move work *down* the tiers.

## 6. The levers the generic advice misses

### 6.1 Batch API — 50% off, near-perfect fit
Async, 50% cheaper, up to 100k requests per batch, most complete within an hour.
Our bulk work is **exactly** this shape: non-interactive, embarrassingly
parallel, latency-insensitive. Candidates: bulk mapping attempts, corpus
reformatting, import conversions, coverage sweeps. Anything that doesn't need a
human watching should go through Batch.

### 6.2 Prompt caching — but keep the prefix frozen
Cache reads cost ~**0.1×**; writes cost 1.25× (5-min TTL) or 2× (1-hour). Caching
is a **prefix match** — any byte change invalidates everything after it.

`TRANSLATION.md` + the function registry is read by every mapping task, so it is
an ideal cached prefix. Two consequences that are actually design constraints:

- **Don't edit `TRANSLATION.md` mid-batch.** Batch the rule changes, then run the
  mappings. Editing the spec between every mapping destroys the cache.
- Keep the registry **deterministically serialized** (sorted, stable ordering) —
  an unsorted dump changes bytes and silently kills the cache.

Verify with `usage.cache_read_input_tokens`; if it's zero across similar tasks,
something in the prefix is varying.

### 6.3 Effort — a per-request lever, often bigger than model choice
`low`/`medium`/`high`/`xhigh`/`max`. Lower effort means fewer, more-consolidated
tool calls and less preamble. For routine mapping work gated by a test,
**medium effort on a mid model** usually beats high effort everywhere. Reserve
`high`/`xhigh` for Tier 3. Sweep it on real tasks rather than guessing — the
relationship isn't monotonic, and higher effort sometimes *reduces* total cost by
cutting turn count.

### 6.4 Escalate, don't loop
The real runaway-cost risk isn't a single expensive call — it's an agent looping
on a task it can't do. Cap retries, and when a cheap model fails N times,
**escalate to the next tier instead of retrying**. A failed cheap attempt is
information, not just waste.

### 6.5 Family-at-once is a cost win too
From the Bun review: implement all `join` kinds together, all string-comparison
operators together. One context load instead of nine. It was a *correctness*
recommendation (family-wide traps only cohere as a set) — it's also straightforwardly
cheaper.

## 7. Proposed subagent configuration

Under `.claude/agents/`. Deliberately minimal — each gets only the tools it
needs. The last two came later, with the maintenance framework
([`maintenance/README.md`](maintenance/README.md)).

| Agent | Model | Tools | Purpose |
|---|---|---|---|
| `corpus-wrangler` | Haiku 4.5 | Read, Write, Glob, Grep, Bash | Reformat imported corpora into the case schema; author case files. Returns counts, not file dumps. |
| `mapping-author` | Sonnet 5 | Read, Edit, Bash | Implement one registry row + its test; run the gate; report pass/fail. |
| `adversarial-reviewer` | Sonnet 5 | Read, Bash | **Diff only, no implementation.** Try to break a mapping against the trap catalog. Never edits. |
| `spec-architect` | Opus 5 | all | R-rules, IR design, semantic judgment calls. Rare, high-value. |
| `debt-scout` | Sonnet 5 | Read, Grep, Glob, Bash | **Survey only.** Rank maintenance work by interest rate from `tools/maintenance_metrics.py`. Never edits. |
| `refactorer` | Sonnet 5 | Read, Edit, Write, Glob, Grep, Bash | One named behaviour-preserving refactoring, gated on a byte-identical SQL snapshot. Never changes behaviour. |

Two rules borrowed from Bun: the **implementer never reviews and the reviewer
never implements**, and the reviewer sees only the diff.

Keep `CLAUDE.md` lean — it is prepended to every agent's context, so static bulk
there is a tax on every single call.

## 8. What this looks like in practice

1. **Spend up front, on the right things** — R-rules and the generators (Tier 3 +
   Tier 0). This is the "3 hours of prep bought 11 days of execution" ratio.
2. **Everything mechanical becomes a script** (Tier 0).
3. **The oracle produces frozen expectations once** (a Tier 0 script driving the
   emulator), then per-push CI is free — no model, no Docker.
4. **The ~350 mappings drain through Tier 1/2 via Batch**, each gated by a test,
   escalating to Tier 3 only on repeated failure.
5. **`TRANSLATION.md` stays frozen during batches** so the cache holds.

## 9. Open questions

- Whether to wire the Batch API into the mapping loop now or after the M1 MVP
  proves the loop works end-to-end.
- Whether `adversarial-reviewer` needs Opus for the subtlest traps (`has`
  tokenization, `dynamic` nulls) or whether Sonnet + the trap catalog suffices —
  worth measuring on the first family.
- Whether to materialize the `.claude/agents/` configs now or once implementation
  actually starts.

## 10. Reference — current pricing

Per million tokens (input / output), first-party Claude API:

| Model | Input | Output |
|---|---|---|
| Claude Opus 5 | $5.00 | $25.00 |
| Claude Sonnet 5 | $3.00 ($2.00 intro through 2026-08-31) | $15.00 ($10.00 intro) |
| Claude Haiku 4.5 | $1.00 | $5.00 |

Batch API: **50% off** all of the above. Cache reads: **~0.1×** input. Cache
writes: 1.25× (5-min TTL) / 2× (1-hour TTL).
