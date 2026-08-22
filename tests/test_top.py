"""L5 trap tests — the ``top`` operator.

`top N by X` is `sort by X | take N` fused into one step, and it inherits
`sort`'s two traps rather than SQL's defaults:

* **the default direction is descending** (R6), the opposite of `ORDER BY`;
* **null is the smallest value**, so it sorts first ascending and last
  descending — again the opposite of DuckDB's own `NULLS LAST` default.

Both are emitted explicitly rather than left to either engine. Measured on the
emulator: `top 3 by a` over `3, 1, null, 2, 5` returns `5, 3, 2`, and
`top 3 by a asc` returns `null, 1, 2`.

Which rows come back when the sort key **ties** is undefined in both engines
(R10), so no test here pins a tie.
"""

from __future__ import annotations

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")

#: A null in the middle and an unsorted key order, so a `top` that forgot the
#: direction or the null placement produces visibly different rows rather than
#: plausible ones.
DT = (
    "datatable(a:long, s:string, r:real)"
    "[3,'c',1.5, 1,'a',2.5, long(null),'n',real(null), 2,'b',0.5, 5,'e',9.5]"
)


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    return c


def _rows(con, clause):
    return duckdb_kql.kql(con, f"{DT} | {clause}").fetchall()


def _keys(con, clause):
    return [r[0] for r in _rows(con, clause)]


# ---------------------------------------------------------------------------
# R6 — direction and null placement
# ---------------------------------------------------------------------------


def test_the_default_direction_is_descending(con) -> None:
    """SQL's `ORDER BY` defaults to ascending; KQL's `top` does not."""
    assert _keys(con, "top 3 by a") == [5, 3, 2]
    assert _keys(con, "top 3 by a") == _keys(con, "top 3 by a desc")


def test_ascending_must_be_asked_for(con) -> None:
    """Null is the smallest value, so ascending puts it first."""
    assert _keys(con, "top 3 by a asc") == [None, 1, 2]


def test_null_placement_can_be_overridden(con) -> None:
    assert _keys(con, "top 3 by a asc nulls last") == [1, 2, 3]
    assert _keys(con, "top 3 by a desc nulls first") == [None, 5, 3]
    assert _keys(con, "top 3 by a asc nulls first") == [None, 1, 2]
    assert _keys(con, "top 3 by a desc nulls last") == [5, 3, 2]


def test_null_sorts_last_descending(con) -> None:
    """The whole table, so the null's position is visible rather than cut off."""
    assert _keys(con, "top 99 by a") == [5, 3, 2, 1, None]
    assert _keys(con, "top 99 by a asc") == [None, 1, 2, 3, 5]


# ---------------------------------------------------------------------------
# The count
# ---------------------------------------------------------------------------


def test_a_count_of_zero_returns_no_rows(con) -> None:
    assert _rows(con, "top 0 by a") == []


def test_a_negative_count_returns_no_rows(con) -> None:
    """Measured: Kusto answers an empty table. DuckDB *refuses* a negative
    LIMIT, so the count is clamped — an engine error where the reference engine
    returns rows is a divergence like any other."""
    assert _rows(con, "top -1 by a") == []
    assert "LIMIT 0" in duckdb_kql.to_sql(f"{DT} | top -1 by a")


def test_a_count_larger_than_the_table_returns_everything(con) -> None:
    assert len(_rows(con, "top 99 by a")) == 5


def test_a_count_of_one(con) -> None:
    assert _keys(con, "top 1 by a") == [5]


def test_a_non_literal_count_is_refused(con) -> None:
    """The count is an expression in the grammar. `top` used to be unsupported
    outright; accepting `top toint(x) by a` would put a column where DuckDB
    wants a constant."""
    with pytest.raises(duckdb_kql.KqlUnsupportedError):
        duckdb_kql.kql(con, f"{DT} | top a by a")


# ---------------------------------------------------------------------------
# Sort keys
# ---------------------------------------------------------------------------


def test_a_real_key(con) -> None:
    assert _rows(con, "top 2 by r") == [(5, "e", 9.5), (1, "a", 2.5)]


def test_a_string_key(con) -> None:
    assert _keys(con, "top 2 by s") == [None, 5]      # 'n', 'e'
    assert _keys(con, "top 2 by s asc") == [1, 2]     # 'a', 'b'


def test_an_expression_key(con) -> None:
    assert _rows(con, "top 2 by a * -1") == [(1, "a", 2.5), (2, "b", 0.5)]


def test_two_keys_are_a_syntax_error(con) -> None:
    """Kusto refuses it too — `top` takes exactly one ordered expression."""
    with pytest.raises(duckdb_kql.KqlSyntaxError):
        duckdb_kql.kql(con, f"{DT} | top 2 by a, s")


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def test_operators_after_top_see_only_the_kept_rows(con) -> None:
    assert _rows(con, "top 3 by a | count") == [(3,)]
    assert _rows(con, "top 3 by a | summarize n = count()") == [(3,)]


def test_a_filter_before_top(con) -> None:
    assert _keys(con, "where a > 1 | top 2 by a") == [5, 3]


def test_project_after_top(con) -> None:
    assert _rows(con, "top 2 by a | project s") == [("e",), ("c",)]


def test_top_can_be_re_sorted(con) -> None:
    assert _keys(con, "top 3 by a | sort by a asc") == [2, 3, 5]


def test_top_of_a_top(con) -> None:
    assert _keys(con, "top 4 by a asc | top 2 by a desc") == [3, 2]


def test_extend_after_top(con) -> None:
    assert _rows(con, "top 2 by a | extend z = a + 1 | project a, z") == [
        (5, 6), (3, 4)
    ]


# ---------------------------------------------------------------------------
# Emitted SQL
# ---------------------------------------------------------------------------


def test_it_is_one_cte_with_order_by_and_limit(con) -> None:
    """One operator, one CTE (TRANSLATION.md §1) — not a sort stage plus a
    take stage, which would read nothing like the query."""
    sql = duckdb_kql.to_sql(f"{DT} | top 3 by a")
    assert 'ORDER BY "a" DESC NULLS LAST LIMIT 3' in sql
    assert sql.count("ORDER BY") == 1


def test_the_direction_is_always_explicit(con) -> None:
    """Never left to DuckDB's default, which is the opposite of KQL's (R6)."""
    for clause, expected in (
        ("top 3 by a", 'DESC NULLS LAST'),
        ("top 3 by a asc", 'ASC NULLS FIRST'),
        ("top 3 by a asc nulls last", 'ASC NULLS LAST'),
        ("top 3 by a desc nulls first", 'DESC NULLS FIRST'),
    ):
        assert expected in duckdb_kql.to_sql(f"{DT} | {clause}")


# ---------------------------------------------------------------------------
# `x in (T | ... | top N by ...)` — a multi-column subquery
# ---------------------------------------------------------------------------
#
# Supporting `top` made `T | summarize … | top 5 by …` translatable, and that
# turned two corpus cases into "Subquery returns 2 columns - expected 1".
# KQL tests against the subquery's FIRST column and ignores the rest —
# measured: `x in (T)` where T is `(k, v)` matches on `k`, and a value of the
# wrong type for `k` is an error (SEM0025) rather than a fallback to `v`.


@pytest.fixture
def states(con):
    con.execute("CREATE TABLE S(State VARCHAR, n BIGINT)")
    con.execute("INSERT INTO S VALUES ('a',3),('b',2),('c',1),('z',9)")
    return con


def test_in_a_multi_column_subquery_uses_the_first_column(states) -> None:
    assert duckdb_kql.kql(
        states,
        "S | where State in (S | summarize m = count() by State | top 2 by m)"
        " | count",
    ).fetchall() == [(2,)]


def test_in_a_multi_column_tabular_let(states) -> None:
    assert duckdb_kql.kql(
        states,
        "let T = S | summarize m = count() by State | top 2 by m;"
        " S | where State in (T) | count",
    ).fetchall() == [(2,)]


def test_not_in_a_multi_column_subquery(states) -> None:
    assert duckdb_kql.kql(
        states,
        "S | where State !in (S | summarize m = count() by State | top 2 by m)"
        " | count",
    ).fetchall() == [(2,)]


def test_case_insensitive_in_over_a_multi_column_subquery(states) -> None:
    assert duckdb_kql.kql(
        states,
        "S | where State in~ (S | summarize m = count() by State | top 2 by m)"
        " | count",
    ).fetchall() == [(2,)]


def test_a_single_column_subquery_still_works(states) -> None:
    assert duckdb_kql.kql(
        states, "S | where State in (S | project State) | count"
    ).fetchall() == [(4,)]
