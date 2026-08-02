"""Comparison-engine tests — the semantics in docs/test-plan.md §4.2.

These guard the comparison rules themselves. Getting them wrong is costly in
both directions: too strict floods the suite with false failures, too loose
hides real KQL/DuckDB divergence.
"""

from __future__ import annotations

import pytest

from duckdb_kql.comparison import (
    ComparisonOptions,
    compare,
    is_order_significant,
    normalize_type,
    uses_approximate_function,
)


def tbl(columns, rows, types=None):
    return {"columns": columns, "rows": rows, "column_types": types or []}


# --- row ordering ---------------------------------------------------------

def test_unordered_by_default_ignores_row_order():
    a = tbl(["k"], [[1], [2], [3]])
    b = tbl(["k"], [[3], [1], [2]])
    assert compare(a, b).equal


def test_ordered_comparison_respects_row_order():
    a = tbl(["k"], [[1], [2]])
    b = tbl(["k"], [[2], [1]])
    assert not compare(a, b, ComparisonOptions(ordered=True)).equal


def test_unordered_still_catches_wrong_multiset():
    """Order-insensitive must not become value-insensitive."""
    a = tbl(["k"], [[1], [1], [2]])
    b = tbl(["k"], [[1], [2], [2]])
    result = compare(a, b)
    assert not result.equal
    assert any("missing" in d or "unexpected" in d for d in result.differences)


@pytest.mark.parametrize(
    ("kql", "ordered"),
    [
        ("T | where x > 1", False),
        ("T | sort by x asc", True),
        ("T | order by x", True),
        ("T | top 5 by x", True),
        # Ordering discarded by a later stage — order is no longer meaningful.
        ("T | sort by x | summarize count()", False),
        ("T | sort by x | join (U) on k", False),
        ("T | sort by x | union U", False),
        # Ordering that survives to the end.
        ("T | sort by x | project a, b", True),
    ],
)
def test_order_significance_inferred_from_query(kql, ordered):
    assert is_order_significant(kql) is ordered


# --- approximation (R11) --------------------------------------------------

@pytest.mark.parametrize(
    "kql",
    ["T | summarize dcount(x)", "T | summarize percentile(x, 95)", "T | summarize dcountif(x, y)"],
)
def test_approximate_functions_detected(kql):
    assert uses_approximate_function(kql)


def test_approximate_query_gets_loose_tolerance():
    opts = ComparisonOptions.for_query("T | summarize d = dcount(x)")
    assert opts.rel_tolerance >= 0.01
    # An HLL estimate a couple of percent off must not fail.
    assert compare(tbl(["d"], [[1000]]), tbl(["d"], [[1020]]), opts).equal


def test_exact_query_keeps_tight_tolerance():
    opts = ComparisonOptions.for_query("T | summarize c = count()")
    assert not compare(tbl(["c"], [[1000]]), tbl(["c"], [[1020]]), opts).equal


# --- type normalization ---------------------------------------------------

@pytest.mark.parametrize(
    ("kusto", "duckdb"),
    [
        ("long", "BIGINT"),
        ("int", "INTEGER"),
        ("Int64", "bigint"),
        ("real", "DOUBLE"),
        ("string", "VARCHAR"),
        ("datetime", "TIMESTAMP"),
        ("timespan", "INTERVAL"),
        ("dynamic", "JSON"),
        ("bool", "BOOLEAN"),
        ("guid", "UUID"),
    ],
)
def test_engine_type_names_normalize_to_same_bucket(kusto, duckdb):
    assert normalize_type(kusto) == normalize_type(duckdb)


def test_distinct_types_do_not_collide():
    assert normalize_type("string") != normalize_type("long")
    assert normalize_type("datetime") != normalize_type("timespan")


def test_parameterized_type_is_normalized():
    assert normalize_type("DECIMAL(38,9)") == normalize_type("real")


# --- values ---------------------------------------------------------------

def test_null_only_equals_null():
    assert compare(tbl(["a"], [[None]]), tbl(["a"], [[None]])).equal
    assert not compare(tbl(["a"], [[None]]), tbl(["a"], [[0]])).equal
    assert not compare(tbl(["a"], [[None]]), tbl(["a"], [[""]])).equal


def test_float_and_equivalent_int_compare_equal():
    assert compare(tbl(["a"], [[2.0]]), tbl(["a"], [[2]])).equal


def test_nan_equals_nan():
    assert compare(tbl(["a"], [[float("nan")]]), tbl(["a"], [[float("nan")]]),
                   ComparisonOptions(ordered=True)).equal


def test_nested_structures_compare_by_value():
    a = tbl(["d"], [[{"x": [1, 2], "y": None}]])
    b = tbl(["d"], [[{"y": None, "x": [1, 2]}]])
    assert compare(a, b).equal


def test_bool_not_confused_with_int():
    assert not compare(tbl(["a"], [[True]]), tbl(["a"], [[2]])).equal


# --- shape ----------------------------------------------------------------

def test_column_count_mismatch_fails_fast():
    assert not compare(tbl(["a"], [[1]]), tbl(["a", "b"], [[1, 2]])).equal


def test_column_names_checked_by_default():
    """summarize auto-naming is user-visible (R12), so names matter."""
    assert not compare(tbl(["count_"], [[1]]), tbl(["Count"], [[1]])).equal


def test_row_count_mismatch_reported():
    assert not compare(tbl(["a"], [[1], [2]]), tbl(["a"], [[1]])).equal


def test_allow_prefix_handles_truncated_doc_output():
    """Docs often show only the first N rows."""
    expected = tbl(["a"], [[1], [2]])
    actual = tbl(["a"], [[1], [2], [3], [4]])
    assert compare(expected, actual, ComparisonOptions(ordered=True, allow_prefix=True)).equal
    assert not compare(expected, actual, ComparisonOptions(ordered=True)).equal


def test_missing_tables():
    assert compare(None, None).equal
    assert not compare(None, tbl(["a"], [[1]])).equal
