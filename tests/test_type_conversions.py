"""Trap tests — `gettype`, a timespan's string form, and the numeric conversions.

Each of these answered **quietly wrong**, with a plausible value of the right
type, and every expectation below is the emulator's:

* `gettype` was `lower(json_type(x))`: DuckDB's names (`ubigint`, `varchar`,
  `boolean`) for a dynamic, and `string` for a typed null — Kusto keeps the
  type, `gettype(long(null))` is `long`.
* a timespan printed as DuckDB spells an INTERVAL — `1 day`, `02:03:04.5` —
  through `tostring`, `strcat` and `pack_array` alike, where KQL prints
  `1.00:00:00` and `02:03:04.5000000`.
* `tolong(1d)` was **null**. KQL converts a timespan to its tick count and a
  datetime to its ticks since 0001-01-01.
* `tolong("5.7")` was 5 and `tolong("1e3")` 1000: DuckDB's cast reads both,
  KQL's integer syntax reads neither.
* `toint(9999999999)` was null; Kusto wraps a long to 32 bits.

The obvious fix for the last one — wrap every out-of-range `toint` — is wrong
for the other inputs: a real **saturates** over rows (`toint(1e10)` is
2147483647), a string overflows to **null**, and Kusto's constant folder
disagrees with its own row engine on a real literal (-2147483648), which is
therefore refused.
"""

from __future__ import annotations

import json

import duckdb
import pytest

import duckdb_kql
from duckdb_kql.errors import KqlUnsupportedError


def row(kql: str) -> tuple:
    (result,) = duckdb_kql.kql(duckdb.connect(), kql).fetchall()
    return tuple(result)


def rows(kql: str) -> list[tuple]:
    return duckdb_kql.kql(duckdb.connect(), kql).fetchall()


# ---------------------------------------------------------------------------
# gettype
# ---------------------------------------------------------------------------


def test_gettype_of_scalars_and_typed_nulls() -> None:
    assert row(
        "print gettype(1), gettype(int(1)), gettype(1.5), gettype('s'), gettype(true),"
        " gettype(datetime(2020-01-01)), gettype(1d),"
        " gettype(guid(00000000-0000-0000-0000-000000000001)),"
        " gettype(long(null)), gettype(int(null)), gettype(datetime(null))"
    ) == ("long", "int", "real", "string", "bool", "datetime", "timespan", "guid",
          "long", "int", "datetime")


def test_gettype_of_columns() -> None:
    assert row(
        "datatable(l:long, i:int, r:real, s:string, b:bool, t:datetime, ts:timespan,"
        " g:guid, d:dynamic)[1, 2, 1.5, 'x', true, datetime(2020-01-01), 1d,"
        " guid(00000000-0000-0000-0000-000000000001), dynamic([1])]"
        " | project gettype(l), gettype(i), gettype(r), gettype(s), gettype(b),"
        " gettype(t), gettype(ts), gettype(g), gettype(d), gettype(d[0])"
    ) == ("long", "int", "real", "string", "bool", "datetime", "timespan", "guid",
          "array", "long")


def test_gettype_of_a_dynamic_is_what_it_holds() -> None:
    """Measured: a real inside a dynamic is `double`, not `real`, and an
    object is a `dictionary`; a missing element is `null`."""
    assert row(
        "print gettype(dynamic([1])), gettype(dynamic({'a':1})), gettype(dynamic(null)),"
        " gettype(dynamic(1)), gettype(dynamic('s')), gettype(dynamic(1.5)),"
        " gettype(dynamic(true)), gettype(dynamic([1])[5])"
    ) == ("array", "dictionary", "null", "long", "string", "double", "bool", "null")


# ---------------------------------------------------------------------------
# A timespan's string form
# ---------------------------------------------------------------------------


def test_tostring_of_a_timespan() -> None:
    assert row(
        "print tostring(1d), tostring(time(1.02:03:04.5)), tostring(-2h), tostring(0s),"
        " tostring(timespan(null)), tostring(90m), tostring(1ms), tostring(1.5s),"
        " tostring(100d), tostring(-1.5d), tostring(1microsecond)"
    ) == ("1.00:00:00", "1.02:03:04.5000000", "-02:00:00", "00:00:00", "", "01:30:00",
          "00:00:00.0010000", "00:00:01.5000000", "100.00:00:00", "-1.12:00:00",
          "00:00:00.0000010")


def test_a_timespan_column_in_strcat_and_pack_array() -> None:
    got = rows(
        "datatable(ts:timespan)[1d, time(02:03:04.5)]"
        " | project s = strcat('<', ts, '>'), p = tostring(pack_array(ts)),"
        " b = tostring(bag_pack('t', ts))"
    )
    assert got == [
        ("<1.00:00:00>", '["1.00:00:00"]', '{"t":"1.00:00:00"}'),
        ("<02:03:04.5000000>", '["02:03:04.5000000"]', '{"t":"02:03:04.5000000"}'),
    ]


# ---------------------------------------------------------------------------
# Numeric conversions
# ---------------------------------------------------------------------------


def test_a_timespan_and_a_datetime_convert_to_ticks() -> None:
    assert row(
        "print tolong(1d), toint(1d), todouble(1d), toreal(1.5s), tolong(-2h),"
        " tolong(datetime(2020-01-01)), toint(datetime(2020-01-01))"
    ) == (864000000000, 711573504, 864000000000.0, 15000000.0, -72000000000,
          637134336000000000, -1954807808)


def test_a_string_converts_only_in_kql_integer_syntax() -> None:
    got = rows(
        "datatable(s:string)['5.7', '1e3', ' 7 ', '-3', '0x10', '1,000', '+4', '',"
        " '9999999999', '5.'] | project l = tolong(s), i = toint(s), d = todouble(s)"
    )
    assert got == [
        (None, None, 5.7), (None, None, 1000.0), (7, 7, 7.0), (-3, -3, -3.0),
        (16, 16, None), (None, None, None), (4, 4, 4.0), (None, None, None),
        (9999999999, None, 9999999999.0), (None, None, 5.0),
    ]


def test_toint_wraps_a_long_and_saturates_a_real() -> None:
    assert row(
        "print toint(9999999999), toint(-9999999999), toint(2147483648), toint(4294967296)"
    ) == (1410065407, -1410065407, -2147483648, 0)
    assert rows(
        "datatable(r:real)[5.7, -5.7, 1e10, -1e10, 1e30, real(nan)]"
        " | project tolong(r), toint(r)"
    ) == [(5, 5), (-5, -5), (10000000000, 2147483647), (-10000000000, -2147483648),
          (9223372036854775807, 2147483647), (None, None)]


def test_a_dynamic_converts_as_what_it_holds() -> None:
    got = rows(
        "datatable(d:dynamic)[dynamic(5.7), dynamic('5.7'), dynamic(9999999999),"
        " dynamic(' 7 '), dynamic(true), dynamic([1])]"
        " | project tolong(d), toint(d), todouble(d)"
    )
    assert got == [(5, 5, 5.7), (None, None, 5.7), (9999999999, 1410065407, 9999999999.0),
                   (7, 7, 7.0), (1, 1, 1.0), (None, None, None)]


def test_an_out_of_range_real_constant_is_refused() -> None:
    """Measured: the folder says -2147483648 and the row engine 2147483647."""
    with pytest.raises(KqlUnsupportedError, match="folds"):
        duckdb_kql.to_sql("print toint(1e10)")


# ---------------------------------------------------------------------------
# Literals
# ---------------------------------------------------------------------------


def test_timespan_literals_duckdb_cannot_read() -> None:
    """`time(1.02:03:04)`, `time(2)` and `1milli` failed to translate at all."""
    assert row(
        "print tolong(time(1.02:03:04)), tolong(time(2)), tolong(1milli),"
        " tolong(time(-1.00:00:00)), tolong(10ticks)"
    ) == (937840000000, 1728000000000, 10000, -864000000000, 10)


def test_a_sub_microsecond_timespan_is_refused() -> None:
    """A tick is 100 ns and DuckDB keeps microseconds: `1tick` as zero would
    be a wrong answer, measured `00:00:00.0000001`."""
    with pytest.raises(KqlUnsupportedError, match="microseconds"):
        duckdb_kql.to_sql("print a = 1tick")


def test_a_nan_literal() -> None:
    (a, b, c), = rows("print real(nan), real(+inf), real(-inf)")
    assert a != a and b == float("inf") and c == float("-inf")


def test_a_dynamic_timespan_is_ticks_and_a_pack_array_one_is_text() -> None:
    """Two spellings of one value, both measured."""
    (lit, packed), = rows("print tostring(dynamic([1d])), tostring(pack_array(1d))")
    assert json.loads(lit) == [864000000000]
    assert json.loads(packed) == ["1.00:00:00"]
