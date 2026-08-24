# Working on duckdb-kql

A KQL → DuckDB SQL transpiler. **The charter is one sentence: a wrong answer is
worse than no answer.** Almost every convention below follows from it.

The specs are normative and already written. Read the one that covers what you
are about to touch rather than inferring the rule from the code:

| If you are changing… | Read |
|---|---|
| what the SQL must *mean* | [`docs/TRANSLATION.md`](docs/TRANSLATION.md) — §4 is R1–R21, the semantic invariants |
| structure, without changing behaviour | [`docs/maintenance/README.md`](docs/maintenance/README.md) — the gate, the risk ladder, the 400-line review budget |
| how a change is judged | [`docs/code-review/README.md`](docs/code-review/README.md) |
| how a change gets in | [`CONTRIBUTING.md`](CONTRIBUTING.md) |

## The one habit that matters

**Measure against the emulator; do not trust the documentation, and do not
trust this repo's own prose either.** Microsoft's docs are the specification,
the Kusto Emulator is the oracle, and where they disagree the oracle wins.

This is not a posture. In the adversarial review of 2026-08-24, of eighteen
findings written by a careful reviewer reading the code, **three were inverted**
— the behaviour was the opposite of what the entry claimed — and one of those
came with a suggested fix that would have introduced a bug. This repo's own
normative spec has been wrong. Commit messages in its history have been wrong.
The corpus, the generated support matrix and a function's own docstring
contradicted each other three ways about the same function (`countof`), and the
*code* turned out to be the wrong one of the three.

So: `python tools/differential.py 'print x = ...'` before believing anything.

Three ways that harness has produced false confidence, all of them survivable
only because they were caught — see its module docstring for the details:

1. **Which evaluator you ask changes the answer.** Kusto's constant folder and
   its row engine disagree. `substring('abcdefg', long(null))` is `'abcdefg'`
   folded and null over rows. Use `Differential.both()`, and note that wrapping
   a *constant* in a `datatable` still folds — the expression must reference a
   column.
2. **The emulator renders a null string as `''`.** Ask for `isnull(x)` and
   `strlen(x)`, not `x`.
3. **A needle containing `'` or `|` breaks the query**, both engines refuse, and
   the pair scores as "agreeing". Read the text before believing a mutual
   refusal.

## Before you commit

```bash
python tools/sql_snapshot.py --out /tmp/before.txt   # BEFORE touching anything
# ... change ...
python tools/sql_snapshot.py --compare /tmp/before.txt
python -m pytest -q && ruff check src tools tests demo && python -m mypy
python tools/gen_support_matrix.py            # if you touched the registry
```

The snapshot is the gate that makes "behaviour-preserving" a claim instead of a
hope: it covers ~1,285 corpus queries, against the handful anyone thinks to
test. **For a refactor it must come back byte-identical.** For a fix it will
differ, and every differing line should be one you can explain.

`docs/kql-support.md` is generated — never hand-edit it; edit
`tools/gen_support_matrix.py` and regenerate. A test enforces this.

## Ratchets that may only go up

- `tests/test_behavior.py::BASELINE_PASSING` — corpus cases matching ground
  truth. Currently 301.
- `KNOWN_DIVERGENCES` — an **admission of a bug**, not a waiver. A case listed
  there that starts passing *fails the build*, so the list cannot rot into a
  silent allowlist. It is currently down to one entry.

## Every fix lands with a trap test

Not a test that the code does what it does — a test that records *what was
measured, what the wrong answer was, and why the obvious fix was wrong.* The
existing ones are the model: `tests/test_tostring.py`,
`tests/test_case_collisions.py`, `tests/test_clause_scope.py`.

§4 of TRANSLATION.md cites each rule's trap test **by path**, and
`test_docs.py` fails if a citation stops resolving. It used to cite invented
IDs like `trap-r7-identifiers` that existed nowhere in the tree, and that is
precisely how R7's second clause read as covered while never having been
implemented at all — there was no artifact anyone could open and find missing.

## Hard constraints

- **The Kusto Emulator is dev/CI only.** Never shipped, never exposed as a
  service, never a runtime dependency, never redistributed.
- **Its EULA §2(d) forbids publishing any benchmark-style measurement of it, or
  any comparison against it.** The full rule, and the vocabulary it covers, is
  in [`docs/licensing.md`](docs/licensing.md) — read it before writing anything
  that measures. It binds commits, docs, issues and chat alike.
  `tests/test_licensing.py` greps the repo sentence by sentence and will fail
  the build; only the few files whose *subject* is the prohibition may state it
  in full, and this file is deliberately not one of them.
- Layering: importing `duckdb_kql` must **not** import `duckdb`. Layer 0 is
  translation (antlr4 only), layer 1 adds DuckDB, layer 2 adds pandas. A test
  enforces it.
- Identifiers are always emitted double-quoted (R7); string literals always
  single-quoted with `''` escaping.

## Two traps that have each cost a day

**A reported crash is often the loud half of a bug whose other half is silent.**
`datetime_add("dayofyear", …)` crashed; the same shared table also made
`datetime_add("week_of_year", …)` quietly return a wrong answer. When you fix a
loud failure, look for the quiet sibling.

**A static predicate over the IR is only sound where the IR carries the
answer** — and a bare `ColumnRef` never does. Allow-lists keyed on expression
shape (`_is_bool_expr` was one) are wrong for the commonest operand there is: a
column. Prefer DuckDB's run-time `typeof` where the answer is only needed at
execution. Where it is needed at *translation* time — to refuse, or to choose
what to emit — that is the open work in
[`docs/column-types-proposal.md`](docs/column-types-proposal.md).

## Environment

```bash
sudo -n dockerd &                 # if the daemon is not already up
docker compose up -d kusto        # the emulator
docker inspect --format '{{.State.Health.Status}}' duckdb-kql-kusto   # wait for `healthy`
```

`KustoEmulator().query()` hits the query endpoint; `.command()` hits
`/v1/rest/mgmt`, which is what `.set-or-replace` and the other `.`-commands
need.
