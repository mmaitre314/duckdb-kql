# Area: Testing, oracle & fixtures

**Scope:** `tests/`, `src/duckdb_kql/oracle.py`, `comparison.py`, `fixtures.py`,
`tests/cases/`, `tests/fixtures/`, `tools/regen_expectations.py`,
`tools/make_fixtures.py`.
**Out of scope:** whether a given mapping is semantically right (translation
area) — here the question is whether the *tests would notice* if it weren't.

**Read first:** the [charter](README.md), `docs/test-plan.md` (the case-file
schema is §4.1), `docs/oracle-harness.md`, and CONTRIBUTING's "Adding a mapping"
and Style sections.

This project's entire safety argument rests on the tests, so the tests get
reviewed as adversarially as the code. Bun shipped **19 regressions behind a
fully green 1.38M-assertion suite** — every one from code "syntactically
identical but semantically different." A green suite proves the cases someone
thought of; this review is about the cases they didn't, and about tests that are
green for the *wrong reason*.

## Test integrity — the non-negotiables

- [ ] **No expectation was weakened, skipped, `xfail`ed, or deleted to make a
  change pass.** This is the cardinal sin (TRANSLATION.md principle 4). Diff the
  test files: an assertion that got looser, a `==` that became `in`, an
  `assertEqual` that became `assertIn`, a case flipped from `pass` to `xfail`
  without a written oracle-was-wrong argument — all **S1-severity process
  defects**. Call them out loudly.
- [ ] **No query was special-cased to pass.** A test that pins one specific query
  string rather than the class of behaviour is fixing the query, not the rule.
- [ ] **Expectations come from the emulator, never invented.** A new `expected`
  value must trace to `tools/regen_expectations.py` / the oracle, not to a
  hand-typed guess or Microsoft's docs (the docs are CC-BY prose and several
  divergences here *contradict* them). A guessed expectation is worse than an
  `xfail` with no expectation.

## Does the test actually prove correctness?

- [ ] **Trap test, not happy path.** For anything touching TRANSLATION.md §4, is
  there a test that fails against the *obvious-but-wrong* mapping? "Returns
  something" is not a test. `Text has "err"` → false for `"error"` is; `has`
  returning a row is not.
- [ ] **Named after what breaks.** `test_payload_never_reaches_the_sql_text`
  states what's protected; `test_parameters` doesn't (CONTRIBUTING Style). A
  vaguely-named new test often signals a vaguely-conceived assertion.
- [ ] **Nondeterminism asserted as a set (R10).** Any test involving
  `take`/`sample`/`top`/unordered output must compare **unordered**. A test that
  asserts a row order KQL doesn't guarantee is itself a latent flake and a wrong
  claim — flag it even though it's currently green.
- [ ] **Approximations use tolerance (R11).** `dcount`/`percentile` asserted for
  exact equality is wrong even when it happens to pass on the fixture.
- [ ] **Edge inputs are actually exercised:** empty table, empty string, single
  row, all-null column, out-of-range index, negative number, overflow, non-ASCII.
  Missing edge coverage on a §4 construct is **S3**.

## The oracle and comparison machinery (`oracle.py`, `comparison.py`)

These are the tools that decide pass/fail, so a bug here silently passes wrong
mappings — the highest-leverage code in the test tree.

- [ ] **Comparison is as strict as correctness requires, and no stricter.** Does
  `comparison.py` compare *values and types*, order-insensitively where KQL is
  unordered and order-sensitively where a terminal `sort` exists? A comparator
  that coerces types before comparing (int vs float, naive vs aware datetime,
  Decimal vs float) can hide an R1/R8/R11 bug. A comparator that's *too* strict
  (asserting order KQL doesn't promise) produces false failures that pressure
  people to weaken tests.
- [ ] **Null handling in the comparator.** Does it distinguish null from empty
  string (R4, `isempty` ≠ `isnull`)? From NaN? A comparator treating `None` ==
  `''` masks a whole class of null bugs.
- [ ] **Float/datetime tolerance is explicit and justified**, not an accidental
  epsilon that also hides real drift.
- [ ] **The oracle can't fabricate.** If the emulator is absent, the acceptance
  suite *skips* (CONTRIBUTING) rather than passing vacuously — confirm a change
  didn't turn a skip into a silent pass, or make an oracle-dependent test run
  green with no oracle.

## Fixtures and corpus (`fixtures.py`, `tests/cases/`, `tests/fixtures/`)

- [ ] **Fixtures are deterministic and pinned.** A regenerated fixture
  (StormEvents, PopulationData) that shifts values invalidates every frozen
  expectation built on it — the recent history has exactly this bug
  (expectations frozen against a *draft* fixture). A fixture change must
  re-derive its expectations, and the diff should show both moving together.
- [ ] **Every corpus case carries provenance** (`source`, `source_commit`,
  license) and follows the §4.1 schema. A case missing provenance breaks the
  licensing accounting (`docs/licensing.md`).
- [ ] **No docs' output tables committed as expectations** — they're CC-BY prose;
  harvest queries only (`docs/licensing.md` §3).
- [ ] **`xfail` cases are honest:** an `xfail` with no expectation is correct
  (we don't have ground truth yet); an `xfail` masking a *known-wrong* answer
  that should be a refusal is not.

## Coverage bookkeeping

- [ ] **Baselines only go up.** `tests/test_behavior.py::BASELINE_PASSING` and
  `tests/test_profile_azure_monitor.py::BASELINE_SUPPORTED` are ratchets — a
  change that lowers one is either a regression or a ratchet being loosened to
  hide one. Either way, it's a finding.
- [ ] **The support matrix moved with the code.** `docs/kql-support.md` is
  generated; if coverage changed and the matrix didn't (or was hand-edited), CI
  should catch it — confirm it would.
- [ ] **`filterwarnings = ["error"]`** is in effect — a new test that needs a
  warning ignored is hiding something; check why.

## What to enumerate as "checked clean"

Which §4 rules touched by the change have a trap test that would fail against
the obvious-wrong mapping; that no assertion loosened in the diff; that new
expectations trace to the oracle; and that the comparator distinguishes the
value/type/null/order dimensions the change relies on.
