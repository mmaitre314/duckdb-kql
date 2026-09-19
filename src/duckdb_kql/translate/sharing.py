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
  avoid. So a node is bound only if it is *also* read somewhere the row already
  evaluates unconditionally, which means binding it adds no evaluation that was
  not happening.
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

#: Function name -> the argument positions that are **not** evaluated for every
#: row. `iff(c, a, b)` renders as `CASE WHEN c THEN a ELSE b END`, so `c` is
#: evaluated and `a`/`b` are not. For `case` and `coalesce` only the first
#: argument is unconditional; the rest are reached only if the ones before them
#: decline. A name absent from here evaluates all of its arguments.
_LAZY_FROM: dict[str, int] = {"iff": 1, "iif": 1, "case": 1, "coalesce": 1}

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

    counts, unconditional, order = _survey(exprs)
    reserved = set(taken)
    for expr in exprs:
        reserved |= _column_names(expr)
    prefix = _prefix(reserved)

    _ACTIVE.append(scope)
    try:
        for node in order:  # post-order: a node is planned after what it reads
            if counts[id(node)] < 2 or id(node) not in unconditional:
                continue
            if not _bindable(node):
                continue
            sql = render_expr(node)  # already reading the slots planned above
            if (counts[id(node)] - 1) * len(sql) < MIN_SAVING:
                continue
            name = f"{prefix}{len(scope.names)}"
            scope.slots[id(node)] = name
            scope.names.append(name)
            scope.source = f'(SELECT *, {sql} AS "{name}" FROM {scope.source})'
    except Exception:
        _ACTIVE.pop()
        raise
    return scope


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


def _survey(
    exprs: Sequence[object],
) -> tuple[dict[int, int], set[int], list[Any]]:
    """How often each node is reached, whether ever unconditionally, post-order.

    The counts are by identity. Two nodes that are *equal* but were written
    separately are two expressions, and merging them would be this module
    deciding something it has no business deciding — the sharing it acts on is
    the sharing the lowerer put there.
    """
    counts: dict[int, int] = {}
    unconditional: set[int] = set()
    order: list[Any] = []

    def walk(node: object, conditional: bool) -> None:
        if isinstance(node, (list, tuple)):
            for item in node:
                walk(item, conditional)
            return
        if not dataclasses.is_dataclass(node):
            return
        counts[id(node)] = counts.get(id(node), 0) + 1
        if not conditional:
            unconditional.add(id(node))
        if counts[id(node)] > 1:
            return  # its children were surveyed the first time through
        for child, lazy in _children(node):
            walk(child, conditional or lazy)
        order.append(node)

    for expr in exprs:
        walk(expr, False)
    return counts, unconditional, order


def _children(node: Any) -> Iterable[tuple[object, bool]]:
    """*node*'s children, each flagged if it is evaluated only conditionally."""
    if isinstance(node, ir.FunctionCall):
        lazy_from = _LAZY_FROM.get(node.name.lower())
        for index, arg in enumerate(node.args):
            yield arg, lazy_from is not None and index >= lazy_from
        return
    if isinstance(node, ir.BinaryOp) and node.op.lower() in ("and", "or"):
        # DuckDB may evaluate either side, so this is the cautious reading, not
        # a claim about short-circuiting. It only ever *withholds* a binding.
        yield node.left, False
        yield node.right, True
        return
    for field in dataclasses.fields(node):
        yield getattr(node, field.name), False
