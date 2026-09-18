"""L5 trap tests — `mv-expand` of a computed expression (R18).

`mv-expand item = parse_json(payload)` was refused: the operator rewrites a
column **in place**, and a computed expression is not a column to rewrite. The
reporter's own workaround named the fix — `extend item = parse_json(payload) |
mv-expand item` — and the emulator confirms the two are the same query, rows
and column order alike, including when the alias shadows the column the
expression reads.

So this desugars in the lowerer rather than teaching the emitter a second shape.
`mv-expand`'s emission carries R18's whole weight — lockstep zipping, replace-
in-place, `to typeof` converting rather than declaring — and a rewrite that is
*measured* to be equivalent is cheaper to trust than a second path through it.
The tests below assert that equivalence directly, not just the row values,
because the desugar is only correct if it is indistinguishable.

**The unnamed form is refused.** Kusto accepts `mv-expand parse_json(a)` and
names the result by a rule that depends on the expression: measured, it takes
over the column it reads when it reads exactly one, and is `Column1` when it
reads two or none. Guessing which column an expression "reads" is how the wrong
one gets silently replaced, and the alias that avoids the whole question is one
word long.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlUnsupportedError

duckdb = pytest.importorskip("duckdb")

P = "datatable(payload:string)['[1,2]']"


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    return c


def _shape(con, kql):
    """Columns *and* rows — a desugar that loses a column still returns rows."""
    cursor = duckdb_kql.kql(con, kql)
    return [d[0] for d in cursor.description], cursor.fetchall()


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def test_the_reported_query_expands(con) -> None:
    columns, rows = _shape(con, f"{P} | mv-expand item = parse_json(payload)")
    assert columns == ["payload", "item"]
    assert len(rows) == 2


@pytest.mark.parametrize(
    "sugar,explicit",
    [
        (
            f"{P} | mv-expand item = parse_json(payload)",
            f"{P} | extend item = parse_json(payload) | mv-expand item",
        ),
        # The alias shadowing its own source — `extend` reads the *input* value
        # (R21), which is what makes the rewrite safe here.
        (
            f"{P} | mv-expand payload = parse_json(payload)",
            f"{P} | extend payload = parse_json(payload) | mv-expand payload",
        ),
        (
            f"{P} | mv-expand item = parse_json(payload) to typeof(long)",
            f"{P} | extend item = parse_json(payload) | mv-expand item to typeof(long)",
        ),
        (
            "datatable(a:string,b:string)['[1,2]','[3,4]'] "
            "| mv-expand x = parse_json(a), y = parse_json(b)",
            "datatable(a:string,b:string)['[1,2]','[3,4]'] "
            "| extend x = parse_json(a), y = parse_json(b) | mv-expand x, y",
        ),
        (
            "datatable(payload:string)['[1,2,3]'] "
            "| mv-expand item = parse_json(payload) limit 2",
            "datatable(payload:string)['[1,2,3]'] "
            "| extend item = parse_json(payload) | mv-expand item limit 2",
        ),
        (
            "datatable(payload:string)['[]'] | mv-expand item = parse_json(payload)",
            "datatable(payload:string)['[]'] "
            "| extend item = parse_json(payload) | mv-expand item",
        ),
    ],
)
def test_it_is_indistinguishable_from_the_workaround(con, sugar, explicit) -> None:
    """The claim the desugar rests on, asserted rather than assumed — and the
    empty-array row is here because R18's "no row alone" edge is exactly the
    kind of thing a rewrite loses quietly."""
    assert _shape(con, sugar) == _shape(con, explicit)


def test_a_computed_target_mixes_with_a_plain_one(con) -> None:
    """One `extend` lifted out, the other column left where it was — and the
    two still expand in lockstep rather than crossing (R18)."""
    columns, rows = _shape(
        con,
        "datatable(a:string,d:dynamic)['[1,2]',dynamic([7,8])] "
        "| mv-expand x = parse_json(a), d",
    )
    assert columns == ["a", "d", "x"]
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# What is still refused
# ---------------------------------------------------------------------------


def test_an_unnamed_computed_expression_is_refused(con) -> None:
    """Kusto answers this; the refusal is deliberate. Its name for the result
    is `a` here and `Column1` one probe over, and reproducing that means
    deciding which column an arbitrary expression "reads"."""
    with pytest.raises(KqlUnsupportedError, match="unnamed computed"):
        duckdb_kql.kql(con, f"{P} | mv-expand parse_json(payload)")


def test_the_refusal_says_what_to_write_instead(con) -> None:
    with pytest.raises(KqlUnsupportedError, match="give it a name"):
        duckdb_kql.kql(con, f"{P} | mv-expand parse_json(payload)")


# ---------------------------------------------------------------------------
# The plain-column path must be untouched
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "datatable(d:dynamic)[dynamic([1,2])] | mv-expand d",
        "datatable(d:dynamic)[dynamic([1,2])] | mv-expand x = d",
        "datatable(a:dynamic,b:dynamic)[dynamic([1,2]),dynamic([3,4])] | mv-expand a, b",
        "datatable(d:dynamic)[dynamic([1,2])] | mv-expand d to typeof(long)",
    ],
)
def test_a_bare_column_still_lowers_to_one_operator(con, query: str) -> None:
    """A column target lifts no `extend`, so the operator list is unchanged —
    checked on the IR, since the rows alone would not show a stray operator."""
    from duckdb_kql import ir
    from duckdb_kql.lower import lower

    operators = lower(query).operators
    assert not any(isinstance(op, ir.Extend) for op in operators), operators
    assert sum(isinstance(op, ir.MvExpand) for op in operators) == 1
