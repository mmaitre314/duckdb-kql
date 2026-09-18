"""L5 trap tests — `coalesce` (R4).

Reported: `coalesce('', 'fallback')` answered `''`. It was mapped straight to
SQL `COALESCE`, which skips null and nothing else, while KQL also skips the
**empty string** — the difference the report's hostname-to-IP fallback was
relying on.

The rule is narrower than "falsy", and each row was measured on the emulator:

============================  =========
argument                      skipped?
============================  =========
``''``                        yes
``' '`` (a space)             no
``0``, ``0.0``, ``false``     no
``0s`` (a zero timespan)      no
``dynamic([])``, ``{}``       no
null, of any type             yes
============================  =========

So the test is emptiness of the rendered value, which `CAST(x AS VARCHAR) <> ''`
applies to every type at once — the empty string is the only value whose text
form is empty, and a null casts to null and fails the same comparison. Kusto
requires every argument to share one type (SEM0525) but a bare column does not
say which, so deciding this from a static type would be the trap
`docs/column-types-proposal.md` describes; this side-steps it rather than
guessing.

**The all-skipped case cost a second round.** `coalesce('', '')` is `''` in
Kusto, while all-null longs are null. `isnull` cannot tell the two apart — it is
always false for a KQL string — so the first version looked right and was not:
`strlen` reported 0 against our null. The fix appends the raw arguments after
the filtered ones, so the tail is reached only when everything was null or
empty and then yields the first non-null raw value, which is `''` exactly when
one was an empty string.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlUnsupportedError

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    return c


def _one(con, kql):
    return duckdb_kql.kql(con, kql).fetchall()[0][0]


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def test_an_empty_string_is_skipped(con) -> None:
    assert _one(con, "print r = coalesce('', 'fallback')") == "fallback"


def test_an_empty_string_from_a_column_is_skipped(con) -> None:
    """Over rows, not folded — the two evaluators disagree elsewhere."""
    assert _one(
        con, "datatable(a:string,b:string)['','fallback'] | project r = coalesce(a,b)"
    ) == "fallback"


def test_the_dynamic_null_repro(con) -> None:
    """The report's second spelling, via `tostring` of a null dynamic."""
    assert _one(
        con, "print r = coalesce(tostring(dynamic(null)), 'fallback')"
    ) == "fallback"


def test_the_first_non_empty_wins_not_the_last(con) -> None:
    assert _one(
        con,
        "datatable(a:string,b:string,c:string)['','hit','no'] | project r = coalesce(a,b,c)",
    ) == "hit"


# ---------------------------------------------------------------------------
# What is NOT skipped — the rule is emptiness, not falsiness
# ---------------------------------------------------------------------------


def test_a_space_is_not_empty(con) -> None:
    """The obvious over-fix is to trim; Kusto does not."""
    assert _one(
        con,
        "datatable(a:string,b:string)[' ','fb'] | project r = strcat('[',coalesce(a,b),']')",
    ) == "[ ]"


@pytest.mark.parametrize(
    "table,expected",
    [
        ("datatable(a:long,b:long)[0,5]", 0),
        ("datatable(a:real,b:real)[0.0,5.0]", 0.0),
        ("datatable(a:bool,b:bool)[false,true]", False),
    ],
)
def test_a_zero_value_is_a_value(con, table: str, expected) -> None:
    """A SQL-shaped guess — "skip the falsy ones" — gets every one of these
    wrong, and each is measured."""
    assert _one(con, f"{table} | project r = coalesce(a,b)") == expected


def test_a_zero_timespan_is_a_value(con) -> None:
    assert _one(
        con,
        "datatable(a:timespan,b:timespan)[0s,5s] | project r = tostring(coalesce(a,b))",
    ) == "00:00:00"


@pytest.mark.parametrize("empty", ["dynamic([])", "dynamic({})"])
def test_an_empty_dynamic_is_a_value(con, empty: str) -> None:
    """An empty array is not an empty *string*, and renders as `[]`."""
    assert _one(
        con,
        f"datatable(a:dynamic,b:dynamic)[{empty},dynamic([1])] "
        "| project r = tostring(coalesce(a,b))",
    ) == empty[len("dynamic(") : -1]


def test_a_null_of_a_non_string_type_is_still_skipped(con) -> None:
    assert _one(
        con, "datatable(a:long,b:long)[long(null),5] | project r = coalesce(a,b)"
    ) == 5


# ---------------------------------------------------------------------------
# Everything skipped — where `isnull` lies and `strlen` does not
# ---------------------------------------------------------------------------


def test_all_empty_strings_answer_the_empty_string(con) -> None:
    """The round-two bug. Returning null here passes an `isnull` check and
    fails a `strlen` one, because a KQL string has no null distinct from `''`."""
    assert _one(
        con, "datatable(a:string,b:string)['',''] | project l = strlen(coalesce(a,b))"
    ) == 0


def test_all_null_strings_answer_the_empty_string(con) -> None:
    assert _one(
        con,
        "datatable(a:string,b:string)[dynamic(null),dynamic(null)] "
        "| project l = strlen(coalesce(tostring(a),tostring(b)))",
    ) == 0


def test_all_null_longs_answer_null(con) -> None:
    """The other half of the same branch: no argument was an empty string, so
    the tail yields null and the type is preserved."""
    assert _one(
        con,
        "datatable(a:long,b:long)[long(null),long(null)] | project n = isnull(coalesce(a,b))",
    ) is True


# ---------------------------------------------------------------------------
# Arity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("args", ["'a'", ", ".join(["'a'"] * 65)])
def test_the_arity_bounds_are_kustos(con, args: str) -> None:
    """Measured: SEM0223, "function expects [2..64] argument(s)"."""
    with pytest.raises(KqlUnsupportedError, match="2 to 64"):
        duckdb_kql.kql(con, f"print r = coalesce({args})")
