"""L5 trap tests — `between` / `!between`.

Spelled by the lexer, given its own parse rule, present in ten corpus queries,
and lowered by nothing: `T | where x between (1 .. 10)` was refused as
`expression:BetweenEqualityExpression`. Every expectation below was measured on
the Kusto Emulator over **rows** rather than folded constants.

Two things make it more than `x >= low and x <= high`.

**The high bound may be a duration.** `t between (datetime(2020-01-01) .. 3d)`
ends at 2020-01-04 inclusive — measured — so a timespan high bound is added to
the low one. It is conditional on the types, not the syntax: `d between (2h ..
6h)` over a *timespan* column is the plain reading (7h is outside it), and Kusto
refuses the other mixtures outright (`long .. timespan` is SEM0234, `timespan ..
datetime` SEM0232). Kusto decides this from its static types; this translator
has none for a bare column, so the completion is claimed only where the IR
carries it and everything else falls through to the plain reading. That is safe
in the one direction that matters — see
`test_an_undecidable_duration_bound_errors_rather_than_guessing`.

**Kusto takes only ordered scalars.** `string`, `dynamic` and `guid` are
SEM0208 there, while SQL's `BETWEEN` orders strings perfectly happily and would
answer.

The null rules, by contrast, are exactly SQL's, which is worth recording
because the `!contains` family's are *not* and had to be pinned separately.
"""

from __future__ import annotations

import datetime as dt

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlUnsupportedError

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    return c


def _rows(con, kql):
    return duckdb_kql.kql(con, kql).fetchall()


# ---------------------------------------------------------------------------
# The shape that was refused
# ---------------------------------------------------------------------------


def test_the_reported_query_translates(con) -> None:
    """Both bounds are expressions over another column — and neither is
    statically typed, so this is the plain reading, which is the right one:
    `o + 1h` is a datetime."""
    # (t, o) pairs: 12:00 within [11:30, 13:30] keeps the row; 09:00 within
    # [11:00, 13:00] does not. Both bounds move with `o`, which is the point.
    assert _rows(
        con,
        "datatable(t:datetime, o:datetime)["
        "datetime(2020-01-01T12:00), datetime(2020-01-01T12:30),"
        "datetime(2020-01-01T09:00), datetime(2020-01-01T12:00)]"
        "| where t between ((o - 1h) .. (o + 1h)) | project t",
    ) == [(dt.datetime(2020, 1, 1, 12, 0),)]


# ---------------------------------------------------------------------------
# Bounds and nulls — measured, and identical to SQL's
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "x,expected",
    [(1, False), (2, True), (3, True), (10, True), (11, False)],
)
def test_both_ends_are_inclusive(con, x: int, expected: bool) -> None:
    assert _rows(con, f"print b = {x} between (2 .. 10)") == [(expected,)]


def test_a_null_value_is_null_not_false(con) -> None:
    """The distinction `where` hides and `project` shows: a null value gives a
    null *result*, so it fails the filter without ever being false."""
    assert _rows(
        con, "datatable(x:long)[long(null)] | project b = isnull(x between (2 .. 10))"
    ) == [(True,)]


def test_a_null_bound_is_null(con) -> None:
    assert _rows(
        con,
        "datatable(x:long)[5] | project "
        "lo = isnull(x between (long(null) .. 10)), "
        "hi = isnull(x between (1 .. long(null)))",
    ) == [(True, True)]


def test_a_reversed_range_is_false_not_an_error(con) -> None:
    assert _rows(con, "print b = 5 between (10 .. 1)") == [(False,)]


def test_negation_propagates_null_too(con) -> None:
    """`!between` is `NOT BETWEEN`, not "everything the range excludes" — a null
    stays null rather than becoming true. Unlike `!contains`, whose null
    handling could *not* be derived from `NOT (...)` and had to be measured."""
    assert _rows(
        con,
        "datatable(x:long)[1, 5, long(null)] | project "
        "x, nb = x !between (2 .. 10), nb_null = isnull(x !between (2 .. 10))",
    ) == [(1, True, False), (5, False, False), (None, None, True)]


# ---------------------------------------------------------------------------
# The duration bound
# ---------------------------------------------------------------------------


def test_a_timespan_high_bound_is_a_duration(con) -> None:
    """Measured: the window is [2020-01-01, 2020-01-04], inclusive of the end."""
    assert _rows(
        con,
        "datatable(t:datetime)[datetime(2020-01-03T23:59:59), datetime(2020-01-04),"
        " datetime(2020-01-04T00:00:01)]"
        "| project b = t between (datetime(2020-01-01) .. 3d)",
    ) == [(True,), (True,), (False,)]


def test_a_timespan_pair_is_not_a_duration(con) -> None:
    """The case that proves it is a type rule and not "a timespan on the right".

    7h is the discriminating value: inside [2h, 2h+6h] but outside [2h, 6h].
    Measured false, so timespan/timespan is the plain reading.
    """
    assert _rows(con, "datatable(d:timespan)[7h] | project b = d between (2h .. 6h)") == [
        (False,)
    ]


def test_the_completion_survives_a_datetime_returning_call(con) -> None:
    """`ago(...)` is statically a datetime, so `ago(2d) .. 1d` completes."""
    sql = str(duckdb_kql.to_sql("T | where t between (ago(2d) .. 1d)"))
    assert sql.count("INTERVAL '2d'") == 2, sql  # low rendered twice, i.e. added
    assert "INTERVAL '1d'" in sql


def test_an_undecidable_duration_bound_errors_rather_than_guessing(con) -> None:
    """The residue, pinned deliberately.

    `t between (SomeColumn .. 3d)` is a duration window on a cluster whenever
    the column is a datetime. Here the column carries no type, so the plain
    reading is emitted — and it does **not** answer: DuckDB will not compare a
    TIMESTAMP with an INTERVAL. A refusal, not a window silently one bound
    short, which is the trade the charter asks for. Closing it needs
    `docs/column-types-proposal.md`, not a shape predicate.
    """
    with pytest.raises(Exception, match="(?i)timestamp|interval|binder"):
        duckdb_kql.kql(
            con,
            "datatable(t:datetime, s:datetime)[datetime(2020-01-01), datetime(2020-01-01)]"
            "| where t between (s .. 3d)",
        )


# ---------------------------------------------------------------------------
# Types Kusto refuses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kql",
    [
        "print b = 'b' between ('a' .. 'c')",
        "print b = 5 between ('a' .. 'c')",
        "print b = dynamic([1]) between (dynamic([0]) .. dynamic([2]))",
    ],
)
def test_an_unordered_operand_is_refused(kql: str) -> None:
    """SQL's `BETWEEN` orders strings, so without this we answer where a cluster
    raises SEM0208 — a divergence even though it is not a wrong number."""
    with pytest.raises(KqlUnsupportedError, match="between"):
        duckdb_kql.to_sql(kql)


def test_the_ordered_scalars_are_all_accepted(con) -> None:
    """Numeric, datetime, timespan and bool — measured accepted, so not refused."""
    assert _rows(
        con,
        "print a = 2 between (int(1) .. int(3)), b = 2.5 between (2.0 .. 3.0), "
        "c = datetime(2020-06-01) between (datetime(2020-01-01) .. datetime(2021-01-01)), "
        "d = 5h between (2h .. 6h), e = true between (false .. true)",
    ) == [(True, True, True, True, True)]


# ---------------------------------------------------------------------------
# The walkers — where a new IR node goes wrong quietly
# ---------------------------------------------------------------------------


def test_scalar_lets_reach_the_bounds(con) -> None:
    """The corpus case that caught it (`bin-function-03`).

    `ir.Between` is a container, and every generic pass over expressions has to
    descend into it. `_substitute` did not, so the bounds stayed `ColumnRef`s
    and DuckDB reported a missing column named `Start` — a new node's children
    being invisible to a walker, which no test of `between` itself would show.
    """
    rows = _rows(
        con,
        "let Start = datetime(2007-04-07);"
        "let End = Start + 7d;"
        "datatable(t:datetime)[datetime(2007-04-08), datetime(2007-05-01)]"
        "| where t between (Start .. End) | project t",
    )
    assert len(rows) == 1


def test_a_between_inside_an_aggregate(con) -> None:
    """`summarize` lifts aggregates by walking expressions; the same descent."""
    assert _rows(
        con,
        "datatable(x:long)[1, 5, 20] | summarize n = countif(x between (2 .. 10))",
    ) == [(1,)]
