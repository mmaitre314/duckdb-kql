# Code maintenance — the framework

> **Status: normative process doc.** This is *how* `duckdb-kql` is kept
> changeable, and what an agent or a human must do before, during and after a
> change that is not a new feature. It sits beside
> [`../TRANSLATION.md`](../TRANSLATION.md) (what the code must *do*),
> [`../code-review/README.md`](../code-review/README.md) (how a change is
> *judged*), and [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) (how a change
> gets in). Where maintenance and semantics collide, TRANSLATION.md wins.

This project's charter is one sentence: **a wrong answer is worse than no
answer.** Applied to maintenance, it has a sharp consequence.

In most codebases a bad refactor breaks the build. Here the dangerous refactor
is the one that **compiles, passes, and quietly emits different SQL** — because
the thing being restructured is a translator between two languages that look
alike exactly where they differ. This repo has already written down what that
failure looks like at scale:
[`../lessons-from-bun-rewrite.md`](../lessons-from-bun-rewrite.md) §2 — *"most of
the regressions came from code that's syntactically identical in both languages
but semantically different."* Nineteen regressions escaped a 1.38-million-
assertion suite. Ours is smaller.

So maintenance here is not "leave the campsite cleaner." It is a gated
procedure with a proof obligation: **show that the output did not move.**

## What the evidence says

- **Refactoring is behaviour-preserving *by definition*.** Changing structure and
  changing behaviour are two different activities, and Kent Beck's *two hats*
  says you wear one at a time — never in the same commit. A commit that does
  both cannot be reviewed, because the reviewer has no way to tell which diff
  lines were supposed to change the answer.
  ([Fowler, *Refactoring* 2nd ed.](https://martinfowler.com/books/refactoring.html);
  [two hats](https://refactoring.com/))
- **Refactoring is driven by change, not by ugliness.** Silva, Tsantalis and
  Valente asked GitHub contributors why they refactored, across 124 projects: the
  dominant motivations were *requirement changes* — making a pending change
  possible — not the detection of a smell. Which is Beck's rule from the other
  side: *make the change easy, then make the easy change*.
  ([Why We Refactor, FSE 2016](https://dl.acm.org/doi/10.1145/2950290.2950305))
- **Refactoring's top *risk* is regression, and its top *benefit* is
  readability.** Kim, Zimmermann and Nagappan surveyed and instrumented
  refactoring at Microsoft: developers named regression as the leading risk and
  improved readability/maintainability as the leading benefit; modules that
  were refactored showed reduced inter-module dependencies and fewer post-release
  defects. Both halves matter — the payoff is real *and* the risk is real.
  ([TSE 2014](https://www.microsoft.com/en-us/research/publication/an-empirical-study-of-refactoring-challenges-and-benefits-at-microsoft/))
- **Not all refactorings carry the same risk.** Empirical work on
  refactoring-induced faults finds the risk concentrated in changes that move
  behaviour between types — hierarchy and signature changes — while local ones
  (rename, extract, inline) are comparatively cheap. That asymmetry is the
  [risk ladder](#the-risk-ladder) below.
  (Bavota, De Lucia, Di Penta, Oliveto & Palomba, *When does a refactoring induce
  bugs?*, SCAM 2012)
- **Target hotspots, not eyesores.** A large file nobody edits is not costing
  anything; a large file that changes every week is where the next defect will
  be. Rank by **change frequency × size**, not by aesthetics.
  (Tornhill, *Your Code as a Crime Scene*)
- **Mechanical, large-scale changes are their own discipline.** Google's
  large-scale-change process is: generate the change with tooling rather than by
  hand, keep it trivially reversible, and review the *pattern* once and the
  instances mechanically. If a refactor is too big to review, the fix is to make
  it generated and uniform, not to ask for a bigger review.
  ([*Software Engineering at Google*, ch. 22](https://abseil.io/resources/swe-book/html/ch22.html))
- **Small diffs, or no review at all.** The same SmartBear/Cisco finding the
  [review charter](../code-review/README.md) is built on — discovery falls off
  past **200–400 changed lines** — applies with extra force here, because a
  refactor is *supposed* to be boring. A boring 900-line diff gets skimmed.
- **Internal quality pays back fast.** The trade is not "quality versus speed";
  poor internal quality slows the *next* change, and the payback period is weeks,
  not years. ([Fowler, *Is High Quality Software Worth the Cost?*](https://martinfowler.com/articles/is-quality-worth-cost.html))
- **Nothing here is optional-forever.** Lehman's laws: a system in use must keep
  changing, and its complexity grows unless work is done to reduce it.
  Maintenance is not a phase that ends. (Lehman, *Programs, Life Cycles, and
  Laws of Software Evolution*, Proc. IEEE 1980)

## The rules

Numbered so a review can cite one. **The R-rules in TRANSLATION.md are semantic
rules; M1–M12 here are process rules.** They do not overlap.

1. **M1 — Two hats, two commits.** A commit either changes behaviour or changes
   structure. Never both. If a refactor is needed to make a feature possible, it
   lands *first*, on its own, green on its own.
2. **M2 — Behaviour-preserving means the emitted SQL, not just the tests.**
   Before/after `tools/sql_snapshot.py` over the frozen corpus must be
   **byte-identical**. The suite proves the cases someone thought of; the
   snapshot covers the other ~1,200. See the
   [playbook](refactoring-playbook.md#the-gate).
3. **M3 — A refusal is behaviour.** Turning a `KqlUnsupportedError` into SQL, or
   changing what a refusal *names*, is a semantic change and needs a
   spec decision — not a refactoring commit. The snapshot records refusals for
   exactly this reason.
4. **M4 — Ratchets only go up.** `BASELINE_PASSING`, `BASELINE_SUPPORTED`,
   `BASELINE_PARSED`, `BASELINE_FROZEN`. A refactor that lowers one has changed
   behaviour and mislabelled itself.
5. **M5 — Never weaken, skip, delete or `xfail` a test to make a restructuring
   pass.** If a test fails during a refactor, the refactor is wrong. This is the
   [`mapping-author`](../../.claude/agents/mapping-author.md) rule, restated
   where it is most tempting to break.
6. **M6 — Refactor in service of something.** A pending change, a measured
   hotspot, or a debt entry with a cost. Speculative restructuring is
   [over-engineering](../code-review/README.md#the-universal-checklist-every-area-every-review)
   with extra steps, and it spends the review budget that the next real change
   needs.
7. **M7 — Prefer deleting to abstracting.** The cheapest maintenance action in
   this repo is removing something: a dead branch, a superseded helper, a
   suppression that no longer suppresses anything. Deletion is provable by the
   gate and costs nothing to review.
8. **M8 — Move behaviour into data, never into new branches.** Support grows by
   adding rows to `translate/functions.py`, not by adding cases to the emitter.
   The metric is `hand_written_loc_per_row`; when it climbs, the architecture is
   drifting back into code (`../ai-cost-strategy.md` §6.2).
9. **M9 — Do not churn the cached prefix.** `TRANSLATION.md` and the registry are
   a prompt-cache prefix for every bulk mapping run: any byte change invalidates
   everything after it. Re-sorting the registry "for tidiness" is a real cost,
   not a free cleanup. Batch such changes; never dribble them mid-run
   (`../ai-cost-strategy.md` §6.2).
10. **M10 — Generated and vendored code is never refactored.**
    `src/duckdb_kql/_antlr/` is regenerated verbatim (`../../grammar/UPSTREAM.md`);
    `docs/kql-support.md` is generated. Change the source, run the generator, and
    let the artifact follow — in the *same* commit.
11. **M11 — Every suppression carries a reason, and removing one is real work.**
    `# noqa`, `# type: ignore`, a mypy override, a `skipif`: each is a checker
    told to look away. Adding one without a written reason is a defect; the
    running count is a [tracked metric](metrics.md#5-suppression-ledger--what-has-stopped-being-checked).
12. **M12 — The public surface deprecates loudly, and on a schedule.** Layer
    boundaries and exported names are a promise (`../code-review/public-api-and-typing.md`).
    Renaming inside a layer is free; renaming across one is a release-note event
    with a deprecation path, not a refactor.

## The workflow

Five steps. An agent that skips step 1 or step 4 has not done maintenance work,
whatever the diff looks like.

1. **Measure.** `python tools/maintenance_metrics.py`. Pick the target from the
   numbers — the hotspot list, the over-budget functions, the suppression
   ledger — not from what you happened to read last.
2. **Justify.** Write the one sentence: *"this refactor exists so that X becomes
   possible / so that Y stops costing us Z."* If the sentence needs an "and", the
   refactor is two refactors (M1).
3. **Snapshot.** `python tools/sql_snapshot.py --out before.txt`, on the base
   commit, before touching anything.
4. **Transform in small, named steps**, running the fast gate between each. Use
   the [catalog](refactoring-playbook.md#the-catalog) — a named refactoring with
   a known mechanic is safer than an improvised rewrite, and it gives the commit
   message a verb.
5. **Prove and record.** The full gate, the snapshot comparison, and a commit
   message that states which refactoring was applied and what the gate said. Then
   re-run the metrics and put the delta in the commit message — that is how
   impact gets measured instead of asserted.

## The risk ladder

Tiers are about the *proof obligation*, not the effort. Everything on this ladder
still owes M2's byte-identical snapshot; the higher tiers owe more on top.

| Tier | Refactoring | Why it is where it is | Extra proof required |
|---|---|---|---|
| **Safe** | Rename a local/private symbol, extract or inline a private helper, split a module without changing exports, delete dead code, hoist a constant | Local, mechanical, and the type checker sees the whole blast radius | The standard gate |
| **Careful** | Extract a class, replace conditional with table lookup, unify duplicated emitter branches, convert a special form into a registry row | Behaviour moves between call sites; two branches "obviously the same" often differ in a null or case rule | Name the trap the two branches handled differently, and point at the test that pins it |
| **Risky** | Change an IR node shape, alter a rendering interface, restructure `lower.py` dispatch, touch quoting/escaping, move a check across a layer boundary | This is where "syntactically identical, semantically different" lives; quoting is simultaneously a correctness and a [security](../code-review/security-and-injection.md) surface | Snapshot **plus** a targeted [adversarial review](../../.claude/agents/adversarial-reviewer.md) of the diff, and the relevant R-rule trap tests named explicitly |
| **Spec change** | Anything that changes what is emitted, refused, or ordered | Not a refactor at all, whatever the diff looks like | [`spec-architect`](../../.claude/agents/spec-architect.md), an R-rule, and oracle verification |

## The playbooks

| Playbook | File | Owns |
|---|---|---|
| Refactoring | [`refactoring-playbook.md`](refactoring-playbook.md) | The gate, the step-by-step mechanic, and the catalog of refactorings this codebase actually needs |
| Technical debt | [`technical-debt.md`](technical-debt.md) | What counts as debt here, how it gets recorded, and when it gets paid |
| Dependencies & upgrades | [`dependencies-and-upgrades.md`](dependencies-and-upgrades.md) | Pins, the layering promise, Python floor/ceiling, the vendored parser, the emulator image |
| Metrics | [`metrics.md`](metrics.md) | What is measured, with what command, in which direction, and how each one can be gamed |

## The agents

| Agent | Does | Never |
|---|---|---|
| [`debt-scout`](../../.claude/agents/debt-scout.md) | Runs the metrics, ranks hotspots, returns a costed worklist | Edits anything |
| [`refactorer`](../../.claude/agents/refactorer.md) | Executes **one** named refactoring to the gate | Changes behaviour, adds features, or moves a baseline |
| [`adversarial-reviewer`](../../.claude/agents/adversarial-reviewer.md) | Attacks a risky-tier diff | Implements |

The Bun rule the review framework already borrows holds here too: **the agent
that performed the refactor does not review it**, and the reviewer sees the diff
without the refactorer's reasoning. An agent that restructured the code wants the
restructuring to be accepted.

## Past surveys

- [`survey-2026-08-23.md`](survey-2026-08-23.md) — the first full pass, at
  `6f12ed4`: 5 findings (1 Safe/Careful, 1 Careful, 2 Safe, 1 docs-only), 9 areas
  enumerated as healthy. It also caught a defect in the metrics tool itself — the
  RUF100 check was measured with `--select` rather than `--extend-select`, which
  overcounted unused suppressions 125 against a true 113. That is fixed; the rest
  is open.

## What "done" looks like

A maintenance change is finished when all five are true:

- The snapshot is byte-identical, or the diff is explained line by line and the
  commit is re-labelled as a behaviour change (M1, M2).
- `ruff check src tools tests demo`, `mypy`, and `pytest` are green — the same
  three CONTRIBUTING requires.
- No baseline moved down, no test was weakened, no suppression was added without
  a reason (M4, M5, M11).
- The commit message names the refactoring and quotes the gate result.
- The metric it was supposed to move actually moved, and the delta is recorded.
