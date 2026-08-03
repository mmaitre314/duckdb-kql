"""L5 trap tests — ``let`` bindings.

``let`` was the single largest blocker in the corpus (250 frozen cases), and it
used to be *refused* precisely because it is dangerous to get wrong: a `let` is
not a `QueryStatement`, so an implementation that counts query statements sees
one statement, translates it, and silently drops the binding — producing a query
that runs and returns the wrong rows.

Two binding shapes, resolved differently:

* **scalar** (``let x = 5``) — substituted into the IR, because a `let` is a
  query-scope binding rather than a column;
* **tabular** (``let T = X | where …``) — emitted as a named CTE, so a reference
  to it in the body needs no rewriting.
"""

from __future__ import annotations

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    c.execute("CREATE TABLE T(a INTEGER, s VARCHAR)")
    c.execute("INSERT INTO T VALUES (1,'x'),(5,'y'),(9,'x')")
    c.execute("CREATE TABLE R(a INTEGER, r VARCHAR)")
    c.execute("INSERT INTO R VALUES (1,'p'),(9,'q')")
    return c


def _rows(con, kql):
    rel = duckdb_kql.sql(con, kql)
    return sorted(rel.fetchall(), key=lambda r: tuple(str(x) for x in r))


def test_scalar_let_is_applied_not_dropped(con) -> None:
    """The regression this whole feature exists to prevent.

    Dropping the binding leaves `T | where a > x` with an unbound `x`; if that
    ever resolved to something, the query would return the wrong rows silently.
    """
    assert _rows(con, "let x = 5; T | where a > x") == [(9, "x")]
    assert _rows(con, "let x = 0; T | where a > x") == [(1, "x"), (5, "y"), (9, "x")]


def test_scalar_lets_may_reference_earlier_ones(con) -> None:
    assert _rows(con, "let x = 5; let y = x * 2; print y")[0][0] == 10


def test_scalar_let_in_every_operator_position(con) -> None:
    assert set(_rows(con, "let n = 5; T | extend b = a + n | project b")) == {
        (6,), (10,), (14,)
    }
    assert _rows(con, "let n = 1; T | summarize c = count() by g = a > n")[0] == (
        False, 1
    )


def test_tabular_let_becomes_a_usable_table(con) -> None:
    assert _rows(con, "let T2 = T | where a > 1; T2 | count") == [(2,)]


def test_tabular_let_alias(con) -> None:
    """`let A = T` aliases a table rather than binding a scalar."""
    assert _rows(con, "let A = T; A | count") == [(3,)]


def test_materialize_is_a_hint_and_unwraps(con) -> None:
    """materialize() caches in a distributed engine; it cannot change results."""
    plain = _rows(con, "let m = T | where a > 1; m | summarize count()")
    hinted = _rows(con, "let m = materialize(T | where a > 1); m | summarize count()")
    assert plain == hinted == [(2,)]


def test_tabular_let_can_be_joined(con) -> None:
    """The join needs the let's columns, which are resolved from the binding."""
    rel = duckdb_kql.sql(con, "let A = T; A | join kind=inner (R) on a | project a, s, r")
    assert list(rel.columns) == ["a", "s", "r"]
    assert sorted(rel.fetchall()) == [(1, "x", "p"), (9, "x", "q")]


def test_let_function_declarations_still_refuse(con) -> None:
    """User-defined functions are a later wave; refusing beats guessing."""
    with pytest.raises(duckdb_kql.KqlUnsupportedError):
        duckdb_kql.to_sql("let f = (a:int) { a + 1 }; print f(1)")


# --- timespan handling that `let` cases exposed ----------------------------
def test_timespan_string_with_a_day_part(con) -> None:
    """KQL timespans are `[-][d.]hh:mm:ss`; DuckDB's INTERVAL cast drops the days."""
    import datetime as dt

    assert _rows(con, "print totimespan('4.00:00:00')")[0][0] == dt.timedelta(days=4)
    assert _rows(con, "print totimespan('0.00:03:00')")[0][0] == dt.timedelta(minutes=3)
    assert _rows(con, "print totimespan('00:03:00')")[0][0] == dt.timedelta(minutes=3)
    assert _rows(con, "print totimespan('garbage')")[0][0] is None


def test_totimespan_of_a_timespan_is_identity(con) -> None:
    import datetime as dt

    assert _rows(con, "print totimespan(4d)")[0][0] == dt.timedelta(days=4)


def test_dividing_two_timespans_yields_a_number(con) -> None:
    """`dayofweek()` returns a TIMESPAN, so `dow / 1d` is "how many days"."""
    assert _rows(con, "let dow = dayofweek(datetime(1970-5-12)); print toint(dow / 1d)") == [
        (2,)
    ]
