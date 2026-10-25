"""Trap tests — `totimespan` converts every input type, and never rounds away a tick.

Reported: `totimespan(ts)` over a **timespan column** failed with a raw DuckDB
binder error. It short-circuited only an argument the IR *knew* was a timespan,
and a bare column never is, so the column reached the string parser —
`regexp_matches(INTERVAL, …)` has no overload. CLAUDE.md's static-predicate
trap, exactly.

The loud half had a quiet one. Measured on the emulator, Kusto converts every
type, folded and over rows alike:

* a timespan is itself, and a string parses as one, whitespace and all —
  `' 1.00:00:00 '` is a day, and was null here, because the day-prefix branch
  did not trim;
* a number or a bool is a **tick count** — `totimespan(1230)` is
  `00:00:00.0001230`, a real truncates as `tolong` does;
* a datetime is its ticks since 0001-01-01, as `tolong(t)` is;
* a dynamic converts as what it holds — `totimespan(dynamic(50))` is five
  microseconds and `dynamic('01:00:00')` an hour, where **both answered null**,
  with no error.

**Why the obvious fix is wrong.** A tick is 100 ns and DuckDB keeps whole
microseconds. Converting `ticks // 10` answers `00:00:00.0000120` for 123
ticks — a wrong answer that looks right. A tick count, a timespan string, or a
`time(…)` literal that is not a whole number of microseconds is therefore
**refused**: at translation for a literal, as `1tick` already was, and at run
time otherwise. `time(00:00:00.0000005)` and `totimespan('00:00:00.0000005')`
had both answered zero.
"""

from __future__ import annotations

import datetime as dt

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlUnsupportedError

duckdb = pytest.importorskip("duckdb")


def _ticks(declared: str, value: str) -> object:
    """`tolong(totimespan(c))` over one row — the tick count, exactly."""
    query = f"datatable({declared})[{value}] | project x = tolong(totimespan(c))"
    ((ticks,),) = duckdb_kql.kql(duckdb_kql.connect(), query).fetchall()
    return ticks


#: `(column type, value, Kusto's tick count)` — every one measured over rows.
MEASURED = [
    ("c:timespan", "1h", 36_000_000_000),
    ("c:timespan", "time(-1.02:03:04.5)", -937_845_000_000),
    ("c:timespan", "timespan(null)", None),
    ("c:datetime", "datetime(0001-01-01)", 0),
    ("c:datetime", "datetime(1970-01-01)", 621_355_968_000_000_000),
    ("c:datetime", "datetime(2020-01-02T03:04:05.5)", 637_135_310_455_000_000),
    ("c:long", "1230", 1230),
    ("c:long", "-10", -10),
    ("c:long", "long(null)", None),
    ("c:int", "int(50)", 50),
    ("c:real", "10.0", 10),
    ("c:real", "36000000000.0", 36_000_000_000),
    ("c:real", "real(nan)", None),
    ("c:real", "real(+inf)", None),
    ("c:bool", "false", 0),
    ("c:dynamic", "dynamic(10)", 10),
    ("c:dynamic", "dynamic(-10)", -10),  # DuckDB's json_type: BIGINT, where 10 is UBIGINT
    ("c:dynamic", "dynamic(20.5)", 20),
    ("c:dynamic", "dynamic(false)", 0),
    ("c:dynamic", "dynamic('01:00:00')", 36_000_000_000),
    ("c:dynamic", "dynamic(' 1.00:00:00 ')", 864_000_000_000),
    ("c:dynamic", "dynamic('abc')", None),
    ("c:dynamic", "dynamic([1])", None),
    ("c:dynamic", "dynamic({'a':1})", None),
    ("c:dynamic", "dynamic(null)", None),
    ("c:string", "'1.02:03:04'", 937_840_000_000),
    ("c:string", "' 1.00:00:00 '", 864_000_000_000),
    ("c:string", "'\t2.00:00:00'", 1_728_000_000_000),
    ("c:string", "' -1.02:03:04'", -937_840_000_000),
    ("c:string", "'00:00:01.1234560'", 11_234_560),
    ("c:string", "'abc'", None),
    ("c:string", "'10'", None),
    ("c:guid", "guid(74be27de-1e4e-49d9-b579-fe0b331d3642)", None),
]


@pytest.mark.parametrize(("declared", "value", "ticks"), MEASURED)
def test_converts_as_kusto_does(declared: str, value: str, ticks: object) -> None:
    assert _ticks(declared, value) == ticks


def test_a_timespan_column_is_itself() -> None:
    """The report: this was a BinderException."""
    con = duckdb_kql.connect()
    con.execute("CREATE TABLE T AS SELECT INTERVAL 90 MINUTE AS ts")
    assert duckdb_kql.kql(con, "T | project x = totimespan(ts)").fetchall() == [
        (dt.timedelta(minutes=90),)
    ]


@pytest.mark.parametrize(
    ("declared", "value"),
    [
        ("c:long", "123"),  # Kusto: 00:00:00.0000123
        ("c:int", "int(5)"),  # 00:00:00.0000005
        ("c:real", "1.5"),  # one tick, truncated
        ("c:bool", "true"),  # one tick
        ("c:dynamic", "dynamic(5)"),
        ("c:string", "'00:00:00.0000005'"),
        ("c:string", "'00:00:00.00000049'"),  # Kusto drops the eighth digit, not the seventh
    ],
)
def test_a_tick_that_is_not_a_whole_microsecond_is_an_error(declared: str, value: str) -> None:
    with pytest.raises(duckdb.Error, match="duckdb-kql"):
        _ticks(declared, value)


def test_rounding_to_microseconds_would_have_answered() -> None:
    """What `ticks // 10` gives for 123 ticks: 12 µs, where Kusto has 12.3."""
    con = duckdb.connect()
    assert con.sql("SELECT epoch_us(to_microseconds(123 // 10)) * 10").fetchall() == [(120,)]


def test_an_eighth_digit_is_dropped_as_kusto_drops_it() -> None:
    assert _ticks("c:string", "'00:00:00.00000009'") == 0


@pytest.mark.parametrize("literal", ["time(00:00:00.0000005)", "1tick", "time(1.00:00:00.1234567)"])
def test_a_sub_microsecond_literal_is_refused(literal: str) -> None:
    with pytest.raises(KqlUnsupportedError):
        duckdb_kql.to_sql(f"print x = {literal}")


def test_a_whole_microsecond_seven_digit_literal_is_kept() -> None:
    query = "print x = tolong(time(00:00:01.1234560))"
    assert duckdb_kql.kql(duckdb_kql.connect(), query).fetchall() == [(11_234_560,)]


def test_timespan_of_a_column_is_not_a_literal() -> None:
    """`timespan(l)` is literal syntax, which Kusto refuses for a column; it
    reached DuckDB as `INTERVAL 'l'` and failed there."""
    with pytest.raises(KqlUnsupportedError):
        duckdb_kql.to_sql("datatable(l:long)[1] | project x = timespan(l)")
