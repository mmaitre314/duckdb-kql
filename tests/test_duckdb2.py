"""Trap tests — SQL that DuckDB 2.0 runs the way DuckDB 1.x did.

DuckDB 2.0.0 changed what several things this translator emits *mean*, with no
diff in this repository: the emitted SQL was byte-identical, and 544 tests
failed. Measured, the changes that reach emitted SQL:

* **An arrow lambda is a bind error.** `list_transform(l, x -> x + 1)` is
  "Deprecated lambda arrow (->) detected"; `lambda x: x + 1` is the spelling,
  accepted from DuckDB 1.3. Every emitter moved, and nothing here needs a test
  of its own: `test_behavior` binds every corpus query, so an arrow that comes
  back fails there. JSON's `->` and `->>` operators are unaffected.
* **A division by zero raises.** `7 // 0`, `7 % 0` and their real forms are an
  Invalid Input Error (`error_on_division_by_zero`, on by default) where 1.x
  answered null — which is Kusto's answer, measured over rows for `/`, `%`
  and `bin` of `long` and `int`. The obvious fix, `SET
  error_on_division_by_zero = false` in `connect()`, covers only this
  package's connections: `to_sql()` hands its SQL to the caller's. So the
  divisor is `NULLIF(…, 0)` in the SQL itself.
* **An AND is reordered.** `x has_any (T)` gates a per-row regex behind a
  `contains` test, and the gate only pays if it runs first. DuckDB 2.0 runs
  the regex first: the corpus query went from 57 ms to 1.7 s. A CASE fixes
  the order, and the obvious CASE (`ELSE false`) is wrong: `has_all` reads the
  null a null needle gives, and Kusto ignores that needle.
* **A constant `array_to_string` is evaluated while binding**, under a guard
  that is false: `strcat_array(dynamic("abc"), "-")` raised "Expected ARRAY".
  `test_strcat_array` holds it; the cast there is a TRY_CAST.

One more change reaches callers rather than SQL: a relation no longer has
`fetchone()`, so `duckdb_kql.query(...).fetchone()` is gone with it.
"""

from __future__ import annotations

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")


def test_division_by_zero_is_null_on_a_bare_connection() -> None:
    con = duckdb.connect()  # the caller's, not duckdb_kql.connect()
    assert con.sql("SELECT current_setting('error_on_division_by_zero')").fetchall() == [
        (True,)
    ]
    sql = duckdb_kql.to_sql(
        "datatable(a:long, b:long, i:int, z:int)[7, 0, -7, 0]"
        " | project a / b, a % b, bin(a, b), i / z, i % z"
    )
    # Measured on the emulator: every one null.
    assert con.sql(str(sql)).fetchall() == [(None, None, None, None, None)]


HAS_LIST = (
    "let N = datatable(n:long)[1, long(null)];"
    ' datatable(s:string)["x 1 y", "x 2 y"]'
    " | extend all1 = s has_all (N), any1 = s has_any (N)"
)


def test_a_null_needle_is_ignored_by_has_all() -> None:
    """Measured: `'x 1 y' has_all ([1, null])` is true — the null needle does
    not count against it. `prefilter AND regex` and `CASE WHEN prefilter THEN
    regex ELSE prefilter END` both give null for it, which `has_all` skips;
    `ELSE false` would count it as a failure and answer false."""
    con = duckdb_kql.connect()
    assert duckdb_kql.query(con, HAS_LIST).fetchall() == [
        ("x 1 y", True, True),
        ("x 2 y", False, False),
    ]


def test_the_has_list_gate_runs_before_the_regex() -> None:
    sql = str(duckdb_kql.to_sql(HAS_LIST))
    assert "(CASE WHEN contains(lower(" in sql
    assert "contains(lower(\"s\"), lower(CAST(_needle AS VARCHAR))) AND" not in sql
