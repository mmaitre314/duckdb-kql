"""L5 trap tests — ``range`` source, the ``in`` family, and ``render``.

Three small features whose traps are all of the "looks equivalent" kind:

* ``range`` is **inclusive of both endpoints**. DuckDB has a function literally
  named ``range`` that *excludes* the stop, so the obvious mapping is wrong;
  ``generate_series`` is the inclusive one.
* ``in~`` / ``!in~`` are case-**IN**sensitive, matching `=~` rather than `==`.
* ``render`` is a visualization directive. The emulator returns the primary
  result table unchanged, so dropping it is correct rather than a shortcut —
  verified below by comparing against the same query without it.
"""

from __future__ import annotations

import datetime as dt

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    return c


def _rows(con, kql):
    return duckdb_kql.sql(con, kql).fetchall()


# --- range -----------------------------------------------------------------
def test_range_includes_both_endpoints(con) -> None:
    """DuckDB's `range()` excludes the stop; `generate_series` includes it."""
    assert _rows(con, "range x from 1 to 5 step 1") == [(1,), (2,), (3,), (4,), (5,)]


def test_range_with_a_step_that_overshoots(con) -> None:
    assert _rows(con, "range x from 1 to 5 step 2") == [(1,), (3,), (5,)]


def test_backwards_range_is_empty(con) -> None:
    assert _rows(con, "range x from 1 to 0 step 1") == []


def test_range_over_datetimes(con) -> None:
    rows = _rows(
        con, "range t from datetime(2007-01-01) to datetime(2007-01-03) step 1d"
    )
    assert rows == [
        (dt.datetime(2007, 1, 1),),
        (dt.datetime(2007, 1, 2),),
        (dt.datetime(2007, 1, 3),),
    ]


def test_range_feeds_the_rest_of_the_pipeline(con) -> None:
    assert _rows(con, "range x from 1 to 3 step 1 | summarize sum(x)") == [(6,)]


# --- in / !in / in~ / !in~ -------------------------------------------------
@pytest.mark.parametrize(
    ("predicate", "expected"),
    [
        ("s in ('a','c')", ["a", "c"]),
        ("s !in ('a')", ["B", "c"]),
        # `~` is case-INsensitive: 'B' matches 'b'.
        ("s in~ ('b')", ["B"]),
        ("s !in~ ('b')", ["a", "c"]),
        # Case-sensitive `in` must NOT match 'B' against 'b'.
        ("s in ('b')", []),
    ],
)
def test_in_family_case_sensitivity(con, predicate: str, expected: list[str]) -> None:
    rows = _rows(con, f"datatable(s:string)['a','B','c'] | where {predicate}")
    assert sorted(r[0] for r in rows) == sorted(expected)


def test_in_with_numbers(con) -> None:
    rows = _rows(con, "datatable(x:int)[1,2,3] | where x in (1,3)")
    assert sorted(r[0] for r in rows) == [1, 3]


def test_in_accepts_a_tabular_right_hand_side(con) -> None:
    """`x in (T | project col)` — the reason grammar PATCH 001 exists."""
    con.execute("CREATE TABLE Big(State VARCHAR)")
    con.execute("INSERT INTO Big VALUES ('TEXAS'),('OHIO')")
    con.execute("CREATE TABLE Ev(State VARCHAR, n INTEGER)")
    con.execute("INSERT INTO Ev VALUES ('TEXAS',1),('MAINE',2),('OHIO',3)")

    rows = _rows(con, "let big = Big | project State; Ev | where State in (big)")
    assert sorted(r[0] for r in rows) == ["OHIO", "TEXAS"]

    rows = _rows(con, "let big = Big | project State; Ev | where State !in (big)")
    assert [r[0] for r in rows] == ["MAINE"]


def test_in_tabular_is_case_insensitive_with_tilde(con) -> None:
    con.execute("CREATE TABLE B2(s VARCHAR)")
    con.execute("INSERT INTO B2 VALUES ('texas')")
    con.execute("CREATE TABLE E2(s VARCHAR)")
    con.execute("INSERT INTO E2 VALUES ('TEXAS'),('MAINE')")
    rows = _rows(con, "let b = B2 | project s; E2 | where s in~ (b)")
    assert [r[0] for r in rows] == ["TEXAS"]


# --- render ----------------------------------------------------------------
@pytest.mark.parametrize(
    "clause",
    [
        "render barchart",
        "render timechart",
        "render treemap with(title='Storm Events')",
        "render piechart with (xtitle='a', ytitle='b')",
    ],
)
def test_render_leaves_the_result_untouched(con, clause: str) -> None:
    base = "datatable(x:int, y:int)[1,10, 2,20] | project x, y"
    plain = duckdb_kql.sql(con, base)
    rendered = duckdb_kql.sql(con, f"{base} | {clause}")
    assert list(plain.columns) == list(rendered.columns)
    assert plain.fetchall() == rendered.fetchall()


# --- summarize name collisions ---------------------------------------------
def test_duplicate_aggregate_names_get_kql_suffixes(con) -> None:
    """KQL suffixes with no separator (`set_y1`); DuckDB would emit `set_y_1`."""
    rel = duckdb_kql.sql(
        con,
        "range x from 1 to 2 step 1 | extend y = iff(x == 1, real(null), real(5)) "
        "| summarize make_set(y), make_set(y)",
    )
    assert list(rel.columns) == ["set_y", "set_y1"]
