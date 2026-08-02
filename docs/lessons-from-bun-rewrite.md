# Lessons from Bun's Zig → Rust Rewrite

> Status: review 2026-08-02. Assessment of Bun's AI-driven rewrite and which of
> its practices transfer to `duckdb-kql` — a much smaller, **greenfield** project.
>
> **Research caveat:** the primary source (`bun.com/blog/bun-in-rust`) is blocked
> from this environment (HTTP 403 at the network proxy), so this is assembled from
> secondary reporting and quoted excerpts. Figures below should be treated as
> approximately-reported, not verified first-hand.

## 1. What Bun did

Bun ported its runtime from **Zig to Rust**: reportedly ~535K–1M lines, 6,778
commits, ~64 parallel Claude instances, ~50 looping workflows, **11 days**,
~$165K. The merged PR passed **99.8%** of the pre-existing test suite, starting
from ~16,000 compiler errors. Memory use on a 2,000-build workload dropped from
6.7 GB to 609 MB, with 2–5% perf gains.

Their pipeline, in order:
1. **Spec first** — `PORTING.md` (Zig→Rust architecture rules) and `LIFETIMES.tsv`
   (a data-structure lifetime map) were written **before any Rust existed**, then
   adversarially reviewed, then hand-read. Reported as *"three hours of prep
   buying eleven days of unattended work."*
2. **Mechanical translation** — per file: 1 implementer agent wrote the `.rs`,
   **≥2 adversarial reviewers** (separate contexts) checked it matched the `.zig`
   behavior and followed the guides, then another agent applied corrections.
3. **Compiler-error loop** — autonomous loops compiled, fed errors back, patched,
   repeated. *"The compiler was the ticket system — the type checker generated the
   backlog and the loop drained it."*
4. **Conformance gate** — the **existing TypeScript test suite (~1M assertions)**,
   being language-independent, survived the rewrite and served as the behavioral
   oracle. Plus Miri (Tree Borrows) CI for FFI-free crates.
5. **Human review** — the maintainer spot-read Zig/Rust side-by-side and audited
   whether the adversarial reviewers were actually catching discrepancies.

**It is contested.** Zig's creator called it *"unreviewed slop"*; the merge shipped
**~13,365 `unsafe` blocks** (critics compare `uv`'s 73), because agents preserved
Zig's global-mutable-state patterns inside `unsafe` rather than redesigning for
Rust's ownership model. Some observers noted tests appeared *modified* for the
Rust version rather than faithfully ported, which weakens the 99.8% headline. Bun
is now incrementally refactoring to remove `unsafe` and adopt idiomatic Rust.

## 2. The disanalogy — read this before borrowing anything

| | Bun | `duckdb-kql` |
|---|---|---|
| Nature | **Port** of a working implementation | **Greenfield** — nothing to translate from |
| Reference | The Zig source, readable line-by-line | No source; only a *language spec* + a black-box engine |
| Oracle | Pre-existing suite, ~1M assertions | **We must build the suite first** |
| Backlog generator | Rust compiler (16K errors) | Python has no equivalent |
| Scale | 64 agents, 11 days, $165K | One developer, incremental |
| Semantics | Known (defined by the Zig code) | **Unknown — discovered as we go** |

The last row is the important one. Bun always knew what "correct" meant: whatever
the Zig did. We are *discovering* KQL's semantics, which is exactly why our
divergence catalog and oracle exist. So the **techniques** transfer; the
**"translate everything at once" strategy does not** (§4).

## 3. What genuinely transfers

### L1 — The conformance oracle is a *precondition*, not a deliverable ⭐
The single enabling factor for Bun was a **language-independent test suite that
survived the rewrite**. Without it, none of the automation would have been
trustworthy.

We independently arrived at the same architecture (Kusto Emulator + harvested
corpus). Bun's experience **validates our sequencing** — `test-plan.md` §10 puts
"corpus & harness first, land the whole scraped corpus as `xfail` immediately"
*before* implementation. **Action: treat that as a hard gate.** No translation
work starts until the corpus + comparison harness runs end-to-end.

### L2 — Write the normative spec *before* the implementation ⭐
`PORTING.md` + `LIFETIMES.tsv` before a single line of Rust is the highest-leverage
thing they did, and it is *cheap* — it scales down perfectly.

**Action: add `docs/TRANSLATION.md`** — the normative KQL→DuckDB mapping
conventions the implementation must follow, consolidating what's currently
scattered across `implementation-plan.md` §5 and `test-plan.md` §6:
- type mapping (`datetime`→`TIMESTAMP`, `timespan`→`INTERVAL`, `dynamic`→`JSON`);
- **null-propagation rule** (KQL returns null where SQL would error → `TRY_CAST`);
- **case-sensitivity rules** (`==` vs `=~`, `has`/`contains` defaults);
- the CTE-chaining convention and IR node conventions;
- when a Python UDF is permitted vs. required to be native SQL.

And the `LIFETIMES.tsv` analogue: keep the **function mapping table as data**
(a TSV/YAML registry), not hand-written code — reviewable, diffable, and
generatable into both the translator and the coverage matrix.

### L3 — Make the backlog mechanical and self-draining ⭐
Bun's loop worked because the compiler *emitted the work queue*. Python gives us
nothing equivalent — but **our `xfail` corpus is exactly that**: every failing
case is a ticket, implementation drains it, the coverage matrix is the burn-down.

We already have the mechanism; Bun reframes it from *reporting* to *work queue*.
**Action:** the test runner should emit a **ranked worklist** — failing cases
grouped by the operator/function they're blocked on, ordered by the §8 priority
score. That is the ticket system.

### L4 — Adversarial review in a separate context
1 implementer + ≥2 adversarial reviewers, each asked to *exhaustively argue why
this is wrong*. Cheap and directly applicable at the **mapping** level rather than
the file level: for each new operator/function mapping, a separate pass that tries
to break it against the trap catalog — *case sensitivity? nulls? empty input?
type coercion? tie-breaking?* — **before** trusting a green test. A passing test
only proves the cases you thought of.

### L5 — Fix the generator, not the artifact
Bun: *"when something goes wrong, fix the process that generates the code instead
of hand-fixing the code."* Our declarative mapping table makes this natural.
**Action — policy:** never special-case a single query to make a test go green;
fix the mapping rule (or add a trap rule) so the whole class is repaired.

### L6 — A high pass rate hides a long tail
99.8% of a huge suite still leaves thousands of failures, and the headline was
undercut by tests having been modified to pass. Two rules for us:
- **Never report a bare pass percentage.** Report the **coverage matrix** per
  language item (supported / partial / unsupported). This is already the design —
  Bun is the cautionary tale for why it matters.
- **Never weaken a test to make it pass.** Expectations come from the oracle; if
  the oracle says we're wrong, we're wrong. Changing an expectation requires an
  explicit note on *why the oracle's answer was itself wrong*.

### L7 — Invest in prep proportionally
The $165K/64-agent scale doesn't transfer; the **ratio** does. Hours of spec prep
bought days of unattended execution. For us that means: a day spent on
`TRANSLATION.md` + the trap catalog before writing the translator is the
proportionally correct investment, not procrastination.

## 4. What does *not* transfer

**"Everything all at once beats incremental."** Jarred's argument — incremental
adds temporary scaffolding you hope to delete later — is sound **for a port**,
where a complete, working reference defines correctness up front. We're greenfield
against a language whose semantics we're still discovering, so a big-bang attempt
would produce a large body of plausible-looking, unverified translation — exactly
the failure mode Bun's critics point at. **Our wave-based plan stands.**

*But one real nuance transfers:* **within** a wave, implement a whole **family**
at once rather than one function at a time — all `join` kinds together, all
string-comparison operators together, all conversion functions together. The
semantic traps are family-wide (the case-sensitivity rules only cohere when
implemented as a set), and family-at-once is how you catch them.

**Line-by-line mechanical translation.** Critics' sharpest point: an LLM
translating line-by-line *"doesn't preserve why a particular memory ordering or
error path exists."* We have no source to translate, so we avoid this failure mode
by construction — provided we keep the **public docs as the normative spec** and
the emulator as *verification* (already our §11.5 posture), rather than
reverse-engineering behavior blindly.

**Preserving the source architecture.** Bun kept Zig's patterns and paid in 13,365
`unsafe` blocks. Our analogue would be mimicking `kql-to-sql`'s C# structure in
Python. We should read reference implementations for **semantics**, never copy
their **architecture** — our IR and emitter should be idiomatic Python.

## 5. Concrete actions for this repo

1. **Add `docs/TRANSLATION.md`** (L2) before translator work — normative mapping
   conventions + the null/case/type rules. *Highest leverage, lowest cost.*
2. **Make the function mapping table data, not code** (L2) — a reviewable registry
   that generates both the translator and the coverage matrix.
3. **Harden the corpus-first gate** (L1) — no translation work until the harness
   runs end-to-end. Already in `test-plan.md` §10; restate as a gate.
4. **Emit a ranked worklist from failing `xfail` cases** (L3) — the ticket system.
5. **Adopt two policies** (L5, L6): *fix the rule, not the query*; and *never
   weaken a test to make it pass*.
6. **Add a per-mapping adversarial review step** (L4) against the trap catalog.
7. **Implement family-at-once within each wave** (§4 nuance) — update the
   `test-plan.md` §8 waves to be explicitly family-grouped.

## 6. Sources

Primary (inaccessible from this environment, listed for completeness):
https://bun.com/blog/bun-in-rust

Secondary reporting used:
- https://simonwillison.net/2026/Jul/8/rewriting-bun-in-rust/
- https://blog.pragmaticengineer.com/the-pulse-what-can-we-learn-from-buns-rapid-rust-rewrite-with-ai/
- https://fawadhs.dev/blog/bun-rust-rewrite-technical-review
- https://byteiota.com/bun-rust-rewrite-merged-the-13000-unsafe-block-problem/
- https://www.theregister.com/devops/2026/07/14/zig-creator-calls-buns-claude-rust-rewrite-unreviewed-slop/5270743
- https://en.liujiacai.net/2026/05/16/bun-rust-port/
