"""L5 trap tests — three functions whose KQL meaning is not their SQL meaning.

Each was found by a differential sweep and each failed differently:

* **``substring``** only accepted two arguments, so the ordinary
  ``substring(s, start, length)`` was refused outright. The 0-based start was
  already handled; the negative start was not, and it is the trap — a start
  that reaches back past the beginning of the string is **empty**, not clamped
  to the whole string.
* **``floor``** was mapped to SQL's ``floor``. In KQL ``floor`` *is* ``bin``:
  the emulator refuses ``floor(7.9)`` with "SEM0219: bin(): function expects 2
  argument(s)", and ``floor(-7, 5)`` is ``-10`` — the bin answer — where SQL's
  floor of -7 is -7.
* **``todatetime``** of a value that is already a datetime is a no-op in KQL,
  but the string-parsing template had no binding for a TIMESTAMP argument, so
  it escaped as a raw DuckDB ``BinderException`` rather than any KQL error.

Every expectation below is what the Kusto Emulator returned.
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
    c.execute("CREATE TABLE S(s VARCHAR)")
    c.execute("INSERT INTO S VALUES ('abcdefg'),('xy')")
    c.execute("CREATE TABLE D(T TIMESTAMP)")
    c.execute("INSERT INTO D VALUES ('2020-01-02 03:04:05'),(NULL)")
    return c


def _one(con, kql):
    return duckdb_kql.kql(con, kql).fetchall()[0][0]


# ---------------------------------------------------------------------------
# substring (R11)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        # the three-argument form, which used to be refused entirely
        ("substring('abcdefg', 1, 3)", "bcd"),
        ("substring('abcdefg', 0, 3)", "abc"),
        ("substring('abcdefg', 5, 10)", "fg"),      # over-long length clamps
        ("substring('abcdefg', 10, 3)", ""),        # past the end is empty
        ("substring('abcdefg', 1, 0)", ""),
        ("substring('abcdefg', 1, -1)", ""),        # negative length is empty
        ("substring('', 0, 3)", ""),
        ("substring('abc', 3, 1)", ""),
        # a negative start counts from the END
        ("substring('abcdefg', -3, 2)", "ef"),
        ("substring('abcdefg', -3)", "efg"),
        ("substring('abcdefg', -3, 10)", "efg"),
        # ...and reaching back past the start is EMPTY, not the whole string
        ("substring('abc', -10, 2)", ""),
        ("substring('abc', -10)", ""),
        # the two-argument form
        ("substring('abcdefg', 2)", "cdefg"),
        ("substring('abcdefg', 0)", "abcdefg"),
        ("substring('abc', 3)", ""),
    ],
)
def test_substring(con, call: str, expected: str) -> None:
    assert _one(con, f"print x = {call}") == expected


def test_substring_is_character_oriented_not_byte_oriented(con) -> None:
    """R11: 'héllo' indexes by character, so 1..3 is 'éll' and not two bytes."""
    assert _one(con, "print x = substring('héllo', 1, 3)") == "éll"
    assert _one(con, "print x = strlen(substring('héllo', 1, 3))") == 3


def test_substring_over_a_column(con) -> None:
    rows = duckdb_kql.kql(con, "S | project y = substring(s, 1, 3)").fetchall()
    assert sorted(rows) == [("bcd",), ("y",)]


def test_substring_of_a_column_with_a_negative_start(con) -> None:
    rows = duckdb_kql.kql(con, "S | project y = substring(s, -2)").fetchall()
    assert sorted(rows) == [("fg",), ("xy",)]


def test_substring_rejects_a_fourth_argument(con) -> None:
    with pytest.raises(duckdb_kql.KqlUnsupportedError):
        duckdb_kql.kql(con, "print x = substring('abc', 0, 1, 2)")


# ---------------------------------------------------------------------------
# floor is bin
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        ("floor(7, 5)", 5),
        ("floor(-7, 5)", -10),      # SQL's floor(-7) would be -7
        ("floor(7.5, 1)", 7.0),
        ("floor(7.5, 0.5)", 7.5),
        ("floor(-7.5, 1)", -8.0),
        ("floor(0, 5)", 0),
    ],
)
def test_floor_rounds_down_to_a_multiple(con, call: str, expected: object) -> None:
    assert _one(con, f"print x = {call}") == expected


def test_floor_agrees_with_bin(con) -> None:
    for args in ("7, 5", "-7, 5", "7.5, 0.5"):
        assert _one(con, f"print x = floor({args})") == _one(
            con, f"print x = bin({args})"
        )


def test_floor_of_a_datetime(con) -> None:
    assert _one(con, "print x = floor(datetime(2020-01-01 13:45), 1d)") == dt.datetime(
        2020, 1, 1
    )


def test_floor_of_a_timespan(con) -> None:
    assert _one(con, "print x = floor(3h + 15m, 1h)") == dt.timedelta(hours=3)


def test_one_argument_floor_is_refused(con) -> None:
    """Kusto refuses it too — "bin(): function expects 2 argument(s)".

    Answering 7 for `floor(7.9)` would be replying to a query no cluster runs.
    """
    with pytest.raises(duckdb_kql.KqlUnsupportedError):
        duckdb_kql.kql(con, "print x = floor(7.9)")


# ---------------------------------------------------------------------------
# todatetime of a datetime
# ---------------------------------------------------------------------------


def test_todatetime_of_a_datetime_column(con) -> None:
    """The reported crash: a raw BinderException, not a KQL error."""
    rows = duckdb_kql.kql(con, "D | project x = todatetime(T)").fetchall()
    assert sorted(rows, key=str) == [(None,), (dt.datetime(2020, 1, 2, 3, 4, 5),)]


def test_todatetime_of_a_datetime_literal(con) -> None:
    assert _one(con, "print x = todatetime(datetime(2020-01-02))") == dt.datetime(
        2020, 1, 2
    )


def test_todatetime_nests(con) -> None:
    assert duckdb_kql.kql(
        con, "D | where isnotnull(T) | project x = todatetime(todatetime(T))"
    ).fetchall() == [(dt.datetime(2020, 1, 2, 3, 4, 5),)]


def test_todatetime_in_a_predicate(con) -> None:
    assert _one(
        con, "D | where todatetime(T) == datetime(2020-01-02 03:04:05) | count"
    ) == 1


def test_todatetime_still_parses_strings(con) -> None:
    """The binding fix must not cost the wider format surface (R1, R8)."""
    assert _one(con, "print x = todatetime('2022-12-02T13:45:56+02:00')") == dt.datetime(
        2022, 12, 2, 11, 45, 56
    )
    assert _one(con, "print x = todatetime('12-02-2022')") == dt.datetime(2022, 12, 2)
    assert _one(con, "print x = todatetime('garbage')") is None
