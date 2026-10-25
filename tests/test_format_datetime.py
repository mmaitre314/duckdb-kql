"""Trap tests — `format_datetime` follows Kusto's measured format parser, not .NET's documented one.

Kusto's format elements look like .NET's custom date format strings, and the
Microsoft page describes them in .NET's terms. Measured, three things differ,
and each would be a silent wrong answer if implemented from the documentation:

* **`F` keeps trailing zeros.** `FFFFFF` of `.0617500` is `061750`; .NET's — and
  the documented table's — `FFFFFFF` of `.0001150` is `000115`. Kusto's `F` is
  the same digits as `f`, or nothing when they are all zero (`FF` of `.0050000`
  is empty).
* **An empty `F` keeps the `.` before it.** `ss.FFF` of a whole second is `05.`;
  .NET drops the separator.
* **A long run is greedy repeats, not a wider field.** `ddd` is `dd` then `d`
  (`055` on the 5th, not a day name) and `yyyyy` is `yyyy` then `y`.

The format is parsed at translation time, so it must be a non-empty literal, as
Kusto requires (SEM0207, SEM0217), and an unknown letter or a delimiter outside
Kusto's list (`T`, `+`, a tab, `%d`) is refused, as Kusto refuses it
(SEM0255). Three things Kusto answers are refused here on purpose:

* `fffffff` and `FFFFFFF` — the seventh digit is the 100ns tick, which DuckDB
  does not store, as with `datetime_part("nanosecond", …)`;
* `yyy` — Kusto answers it as `yy` when it ends the format, and refuses it
  anywhere else (`yyy-`, `yyyMM`), which is a parser accident, not a rule.

A null date is null over rows. Kusto's constant folder answers `''` for
`format_datetime(datetime(null), …)`; the row engine is what a query reaches.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlError

duckdb = pytest.importorskip("duckdb")

DATES = [
    "datetime(2009-06-05T03:04:05.0617500)",
    "datetime(2009-06-05T00:00:00)",
    "datetime(2009-06-05T12:00:00)",
    "datetime(2009-06-05T13:07:09)",
    "datetime(0001-01-01)",
    "datetime(0900-01-01)",
    "datetime(2009-06-05T03:04:05.0050000)",
    "datetime(2009-06-05T03:04:05.0001150)",
]

#: format -> the answer for each of DATES, in order. Measured over rows.
MEASURED = {
    "h hh H HH tt": [
        "3 03 3 03 AM", "12 12 0 00 AM", "12 12 12 12 PM", "1 01 13 13 PM",
        "12 12 0 00 AM", "12 12 0 00 AM", "3 03 3 03 AM", "3 03 3 03 AM",
    ],
    "y yy yyyy": [
        "9 09 2009", "9 09 2009", "9 09 2009", "9 09 2009",
        "1 01 0001", "0 00 0900", "9 09 2009", "9 09 2009",
    ],
    "d dd M MM m mm s ss": [
        "5 05 6 06 4 04 5 05", "5 05 6 06 0 00 0 00", "5 05 6 06 0 00 0 00",
        "5 05 6 06 7 07 9 09", "1 01 1 01 0 00 0 00", "1 01 1 01 0 00 0 00",
        "5 05 6 06 4 04 5 05", "5 05 6 06 4 04 5 05",
    ],
    "f ff fff ffff fffff ffffff": [
        "0 06 061 0617 06175 061750", "0 00 000 0000 00000 000000",
        "0 00 000 0000 00000 000000", "0 00 000 0000 00000 000000",
        "0 00 000 0000 00000 000000", "0 00 000 0000 00000 000000",
        "0 00 005 0050 00500 005000", "0 00 000 0001 00011 000115",
    ],
    "F|FF|FFF|FFFF|FFFFF|FFFFFF".replace("|", "_"): [
        "_06_061_0617_06175_061750", "_____", "_____", "_____", "_____", "_____",
        "__005_0050_00500_005000", "___0001_00011_000115",
    ],
    "ss.FFF": ["05.061", "00.", "00.", "09.", "00.", "00.", "05.005", "05."],
    "ddd MMM hhh tttt yyyyy": [
        "055 066 033 AMAM 20099", "055 066 1212 AMAM 20099", "055 066 1212 PMPM 20099",
        "055 066 011 PMPM 20099", "011 011 1212 AMAM 00011", "011 011 1212 AMAM 09000",
        "055 066 033 AMAM 20099", "055 066 033 AMAM 20099",
    ],
    "yyyy-MM-dd [HH:mm:ss],_/": [
        "2009-06-05 [03:04:05],_/", "2009-06-05 [00:00:00],_/", "2009-06-05 [12:00:00],_/",
        "2009-06-05 [13:07:09],_/", "0001-01-01 [00:00:00],_/", "0900-01-01 [00:00:00],_/",
        "2009-06-05 [03:04:05],_/", "2009-06-05 [03:04:05],_/",
    ],
}


@pytest.mark.parametrize("fmt", list(MEASURED))
def test_matches_kusto_over_rows(fmt: str) -> None:
    query = (
        f"datatable(i:long, t:datetime)[{', '.join(f'{i}, {d}' for i, d in enumerate(DATES))}]"
        f" | order by i asc | project x = format_datetime(t, '{fmt}')"
    )
    rows = duckdb_kql.kql(duckdb_kql.connect(), query).fetchall()
    assert [r[0] for r in rows] == MEASURED[fmt]


def test_the_documented_examples() -> None:
    query = (
        "let dt = datetime(2017-01-29 09:00:05); print "
        "v1 = format_datetime(dt, 'yy-MM-dd [HH:mm:ss]'), "
        "v2 = format_datetime(dt, 'yyyy-M-dd [H:mm:ss]'), "
        "v3 = format_datetime(dt, 'yy-MM-dd [hh:mm:ss tt]')"
    )
    assert duckdb_kql.kql(duckdb_kql.connect(), query).fetchall() == [
        ("17-01-29 [09:00:05]", "2017-1-29 [9:00:05]", "17-01-29 [09:00:05 AM]")
    ]


def test_a_null_date_is_null_over_rows() -> None:
    con = duckdb_kql.connect()
    con.execute("CREATE TABLE T AS SELECT NULL::TIMESTAMP AS t")
    query = (
        "T | project a = isnull(format_datetime(t, 'yyyy')), "
        "b = isnull(format_datetime(t, 'ss.FFF'))"
    )
    assert duckdb_kql.kql(con, query).fetchall() == [(True, True)]


def test_a_table_column_is_formatted_once_per_row() -> None:
    con = duckdb_kql.connect()
    con.execute("CREATE TABLE T(t TIMESTAMP)")
    con.execute("INSERT INTO T VALUES ('2021-02-03 04:05:06.25'), ('2020-01-01 01:02:03.5')")
    query = "T | order by t asc | project x = format_datetime(t, 'yyyy-MM-dd HH:mm:ss.FF')"
    assert duckdb_kql.kql(con, query).fetchall() == [
        ("2020-01-01 01:02:03.50",), ("2021-02-03 04:05:06.25",)
    ]


@pytest.mark.parametrize(
    "fmt",
    ["t", "ttt", "z", "K", "T", "x", " at ", "+", "%d", "yyyy\\tMM",
     "yyy", "yyy-", "yyyMM", "fffffff", "FFFFFFF", "ffffffff", ""],
)
def test_refused(fmt: str) -> None:
    with pytest.raises(KqlError):
        duckdb_kql.to_sql(f"print x = format_datetime(datetime(2009-06-05), '{fmt}')")


@pytest.mark.parametrize(
    "query",
    [
        "datatable(t:datetime, f:string)[datetime(2009-06-05), 'yyyy']"
        " | project x = format_datetime(t, f)",
        "print x = format_datetime('2009-06-05', 'yyyy')",
        "print x = format_datetime(1d, 'HH')",
        "print x = format_datetime(datetime(2009-06-05))",
    ],
)
def test_what_kusto_refuses_is_refused(query: str) -> None:
    """SEM0207, SEM0254 twice and SEM0219 on the emulator."""
    with pytest.raises(KqlError):
        duckdb_kql.to_sql(query)


def test_summarize_by_passes_the_name_through() -> None:
    cursor = duckdb_kql.kql(
        duckdb_kql.connect(),
        "datatable(t:datetime)[datetime(2020-01-01)]"
        " | summarize count() by format_datetime(t, 'yyyy')",
    )
    assert [d[0] for d in cursor.description] == ["t", "count_"]
