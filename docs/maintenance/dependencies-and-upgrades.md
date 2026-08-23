# Playbook: dependencies and upgrades

**Scope:** adding, pinning, upgrading and removing anything outside this
repository — the ANTLR runtime, DuckDB, pandas/pyarrow, the Python floor and
ceiling, the emulator image, the dev toolchain.
**Read first:** the [charter](README.md), CONTRIBUTING's *Things CI will check
that are easy to miss*, and [`../licensing.md`](../licensing.md).

An upgrade is the one maintenance action that can change answers **with no diff
in this repository at all**. `duckdb>=0.10` is an unpinned range: a new DuckDB
release can change a cast, a collation, a null ordering or a regex dialect, and
nothing in a code review will show it. Treat every upgrade as a behaviour change
until the gate says otherwise.

## The rule that comes first

**A new runtime dependency is an architecture change, not a chore.** The layering
promise is that Layer 0 — KQL text to DuckDB SQL — needs only the parser runtime,
and CI enforces it by installing the package with duckdb genuinely absent and
translating a query. Anything added to `[project.dependencies]` breaks that
promise for every consumer who only wanted a transpiler.

So, before adding one, in order: can it be stdlib? Can it be an *extra*? Can it be
vendored as a few lines with a licence note? Only then a dependency — and it goes
in the pull request description with the reason, not in a lockfile bump.
Removing one is always allowed and always welcome.

## Upgrade classes, and what each owes

| What is moving | The real risk | What must run |
|---|---|---|
| **DuckDB** | Emitted SQL means something different. Silent, and exactly the project's failure mode | `tools/sql_snapshot.py --compare` proves the *emitted text* did not move — which is not the question here, so it is **not sufficient**. Run `pytest` in full, including `tests/test_behavior.py`, which executes queries against the frozen ground truth |
| **ANTLR runtime** | Parse-tree shape or API drift, and the committed parser must match the generator | Bump `antlr4-python3-runtime` in `pyproject.toml` **and** `ANTLR_VERSION` in `tools/regen_parser.sh` together, re-run the script, commit the regenerated parser in the same commit, update `grammar/UPSTREAM.md` provenance. CI compares the committed parser against the grammar |
| **pandas / pyarrow** | Type or dtype changes reaching `dataframe_from_result_table`, and stub changes reaching the typed surface | `pytest`, plus `mypy` — `pandas-stubs` is a dev dependency precisely so `df()` does not silently resolve to `Any` |
| **Python floor (3.10)** | Syntax and typing features that fail only on the floor | The CI matrix runs the floor and the ceiling. Raising the floor is a packaging decision with a `requires-python` bump, a classifier change and a release note |
| **Python ceiling** | New interpreter, new warnings — and `filterwarnings = ["error"]` turns a new `DeprecationWarning` into a red suite | Add the leg to the matrix and the classifier in the same commit |
| **Dev toolchain (ruff, mypy)** | New rules fire; the suite goes red for reasons unrelated to any change | Land the version bump and the fixes as **separate commits** (M1). If the new rules are numerous, the fix commit is [mechanical](refactoring-playbook.md#large-scale-and-mechanical-changes) and generated |
| **Emulator image** | Ground truth itself moves | Regenerate expectations deliberately, never as a side effect. A changed expectation needs a written argument about which side was wrong ([`mapping-author`](../../.claude/agents/mapping-author.md) rule) |

## The pins that must move together

Three pairs. Breaking one of them is the classic "CI is green because the two
halves agree with each other and not with reality":

- `antlr4-python3-runtime` in `pyproject.toml` ⇄ `ANTLR_VERSION` in
  `tools/regen_parser.sh` ⇄ `grammar/UPSTREAM.md`.
- `grammar/Kql.g4` ⇄ the committed `src/duckdb_kql/_antlr/` (CI checks).
- `requires-python` ⇄ the `Programming Language :: Python ::` classifiers ⇄ the
  CI matrix legs.

The version *triple* problem the rest of the world has — pyproject, `__version__`
and the tag — does not exist here: the git tag is the version and `hatch-vcs`
reads it at build time (CONTRIBUTING → Releasing). Do not reintroduce a version
literal in a file.

## Licensing is part of the upgrade

Adding or vendoring anything third-party means an entry in
[`THIRD-PARTY-NOTICES.md`](../../THIRD-PARTY-NOTICES.md), a copy under
`licenses/` where the licence requires one (Apache-2.0 §4(a) is why the generated
parser's licence ships in the wheel), and a check against
[`../licensing.md`](../licensing.md). `tests/test_licensing.py` exists so this
cannot quietly stop being true.

The Kusto Emulator stays what it is: **development and CI only** — never a
runtime dependency, never redistributed, and no timing or performance numbers
published from it (`../licensing.md` §5).

## Cadence

- **Security advisories:** immediately, on their own branch, with the gate run in
  full. A security bump still may not carry a refactor with it.
- **Everything else:** batched and deliberate, one upgrade class per commit, so a
  bisect points at one thing. A batch that mixes a DuckDB bump with a ruff bump
  cannot be reverted usefully.
- **Before a release:** confirm the pins that must move together still agree, and
  that no CI leg has quietly been dropped
  ([tooling review area](../code-review/tooling-packaging-ci-docs.md)).

## What to record

In the commit message, three lines: what moved and from/to, what ran, what it
said. For a DuckDB bump specifically, name the behaviour suite result — that is
the only evidence that answers did not change:

```
deps: duckdb 1.1 -> 1.2

Gate: pytest (full, incl. test_behavior) green; BASELINE_PASSING unchanged at 291.
SQL snapshot identical (translation is unaffected by the engine version).
```
