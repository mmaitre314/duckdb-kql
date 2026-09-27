"""L5 trap tests — a pinned query clock, and the offset that used to vanish.

Requested so that a query written against `now()`/`ago()` can be tested
unchanged, instead of building fixtures relative to wall-clock time and watching
a window boundary fail once a week.

**A real bug fell out of implementing it.** `now(offset)` silently ignored its
offset: the registry declared arities ``(0, 1)`` against the template
``(now() AT TIME ZONE 'UTC')``, which has no ``{0}``, and `str.format` discards
an argument a template does not mention. So `now(1h)` rendered *identically* to
`now()` and answered an hour early with no error anywhere. Measured on the
emulator, `now(1h) - now()` is 3600 seconds, `now(-2d)` is -172800, and
`now(1h) == ago(-1h)`; eight such probes agree now and the first of them did not
before. One corpus case, `now-function-01`, is the single snapshot line that
moved.

Three design points the tests below pin, each of which could have gone the other
way:

* the pinned clock renders as **one bound placeholder**, not as a formatted
  literal per reference, so every reference is visibly the same value and none of
  it is text that needed escaping;
* the value is bound only if something actually read the clock, because handing
  DuckDB a parameter no placeholder mentions is an error;
* it is **request-scoped**, not client-scoped, so nothing is stored and a later
  request without the option gets the real clock back.

The last test is the one that says why this is not a find-and-replace: the
override acts on the parsed query, so a string containing the text `now()` and a
column named `now` are left alone.
"""

from __future__ import annotations

import datetime as dt

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")

FIXED = dt.datetime(2020, 1, 2, 12, tzinfo=dt.timezone.utc)
#: What it becomes once read: KQL datetimes are naive UTC (R8).
NAIVE = dt.datetime(2020, 1, 2, 12)


@pytest.fixture
def con():
    with duckdb_kql.connect() as connection:
        yield connection


def pinned(con, kql):
    return duckdb_kql.kql(con, kql, query_now=FIXED).fetchall()


@pytest.mark.parametrize(
    ("kql", "expected"),
    [
        ("print t = now()", NAIVE),
        ("print t = now(1h)", NAIVE + dt.timedelta(hours=1)),
        ("print t = now(-2h)", NAIVE - dt.timedelta(hours=2)),
        ("print t = now(0s)", NAIVE),
        ("print t = ago(1d)", NAIVE - dt.timedelta(days=1)),
        ("print t = ago(-1h)", NAIVE + dt.timedelta(hours=1)),
    ],
)
def test_every_spelling_resolves_against_the_pin(con, kql: str, expected) -> None:
    assert pinned(con, kql) == [(expected,)]


def test_now_with_an_offset_is_not_the_same_as_now(con) -> None:
    """The bug itself. These two rendered identically, and one was wrong.

    Asserted on the *difference* rather than absolute values, because that is
    what the emulator can be asked without a shared clock — 3600 seconds, and
    -172800 for `now(-2d)`.
    """
    for offset, seconds in (("1h", 3600), ("-2d", -172800), ("90m", 5400)):
        rows = duckdb_kql.kql(
            con, f"print d = tolong((now({offset}) - now()) / 1s)"
        ).fetchall()
        assert rows == [(seconds,)], offset

    assert duckdb_kql.kql(con, "print same = (now(1h) == ago(-1h))").fetchall() == [
        (True,)
    ]


def test_one_binding_is_shared_by_the_whole_statement(con) -> None:
    """Not a literal per reference, and not two independent clock reads."""
    result = duckdb_kql.to_sql(
        "print a = now(), b = ago(1d), c = now(1h)", query_now=FIXED
    )

    assert str(result).count("$kqlnow") == 3
    assert result.parameters == {"kqlnow": NAIVE}
    assert "now()" not in str(result), "the wall clock is still in there"


@pytest.mark.parametrize(
    "kql",
    [
        "let n = now(); print a = n, b = n",
        "datatable(x:int)[1,2] | project t = iff(x == 1, now(), now())",
        "let T = datatable(x:int)[1] | extend t = now(); T | project t",
        "datatable(k:int)[1] | join (datatable(k:int)[1] | extend t = now()) on k"
        " | project t",
    ],
)
def test_every_branch_and_nesting_sees_the_same_instant(con, kql: str) -> None:
    """A `let`, both `iff` arms, a tabular `let`, a join's right side.

    The clock context is deliberately *inherited* by a nested translation, which
    is the opposite of what the expression-sharing barrier does — a `now()` under
    a join is the same instant as one above it.
    """
    rows = pinned(con, kql)

    assert rows, kql
    assert {value for row in rows for value in row} == {NAIVE}, kql


def test_window_endpoints_are_exactly_expressible(con) -> None:
    """The motivation: inclusive and exclusive boundaries, reproducibly.

    Rows sit exactly on both endpoints and one second outside each.
    """
    rows = (
        "datatable(t:datetime)["
        "datetime(2020-01-01 12:00), datetime(2020-01-01 11:59:59),"
        "datetime(2020-01-02 12:00), datetime(2020-01-02 12:00:01)]"
    )
    inclusive = f"{rows} | where t >= ago(1d) and t <= now() | count"
    exclusive = f"{rows} | where t > ago(1d) and t < now() | count"

    assert pinned(con, inclusive) == [(2,)], "the endpoints themselves"
    assert pinned(con, exclusive) == [(0,)], "only the endpoints are in range"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (FIXED, NAIVE),
        (dt.datetime(2020, 1, 2, 14, tzinfo=dt.timezone(dt.timedelta(hours=2))), NAIVE),
        ("2020-01-02T12:00:00Z", NAIVE),
        ("2020-01-02 12:00:00", NAIVE),
        (dt.date(2020, 1, 2), dt.datetime(2020, 1, 2)),
    ],
)
def test_the_value_is_read_the_way_a_declared_parameter_is(value, expected) -> None:
    """Same coercion as `set_parameter` on a `datetime`, aware values to UTC."""
    assert duckdb_kql.to_sql("print t = now()", query_now=value).parameters == {
        "kqlnow": expected
    }


@pytest.mark.parametrize("value", ["not-a-datetime", 12345, 3.5, [], object()])
def test_a_value_that_is_not_a_datetime_is_refused_by_name(value) -> None:
    """Silently ignoring it would be a test that believes it pinned the clock."""
    with pytest.raises(Exception, match="query_now"):
        duckdb_kql.to_sql("print t = now()", query_now=value)


def test_omitting_it_changes_nothing() -> None:
    """The must-not-change half, asserted on the SQL rather than on a datetime."""
    result = duckdb_kql.to_sql("print t = now()")

    assert str(result) == 'SELECT (now() AT TIME ZONE \'UTC\') AS "t"'
    assert result.parameters == {}


def test_nothing_is_bound_when_the_clock_is_never_read() -> None:
    """DuckDB rejects a parameter no placeholder mentions, so this is not tidiness."""
    result = duckdb_kql.to_sql("print x = 1", query_now=FIXED)

    assert result.parameters == {}
    assert "$kqlnow" not in str(result)


def test_the_option_is_request_scoped_and_isolated(con) -> None:
    """Two requests, two clocks, then one with none — all on one client."""
    from duckdb_kql.kusto import ClientRequestProperties, KustoClient

    later = FIXED + dt.timedelta(days=10)
    with KustoClient(con) as client:
        first, second = ClientRequestProperties(), ClientRequestProperties()
        first.set_option("query_now", FIXED)
        second.set_option("query_now", later)

        def answer(properties=None):
            rows = client.execute(None, "print t = now()", properties).primary_results[0]
            return rows[0]["t"]

        assert answer(first) == FIXED
        assert answer(second) == later
        assert answer(first) == FIXED, "the second request changed the first"
        assert answer().year >= 2026, "no option, so the real clock"


def test_text_that_looks_like_the_function_is_left_alone(con) -> None:
    """Why this is not a find-and-replace over the query text.

    A string literal holding `now()` and a column called `now` both survive,
    because the override acts on the parsed query.
    """
    assert pinned(con, "print s = 'now()', t = strcat('ago(1d)', '!')") == [
        ("now()", "ago(1d)!")
    ]
    assert pinned(con, "datatable(now:long)[5] | project now") == [(5,)]


# --------------------------------------------------------------------------
# `set query_now = datetime(...)` — the same option, written into the query
# --------------------------------------------------------------------------
#
# Kusto lets a caller send a request option beside the query *or* write it into
# the text as a `set` statement, and they mean the same thing. So the statement
# form is classified by the same table `set_option` uses — `duckdb_kql.options`,
# moved to Layer 0 for this, because a `set` statement is translation and cannot
# depend on the Layer 2 client.
#
# Measured on the emulator, 17 probes: the pinned clock reaches `now()`, `ago`,
# `now(offset)`, a `let`, both `iff` arms, `bin()`, a row set and a `where`
# window; a later `set` of the same option wins; it composes with `declare` and
# `let`; and a value that is not a datetime literal is refused — Kusto answers
# SEM0020 for a string, a timespan, a long, a bare name, and no value at all.

PINNED = "set query_now = datetime(2020-01-02 12:00);"


@pytest.mark.parametrize(
    ("kql", "expected"),
    [
        (f"{PINNED} print t = now()", NAIVE),
        (f"{PINNED} print t = ago(1d)", NAIVE - dt.timedelta(days=1)),
        (f"{PINNED} print t = now(1h)", NAIVE + dt.timedelta(hours=1)),
        (f"{PINNED} let n = now(); print t = n", NAIVE),
        (f"{PINNED} print t = bin(now(), 1h)", NAIVE),
    ],
)
def test_the_set_statement_pins_the_clock(con, kql: str, expected) -> None:
    assert duckdb_kql.kql(con, kql).fetchall() == [(expected,)]


def test_a_later_set_of_the_same_option_wins(con) -> None:
    """Measured: Kusto answers 2021, so the last one is the request property."""
    kql = (
        "set query_now = datetime(2020-01-02);"
        " set query_now = datetime(2021-01-02); print t = now()"
    )
    assert duckdb_kql.kql(con, kql).fetchall() == [(dt.datetime(2021, 1, 2),)]


def test_the_statement_overrides_the_argument(con) -> None:
    """Both given: the text wins, because a `set` *sets the request property*.

    The reverse would mean a query that says what clock it wants and silently
    gets another. It is also why `to_sql` opens the clock context before
    translating and reads the value back out afterwards, rather than deciding up
    front.
    """
    rows = duckdb_kql.kql(
        con, f"{PINNED} print t = now()", query_now=dt.datetime(2015, 5, 5)
    ).fetchall()

    assert rows == [(NAIVE,)]


@pytest.mark.parametrize("value", ["'2020-01-02 12:00'", "1d", "5", "foo"])
def test_a_value_that_is_not_a_datetime_literal_is_refused(value: str) -> None:
    """Kusto answers SEM0020 for every one of these, measured."""
    with pytest.raises(Exception, match="set query_now"):
        duckdb_kql.to_sql(f"set query_now = {value}; print t = now()")


def test_a_value_the_grammar_will_not_even_take_is_still_refused() -> None:
    """`set query_now = now()` is a *syntax* error, not our refusal.

    The grammar's value position is `identifierOrKeywordName | literalExpression`,
    so a function call cannot be written there at all. Kusto refuses it too — with
    SEM0020 rather than a parse error — so both engines say no and only the error
    class differs. Recorded because the two refusals arrive from different places
    and a future change might route one of them into an accept.
    """
    from duckdb_kql.errors import KqlError

    with pytest.raises(KqlError):
        duckdb_kql.to_sql("set query_now = now(); print t = now()")


def test_set_query_now_with_no_value_is_refused() -> None:
    """The grammar allows a valueless `set`; this option is not one of those."""
    with pytest.raises(Exception, match="needs a value"):
        duckdb_kql.to_sql("set query_now; print t = now()")


def test_an_option_that_is_a_no_op_here_is_accepted_silently(con) -> None:
    """Not a refusal, on purpose.

    An option the table classifies as a no-op does nothing here *because doing
    nothing is the behaviour it asks for*. Refusing it would reject a query Kusto
    runs, over something that changes no answer either way.
    """
    assert duckdb_kql.kql(con, "set request_app_name = 'tests'; print x = 1").fetchall() == [
        (1,)
    ]
    assert duckdb_kql.kql(con, "set deferpartialqueryfailures; print x = 1").fetchall() == [
        (1,)
    ]


@pytest.mark.parametrize(
    ("option", "expected"),
    [
        ("truncationmaxrecords = 5", "complete answer"),
        ("notruncation", "truncation"),
        ("query_language = sql", "(?i)language"),
        ("query_datetime_scope_column = 'T'", "widen"),
    ],
)
def test_an_option_that_would_change_the_answer_is_refused(option, expected) -> None:
    """These reach the same reasons the client's `set_option` gives."""
    with pytest.raises(Exception, match=expected):
        duckdb_kql.to_sql(f"set {option}; print x = 1")


@pytest.mark.parametrize("option", ["servertimeout = 30s", "norequesttimeout"])
def test_an_execution_time_option_says_where_the_working_spelling_is(option) -> None:
    """Implemented as a request option, impossible as a statement.

    The deadline interrupts DuckDB while the query runs; a `set` statement is
    read when it is translated, where there is nothing running to interrupt.
    Accepting it there would look honoured and do nothing.
    """
    with pytest.raises(Exception, match="request option"):
        duckdb_kql.to_sql(f"set {option}; print x = 1")


def test_an_unrecognised_option_does_not_claim_kusto_lacks_it() -> None:
    """Wording, and it is a correctness question about what we are asserting.

    Kusto has more request options than the table lists, and accepts an unknown
    one silently — measured, `set not_a_real_option = 5` runs there. We refuse,
    which is the safe direction for a misspelled `truncationmaxrecords`. But the
    message must be about *this translator*, not about Kusto: `query_datascope`
    is a real option we do not implement, and saying "no such option" would be a
    false claim.
    """
    with pytest.raises(Exception, match="options this translator implements"):
        duckdb_kql.to_sql("set query_datascope = 1; print x = 1")


def test_the_two_spellings_read_one_table() -> None:
    """The client and `lower` must not drift into two classifications."""
    from duckdb_kql.kusto import OPTION_SUPPORT as via_client
    from duckdb_kql.options import OPTION_SUPPORT as via_layer_0

    assert via_client is via_layer_0
