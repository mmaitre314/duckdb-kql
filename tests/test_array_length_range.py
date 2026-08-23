"""L5 trap tests — ``array_length`` and the ``range`` bounds it feeds.

``range i from 0 to array_length(a) - 1 step 1`` is the ordinary way to walk a
dynamic array, and it failed to *bind*:

    Binder Error: No function matches the given name and argument types
    'generate_series(BIGINT, HUGEINT, BIGINT)'

DuckDB's ``json_array_length`` returns **UBIGINT**, so subtracting 1 widened the
expression to HUGEINT — a type ``generate_series`` has no overload for. KQL's
``array_length`` is a ``long``, so the cast is fidelity rather than a patch.

Measuring that also turned up a second divergence in the same mapping: KQL
returns **null** for a non-array, where ``json_array_length`` answers 0. A loop
bound is exactly where a spurious 0 disappears without trace.
"""

from __future__ import annotations

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    return c


def _rows(con, kql):
    return duckdb_kql.kql(con, kql).fetchall()


def _one(con, kql):
    return _rows(con, kql)[0][0]


# ---------------------------------------------------------------------------
# The reported query
# ---------------------------------------------------------------------------


def test_range_over_an_arrays_length(con) -> None:
    assert _rows(
        con,
        'let tids = dynamic(["a", "b"]);\n'
        "range i from 0 to array_length(tids) - 1 step 1",
    ) == [(0,), (1,)]


def test_range_over_an_empty_array_yields_no_rows(con) -> None:
    """`0 to -1` is empty in both engines — it must not become a huge range."""
    assert _rows(
        con, "let t = dynamic([]); range i from 0 to array_length(t) - 1 step 1"
    ) == []


def test_range_over_a_length_drives_a_count(con) -> None:
    assert _rows(
        con,
        'let t = dynamic(["a","b","c"]);'
        " range i from 0 to array_length(t) - 1 step 1 | summarize n = count()",
    ) == [(3,)]


# ---------------------------------------------------------------------------
# array_length itself
# ---------------------------------------------------------------------------


def test_array_length_counts_elements(con) -> None:
    assert _one(con, "print n = array_length(dynamic([1,2]))") == 2
    assert _one(con, "print n = array_length(dynamic([]))") == 0


def test_array_length_is_a_long_not_an_unsigned(con) -> None:
    """The root cause: an unsigned result widens the arithmetic around it."""
    assert str(duckdb_kql.kql(con, "print n = array_length(dynamic([1,2]))").types[0]) == (
        "BIGINT"
    )
    assert _one(con, "print n = array_length(dynamic([1,2])) - 1") == 1
    assert _one(con, "print n = array_length(dynamic([1,2])) + 1") == 3


def test_array_length_of_a_non_array_is_null(con) -> None:
    """Measured: null, not 0. An object is not an array of length zero."""
    assert _one(con, "print n = array_length(dynamic({'a':1}))") is None
    assert _one(con, "print n = array_length(dynamic(null))") is None


def test_array_length_reports_as_long(con) -> None:
    assert _rows(con, "print n = array_length(dynamic([1,2])) | getschema") == [
        ("n", 0, "System.Int64", "long")
    ]


def test_array_length_over_a_column(con) -> None:
    con.execute("CREATE TABLE A(d JSON)")
    con.execute("""INSERT INTO A VALUES ('[1,2]'), ('[]'), ('{"a":1}'), (NULL)""")
    assert sorted(_rows(con, "A | project n = array_length(d)"), key=str) == [
        (0,), (2,), (None,), (None,)
    ]


def test_array_length_of_split(con) -> None:
    assert _one(con, "print s = array_length(split('a,b,c', ','))") == 3


# ---------------------------------------------------------------------------
# range with computed bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("clause", "expected"),
    [
        ("range i from 0 to 3 step 1", [0, 1, 2, 3]),
        ("range i from 0 to -1 step 1", []),
        ("range i from 1 to 10 step 3", [1, 4, 7, 10]),
        ("range i from -2 to 2 step 2", [-2, 0, 2]),
    ],
)
def test_range_endpoints_are_inclusive(con, clause: str, expected: list[int]) -> None:
    assert [r[0] for r in _rows(con, clause)] == expected


def test_a_temporal_range_is_not_cast_to_bigint(con) -> None:
    """`generate_series(TIMESTAMP, TIMESTAMP, INTERVAL)` is its own overload.

    Casting the bounds to BIGINT fixes the numeric case and destroys this one —
    a datetime does not even convert. Pinned because the first attempt at the
    numeric fix broke exactly this.
    """
    import datetime as dt

    rows = _rows(
        con,
        "range d from datetime(2020-01-01) to datetime(2020-01-03) step 1d",
    )
    assert [r[0] for r in rows] == [
        dt.datetime(2020, 1, 1), dt.datetime(2020, 1, 2), dt.datetime(2020, 1, 3)
    ]


def test_range_bounds_from_a_scalar_let(con) -> None:
    assert [r[0] for r in _rows(con, "let n = 3; range i from 0 to n - 1 step 1")] == [
        0, 1, 2
    ]
