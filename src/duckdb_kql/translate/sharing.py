"""Bind a repeated sub-expression to a column instead of emitting it twice.

A scalar ``let`` is substitutional — measured on the emulator, ``let r = rand()``
read three times answers three different numbers — so the lowerer inlines each
binding at every reference. That is correct and it is also quadratic at best:
a binding read twice doubles, and a chain of ten such bindings is 2^10 = 1,024
copies of the first one. A reported query of 13 KB of KQL translated to 1.39 MB
of SQL this way, and DuckDB spent seconds binding a statement whose *text* was
the whole problem.

The lowerer leaves the sharing visible: substitution puts the **same object** at
every reference, so the IR is a DAG and a repeated sub-expression is one node
reached twice, not two equal nodes. This module reads that, and hands the
repeated node a column of its own::

    SELECT <expr, with "_kqlbind0" where the node was>
    FROM (SELECT *, <the node> AS "_kqlbind0" FROM _s0)

**The shape matters more than the idea.** Three encodings of "compute it once"
were measured over a million rows, at chain depth 20:

* inline, as today — 101 MB of SQL, and unusable long before that;
* one ``SELECT`` whose later items read earlier aliases — 2.1 KB of SQL and no
  faster than inline, because DuckDB resolves a lateral alias by *substituting*
  it, arriving back at the expression this module exists to avoid;
* a scalar subquery per binding — 2.2 KB, and slower still: correlated, so each
  level costs a decorrelation.

Only the nested derived table above is both small and fast (0.38s where inline
at depth 14 took 4.3s), and it is flat at depth 5 where the others already lose.

**What may not be bound.** Two guards, and neither is theoretical:

* **Volatility.** Binding collapses N evaluations into one, which is precisely
  the difference `rand()` shows. No volatile KQL function has a DuckDB mapping
  today — that is why this is safe at all right now — and
  ``tests/test_let_sharing.py`` fails the day one gains one.
* **Conditional evaluation.** A binding becomes a column, and a column is
  computed for every row whether or not the `iff` that reads it takes that
  branch. Hoisting an expression out of an untaken branch can turn an answer
  into a DuckDB error — that is not a hypothetical, it is the bug
  :func:`~duckdb_kql.translate._render_parse_ipv4` carries a ``TRY_CAST`` to
  avoid.

  The first version answered that by **refusing** to bind anything read only
  inside a branch, and a tester found the hole in a day: wrapping the result of
  a ten-step chain in one `iff` puts every step behind a branch, and the whole
  2^n expansion came back — 1,024 copies, 366 KB, on a query one `iff` away
  from the shape that was fixed. So the branch is **rebuilt around the
  binding** instead::

      iff(c, x, BIG)  ->  CASE WHEN c THEN NULL ELSE BIG END AS "_kqlbind0"

  `BIG` runs on exactly the rows it ran on before, and the protection is the
  same one. Which branch a node sits behind is the longest common prefix of the
  paths that reach it, so a node read in a condition *and* in a branch is
  unconditional, and one read in both arms of the same `iff` is too — it is
  needed either way. `iff`, `iif` and `case` are rebuilt; `coalesce` and
  `and`/`or` are not, and a node reachable only through one of those stays
  inlined (see :data:`_OPAQUE_FROM`).
"""

from __future__ import annotations

import contextlib
import dataclasses
from collections.abc import Iterable, Iterator, Sequence
from typing import Any

from .. import ir

#: KQL functions whose value may differ between two evaluations in one row.
#: Binding one would turn N draws into one. None of these has a DuckDB mapping
#: today; the list is here so that adding one is a decision rather than an
#: accident, and `tests/test_let_sharing.py` notices if it stops being true.
#:
#: `now()` and `ago()` are deliberately absent: Kusto fixes `now()` for the
#: query and DuckDB fixes it for the transaction, so two evaluations already
#: agree and binding changes nothing.
VOLATILE: frozenset[str] = frozenset(
    {"rand", "new_guid", "ingestion_time", "current_principal", "guid"}
)

#: Function name -> the first argument position that is **not** evaluated for
#: every row. `iff(c, a, b)` renders as `CASE WHEN c THEN a ELSE b END`, so `c`
#: is evaluated and `a`/`b` are not; for `case` only the first predicate is
#: unconditional. A name absent from here evaluates all of its arguments.
#:
#: These two are *reconstructible*: given the branch a node sits in, the CASE
#: that selects it can be rebuilt around the binding, which is what lets a
#: repeat inside a branch be bound at all (see :func:`_under_guard`).
_LAZY_FROM: dict[str, int] = {"iff": 1, "iif": 1, "case": 1}

#: Lazy positions whose selecting condition this module does **not** rebuild.
#: `coalesce` renders as a chain over each argument's *emptiness* rather than
#: over a predicate the IR holds, and `and`/`or` are wrapped by KQL's
#: three-valued null semantics; reproducing either here would be a second
#: implementation of a rule that already lives in the emitter, free to drift.
#: A repeat reachable only through one of these stays inlined, as everything
#: did before guards existed.
_OPAQUE_FROM: dict[str, int] = {"coalesce": 1}

#: A path step: the node whose branch was taken, and which argument of it. The
#: id travels alongside because the IR is frozen and compares by value, and two
#: *equal* `iff`s in one expression are two different branches.
Step = tuple[int, Any, int]
Path = tuple[Step, ...]

#: The step that stands for "reached through a lazy position this module will
#: not rebuild". Any path containing it is unbindable.
_OPAQUE: Step = (0, None, -1)

#: The duplication a binding must remove to be worth a derived table, in
#: characters of SQL. A stage costs about forty characters plus a reference per
#: use, and a subquery boundary is not free at run time either, so binding a
#: short expression is a pessimisation dressed as an optimisation. Two hundred
#: is roughly one screen line of SQL, and it leaves the whole frozen corpus
#: byte-identical — no case there repeats anything this large.
#:
#: Greedy and per-node, which looks like it leaves the doubling in place for a
#: chain of *small* steps: `a + a` with a short `a` saves too little to bind.
#: It does not, and the reason is the one that makes a threshold safe at all —
#: the inlined text doubles along with the chain, so within two or three steps
#: one copy is over the threshold by itself and binds. The gap between bindings
#: is bounded by this number, so the growth is linear whatever the unit size:
#: measured at 415 characters for a five-step doubling chain and 2,939 for a
#: forty-step one, against 2^40 copies unbound.
MIN_SAVING = 200

#: Slot names. Doubled up if a column of the same name is in scope — see
#: :func:`_prefix`.
_SLOT = "_kqlbind"


@dataclasses.dataclass
class Scope:
    """One operator's bindings: what was bound, and what to select from."""

    #: id(node) -> the column standing in for it, consulted by `render_expr`.
    slots: dict[int, str] = dataclasses.field(default_factory=dict)
    #: The slot names, in the order their stages nest.
    names: list[str] = dataclasses.field(default_factory=list)
    #: The FROM the operator should now read — *prev*, wrapped once per slot.
    source: str = ""


_ACTIVE: list[Scope] = []


def slot_for(node: object) -> str | None:
    """The column standing in for *node*, if the operator being rendered bound it.

    Called from `render_expr` before it does anything else, so every path into
    a bound node — an argument, an operand, a regex position — picks the column
    up without each renderer having to know this module exists.
    """
    if not _ACTIVE:
        return None
    return _ACTIVE[-1].slots.get(id(node))


def bind_repeats(
    exprs: Sequence[object], prev: str, taken: Iterable[str] | None
) -> Scope:
    """Plan an operator's bindings and push the scope they render under.

    *exprs* are the operator's expressions, *prev* the FROM it would have read,
    and *taken* the column names in scope, which a slot may not shadow. **None
    means the columns are not known, and then nothing is bound** — not as
    caution, as a measured necessity. The stages carry the input through with
    `SELECT *`, so a column already called ``_kqlbind0`` would come through
    beside the slot of that name and DuckDB resolves the reference to the first
    of the two without a word: ``T | project r = f(s)`` over a table with such a
    column answered that column's value instead of `f(s)`. A wrong answer, and a
    quiet one. Layer 1 always has a schema; Layer 0 without one gives up the
    binding rather than guess.

    The caller renders under the returned scope and then calls :func:`release`.
    """
    from . import render_expr  # circular at module scope: render_expr needs this

    scope = Scope(source=prev)
    if taken is None:
        _ACTIVE.append(scope)
        return scope

    counts, guards = _reach(exprs)
    reserved = set(taken)
    for expr in exprs:
        reserved |= _column_names(expr)
    prefix = _prefix(reserved)

    _ACTIVE.append(scope)
    try:
        for node in _postorder(exprs):  # a node is planned after what it reads
            if counts[id(node)] < 2 or not _bindable(node):
                continue
            guard = guards[id(node)]
            if not _rebuildable(node, guard):
                continue
            sql = render_expr(node)  # already reading the slots planned above
            if (counts[id(node)] - 1) * len(sql) < MIN_SAVING:
                continue
            name = f"{prefix}{len(scope.names)}"
            bound = _under_guard(sql, guard, render_expr)
            scope.slots[id(node)] = name
            scope.names.append(name)
            scope.source = f'(SELECT *, {bound} AS "{name}" FROM {scope.source})'
    except Exception:
        _ACTIVE.pop()
        raise
    return scope


def _rebuildable(node: object, guard: Path) -> bool:
    """Whether *node*'s selecting branch can be put back around its binding.

    Two ways it cannot. The path may run through a lazy position this module
    does not reconstruct (`_OPAQUE_FROM`, `and`/`or`). Or the node may appear
    **inside one of the conditions that select it** — rendering the guard would
    then expand the very expression being bound, trading one duplication for
    another. The common-prefix rule makes that nearly unreachable, since an
    occurrence in a condition contributes a path that stops short of the step
    below it; the check is here because "nearly" is not a guarantee and the
    failure would be silent bloat rather than a wrong answer.
    """
    for _, owner, index in guard:
        if owner is None:
            return False
        for condition in _conditions_before(owner, index):
            if any(child is node for child in _descend(condition)):
                return False
    return True


def _conditions_before(owner: ir.FunctionCall, index: int) -> list[object]:
    """The predicates that must be evaluated to reach ``owner.args[index]``.

    `iff(c, a, b)` reaches either branch through `c` alone. `case(p1, v1, p2,
    v2, …, else)` reaches argument *i* through every predicate at an even
    position before it — `p2` is itself only evaluated once `p1` has declined,
    which is why the conditions nest below rather than being ANDed together.
    """
    if owner.name.lower() in ("iff", "iif"):
        return [owner.args[0]]
    return [owner.args[i] for i in range(0, index, 2)]


def _under_guard(sql: str, guard: Path, render: Any) -> str:
    """Wrap *sql* in the CASE that selects the branch it was read from.

    This is what lets a repeat inside a branch be bound at all. The binding is a
    column, and a column is computed for every row — so hoisting an expression
    out of an untaken branch can raise a DuckDB error on a row that used to
    return an answer, which is the bug `parse_ipv4` carries `TRY_CAST` for.
    Rebuilding the branch keeps the evaluation exactly where it was::

        iff(c, x, BIG)  ->  CASE WHEN c THEN NULL ELSE BIG END AS "_kqlbind0"

    `BIG` runs on precisely the rows it ran on before, and on the others the
    column is null — which nothing reads, because the reference sits in the
    branch that was not taken.

    The **NULL arm is not decoration**: `CASE WHEN NOT c THEN BIG END` looks
    equivalent and is not, because `c` may be null. KQL's `iff` takes the ELSE
    for a null predicate, `NOT null` is null, and the binding would then skip an
    evaluation the query performs. Mirroring the original CASE arm for arm is
    the only version that cannot get the three-valued case wrong.

    The conditions are rendered the same way the expression renders them, so
    the null handling around a comparison comes along and the arm chosen here
    is the arm chosen there.
    """
    for _, owner, index in reversed(guard):
        rendered = [render(c) for c in _conditions_before(owner, index)]
        if _selects_on_match(owner, index):
            # The arm is taken when the last predicate holds; the ones before
            # it had to decline to get here.
            arms = [f"WHEN {c} THEN NULL" for c in rendered[:-1]]
            arms.append(f"WHEN {rendered[-1]} THEN {sql}")
            sql = f"CASE {' '.join(arms)} END"
        else:
            # Reached by falling through every predicate above it.
            arms = [f"WHEN {c} THEN NULL" for c in rendered]
            sql = f"CASE {' '.join(arms)} ELSE {sql} END"
    return sql


def _selects_on_match(owner: ir.FunctionCall, index: int) -> bool:
    """Whether ``owner.args[index]`` is taken when the last predicate **holds**.

    True for a value branch — `iff`'s THEN, and `case`'s odd positions. False
    for the arms reached by falling through: `iff`'s ELSE, `case`'s later
    predicates and its final default.
    """
    if owner.name.lower() in ("iff", "iif"):
        return index == 1
    return index % 2 == 1


def release() -> None:
    """Pop the scope :func:`bind_repeats` pushed. Always paired with it."""
    _ACTIVE.pop()


@contextlib.contextmanager
def barrier() -> Iterator[None]:
    """Hide the enclosing scopes while a *nested* query is translated.

    `toscalar(T | ...)` renders a whole query from inside an expression, and
    that query has its own FROM. A slot bound by the operator around it names a
    column that does not exist down there, so without this the inner SQL would
    reference `"_kqlbind0"` and fail to bind — which needs a `let` read on both
    sides of the `toscalar` to happen at all, and would therefore have been
    found by a user rather than by this test suite.
    """
    global _ACTIVE
    outer, _ACTIVE = _ACTIVE, []
    try:
        yield
    finally:
        _ACTIVE = outer


def _prefix(reserved: set[str]) -> str:
    """A slot prefix no column in scope can collide with.

    The stages carry the incoming columns through with ``SELECT *``, so a slot
    named like an existing column would produce two columns of that name and
    DuckDB would not complain — the silent kind of wrong. Lengthening the
    prefix until nothing in scope starts with it is enough, because a collision
    needs the *whole* name to match.
    """
    prefix = _SLOT
    while any(name.startswith(prefix) for name in reserved):
        prefix = "_" + prefix
    return prefix


def _bindable(node: object) -> bool:
    """Whether *node* may be computed once and read as a column."""
    if not isinstance(node, ir.Expr):
        return False
    if isinstance(node, (ir.Literal, ir.ColumnRef, ir.Parameter, ir.Wildcard)):
        return False  # one token, or `*` — a stage could only add characters
    for child in _descend(node):
        if isinstance(child, ir.RenderedAggregate):
            # Already-rendered aggregate SQL. An aggregate inside the derived
            # table would be computing over the wrong rows, if it bound at all.
            return False
        if isinstance(child, ir.FunctionCall) and child.name.lower() in VOLATILE:
            return False
    return True


def _descend(node: object) -> Iterable[object]:
    """*node* and every dataclass below it, by field rather than by isinstance.

    Walking generically is the point: a node type added later is descended into
    without anybody remembering to come back here, which is the failure
    `_substitute` documents — a walker that does not know about a new node
    silently stops looking inside it.
    """
    stack = [node]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if isinstance(current, (list, tuple)):
            stack.extend(current)
            continue
        if not dataclasses.is_dataclass(current) or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        for field in dataclasses.fields(current):
            stack.append(getattr(current, field.name))


def _column_names(node: object) -> set[str]:
    """Every column name read below *node*."""
    return {n.name for n in _descend(node) if isinstance(n, ir.ColumnRef)}


def _reach(exprs: Sequence[object]) -> tuple[dict[int, int], dict[int, Path]]:
    """How often each node is reached, and the branch it is reached *behind*.

    The counts are by identity. Two nodes that are *equal* but were written
    separately are two expressions, and merging them would be this module
    deciding something it has no business deciding — the sharing it acts on is
    the sharing the lowerer put there.

    The guard is the **longest common prefix** of every path that reaches the
    node, which is the branch selection all its occurrences agree on. A node
    read once in an `iff`'s condition and once in its ELSE has the empty prefix
    and is unconditional; one read only inside the ELSE keeps that step. Taking
    the common prefix over-approximates — it can name a branch under which some
    occurrence would not in fact have been evaluated — and it over-approximates
    in the safe direction, because binding at a *shallower* point than the
    reads never skips an evaluation that used to happen.

    A node is re-walked when its prefix **shrinks**, since its children's
    guards are derived from it. The prefix can only ever get shorter, so this
    terminates, and in the shapes that reach here at all it fires once or twice.
    """
    counts: dict[int, int] = {}
    guards: dict[int, Path] = {}

    def walk(node: object, path: Path) -> None:
        if isinstance(node, (list, tuple)):
            for item in node:
                walk(item, path)
            return
        if not dataclasses.is_dataclass(node):
            return
        counts[id(node)] = counts.get(id(node), 0) + 1
        known = guards.get(id(node))
        merged = path if known is None else _common(known, path)
        if known is not None and merged == known:
            return  # nothing below it can change
        guards[id(node)] = merged
        for child, step in _children(node):
            walk(child, merged if step is None else (*merged, step))

    for expr in exprs:
        walk(expr, ())
    return counts, guards


def _common(left: Path, right: Path) -> Path:
    """The longest prefix the two paths share, compared by branch identity."""
    shared = 0
    for one, two in zip(left, right, strict=False):
        if one[0] != two[0] or one[2] != two[2]:
            break
        shared += 1
    return left[:shared]


def _postorder(exprs: Sequence[object]) -> list[Any]:
    """Every node once, children before parents.

    Separate from :func:`_reach` because that one re-walks, and a node appended
    on a re-walk would land after the parent that reads it — which is exactly
    the order the binding loop must not have.
    """
    order: list[Any] = []
    seen: set[int] = set()

    def walk(node: object) -> None:
        if isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
            return
        if not dataclasses.is_dataclass(node) or id(node) in seen:
            return
        seen.add(id(node))
        for child, _ in _children(node):
            walk(child)
        order.append(node)

    for expr in exprs:
        walk(expr)
    return order


def _children(node: Any) -> Iterable[tuple[object, Step | None]]:
    """*node*'s children, each with the branch step that reaches it, if any."""
    if isinstance(node, ir.FunctionCall):
        name = node.name.lower()
        opaque = _OPAQUE_FROM.get(name)
        lazy = _LAZY_FROM.get(name)
        for index, arg in enumerate(node.args):
            if opaque is not None and index >= opaque:
                yield arg, _OPAQUE
            elif lazy is not None and index >= lazy:
                yield arg, (id(node), node, index)
            else:
                yield arg, None
        return
    if isinstance(node, ir.BinaryOp) and node.op.lower() in ("and", "or"):
        # DuckDB may evaluate either side, so this is the cautious reading, not
        # a claim about short-circuiting. It only ever withholds a binding.
        yield node.left, None
        yield node.right, _OPAQUE
        return
    for field in dataclasses.fields(node):
        yield getattr(node, field.name), None
