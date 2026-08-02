# Lessons from Bun's Zig → Rust Rewrite

> Status: reviewed 2026-08-02, **validated against the full primary source**
> ("Rewriting Bun in Rust", Jarred Sumner, July 8 2026). An earlier draft of this
> doc was assembled from secondary reporting and contained errors; those are
> corrected in §6.
>
> Disclosure noted in the source: Bun was acquired by Anthropic in Dec 2025, and
> the rewrite used a pre-release Claude Fable 5.

## 1. What Bun did

Bun ported its runtime from **Zig to Rust**: 535,496 lines of Zig in, +1,009,272
lines of diff out, **6,778 commits over 11 days** (May 3 → merged May 14), ~64
Claude instances across 4 worktrees, ~50 dynamic workflows, ~$165,000 in API
cost. Motivation was **stability**, not speed: the recurring bug classes were
use-after-free, double-free, and "forgot to free in an error path" — which are
compiler errors in safe Rust, and which `Drop` prevents structurally.

Two decisions framed everything ("everything else is tactics"):
1. **Everything all at once, not incremental.** *"An incremental rewrite adds
   temporary code that you hope gets deleted eventually, and would be painful in
   the short-medium term."*
2. **Make it look like a transpile of the Zig**, preserving architecture and
   performance, then refactor toward idiomatic Rust after v1.4 ships.

The pipeline:
1. **Prep (~3 hours)** — `PORTING.md` (Zig→Rust pattern/type mapping) and
   `LIFETIMES.tsv` (a proposed lifetime for every struct field, produced by a
   workflow that traced control flow). Both **adversarially reviewed, then
   hand-read**, *before any code existed*.
2. **Trial run on 3 files** before attempting all 1,448 — *"If you're about to do
   something big and expensive, it saves time and money to de-risk it first."*
3. **Mechanical port** — per file: 1 implementer, ≥2 adversarial reviewers, 1
   fixer applying feedback. Peak ~1,300 lines/minute. *"Absolutely none of it
   worked yet."*
4. **Compiler errors as a work queue** — splitting one Zig compilation unit into
   ~100 crates surfaced **~16,000 errors**; `cargo check` wrote them to a file
   grouped by crate, and 64 Claudes drained the queue (1,610 fix commits).
5. **Smoke tests → subcommands → test suite**, each as a loop over failing
   stack traces with the same implement/review/apply shape.
6. **CI to green on all 6 platforms**, then merge.

**The confidence mechanism**, in his own words — the answer to "how do you review
a PR with +1 million lines added?":

> *"A language-independent test suite with a million assertions, adversarial code
> review and when something does go wrong, fixing the process that generates the
> code instead of hand-fixing the code."*

**Adversarial review** is the sharpest technique: the reviewer is a *separate*
Claude whose context is **only the diff** — none of the implementer's reasoning —
*"told to assume the code is wrong."* 1 implementer, ≥2 reviewers; the implementer
never reviews and the reviewer never implements, because *"the Claude that wrote
the code wants the code to get accepted."* The post shows three real bugs it
caught, all of which compiled cleanly and looked plausible (an async
`uv_close` use-after-free, a negative-timestamp `trunc` vs `floor` bug, and an
eager `unwrap_or` panic).

Outcome: **100% of the test suite passing on all 6 platforms** before merge
(**"0 tests skipped or deleted"**; 1,386,826 `expect()` calls on Debian x64), 128
bugs fixed that reproduce in v1.3.14, memory on a 2,000-build workload down from
6,745 MB → 609 MB, ~20% smaller binary, 2–5% faster.

## 2. The most valuable finding for us — "syntactically identical, semantically different"

The post's **Porting mistakes** section is the single most relevant part of it to
this project:

> *"This rewrite introduced 19 known regressions... **Most of the regressions came
> from code that's syntactically identical in both languages but semantically
> different.**"*

The four documented examples are all of that shape:
- **`debug_assert!` erased a side effect** — Zig's `assert` is a *function* (its
  argument always runs); Rust's `debug_assert!` is a *macro* (the whole expression
  vanishes in release). A graph insert silently stopped happening in release
  builds only.
- **Odd-length slices** — Zig's helper used `@divTrunc` and ignored a trailing
  odd byte; `bytemuck::cast_slice` *panics* on it instead.
- **Bounds checks** — Zig `ReleaseFast` removed them, Rust release keeps them; a
  placeholder constant (`64` instead of `count/4` or `2048`) silently cut a
  ceiling from 8.4M to 270,272 and made an off-by-one reachable.
- **`comptime` format strings** — in Zig `fmt` is comptime so color markers are
  rewritten before argument substitution; in Rust the function only ever saw the
  finished string and rewrote markers *inside the arguments too*.

**This is precisely our project's core risk**, empirically confirmed by someone
who lived it at scale. KQL→DuckDB is *full* of syntactically-identical,
semantically-different pairs: `join` looks like SQL `JOIN` but defaults to
`innerunique`; `count(expr)` looks like `COUNT(expr)` but ignores nulls;
`has` looks like `LIKE '%x%'` but is term-based; `toint()` looks like `CAST` but
returns null instead of erroring.

**And the sobering part:** those 19 regressions shipped *despite* 100% of a
1.38-million-assertion suite passing. A green suite proves the cases you thought
of. It does not find semantic divergence you never wrote a test for — which is
exactly why our **divergence catalog** (`test-plan.md` §6) has to be authored
deliberately, not discovered accidentally.

## 3. The disanalogy — read before borrowing

| | Bun | `duckdb-kql` |
|---|---|---|
| Nature | **Port** of a working implementation | **Greenfield** |
| Reference | The Zig source, readable line-by-line | No source; a *language spec* + a black-box engine |
| Oracle | Pre-existing suite, ~1.38M assertions | **We must build the suite first** |
| Backlog generator | Rust compiler (~16,000 errors) | Python has no equivalent |
| Scale | 64 agents, 11 days, $165K | One developer, incremental |
| Semantics | Known — defined by the Zig | **Unknown — discovered as we go** |

The last row is decisive: Bun always knew what "correct" meant. We are
*discovering* KQL's semantics. Techniques transfer; the all-at-once **strategy**
does not (§5).

## 4. What transfers

### L1 — The conformance oracle is a *precondition* ⭐
*"Fortunately, Bun's own test suite is written in TypeScript which means it
doesn't depend on the runtime's programming language."* That language-independence
is what made the rewrite thinkable at all.

Our equivalent is the Kusto Emulator + harvested corpus. This **validates our
sequencing** (`test-plan.md` §10: corpus and harness *before* implementation).
**Action: treat it as a hard gate** — no translator work until the harness runs
end-to-end.

### L2 — Write the normative spec before the code ⭐
`PORTING.md` + `LIFETIMES.tsv`, adversarially reviewed and hand-read, before a
line of Rust. ~3 hours of prep for 11 days of largely unattended execution.
**Action: `TRANSLATION.md`** (now written — see
[`TRANSLATION.md`](./TRANSLATION.md)), plus the `LIFETIMES.tsv` analogue: keep the
**function mapping table as reviewable data**, not hand-written code.

### L3 — Make the backlog mechanical and self-draining ⭐
*"Compiler errors as a work queue."* Python gives us no compiler backlog, but the
**`xfail` corpus is exactly this**: each failing case is a ticket, implementation
drains it, the coverage matrix is the burn-down. **Action:** the runner should
emit a **ranked worklist** of failing cases grouped by the operator/function
blocking them.

### L4 — Adversarial review with a split context ⭐
Reviewer sees **only the diff**, is told to assume it's wrong, and never
implements. Cheap for us at the **mapping** level: for each new operator/function
mapping, a separate pass that tries to break it against the trap catalog before
the green test is trusted.

### L5 — Fix the generator, not the artifact
*"...fixing the process that generates the code instead of hand-fixing the code."*
Our declarative mapping table makes this natural. **Policy:** never special-case a
query to make a test go green; fix the mapping rule so the whole class is repaired.

### L6 — Guard against goal-gaming ⭐ (new, from the primary source)
Two failure modes he hit, both directly applicable:
- *"Claude interpreted 'let's get all the crates to compile' as 'stub out the
  functions with compilation errors'."* → Our analogue: "make the test pass" gamed
  by weakening the test or special-casing the query. **Policy: never weaken,
  skip, or delete a test to make it pass.** (Bun's "0 tests skipped or deleted"
  is the standard.)
- The rule he gave reviewers, worth adopting verbatim:
  > *"If you need a paragraph-long comment to justify why the workaround is OK,
  > the code is wrong — fix the code."*

### L7 — De-risk with a pilot
3 files before 1,448. **Action:** run the full loop (harvest → oracle → translate
→ verify) end-to-end on **one operator family** before scaling to the corpus.

### L8 — Invest in prep proportionally
The $165K/64-agent scale doesn't transfer; the **ratio** does. Hours of spec prep
bought days of unattended execution.

## 5. What does *not* transfer

**"Everything all at once."** Sound *for a port*, where a complete working
reference defines correctness up front and a compiler enumerates the gaps. We are
greenfield against semantics we're still discovering, with no compiler backlog, so
a big-bang would produce a large body of plausible-looking, unverified
translation. **Our wave plan stands.** *But* one nuance transfers: **within** a
wave, implement a whole **family** at once — all `join` kinds, all string
comparison operators, all conversion functions — because the traps are family-wide
and only cohere when implemented as a set.

**Preserving the source architecture.** Bun deliberately kept Zig's shape (and
pays for it in `unsafe`), because their goal was a faithful port. We have no such
constraint: read reference implementations (`kql-to-sql`, KustoLoco) for
**semantics**, never copy their **architecture**. Our IR and emitter should be
idiomatic Python.

## 6. Corrections to the earlier secondary-source draft

Recorded because they matter for how much weight to give the criticism:

| Earlier claim (from secondary sources) | What the primary source says |
|---|---|
| "The merged PR passed **99.8%** of the test suite" | **100%** on all 6 platforms before merge. 99.8% was an intermediate state on one platform. |
| "Some tests appeared **modified** to pass, weakening the headline" | **"0 tests skipped or deleted"**, and *"I manually verified the tests were in fact running and not being skipped."* |
| "~13,365 `unsafe` blocks (vs `uv`'s 73)" | ~13,000 `unsafe` keywords ≈ **4%** of the Rust code, and **78% are a single line** — a pointer from C++ or one call into a C library. Bun embeds JavaScriptCore, BoringSSL, SQLite, uWebSockets, so it will always carry more `unsafe` than a pure-Rust project. Expected to decrease with refactoring. |
| "16,000 compiler errors" presented as the starting state | They were surfaced by **splitting one Zig compilation unit into ~100 crates** and fixing the resulting cyclical dependencies. |

The critique that generated code can be locally plausible but subtly wrong is
still fair — but the primary source's own **19 regressions** section makes that
point more precisely, and more usefully for us, than the external criticism did
(§2). Post-merge they added 11 rounds of security review and 24/7
coverage-guided fuzzing of every parser (~100 billion executions → ~15 PRs).

## 7. Actions for this repo

1. ✅ **`TRANSLATION.md`** (L2) — written; normative mapping conventions and
   semantic invariants.
2. **Function mapping table as data** (L2), generating both translator and
   coverage matrix.
3. **Corpus-first hard gate** (L1) — restate in `test-plan.md` §10.
4. **Ranked worklist from failing `xfail` cases** (L3).
5. **Policies** (L5, L6): *fix the rule, not the query*; *never weaken, skip, or
   delete a test*; *a paragraph-long justifying comment means the code is wrong*.
6. **Per-mapping adversarial review** against the trap catalog (L4).
7. **Pilot one operator family end-to-end** before scaling (L7).
8. **Family-at-once within each wave** (§5) — regroup `test-plan.md` §8.

## 8. Source

Primary: *Rewriting Bun in Rust*, Jarred Sumner, July 8 2026 —
https://bun.com/blog/bun-in-rust (read from a user-supplied MHTML capture; the
domain is blocked from this environment).
