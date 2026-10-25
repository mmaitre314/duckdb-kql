"""Trap tests — the trigonometric functions, and the one that is null where DuckDB says NaN.

`sin`, `cos`, `tan`, `cot`, `asin`, `acos`, `atan`, `atan2`, `pi`, `degrees`
and `radians` were missing. Mapping them is mostly DuckDB's function of the same
name, and the two traps are in what the names do not say:

* **`asin` and `acos` outside [-1, 1] are null in Kusto and NaN in DuckDB.**
  Measured folded and over rows: `asin(2)`, `acos(-2)`, `asin(1.0000000001)`
  and `asin(real(+inf))` are null, while `asin(real(nan))` is NaN in both.
  The obvious guard, `CASE WHEN abs(x) > 1 THEN NULL …`, sends the NaN to null
  too: DuckDB orders NaN above every number, so `NaN > 1` is *true* there.
* **A `bool` is a number.** `sin(true)` is 0.8414709848078965 in Kusto, and
  DuckDB has no `sin(BOOLEAN)` — the argument goes through `long_or_wider`,
  which widens a BOOLEAN and still refuses a string or a datetime, as Kusto
  does (SEM0201).

The rest was measured and agrees as DuckDB has it: every result is a `real`;
`cot(0)` is +inf and `cot(-0.0)` -inf; `sin(+inf)` is NaN; `atan2(y, x)` takes
`y` first, and `atan2(0, 0)` is 0. **Residue:** `degrees` and `radians` of a
`decimal` are a `decimal` in Kusto and a `real` here — the value agrees to a
double's precision, the type does not.
"""

from __future__ import annotations

import math

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlError

duckdb = pytest.importorskip("duckdb")


def _one(query: str) -> tuple:
    (row,) = duckdb_kql.kql(duckdb_kql.connect(), query).fetchall()
    return row


#: `(x, asin(x) is null, asin(x) is NaN)` over rows — measured.
UNIT_DOMAIN = [
    ("2.0", True, None),
    ("-2.0", True, None),
    ("1.0000000001", True, None),
    ("real(+inf)", True, None),
    ("real(-inf)", True, None),
    ("real(nan)", False, True),
    ("1.0", False, False),
    ("-1.0", False, False),
    ("real(null)", True, None),
]


@pytest.mark.parametrize("fn", ["asin", "acos"])
@pytest.mark.parametrize(("value", "is_null", "is_nan"), UNIT_DOMAIN)
def test_outside_the_unit_interval_is_null_and_nan_stays_nan(
    fn: str, value: str, is_null: bool, is_nan: bool | None
) -> None:
    query = (
        f"datatable(x:real)[{value}] | project n = isnull({fn}(x)), "
        f"nan = isnan({fn}(x))"
    )
    assert _one(query) == (is_null, is_nan)


def test_the_obvious_guard_would_turn_nan_into_null() -> None:
    """What `abs(x) > 1 THEN NULL` does in DuckDB, and why it is not the guard."""
    con = duckdb.connect()
    nan_is_out_of_range = con.sql("SELECT abs('nan'::DOUBLE) > 1").fetchall()
    assert nan_is_out_of_range == [(True,)]
    assert con.sql("SELECT isnan(asin(2.0))").fetchall() == [(True,)], (
        "DuckDB's own asin answers NaN out of range — the reason for a guard at all"
    )


def test_a_folded_out_of_range_argument_is_null_too() -> None:
    assert _one("print a = isnull(asin(2)), b = isnull(acos(-2)), c = asin(1)") == (
        True, True, math.pi / 2,
    )


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("sin(true)", 0.8414709848078965),
        ("cot(true)", 0.6420926159343306),
        ("degrees(true)", 57.29577951308232),
        ("atan2(true, 1)", 0.7853981633974483),
        ("sin(int(1))", 0.8414709848078965),
    ],
)
def test_a_bool_or_an_int_is_a_number(expression: str, expected: float) -> None:
    assert _one(f"print x = {expression}") == (expected,)


def test_measured_values_and_types() -> None:
    row = _one(
        "print a = sin(1), b = cos(1), c = tan(1), d = asin(0.5), e = acos(0.5), "
        "f = atan(1), g = atan2(1, 2), h = cot(1), i = pi(), j = degrees(pi()), "
        "k = radians(180), t = gettype(sin(int(1)))"
    )
    assert row == (
        0.8414709848078965, 0.5403023058681398, 1.5574077246549023,
        0.5235987755982989, 1.0471975511965979, 0.7853981633974483,
        0.4636476090008061, 0.6420926159343306, math.pi, 180.0, math.pi, "real",
    )


def test_atan2_takes_y_first() -> None:
    """`atan2(1, 0)` is π/2 and `atan2(0, -1)` is π — measured."""
    assert _one("print a = atan2(1, 0), b = atan2(0.0, -1.0), c = atan2(0, 0)") == (
        math.pi / 2, math.pi, 0.0,
    )


def test_poles_and_infinities_agree_with_ieee() -> None:
    row = _one(
        "datatable(x:real)[0.0] | project a = isinf(cot(x)), b = isnan(sin(x + real(+inf))), "
        "c = atan(x + real(+inf)), d = isinf(degrees(x + 1e308))"
    )
    assert row == (True, True, math.pi / 2, True)


@pytest.mark.parametrize(
    "query",
    [
        "datatable(s:string)['1'] | project x = sin(s)",
        "datatable(t:datetime)[datetime(2020-01-01)] | project x = asin(t)",
        "datatable(d:dynamic)[1] | project x = cot(d)",
    ],
)
def test_a_non_numeric_column_fails_to_bind_as_kusto_refuses_it(query: str) -> None:
    """SEM0201 on the emulator. A string *literal* is the exception here —
    DuckDB widens `'1'` and answers `sin('1')`, the leniency `sqrt('4')` has
    had all along — and is a refused query answered, not a wrong answer."""
    with pytest.raises(duckdb.Error):
        duckdb_kql.kql(duckdb_kql.connect(), query).fetchall()


def test_pi_takes_no_argument() -> None:
    with pytest.raises(KqlError):
        duckdb_kql.to_sql("print x = pi(1)")


def test_summarize_by_names_follow_kusto_for_each_function() -> None:
    """An allow-list, measured: `sin` … `atan2` pass the column's name through,
    `cot`, `degrees` and `radians` do not."""
    cursor = duckdb_kql.kql(
        duckdb_kql.connect(),
        "datatable(x:real)[0.5] | summarize count() by sin(x), cot(x), degrees(x)",
    )
    assert [d[0] for d in cursor.description] == ["x", "Column1", "Column2", "count_"]
