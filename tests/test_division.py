"""`/` — KQL divides two integers as integers.

`7 / 2` is **3** in Kusto and was `3.5` here: SQL's `/` promotes to double. A
silently wrong number in the most ordinary arithmetic there is, and wrong
everywhere it appeared — `project`, `extend`, `summarize`, a `where` predicate.

The mapping is DuckDB's `//`, which despite the spelling is not floor division:
it truncates toward zero on integers (`-7 // 2` is `-3`, not `-4`) **and behaves
as ordinary division on floats** (`7.5 // 2` is `3.75`). That is exactly KQL's
rule, and DuckDB decides it from the operand types — which is the type
information the translator does not have.

Every expectation below is the emulator's answer for `print z = <expr>`.
Reproduce with:

    docker compose up -d kusto
    curl -s localhost:8080/v1/rest/query -H 'Content-Type: application/json' \
      -d '{"db":"NetDefaultDB","csl":"print z = 7 / 2"}'
"""

from __future__ import annotations

import math

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")

#: `(expression, value)` — measured on the Kusto Emulator.
MEASURED: list[tuple[str, object]] = [
    # Integer division, and it truncates toward zero rather than flooring.
    ("7 / 2", 3),
    ("-7 / 2", -3),
    ("7 / -2", -3),
    ("-7 / -2", 3),
    ("-1 / 2", 0),
    ("7 / 2 / 2", 1),
    ("int(7) / int(2)", 3),
    ("long(7) / long(2)", 3),
    # No precision lost on the way through: a double round trip would.
    ("9223372036854775807 / 2", 4611686018427387903),
    # One real operand makes the whole thing real.
    ("7.0 / 2", 3.5),
    ("7 / 2.0", 3.5),
    ("7.5 / 2", 3.75),
    ("-7.5 / 2", -3.75),
    ("7.0 / 2.0", 3.5),
    ("toreal(7) / 2", 3.5),
    ("todouble(7) / 2", 3.5),
    ("decimal(7) / decimal(2)", 3.5),
    # Division by zero: null for integers, ±Infinity for reals.
    ("1 / 0", None),
    ("0 / 0", None),
    ("1.0 / 0", math.inf),
    ("1.0 / 0.0", math.inf),
    ("-1.0 / 0", -math.inf),
    # Null propagates.
    ("long(null) / 2", None),
    ("2 / long(null)", None),
]


@pytest.fixture(scope="module")
def con():
    return duckdb_kql.connect()


@pytest.mark.parametrize("expression,expected", MEASURED, ids=[m[0] for m in MEASURED])
def test_it_matches_the_emulator(con, expression, expected) -> None:
    (row,) = duckdb_kql.kql(con, f"print z = {expression}").fetchall()
    actual = row[0]
    if expected is None:
        assert actual is None
    elif isinstance(expected, float):
        assert float(actual) == expected
    else:
        # Value *and* integer-ness: 3.0 would be the old bug wearing a
        # rounder hat, and Kusto reports the column as `long`.
        assert actual == expected
        assert not isinstance(actual, float), f"{expression} came back as a float"


def test_integer_division_holds_over_columns_too(con) -> None:
    """The literal cases could be constant-folded; a column proves the mapping."""
    rows = duckdb_kql.kql(
        con, "datatable(a:long, b:long)[7,2, -7,2, 1,0] | project z = a / b"
    ).fetchall()
    assert [r[0] for r in rows] == [3, -3, None]


def test_a_real_column_still_divides_as_a_real(con) -> None:
    rows = duckdb_kql.kql(
        con, "datatable(a:real, b:long)[7.5,2, 7.0,2] | project z = a / b"
    ).fetchall()
    assert [r[0] for r in rows] == [3.75, 3.5]


def test_the_docs_idiom_for_forcing_float_division_works(con) -> None:
    """`1.0 * x / y` is how the KQL docs force a float divide.

    It is also the case that pins `_is_real_expr` seeing *through* the
    multiplication: without that, `y = 0` gives null instead of ±Infinity and
    three corpus cases fail.
    """
    rows = duckdb_kql.kql(
        con, "range x from -1 to 1 step 1 | extend y = 0.0 | extend div = 1.0*x/y"
    ).fetchall()
    values = [r[2] for r in rows]
    assert values[0] == -math.inf
    assert math.isnan(values[1])
    assert values[2] == math.inf


def test_timespan_division_is_untouched(con) -> None:
    """`/` over timespans yields a number, and that path runs before this one."""
    (row,) = duckdb_kql.kql(con, "print z = 2h / 1h").fetchall()
    assert row[0] == 2


def test_the_emitted_sql_uses_the_truncating_operator() -> None:
    """Pinned because `/` looks equally plausible and is silently wrong."""
    assert "//" in str(duckdb_kql.to_sql("print z = 7 / 2"))
    # ... and plain `/` where an operand is visibly a real, so that division by
    # zero gives Infinity rather than null.
    sql = str(duckdb_kql.to_sql("print z = 1.0 / 0"))
    assert "//" not in sql
