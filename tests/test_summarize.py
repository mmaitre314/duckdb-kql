"""L5 trap tests — ``summarize`` (``docs/test-plan.md`` §6).

Every expectation was **measured against the Kusto Emulator**. Two families
matter, and neither is guessable:

* **R12 output naming.** ``count()`` is ``count_``, ``make_list(x)`` is
  ``list_x``, ``sum(x + z)`` is ``sum_`` — column names are user-visible, so a
  near-miss is a wrong answer.
* **Empty and all-null groups.** KQL returns a *neutral value* where SQL returns
  NULL, and the two disagree for almost every aggregate. This hits any group
  whose values are all null, not just empty input.

Reproduce any line here with::

    docker compose up -d kusto
    python -c "from duckdb_kql.oracle import KustoEmulator as K; \
               print(K('http://localhost:8080').query(\"datatable(x:int)[] | summarize sum(x)\"))"
"""

from __future__ import annotations

import math

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")

T = (
    "datatable(x:int, y:string, t:datetime)["
    "1,'a',datetime(2007-01-01), 2,'b',datetime(2007-01-02), 3,'a',datetime(2007-01-03)]"
)
EMPTY = "datatable(x:int, y:string)[]"
ALL_NULL = "datatable(x:int)[int(null), int(null)]"
WITH_NULL = "datatable(x:int, y:string)[1,'a', int(null),'a', 3,'b']"


def _run(kql: str):
    con = duckdb.connect()
    rel = duckdb_kql.sql(con, kql)
    return list(rel.columns), rel.fetchall()


def _one(kql: str):
    return _run(kql)[1][0][0]


# --- R12: auto-generated column names --------------------------------------
@pytest.mark.parametrize(
    ("agg", "expected"),
    [
        ("count()", "count_"),
        ("countif(x > 1)", "countif_"),          # the predicate contributes nothing
        ("sum(x)", "sum_x"),
        ("sumif(x, x > 1)", "sumif_x"),
        ("avg(x)", "avg_x"),
        ("min(x)", "min_x"),
        ("max(x)", "max_x"),
        ("dcount(x)", "dcount_x"),
        ("stdev(x)", "stdev_x"),
        ("variance(x)", "variance_x"),
        ("make_list(x)", "list_x"),              # 'make_' is dropped
        ("make_set(y)", "set_y"),
        ("any(x)", "any_x"),
        ("take_any(x)", "x"),                    # keeps the column's own name
        ("percentile(x, 50)", "percentile_x_50"),
        ("sum(x + 1)", "sum_"),                  # not a bare column -> no suffix
    ],
)
def test_aggregate_output_names(agg: str, expected: str) -> None:
    assert _run(f"{T} | summarize {agg}")[0] == [expected]


@pytest.mark.parametrize(
    ("clause", "expected"),
    [
        ("summarize count() by y", ["y", "count_"]),
        ("summarize c = count() by y", ["y", "c"]),
        # A `by` key that is a function keeps the inner column's name.
        ("summarize count() by bin(t, 1d)", ["t", "count_"]),
        ("summarize count() by tostring(x)", ["x", "count_"]),
        ("summarize count() by k = x", ["k", "count_"]),
    ],
)
def test_group_key_names_and_order(clause: str, expected: list[str]) -> None:
    """Grouping keys come FIRST, whatever the query text order."""
    assert _run(f"{T} | {clause}")[0] == expected


# --- neutral values where SQL would give NULL ------------------------------
@pytest.mark.parametrize("source", [EMPTY, ALL_NULL], ids=["empty", "all-null"])
@pytest.mark.parametrize(
    ("agg", "expected"),
    [
        ("sum(x)", 0),
        ("dcount(x)", 0),
        ("countif(x > 0)", 0),
        ("sumif(x, x > 0)", 0),
        ("stdev(x)", 0.0),
        ("variance(x)", 0.0),
        ("make_list(x)", []),
        ("make_set(x)", []),
        ("min(x)", None),   # min/max DO stay null
        ("max(x)", None),
    ],
)
def test_neutral_values_not_null(source: str, agg: str, expected) -> None:
    """A plain SQL aggregate returns NULL here; KQL does not."""
    assert _one(f"{source} | summarize {agg}") == expected


def test_count_counts_rows_not_values() -> None:
    """count() is about rows, so nulls still count — unlike every aggregate above."""
    assert _one(f"{EMPTY} | summarize count()") == 0
    assert _one(f"{ALL_NULL} | summarize count()") == 2
    assert _one(f"{ALL_NULL} | summarize dcount(x)") == 0


@pytest.mark.parametrize("source", [EMPTY, ALL_NULL], ids=["empty", "all-null"])
def test_avg_of_nothing_is_nan_not_null(source: str) -> None:
    """avg is the odd one out: NaN, not 0 and not null."""
    value = _one(f"{source} | summarize avg(x)")
    assert value is not None and math.isnan(value)


def test_grouped_empty_input_yields_no_rows() -> None:
    """With `by`, an empty input produces zero rows — not one neutral row."""
    assert _run(f"{EMPTY} | summarize count() by y")[1] == []


# --- null handling within groups (R4) --------------------------------------
def test_aggregates_ignore_nulls() -> None:
    assert _one(f"{WITH_NULL} | summarize count()") == 3      # counts ROWS
    assert _one(f"{WITH_NULL} | summarize sum(x)") == 4
    assert _one(f"{WITH_NULL} | summarize avg(x)") == 2.0
    assert _one(f"{WITH_NULL} | summarize dcount(x)") == 2


def test_make_list_skips_nulls() -> None:
    """DuckDB's list() keeps nulls; KQL drops them."""
    assert _one(f"{WITH_NULL} | summarize make_list(x)") == [1, 3]


def test_null_group_key_is_kept() -> None:
    """A null grouping key forms its own group rather than dropping rows."""
    cols, rows = _run(
        "datatable(x:int, y:string)[1,'a', 2,''] | summarize count() by y"
    )
    assert cols == ["y", "count_"]
    assert len(rows) == 2


# --- bin() -----------------------------------------------------------------
def test_bin_datetime_uses_the_unix_epoch() -> None:
    """DuckDB's time_bucket origin is 2000-01-03, which would shift every bucket."""
    import datetime as dt

    assert _one("print bin(datetime(2007-03-05 12:34), 1d)") == dt.datetime(2007, 3, 5)


def test_bin_of_a_timespan_stays_a_timespan() -> None:
    """bin(14d + 3h, 1d) is 14 days — not a date in 1970."""
    import datetime as dt

    assert _one("print bin(14d + 3h, 1d)") == dt.timedelta(days=14)


def test_bin_numeric() -> None:
    assert _one("print bin(4.5, 1)") == 4.0
