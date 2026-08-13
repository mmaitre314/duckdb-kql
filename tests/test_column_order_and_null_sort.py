"""Two traps where the values are right and the *shape* is wrong.

Both were caught by review rather than by the suite, and both are the kind of
defect a green test misses: nothing raises, every number is correct, and the
answer is still not what Kusto gives.

1. **`extend` that overwrites a column moves it to the end.** KQL replaces in
   place. Column order is user-visible (TRANSLATION.md §1, §5) and a `join`
   downstream inherits it, so `['b','c','a']` is a wrong answer, not a cosmetic
   one.

2. **`sort` put nulls at the wrong end.** KQL treats null as the *smallest*
   value; the emitter had `ASC NULLS LAST` / `DESC NULLS FIRST`, which is null
   as the largest — exactly inverted. `TRANSLATION.md` §9 listed this as an
   open question while the code committed to an answer and the comments stated
   it as fact.

Both expectations come from the emulator:

    datatable(a:int, b:int, c:int) [1,2,3] | extend a = 99   -> columns a, b, c
    datatable(x:int) [3, int(null), 1] | sort by x asc       -> null, 1, 3
    datatable(x:int) [3, int(null), 1] | sort by x desc      -> 3, 1, null
"""

from __future__ import annotations

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    c.execute("CREATE TABLE T(a INTEGER, b INTEGER, c INTEGER); INSERT INTO T VALUES (1,2,3)")
    c.execute("CREATE TABLE N(x INTEGER); INSERT INTO N VALUES (3), (NULL), (1)")
    return c


# ---------------------------------------------------------------------------
# extend replaces in place
# ---------------------------------------------------------------------------


def test_extend_overwriting_a_column_keeps_its_position(con) -> None:
    rel = duckdb_kql.kql(con, "T | extend a = 99")
    assert list(rel.columns) == ["a", "b", "c"], (
        "a replaced column moved to the end; KQL replaces in place"
    )
    assert rel.fetchall() == [(99, 2, 3)]


def test_extend_overwriting_a_middle_column_keeps_its_position(con) -> None:
    """The end column is the easy case — `b` is the one that proves position."""
    rel = duckdb_kql.kql(con, "T | extend b = 99")
    assert list(rel.columns) == ["a", "b", "c"]
    assert rel.fetchall() == [(1, 99, 3)]


def test_extend_adding_a_column_appends(con) -> None:
    """The counterweight: in-place replacement must not stop new columns landing."""
    rel = duckdb_kql.kql(con, "T | extend d = 99")
    assert list(rel.columns) == ["a", "b", "c", "d"]
    assert rel.fetchall() == [(1, 2, 3, 99)]


def test_extend_replacing_and_adding_at_once(con) -> None:
    rel = duckdb_kql.kql(con, "T | extend b = 99, z = 7")
    assert list(rel.columns) == ["a", "b", "c", "z"]
    assert rel.fetchall() == [(1, 99, 3, 7)]


def test_extend_ordering_holds_without_a_connection(con) -> None:
    """A `datatable` carries its columns in the IR, so Layer 0 gets this right too."""
    sql = duckdb_kql.to_sql("datatable(a:int, b:int, c:int) [1,2,3] | extend a = 99")
    assert list(con.sql(str(sql)).columns) == ["a", "b", "c"]


def test_output_columns_agrees_with_what_runs(con) -> None:
    """`schema.output_columns` is what a downstream `join` renames against.

    It computed `kept + added` too, so a join after an overwriting `extend`
    inherited the wrong names — the reason this is not merely cosmetic.
    """
    from duckdb_kql.lower import lower  # noqa: PLC0415
    from duckdb_kql.schema import output_columns

    query = lower("T | extend a = 99")
    schema = duckdb_kql.engine.schema(con)
    assert output_columns(query, schema) == list(duckdb_kql.kql(con, "T | extend a = 99").columns)


# ---------------------------------------------------------------------------
# null is the smallest value
# ---------------------------------------------------------------------------


def test_ascending_sort_puts_nulls_first(con) -> None:
    rel = duckdb_kql.kql(con, "N | sort by x asc")
    assert [r[0] for r in rel.fetchall()] == [None, 1, 3], (
        "KQL treats null as the smallest value, so ascending puts it first"
    )


def test_descending_sort_puts_nulls_last(con) -> None:
    rel = duckdb_kql.kql(con, "N | sort by x desc")
    assert [r[0] for r in rel.fetchall()] == [3, 1, None]


def test_bare_sort_defaults_to_desc_and_nulls_last(con) -> None:
    """KQL's default direction is `desc` (R6); the null end follows from it."""
    rel = duckdb_kql.kql(con, "N | sort by x")
    assert [r[0] for r in rel.fetchall()] == [3, 1, None]


@pytest.mark.parametrize(
    "clause,expected",
    [
        ("x asc nulls first", [None, 1, 3]),
        ("x asc nulls last", [1, 3, None]),
        ("x desc nulls first", [None, 3, 1]),
        ("x desc nulls last", [3, 1, None]),
    ],
)
def test_an_explicit_nulls_clause_still_wins(con, clause: str, expected: list) -> None:
    """Fixing the default must not override what the query says outright."""
    rel = duckdb_kql.kql(con, f"N | sort by {clause}")
    assert [r[0] for r in rel.fetchall()] == expected
