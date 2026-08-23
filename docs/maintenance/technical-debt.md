# Playbook: technical debt

**Scope:** recognising debt in this repo, recording it, pricing it, and choosing
when to pay it.
**Read first:** the [charter](README.md).

Ward Cunningham's original metaphor is more useful than the way it is usually
quoted: debt is a *deliberate* choice to ship something not-quite-right in order
to learn sooner, and the interest is paid in every subsequent change until it is
refactored away. Code that is simply wrong is not debt — it is a bug
([Cunningham, OOPSLA '92](http://c2.com/doc/oopsla92.html)). Keeping the two
apart matters here more than usual, because in this project a bug returns a
plausible number and nobody files it.

## What is *not* debt here

Four things look like debt in a normal codebase and are load-bearing design in
this one. Do not "pay them off":

- **A deliberate refusal.** `KqlUnsupportedError` for a construct whose nearest
  DuckDB equivalent would return plausible-looking wrong output is the *correct*
  outcome, permanently. It is listed with a reason in
  `tools/gen_support_matrix.py` and it is a contribution
  ([CONTRIBUTING → When *not* to add a mapping](../../CONTRIBUTING.md)).
- **A documented divergence.** A residue recorded in the divergence catalog with
  its exact conditions is knowledge, not rot.
- **A declared `Any` with a comment.** An ANTLR tree node and a `dynamic`
  document genuinely have no type. The debt would be an *undeclared* one
  (CONTRIBUTING → Style).
- **A special form in `translate/__init__.py` with a written reason.** Some
  shapes a `{0}` template cannot express. The debt is the special form with *no*
  reason, or one that a template could now express.

## What *is* debt here

Ranked roughly by interest rate — how much it costs per subsequent change.

| Debt | Why it charges interest | How it shows up |
|---|---|---|
| **A mapping with no trap test** | The next refactor has nothing to hold it in place; the next reader cannot tell whether the R-rule was checked or skipped | `rows_citing_an_r_rule` below the row count; the mapping's family has no test named after what would break |
| **A check that has stopped checking** | Green stops meaning anything: a `skipif` that always skips in CI, a suppression for a rule nothing runs, a workflow step made `continue-on-error` | [Suppression ledger](metrics.md#5-suppression-ledger--what-has-stopped-being-checked); [tooling review area](../code-review/tooling-packaging-ci-docs.md) |
| **Duplicated emitter branches** | The next R-rule fix lands in one of the two, and the divergence returns a wrong answer from the other | Two arms with near-identical SQL construction; found by reading the [hotspots](metrics.md#4-hotspots--where-does-maintenance-pay) |
| **A special form that could be a row** | Support grows in code instead of data, and every future mapping in that family pays for it (M8) | `hand_written_loc_per_row` rising |
| **An open item in `TRANSLATION.md` §9** | A known-unresolved semantic question; every mapping built near it may need redoing | Read it before starting work in that area |
| **A hot, branch-dense function** | Every change to it is a review the reviewer cannot fully perform | `functions_over_branch_budget` crossed with the hotspot list |
| **Doc or generated-artifact drift** | A doc that claims what the code does not do is a wrong answer at a different altitude | CI catches the generated ones; prose is on us |

## Recording it

**No `TODO` comments.** The repo currently contains **zero** `TODO`, `FIXME`,
`HACK` and `XXX` markers, and that is worth keeping. A TODO is a debt entry with
no owner, no cost, no trigger and no way to find it again; it ages into
decoration. Two places take its place:

1. **The tracker**, for anything needing a decision. One issue, labelled
   `debt`, in this shape:

   ```
   Title:   <the thing>, in <file/area>
   Costs:   what every change in this area pays because of it
   Trigger: what should make us pay it (the next change to X; the ledger crossing N)
   Exit:    the observable state that means it is done — a metric, a deleted file,
            a passing test that does not exist yet
   Tier:    Safe | Careful | Risky  (see the risk ladder)
   ```

   The `Costs`/`Trigger`/`Exit` lines are the whole point: an entry that cannot
   name what it costs is not debt, it is an opinion about style.

2. **The metrics report**, for anything mechanical. `tools/maintenance_metrics.py`
   *is* the register for suppressions, over-budget modules and functions, and
   hotspots — it never goes stale and nobody has to maintain it. If a debt can be
   counted, count it there instead of writing it down.

A comment in the code stays the right answer for one case: explaining a **trap**
at the place someone would otherwise "fix" it — the repo's existing style
(`# KQL weeks start Sunday; date_trunc('week') starts Monday`). That is a
warning, not a debt entry.

## Paying it

**Pay debt in the hotspot you are already touching.** The Boy-Scout instinct —
leave the campsite cleaner — is right about direction and wrong about scope: an
unrelated cleanup smuggled into a feature diff breaks M1, pushes the diff past
the [review budget](metrics.md#7-diff-discipline--is-change-arriving-in-reviewable-pieces),
and makes both halves harder to judge. The version that works:

- **In the file you are changing anyway**, and **in its own commit, first.** That
  is preparatory refactoring: make the change easy, then make the easy change.
- **Never in a file you have no other reason to touch**, unless a debt entry with
  a named trigger says now.
- **Never as a drive-by in a mapping PR.** The mapping's reviewer is looking for
  a semantic trap; a rename in the same diff spends the attention that was
  supposed to catch it.

Payment order, when nothing else decides it:

1. Anything that makes a **check stop checking** (a green that is not green).
2. Debt in the **top two hotspots** — it charges interest every week.
3. **Missing trap tests** for mappings that already exist, worst family first.
4. Everything else, when its trigger fires.

## Deprecating and removing

The public surface is a promise (M12, and the
[public-API review area](../code-review/public-api-and-typing.md)). Inside a
layer, rename freely — that is a Safe refactor. Across the boundary
(`__init__.py` exports, the `kusto/` client shape, CLI flags, error types), the
sequence is:

1. Keep the old name working, delegating to the new one.
2. Say so in the release notes — they are the changelog here
   (CONTRIBUTING → Releasing).
3. Remove it in a later release, in its own commit, never bundled with the
   feature that replaced it.

A `DeprecationWarning` is appropriate for a Python API that a consumer's test
suite can surface. It is *not* appropriate anywhere it could reach a query
result: this project's contract is that unsupported things raise, and warnings
that nobody reads are how a wrong answer gets shipped.

Dead code is the opposite case and needs no ceremony: **delete it** (M7). It is
in git. A private helper with no callers, a branch made unreachable by an earlier
`raise`, a compatibility shim for a Python version below the floor, a fixture no
test loads — all provable by [the gate](refactoring-playbook.md#the-gate) and
free to review.

## Deciding not to pay

A written decision *not* to pay is a legitimate, and underused, outcome. Close
the issue with the reason — "this file has been touched twice in a year; the
interest does not cover the risk of restructuring the emitter" — rather than
leaving it open forever. An unbounded debt backlog is itself a maintenance cost:
it has to be re-read every planning pass to rediscover that nothing in it
matters.
