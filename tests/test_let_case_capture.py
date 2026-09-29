"""A tabular `let` is reached by its exact KQL name, and nothing else is.

Found while measuring stored functions (docs/stored-functions-proposal.md §4.1).
A tabular `let` became a CTE under its **own** name, which left DuckDB to decide
what a table reference meant — and DuckDB matches quoted identifiers
case-insensitively, where KQL's are case-sensitive (R7). So a binding captured
a table whose name differs only in case, with no error anywhere:

    let fpt = datatable(Value:long)[100]; FpT | summarize s = sum(Value)
        Kusto 7, from the table          here 100, from the let

Measured on the emulator, 2026-09-29, along with every other expectation below.

The obvious fix is a case-collision *check* — refuse `fpt` beside `FpT`. That
throws away legal queries (two bindings `a` and `A` are distinct in Kusto, and
`union a, A` answers 3) and still leaves the choice to DuckDB. The fix instead
takes the choice away from it: each binding is rendered under a reserved CTE
name, and a table reference reaches one only by exact name, only where it is in
scope. The generated *stage* names had the same flaw from the other side — a
table called `_s0`, read from a nested query, resolved to the stage — and now
avoid every name the statement reads as a table.
"""

from __future__ import annotations

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def con():
    c = duckdb_kql.connect()
    c.execute("CREATE TABLE FpT (Value BIGINT); INSERT INTO FpT VALUES (7)")
    yield c
    c.close()


def _one(con, kql: str):
    ((value,),) = duckdb_kql.kql(con, kql).fetchall()
    return value


def test_a_let_differing_only_in_case_does_not_capture_the_table(con) -> None:
    assert _one(con, "let fpt = datatable(Value:long)[100]; FpT | summarize s = sum(Value)") == 7


def test_a_let_of_the_exact_name_still_shadows_the_table(con) -> None:
    """The half that must keep working: shadowing by the *same* name is KQL."""
    assert _one(con, "let FpT = datatable(Value:long)[100]; FpT | summarize s = sum(Value)") == 100


def test_two_lets_differing_only_in_case_are_two_bindings(con) -> None:
    """Kusto answers 3 for the union and 2 for `A`. Here the two CTEs used to
    share one name as far as DuckDB was concerned: `Duplicate CTE name`."""
    lets = "let a = datatable(x:long)[1]; let A = datatable(x:long)[2]; "
    assert _one(con, lets + "union a, A | summarize s = sum(x)") == 3
    assert _one(con, lets + "A | summarize s = sum(x)") == 2


def test_a_binding_sees_only_the_bindings_declared_before_it(con) -> None:
    """Sequential scope: `X` reads the table, because the `let FpT` after it is
    not yet bound (Kusto: 7). And a binding does not see itself, so
    `let FpT = FpT | …` reads the table it shadows (Kusto: 14)."""
    assert _one(
        con,
        "let X = FpT | summarize s = sum(Value); "
        "let FpT = datatable(Value:long)[100]; X",
    ) == 7
    assert _one(con, "let FpT = FpT | extend Value = Value * 2; FpT") == 14


def test_a_binding_is_reached_from_a_nested_query(con) -> None:
    """A join's right side and an `in` subquery are nested queries; the binding
    must reach them by its reserved name, and only by its exact KQL one."""
    assert _one(
        con,
        "let R = datatable(Value:long, tag:string)[7, 'r']; "
        "FpT | join kind=inner (R) on Value | project tag",
    ) == "r"
    assert _one(
        con,
        "let r = datatable(Value:long)[100]; FpT | where Value in (r) | count",
    ) == 0


def test_a_table_named_like_a_stage_is_not_captured_by_the_stage(con) -> None:
    """`_s0` is what the first stage CTE is called. Read from a join's right
    side — a nested query, which sees the stages around it — the table resolved
    to the stage: the join matched FpT against itself."""
    con.execute("CREATE TABLE _s0 (Value BIGINT, tag VARCHAR); INSERT INTO _s0 VALUES (7, 'table')")
    assert _one(con, "FpT | join kind=inner (_s0) on Value | project tag") == "table"


def test_a_table_named_like_a_binding_cte_is_not_captured_either(con) -> None:
    """The reserved names are not reserved from the user, so they step aside."""
    con.execute("CREATE TABLE _l0_x (v BIGINT); INSERT INTO _l0_x VALUES (1)")
    assert _one(con, "let x = datatable(v:long)[100]; _l0_x | summarize s = sum(v)") == 1
    sql = str(duckdb_kql.to_sql("let x = datatable(v:long)[100]; _l0_x | where v in (x)"))
    assert '"__l0_x"' in sql and 'FROM "_l0_x"' in sql
