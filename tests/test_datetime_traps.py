"""L5 trap tests — datetime parsing (``docs/test-plan.md`` §6).

Every expectation here was **measured against the Kusto Emulator**, not derived
from the docs or from intuition. The comment on each group records the query
that produced it so the numbers can be re-derived::

    docker compose up -d kusto
    python -c "from duckdb_kql.oracle import KustoEmulator as K; \
               print(K('http://localhost:8080').query(\"print todatetime('12-02-2022')\"))"

These run without the emulator: the ground truth is frozen into the assertions.
"""

from __future__ import annotations

import datetime as dt

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")


def _one(kql: str, tz: str = "UTC"):
    con = duckdb.connect()
    con.execute(f"SET TimeZone='{tz}'")
    return duckdb_kql.kql(con, kql).fetchone()[0]


# ADX: print todatetime('12-02-2022') -> 2022-12-02T00:00:00Z
#      print todatetime('13-01-2022') -> (null)
# The null is the important half: it rules out DD-MM and pins the order to MM-DD.
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2022-12-02", dt.datetime(2022, 12, 2)),
        ("2022-12-02 13:45:56", dt.datetime(2022, 12, 2, 13, 45, 56)),
        ("2022-12-02T13:45:56Z", dt.datetime(2022, 12, 2, 13, 45, 56)),
        ("2022/12/02", dt.datetime(2022, 12, 2)),
        # Formats DuckDB's TIMESTAMP cast rejects outright:
        ("12-02-2022", dt.datetime(2022, 12, 2)),
        ("12/02/2022", dt.datetime(2022, 12, 2)),
        ("12.02.2022", dt.datetime(2022, 12, 2)),
        ("2 Dec 2022", dt.datetime(2022, 12, 2)),
        ("Dec 2, 2022", dt.datetime(2022, 12, 2)),
        ("Fri, 02 Dec 2022 13:45:56 GMT", dt.datetime(2022, 12, 2, 13, 45, 56)),
        ("20221202", dt.datetime(2022, 12, 2)),
        ("12-02-2022 13:45:56", dt.datetime(2022, 12, 2, 13, 45, 56)),
        ("2/3/2022", dt.datetime(2022, 2, 3)),
    ],
)
def test_todatetime_accepts_the_formats_adx_accepts(value: str, expected) -> None:
    assert _one(f"print d = todatetime('{value}')") == expected


@pytest.mark.parametrize("value", ["not a date", "", "13-01-2022", "x12-02-2022"])
def test_todatetime_returns_null_not_an_error(value: str) -> None:
    """R1 — a bad conversion yields null in KQL; it must never raise."""
    assert _one(f"print d = todatetime('{value}')") is None


# ADX: print todatetime('2022-12-02T13:45:56+02:00') -> 2022-12-02T11:45:56Z
#
# The trap: DuckDB's plain TIMESTAMP cast *accepts* this string and keeps the
# local wall time (13:45:56), so the wrong answer arrives with no error at all.
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2022-12-02T13:45:56+02:00", dt.datetime(2022, 12, 2, 11, 45, 56)),
        ("2022-12-02T13:45:56-05:00", dt.datetime(2022, 12, 2, 18, 45, 56)),
    ],
)
def test_utc_offsets_are_resolved_not_discarded(value: str, expected) -> None:
    assert _one(f"print d = todatetime('{value}')") == expected


def test_datetime_literal_matches_todatetime() -> None:
    """`datetime(x)` and `todatetime(x)` must agree — corpus todatetime-function-01.

    They diverged once: the literal rendered as ``TIMESTAMP '...'``, which
    *raises* on the formats only todatetime() handled.
    """
    assert _one("print todatetime('12-02-2022') == datetime('12-02-2022')") is True
    assert _one("print d = datetime('12-02-2022')") == dt.datetime(2022, 12, 2)


@pytest.mark.parametrize("tz", ["UTC", "America/New_York", "Asia/Kolkata"])
def test_results_do_not_depend_on_the_session_timezone(tz: str) -> None:
    """R8 — KQL datetimes are UTC regardless of where the query runs.

    DuckDB reads the session TimeZone when casting an offset-less string, so
    without ``sql()`` forcing UTC these shift by hours on a non-UTC machine.
    Both branches of the mapping are covered: the cast and the strptime list.
    """
    assert _one("print d = todatetime('2022-12-02 13:45:56')", tz=tz) == dt.datetime(
        2022, 12, 2, 13, 45, 56
    )
    assert _one("print d = todatetime('12-02-2022')", tz=tz) == dt.datetime(2022, 12, 2)
    assert _one("print d = todatetime('2022-12-02T13:45:56+02:00')", tz=tz) == dt.datetime(
        2022, 12, 2, 11, 45, 56
    )
