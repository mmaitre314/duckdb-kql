# Maintenance metrics — what to track, and what it is worth

**Scope:** the numbers that say whether maintenance work had an effect.
**Read first:** the [charter](README.md).

```bash
python tools/maintenance_metrics.py            # the report below
python tools/maintenance_metrics.py --json     # same numbers, for a trend file
python tools/maintenance_metrics.py --top 15   # longer ranked lists
```

## Two kinds of number, and only one of them is a gate

- **Ratchets** are *enforced*. They live in the test suite, CI fails when they
  regress, and they may only move up. There are four, and this document does not
  add a fifth.
- **Indicators** are *observed*. `tools/maintenance_metrics.py` computes them and
  nothing fails. They exist to answer "what should I maintain next?" and "did
  that work do anything?"

Keeping indicators out of CI is deliberate. A measurement that gates a build
becomes a target, and a target stops measuring what it measured
(Goodhart/Strathern). "Functions over 60 lines" is a useful compass and a
worthless quota: the fastest way to satisfy it is to split one clear function
into three unclear ones, which makes the code worse and the number better.

Two well-known traps this repo already respects, worth stating so nobody adds
them later by reflex:

- **Coverage is a weak proxy for effectiveness.** Inozemtseva & Holmes found
  coverage is not strongly correlated with test-suite fault detection once suite
  size is controlled for ([ICSE 2014](https://www.linozemtseva.com/research/2014/icse/coverage/)),
  and Google reports using coverage as a signal rather than a hard gate
  (Ivanković, Petrović, Just & Fraser, *Code Coverage at Google*, ESEC/FSE 2019).
  In a translator, a line-coverage number is *especially* misleading: `has` is
  fully covered by a test that asserts the wrong semantics. What this project
  cares about is **trap coverage**, which is the ratchets' job.
- **Lines of code is not quality.** It is used here only as an input to
  hotspot ranking and as a review-budget check — never as a goal.

## The catalog

### 1. Correctness ratchets — *enforced, may only go up*

| Metric | Question it answers | Where |
|---|---|---|
| `behaviour_cases_passing` | How many corpus queries return the emulator's answer | `tests/test_behavior.py::BASELINE_PASSING` |
| `azure_monitor_probes` | Coverage of a published, externally-defined KQL subset | `tests/test_profile_azure_monitor.py::BASELINE_SUPPORTED` |
| `corpus_queries_parsed` | Grammar reach — what the parser accepts at all | `tests/test_corpus.py::BASELINE_PARSED` |
| `frozen_expectations` | How much ground truth is banked and replayable without Docker | `tests/test_corpus.py::BASELINE_FROZEN` |

**For maintenance, these are a tripwire, not a goal.** A refactor is supposed to
leave all four exactly where they were; one that moves any of them *down* has
changed behaviour and is mislabelled (M4). One that moves them *up* is a feature
commit wearing a refactor's clothes (M1).

### 2. Mapping surface — *is support still growing as data?*

| Metric | Direction | Why it matters |
|---|---|---|
| `registry_rows` | up | The coverage surface: scalar + aggregate + operator rows |
| `hand_written_loc_per_row` | **down or flat** | The architecture's health. Support is meant to grow by adding rows, not emitter branches (M8, `../ai-cost-strategy.md` §6.2). A rising number means the table is losing to the code |
| `udf_mappings` | flat at 0 | A Python UDF leaves DuckDB's engine: slow, and ours to keep correct. `TRANSLATION.md` §7 makes it the last resort — a rise is a design signal, not a coverage win |
| `rows_citing_an_r_rule` | up, as a share of rows | A row that cites no R-rule was either checked against the trap catalog and cleared, or never checked. The share is a proxy for which |
| `rows_with_a_gotcha_note` | up | The `Limitations and gotchas` column is [the point of the support matrix](../../CONTRIBUTING.md) |
| `deliberate_refusals` | up is *good* | A refusal with a reason is a contribution. This number falling without matching new mappings means something started guessing |

### 3. Structure — *how hard is the code to change?*

| Metric | Budget | Notes |
|---|---|---|
| `modules_over_budget` | 800 LOC | A module past this cannot be reviewed as a diff or held in one head |
| `functions_over_loc_budget` | 60 lines | A compass. Declarative blocks (`argparse` builders, data tables) are false positives — see [gaming](#how-these-can-be-gamed) |
| `functions_over_branch_budget` | 12 branches | Counts `if`/`for`/`while`/`except`/ternary/`match` plus each extra `and`/`or` arm. Branch density is the better signal of the two: it tracks the number of paths a reviewer must hold, and paths are where wrong answers hide |
| `source_loc` | — | Context for the ratios; not a target in either direction |

Length and branch density *together* are the signal. `cli.py::_parser` is long
and flat (fine). `render_expr` is long **and** 45 branches deep, in the hottest
file in the repo — that is the one to work on.

### 4. Hotspots — *where does maintenance pay?*

`commits × current LOC`, per source file, over the repo's history. This is the
single most decision-useful number in the report: it separates "big" from
"expensive to keep big". A 2,000-line file nobody has touched in a year costs
nothing; the same file edited in a third of all commits is where the next defect
will land (Tornhill, *Your Code as a Crime Scene*).

Use it to *choose* the target. Then check the structure metrics to decide what to
do to it, and the tests to decide whether it is safe to.

### 5. Suppression ledger — *what has stopped being checked?*

| Metric | Direction | Notes |
|---|---|---|
| `noqa_total`, `noqa_by_rule` | flat or down | Each is a linter told to look away |
| `noqa_without_a_reason` | **0** | M11. A reason may sit after the code or on the line above |
| `noqa_suppressing_an_unselected_rule` | **0** | Ruff's own RUF100: a directive suppressing a rule the config never runs. The reason written beside it is enforced by nothing |
| `type_ignore` | flat or down | The package ships `py.typed`; each ignore is a hole in that promise |
| `skipped_tests`, `xfail_tests` | **0** | An unconditional skip is a deleted test with better manners (M5) |
| `conditionally_skipped_tests` | context | `skipif`/`importorskip` are legitimate here (no duckdb, no emulator, no corpus) — but a check that skips itself in CI is [green because it did not run](../code-review/tooling-packaging-ci-docs.md) |
| `mypy_module_overrides`, `ruff_per_file_ignores` | flat | The mypy config says why the per-module ladder was dismantled; three overrides remain, each with a written reason |

### 6. Test surface

`test_functions`, `test_loc`, `test_to_source_loc`. Size, not effectiveness — a
ratio that falls while the mapping surface grows means new mappings are arriving
under-tested. It cannot tell you whether the tests are any good; only the oracle
and the trap catalog can.

### 7. Diff discipline — *is change arriving in reviewable pieces?*

`median_changed_lines`, `p90_changed_lines`, `over_review_budget` across the last
N commits, with generated artifacts (`_antlr/`, `tests/cases/`, `tests/fixtures/`,
`docs/kql-support.md`, `demo/`) excluded so a corpus refresh does not look like a
million-line commit.

The 400-line budget is the SmartBear/Cisco finding the [review
charter](../code-review/README.md) already runs on. Today's numbers say the
median commit is at the ceiling and 18 of the last 50 are over it — worth knowing
about a repo whose defining bug is the one that gets skimmed past. This is the
metric most directly under an agent's control, and the easiest to improve: land
the refactor separately (M1).

## Metrics worth adding, and what each would cost

Not collected today. Listed with the decision each would need, so nobody adds one
by reflex.

| Candidate | What it would tell us | Cost / decision needed |
|---|---|---|
| **Mutation score** on `translate/` and `lower.py` | Whether the suite actually *detects* changed behaviour — the only direct measure of the thing this project cares about. Google runs mutation testing at scale for precisely this (Petrović & Ivanković, *State of Mutation Testing at Google*, ICSE-SEIP 2018) | A `mutmut`/`cosmic-ray` run is minutes-to-hours. Would have to be nightly, on a subset, and reported — never a gate |
| **Escaped wrong answers** | The charter metric: how many "runs and returns a different answer than Kusto" reports were filed per release, and how long each took to fix | Free, but needs a convention: a `wrong-answer` issue label and the discipline to apply it |
| **Snapshot drift per PR** | How often a change that called itself a refactor moved the SQL | Cheap once `tools/sql_snapshot.py` runs in CI on a baseline artifact. Worth doing when the corpus stops changing shape |
| **DORA four keys** | Delivery health ([dora.dev](https://dora.dev/guides/dora-metrics-four-keys/)) | Premature: no releases yet and 50 commits of history. Revisit after the first few releases — *change failure rate* (releases needing a follow-up fix) is the one that will matter first |
| **Prompt-cache read share** | Whether M9 is being honoured — a churned `TRANSLATION.md`/registry prefix shows up as `cache_read_input_tokens` collapsing (`../ai-cost-strategy.md` §6.2) | Needs the agent runs' usage telemetry collected somewhere |
| **Cost per merged mapping** | Whether the tiering in `../ai-cost-strategy.md` is working | Same telemetry problem |

## Cadence

- **Every maintenance commit:** run before and after; put the delta in the commit
  message. A refactor with no metric delta and no justification sentence did
  nothing measurable.
- **Every planning pass:** run `--top 15` and let the hotspot list pick the work
  ([`debt-scout`](../../.claude/agents/debt-scout.md) does exactly this).
- **Periodically:** append a `--json` run to a trend file. The single most useful
  view is not any one number but its direction over ten commits.

## How these can be gamed

Stated plainly, because an agent optimising a number will find these on its own:

- **Splitting a clear function to clear the 60-line budget.** Length is a symptom;
  branch density in a hot file is the disease. Splitting `_parser` improves the
  report and the code not at all.
- **Deleting a `# noqa` without fixing what it suppressed.** The ledger falls, the
  lint rule still is not enabled. The honest move is to enable the rule.
- **Moving code between modules to clear the 800-line budget.** If the exports and
  the call graph did not change, nothing changed except the report.
- **Adding tests that assert nothing** to lift `test_to_source_loc`. This is why
  effectiveness is measured by the ratchets and the oracle, never by test count.
- **Lowering a ratchet and calling it a scope change.** The only legitimate reason
  a baseline moves down is a construct that was found to be *wrong* and was
  converted to a loud refusal — which is a spec decision with an oracle result
  behind it, not a maintenance one.

If a number moved and the code did not get easier to change, the number was the
work. Say so in the commit message and move on.

## Baseline — 2026-08-23

`python tools/maintenance_metrics.py --top 5`, at commit `3c4f52e` with this
framework applied — so the suppression count includes the four directives the two
new tools add. This is the "before" for any maintenance work that follows; re-run
rather than trusting it to stay current.

```
CORRECTNESS RATCHETS
  behaviour cases passing: 291
  azure monitor probes: 114
  corpus queries parsed: 1285
  frozen expectations: 1036

MAPPING SURFACE
  registry rows: 168          (scalar 116, aggregate 19, binary operators 33)
  scalar by kind: native 44, template 72
  udf mappings: 0
  rows citing an r rule: 76
  rows with a gotcha note: 24
  translate loc: 2961
  hand written loc per row: 14.9
  deliberate refusals: 8

STRUCTURE
  source loc: 11316    modules: 31    functions: 500
  modules over budget (800): 2
      - src/duckdb_kql/translate/__init__.py (2345)
      - src/duckdb_kql/lower.py (1731)
  functions over loc budget (60): 27
  functions over branch budget (12): 16
  branchiest functions:
      - translate/__init__.py:173 render_expr (45 branches)
      - lower.py:613 _lower_operator (42 branches)
      - comparison.py:575 compare (41 branches)
      - comparison.py:290 _values_equal (39 branches)
      - lower.py:144 _lower_expr (32 branches)

HOTSPOTS (commits x lines)
  - src/duckdb_kql/translate/__init__.py — 17 x 2345 = 39865
  - src/duckdb_kql/lower.py — 15 x 1731 = 25965
  - src/duckdb_kql/translate/functions.py — 10 x 454 = 4540
  - src/duckdb_kql/ir.py — 10 x 387 = 3870
  - src/duckdb_kql/server.py — 6 x 635 = 3810

SUPPRESSIONS
  noqa total: 125   (PLC0415 73, BLE001 21, E402 12, B017 7, N802 4, S603 4, ...)
  noqa without a reason: 81
  noqa suppressing an unselected rule: 125
  type ignore: 9
  skipped tests: 0    xfail tests: 0    conditionally skipped: 76
  mypy module overrides: 3    ruff per file ignores: 1

TESTS
  test files: 49    test functions: 901    test loc: 8803
  test to source loc: 0.78

DIFF DISCIPLINE (last 50 commits, generated paths excluded)
  median changed lines: 285    p90: 975    max: 27508
  over review budget (400): 18
```

Three things this baseline says out loud:

1. **Two files carry the maintenance burden.** `translate/__init__.py` and
   `lower.py` are the two largest modules, the two hottest files, and hold four
   of the five branchiest functions. Every other structural number is noise next
   to that.
2. **The suppression ledger is not doing its job.** All 125 `# noqa` directives
   name rules the enabled `select` set never runs, and 81 carry no reason. The
   [playbook](refactoring-playbook.md#retire-a-suppression--safe-to-careful) has
   the two honest resolutions; both are one deliberate commit.
3. **Commits are at or over the review ceiling.** Median 285, p90 975. The
   cheapest fix is procedural, not structural: land refactors separately from
   features (M1).
