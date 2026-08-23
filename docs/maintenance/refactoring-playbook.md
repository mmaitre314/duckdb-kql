# Playbook: refactoring

**Scope:** any change whose intent is to leave the answers alone and improve the
code — extracting, renaming, splitting, unifying, deleting.
**Out of scope:** anything that changes what is emitted, refused or ordered.
That is a spec change (M3) and goes through
[`spec-architect`](../../.claude/agents/spec-architect.md).

**Read first:** the [charter](README.md) and its
[rules](README.md#the-rules), plus [`../TRANSLATION.md`](../TRANSLATION.md) §4 if
you are anywhere near the translator.

## The gate

Three commands, in this order, every time. The middle one is the one this repo
adds and the one that actually catches semantic drift.

```bash
# 1. Before touching anything, on the base commit:
python tools/sql_snapshot.py --out /tmp/before.txt

# ... refactor ...

# 2. The proof obligation (M2): byte-identical SQL for the whole frozen corpus.
python tools/sql_snapshot.py --compare /tmp/before.txt

# 3. The usual three, which CI also runs:
ruff check src tools tests demo
mypy
pytest
```

`--compare` exits non-zero and prints a unified diff if anything moved. There is
no acceptable non-empty diff for a refactoring commit. A line you believe is
"obviously fine" is either

- **a bug you just wrote** — the common case, because the translator's failure
  mode is plausible-looking output; or
- **a behaviour change**, in which case the commit is mislabelled: split it (M1),
  establish the new expectation against the oracle, and land it as a mapping
  change with its trap test.

Why the snapshot rather than the suite alone: `pytest` proves the ~900 cases
someone thought to write. The snapshot covers **1,285 corpus queries**, including
the ones nobody has mapped yet — and it records *refusals* verbatim, so a
restructuring that quietly converts `KqlUnsupportedError` into SQL shows up as a
diff line instead of as a wrong answer in production.

### The fast loop

The full suite is slower than a refactor's inner loop wants. Between steps:

```bash
pytest --ignore=tests/test_behavior.py -x -q      # skip the ground-truth sweep
pytest -n0 tests/test_<the_area>.py               # serial, live output
```

Run the full gate before every commit anyway. `--dist loadfile` and the
module-level fixtures mean the full suite is not as expensive as it looks
(CONTRIBUTING → *If the suite gets slow*).

## The mechanic

1. **Pick from the numbers.** `python tools/maintenance_metrics.py`. The hotspot
   list is ranked by `commits × lines`, which is the only ranking that
   distinguishes "big" from "expensive".
2. **Write the justification sentence** (M6). Put it in the commit message; it is
   what a reviewer checks the diff against.
3. **Name the refactoring.** Use a name from [the catalog](#the-catalog) or from
   [Fowler's](https://refactoring.com/catalog/) — "extract function", "replace
   conditional with lookup table", "inline variable". A named refactoring has a
   known mechanic and a known failure mode; "cleaned up `lower.py`" has neither
   and cannot be reviewed.
4. **Take one step at a time, gate between steps.** The literature's advice is
   boring and correct: many small transformations, each independently green,
   beats one large one. If the fast loop goes red, undo the last step rather than
   debugging forward — you know exactly which step did it.
5. **Keep the diff under 400 lines** where the change is hand-written. Past that
   it stops being reviewed
   ([review charter](../code-review/README.md#why-checklists-and-why-by-area)).
   If it cannot be that small, it should be *mechanical* — see
   [large-scale changes](#large-scale-and-mechanical-changes).
6. **Re-measure and record.** Put the metric delta in the commit message:
   `translate/__init__.py 2345 → 2180 LOC; branchiest function 45 → 22 branches;
   snapshot identical.` That sentence is the difference between claimed impact
   and measured impact.

## The catalog

Refactorings this codebase actually has work for, each with its tier from the
[risk ladder](README.md#the-risk-ladder) and what proves it. The measurements
cited are from the [baseline snapshot](metrics.md#baseline--2026-08-23); re-run
the tool rather than trusting these numbers to stay current.

### Replace an `isinstance` / name-string chain with dispatch — *Careful*

`render_expr` (`translate/__init__.py`, 157 lines, 45 branches) and
`_lower_operator` (`lower.py`, 129 lines, 42 branches) are both long chains that
test a node's type or its ANTLR class name and return. They are the two most
branch-dense functions in the package and they sit in the two hottest files.

The mechanic is a dispatch table keyed on the node type, or
`functools.singledispatch`. **The trap, and the reason this is Careful and not
Safe:** these chains are *not* pure type dispatch. Several arms are ordered
guard clauses — `BinaryOp` with `/` on a timespan must be tested before the
generic `/`, and the dynamic-comparison arm before the generic comparison. A
table keyed on node type alone silently reorders them, and the result still
compiles, still passes most tests, and returns a different number for
`dow / 1d`.

So: convert only the arms that dispatch on type *alone*, leave guarded arms as an
explicit head or tail of the function, and say in a comment which arms are
order-dependent and why. The snapshot is what proves you got it right.

### Turn a special form into a registry row — *Careful*

The architecture's whole premise is that support grows by adding **data**
(`translate/functions.py`), not code (`translate/__init__.py`)
— `../ai-cost-strategy.md` §6.2. Every construct handled by a hand-written
special form that a `{0}`-style template could express is a row waiting to
happen, and each conversion moves `hand_written_loc_per_row` in the right
direction.

Do not force it. CONTRIBUTING is explicit that a shape a template cannot express
belongs in `translate/__init__.py` *with a comment saying why* — a template
contorted past readability is worse than the special form it replaced.

### Split a module without changing its exports — *Safe*

Two modules are over the 800-line budget: `translate/__init__.py` (2,345) and
`lower.py` (1,731). Both are also the top two hotspots, so this is where a split
pays.

Split along an axis that already exists in the code — the `render_*` operator
family, the `join`/`union` family, the `has`/`in` list family — and re-export
from the package module so no import outside the package moves. `__all__` and the
public surface must be unchanged: if a consumer's import breaks, this was an API
change (M12), not a split.

### Delete dead code — *Safe, and first*

The cheapest thing on this list (M7). Candidates the tools hand you: a private
helper with no callers, a branch made unreachable by an earlier `raise`, a
compatibility shim for a version no longer supported, a fixture no test uses.
`ruff` finds unused imports and locals; `grep` for a private name's call sites is
the rest of it. Deletion is provable by the gate and free to review.

### Retire a suppression — *Safe to Careful*

The suppression ledger currently stands at **125 `# noqa` directives, all of
which ruff reports as unused** against the enabled rule set (`select = ["E", "F",
"I", "UP", "B"]`). They name rules — `PLC0415`, `BLE001`, `S603`, `N802` — that
nothing runs. That is a checker that has quietly stopped checking, in a repo
whose charter is about exactly that failure mode. There are only two honest
resolutions, and both are maintenance work someone should do deliberately:

1. **Widen `select`** so the suppressions become load-bearing and their written
   reasons get enforced. Expect real findings on the way — that is the point.
2. **Delete them** and stop annotating for a rule set the project does not run.

Do not do the third thing, which is to leave 125 directives that look like
enforced discipline and are not. Whichever way it goes, it is a single mechanical
commit plus a `pyproject.toml` change — and it is the only item in this catalog
whose diff is allowed to be large, because it is generated (`ruff --fix`), not
written.

### Unify duplicated emitter branches — *Careful*

Near-identical SQL-building code in two arms is a maintenance cost and a
divergence risk: the next R-rule fix lands in one of them. Before merging two
branches, state **what each one does differently** — case folding, null
handling, quoting, the `_cs` variant — and find the test that pins that
difference. If no test pins it, you have found a missing trap test; write it
*first*, watch it pass on the current code, then unify. Merging two branches that
differ in a null rule is the canonical way to introduce a wrong answer.

### Extract a guard clause / simplify a long function — *Safe*

27 functions exceed the 60-line budget. Treat that number as a compass, not a
target: `cli.py::_parser` is 236 lines of `argparse` declarations and is
perfectly clear — restructuring it would be [motion, not
progress](metrics.md#how-these-can-be-gamed). Spend the effort where length and
*branch density* coincide, and where the file is hot.

## Large-scale and mechanical changes

For a change that must touch many files at once — a rename across the package, a
signature change, a lint-rule migration — follow the Google LSC shape
([SWE at Google ch. 22](https://abseil.io/resources/swe-book/html/ch22.html)):

- **Generate it**, don't hand-edit it: `ruff --fix`, a codemod, or a short script
  committed under `tools/` if it will ever be run twice.
- **Keep it atomic and uniform.** One pattern applied everywhere is reviewable at
  any size; a hundred slightly different edits is not, at any size.
- **Separate the mechanical commit from every judgement call.** If three of the
  hundred sites need thought, land ninety-seven mechanically and the three on
  their own.
- **Say in the commit message how it was generated**, so it can be re-run or
  reversed.

## Refactoring under an agent

`refactorer` runs this playbook; `debt-scout` chooses its targets. Two rules from
the [Bun review](../lessons-from-bun-rewrite.md) apply unchanged:

- **The agent that refactored does not review the refactor.** Risky-tier diffs go
  to [`adversarial-reviewer`](../../.claude/agents/adversarial-reviewer.md) with
  the diff and nothing else.
- **Fix the process, not the instance.** If an agent produced a refactor that
  moved the snapshot, the interesting question is which instruction let it — the
  answer belongs in this file, not only in the reverted commit.
