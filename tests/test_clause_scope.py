"""L5 trap tests — what one clause's assignments can see (R21).

KQL evaluates a clause's assignments against the operator's **input**, not
left-to-right against each other. So a name the same clause binds is not in
scope for it, and Kusto answers SEM0100, *"Failed to resolve scalar expression
named 'a'"*::

    datatable(x:long)[10] | extend a = x + 1, b = a + 1        refused
    datatable(x:long)[10] | extend a = x + 1 | extend b = a + 1  fine, 12

We answered 12 for the first — a query that runs here and fails in production.

**The half that must not change** is the one that looks like the bug. Where the
assigned name is *also an input column*, the reference resolves to the input,
both engines agree, and the answer is deliberately not the sequential one:

    datatable(x:long)[10] | extend x = x + 1, b = x + 1  ->  x=11, b=11

`b` reads the pre-extend `x`. A fix that "repaired" the first case by making
assignments sequential would have broken this one, which is why the check is
`introduced - input` and not `introduced`.

The `summarize` variant of the same rule is that its keys and aggregates share
one scope: `summarize s = sum(a) by a = x` is refused even though `a` is
introduced after the reference in the query text. All measured on the emulator.
"""

from __future__ import annotations

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")

T = "datatable(x:long, y:long)[10, 20]"


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    return c


@pytest.mark.parametrize(
    "tail",
    [
        "extend a = x + 1, b = a + 1",
        "project a = x + 1, b = a + 1",
        "distinct a = x + 1, b = a + 1",
        "summarize c = count() by a = x, b = a + 1",
        # The reference need not be top-level.
        "extend a = x + 1, b = a * a",
        "extend a = x + 1, b = strcat(tostring(a), 'z')",
        # Order-insensitive: `a` is introduced *after* the reference here.
        "summarize s = sum(a) by a = x",
        "summarize a = sum(x) by b = a",
    ],
)
def test_a_same_clause_reference_is_refused(tail: str) -> None:
    with pytest.raises(duckdb_kql.KqlUnsupportedError) as exc:
        duckdb_kql.to_sql(f"{T} | {tail}")
    assert "SEM0100" in str(exc.value)


@pytest.mark.parametrize(
    ("tail", "expected"),
    [
        # Replacing a column under its own name: the reference is to the INPUT.
        ("extend x = x + 1, b = x + 1", [(11, 20, 11)]),
        ("project x = x + 1, b = x + 1", [(11, 11)]),
        # Two independent assignments.
        ("extend a = x + 1, b = y + 1", [(10, 20, 11, 21)]),
        ("extend a = x + 1, b = 2", [(10, 20, 11, 2)]),
        # Split across clauses — this is how the refused query is written.
        ("extend a = x + 1 | extend b = a + 1", [(10, 20, 11, 12)]),
        # summarize shapes that stay legal.
        ("summarize c = count() by a = x", [(10, 1)]),
        ("summarize s = sum(x) by a = y", [(20, 10)]),
        ("summarize s = sum(x), t = sum(y) by x", [(10, 10, 20)]),
        ("summarize c = count() by y, z = x", [(20, 10, 1)]),
        ("distinct x, y", [(10, 20)]),
    ],
)
def test_the_shapes_that_must_keep_working(con, tail: str, expected: list) -> None:
    assert duckdb_kql.kql(con, f"{T} | {tail}").fetchall() == expected


def test_an_unnamed_expression_binds_nothing(con) -> None:
    """`project c` is a *reference*, not an assignment, even where `c` is unknown.

    Counting unnamed entries as bindings turned an ordinary pass-through into a
    forward reference — reporting SEM0100 for what is at worst an unknown
    column. The support-matrix probe caught it, over a schema that lists
    neither name.
    """
    sql = str(duckdb_kql.to_sql("T | project ['my col'], id", schema={"T": ["a", "b"]}))
    assert '"my col"' in sql and '"id"' in sql


def test_the_check_needs_a_schema(con) -> None:
    """Without the input column list the two cases cannot be told apart.

    Stated so the limit is visible: over a bare table with no schema this
    translates rather than refusing. `duckdb_kql.kql()` always has a schema.
    """
    assert "SELECT" in str(duckdb_kql.to_sql("Bare | extend a = x + 1, b = a + 1"))


# ---------------------------------------------------------------------------
# An aggregate in a group key (SEM0237)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tail",
    [
        "summarize count() by bin(count(), 1)",
        "summarize x = max(C) by count()",
        "summarize x = max(C) by min(C)",
        "summarize x = max(C) by sum(C) + 1",
    ],
)
def test_an_aggregate_in_a_by_key_is_refused(tail: str) -> None:
    """The aggregate *list* was guarded since `sum(sum(x))`; the keys were not.

    They reached DuckDB's binder, which answers "GROUP BY clause cannot contain
    aggregates" — a message about SQL, for a query written in KQL.
    """
    with pytest.raises(duckdb_kql.KqlUnsupportedError) as exc:
        duckdb_kql.to_sql(f"datatable(C:long)[1,2] | {tail}")
    assert "SEM0237" in str(exc.value)


def test_an_aggregate_in_the_aggregate_list_is_still_fine(con) -> None:
    q = "datatable(C:long)[1,2] | summarize x = max(C), y = round(sum(C), 1) by z = C"
    assert sorted(duckdb_kql.kql(con, q).fetchall()) == [(1, 1, 1.0), (2, 2, 2.0)]
