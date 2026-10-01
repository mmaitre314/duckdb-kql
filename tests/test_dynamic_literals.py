"""Trap tests — what `dynamic(...)` and `pack_array(...)` store, element by element.

KQL's JSON dialect inside `dynamic(...)` admits values JSON has no spelling
for: `datetime(…)`, a timespan, `guid(…)`, `long(…)`/`real(…)`, a hex number,
adjacent strings and a nested `dynamic(…)`. They used to reach DuckDB as their
source text — `[datetime(2020-01-01)]` — and fail as malformed JSON.

The obvious fix is wrong in two ways, both measured on the emulator:

* **Each kind is stored differently.** A datetime is the *string*
  `"2020-01-01T00:00:00.0000000Z"`, but a timespan is a *long* tick count —
  `dynamic([1d])` is `[864000000000]`, while `pack_array(1d)` stringifies it as
  `"1.00:00:00"`. One rule for "typed literals" gets one of them wrong.
* **The quiet sibling.** `pack_array(datetime(2020-01-01))` never failed: it
  answered `["2020-01-01 00:00:00"]`, DuckDB's spelling, where Kusto stores the
  same `"2020-01-01T00:00:00.0000000Z"` as the literal does. Fixing only the
  loud half would have left two spellings for one value.
"""

from __future__ import annotations

import duckdb
import pytest

import duckdb_kql
from duckdb_kql.errors import KqlUnsupportedError


def text(expression: str) -> str:
    (value,), = duckdb_kql.kql(duckdb.connect(), f"print t = tostring({expression})").fetchall()
    return str(value)


@pytest.mark.parametrize(
    ("literal", "stored"),
    [
        ("dynamic([datetime(2020-01-01), datetime(2020-01-02 03:04:05.6)])",
         '["2020-01-01T00:00:00.0000000Z","2020-01-02T03:04:05.6000000Z"]'),
        # Every datetime literal form todatetime() accepts, the comma form too.
        ("dynamic([datetime(2025, 6, 14), datetime('2020-01-01'), datetime(2020-01-01 10:20)])",
         '["2025-06-14T00:00:00.0000000Z","2020-01-01T00:00:00.0000000Z",'
         '"2020-01-01T10:20:00.0000000Z"]'),
        ('dynamic({"k": [datetime(2021-02-03), 1d]})',
         '{"k":["2021-02-03T00:00:00.0000000Z",864000000000]}'),
        # Timespans are ticks, in every unit and in the clock form.
        ("dynamic([1d, 90m, 1.5h, 1tick, 10ms, 5microseconds, 2days, time(1.02:03:04),"
         " time(00:01:30.5), 1s, timespan(2d), 3hours, 1.5d, 7minutes, 2seconds,"
         " 3milliseconds, 4ticks])",
         "[864000000000,54000000000,54000000000,1,100000,50,1728000000000,937840000000,"
         "905000000,10000000,1728000000000,108000000000,1296000000000,4200000000,"
         "20000000,30000,4]"),
        ("dynamic([time(1.02:03:04.1234567), time(-1.00:00:00), time(10:00), time(2)])",
         "[937841234567,-864000000000,360000000000,1728000000000]"),
        # Whole nanoseconds round down.
        ("dynamic([150nanoseconds, 250nanoseconds, 199nanoseconds])", "[1,2,1]"),
        ("dynamic([guid(ABCDEF00-0000-0000-0000-000000000001)])",
         '["abcdef00-0000-0000-0000-000000000001"]'),
        ("dynamic([long(5), real(1.5), 0x10, -3, true, long(null), real(nan)])",
         '[5,1.5,16,-3,true,null,"NaN"]'),
        ('dynamic({"a": dynamic(null), "b": dynamic({"c": 1})})', '{"a":null,"b":{"c":1}}'),
        # Adjacent literals concatenate: `'it''s'` is two of them, and reads `its`.
        ("""dynamic([@"a\\b", 'it''s', "q\\"x", 'x' 'y' "z", 'say "hi"'])""",
         '["a\\\\b","its","q\\"x","xyz","say \\"hi\\""]'),
    ],
)
def test_a_dynamic_literal_stores_what_kusto_stores(literal: str, stored: str) -> None:
    assert text(literal) == stored


def test_a_stored_datetime_converts_back() -> None:
    q = "print c = todatetime(dynamic([datetime(2020-01-02 03:04:05.6)])[0])"
    (value,), = duckdb_kql.kql(duckdb.connect(), q).fetchall()
    assert str(value) == "2020-01-02 03:04:05.600000"


@pytest.mark.parametrize(
    "literal",
    [
        "dynamic([datetime(null)])",  # Kusto: a typed null cannot appear here
        "dynamic([bool(true)])",      # Kusto: not recognized as a valid Boolean
        "dynamic([1.5ticks])",        # measured 1, while 0.5microseconds is 0
    ],
)
def test_refused(literal: str) -> None:
    with pytest.raises(KqlUnsupportedError):
        duckdb_kql.to_sql(f"print d = {literal}")


def test_pack_array_spells_a_datetime_as_the_literal_does() -> None:
    """The quiet sibling: measured `["2020-01-01T00:00:00.0000000Z", …]`; it was
    DuckDB's `"2020-01-01 00:00:00"`."""
    assert text('pack_array(datetime(2020-01-01), "a", 1, dynamic([1]))') == (
        '["2020-01-01T00:00:00.0000000Z","a",1,[1]]'
    )
    assert text('bag_pack("a", datetime(2020-01-01), "b", "x")') == (
        '{"a":"2020-01-01T00:00:00.0000000Z","b":"x"}'
    )


def test_pack_array_of_a_datetime_column() -> None:
    """A column carries no type at translation time, so the choice is run-time."""
    q = (
        'datatable(t:datetime, s:string)[datetime(2021-02-03), "x"]'
        ' | project p = tostring(pack_array(t, s)), b = tostring(bag_pack("t", t))'
    )
    assert duckdb_kql.kql(duckdb.connect(), q).fetchall() == [
        ('["2021-02-03T00:00:00.0000000Z","x"]', '{"t":"2021-02-03T00:00:00.0000000Z"}')
    ]
