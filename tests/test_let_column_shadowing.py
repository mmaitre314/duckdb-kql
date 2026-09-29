"""A column of the operator's input shadows a `let` of the same name (R23).

Found while measuring stored functions (docs/stored-functions-proposal.md §4.2).
A scalar `let` was substituted wherever its name appeared, so it shadowed a
column — which is the opposite of Kusto's rule, and silent:

    let Value = 5; datatable(Value:long)[7] | extend k = Value
        Kusto k = 7, the column          here k = 5, the let

Every expectation below was measured on the emulator on 2026-09-29. The rule is
"the column if the input has one, otherwise the binding", and it holds for a
declared **query parameter** too — the quiet sibling, same substitution. It does
*not* hold for a scalar function's closure, parameters or locals, which win over
a column; those tests are the half a fix that swapped the precedence everywhere
would break.

Lowering cannot see columns, so a binding read in a pipeline lowers to a
`LetRef` holding the name and the value, and translation decides. Where the
input's columns are unknown — `to_sql` with no schema, which is how
`duckdb-kql translate` works — the obvious answer is to refuse, and it was the
first version: it refused every parameterized query the CLI translates. Instead
the value is assumed behind a guard stage that makes DuckDB fail the query if
the input turns out to have the column. Right answer or no answer, never the
wrong one.
"""

from __future__ import annotations

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")

D = "datatable(Value:long)[7]"


@pytest.fixture
def con():
    c = duckdb_kql.connect()
    yield c
    c.close()


def _rows(con, kql: str, **kwargs) -> list[tuple]:
    return duckdb_kql.kql(con, kql, **kwargs).fetchall()


@pytest.mark.parametrize(
    ("kql", "expected"),
    [
        (f"let Value = 5; {D} | extend k = Value", [(7, 7)]),
        (f"let Value = 5; {D} | where Value > 6 | count", [(1,)]),
        (f"let Value = 5; {D} | summarize s = sum(Value)", [(7,)]),
        (f"let Value = 5; {D} | extend Value = Value + 1", [(8,)]),
        (f"let Value = 5; {D} | extend a = Value * 2, b = Value", [(7, 14, 7)]),
        (f"let Value = 5; {D} | top 1 by Value | extend k = Value", [(7, 7)]),
        (f"let Value = 5; let t = {D} | extend k = Value; t", [(7, 7)]),
        # the binding still answers where there is no such column
        ("let Value = 5; datatable(x:long)[7] | extend k = Value", [(7, 5)]),
        (f"let Value = 5; {D} | project Other = Value | extend k = Value", [(7, 5)]),
        ("let Value = 5; print k = Value", [(5,)]),
        # R21: an assignment sees the operator's *input*, so `v` is the let here
        ("let v = 5; datatable(x:long)[1] | extend v = 10, k = v", [(1, 10, 5)]),
        ("let v = 5; datatable(x:long)[1] | extend v = 10 | extend k = v", [(1, 10, 10)]),
        # a column in `bin`'s width: one group of three, not two
        (
            "let n = 2; datatable(x:long, n:long)[1, 10, 2, 20, 3, 30] "
            "| summarize c = count() by b = bin(x, n)",
            [(0, 3)],
        ),
    ],
)
def test_a_column_shadows_a_let_of_the_same_name(con, kql, expected) -> None:
    assert _rows(con, kql) == expected


def test_a_column_shadows_a_query_parameter_too(con) -> None:
    """The quiet sibling: parameters seeded the same substitution. Kusto 7."""
    kql = f"declare query_parameters(Value:long); {D} | extend k = Value"
    assert _rows(con, kql, parameters={"Value": 5}) == [(7, 7)]
    kql = f"declare query_parameters(p:long); {D} | extend k = p"
    assert _rows(con, kql, parameters={"p": 5}) == [(7, 5)]


def test_a_column_shadows_an_invoked_functions_parameter_and_closure(con) -> None:
    """An `invoke` body is a pipeline over the piped rows, so the rule holds:
    Kusto 100 for both. And a parameter shadows an *outer* binding of its name —
    this answered 1, closing over the outer `let p` before the call bound it."""
    rows = "datatable(x:long, K:long)[1, 100]"
    assert _rows(
        con, f"let f = (rows:(x:long), K:long) {{ rows | extend kk = K }}; {rows} | invoke f(5)"
    ) == [(1, 100, 100)]
    assert _rows(
        con, f"let K = 5; let f = (rows:(x:long)) {{ rows | extend kk = K }}; {rows} | invoke f()"
    ) == [(1, 100, 100)]
    assert _rows(
        con,
        "let p = 1; let f = (rows:(x:long), p:long) { rows | extend k = p }; "
        "datatable(x:long)[1] | invoke f(5)",
    ) == [(1, 5)]


@pytest.mark.parametrize(
    ("kql", "expected"),
    [
        # the closure wins: 7 + 1, not 100 + 1
        (
            "let K = 7; let F = (x:long) { x + K }; datatable(K:long)[100] | extend y = F(1)",
            [(100, 8)],
        ),
        # the function's parameter and local win
        (
            "let F = (Value:long) { Value * 2 }; datatable(Value:long)[100] | extend k = F(1)",
            [(100, 2)],
        ),
        (
            "let F = (x:long) { let y = x + 1; y }; datatable(y:long)[100] | extend k = F(1)",
            [(100, 2)],
        ),
        # a let's own value binds when it is declared, where no column is in scope
        ("let a = 5; let b = a + 1; datatable(a:long)[100] | extend k = b", [(100, 6)]),
    ],
)
def test_what_does_not_change(con, kql, expected) -> None:
    assert _rows(con, kql) == expected


@pytest.mark.parametrize(
    ("kql", "columns"),
    [
        # a bare reference is named after the binding — it was `Column1`
        ("let v = 5; datatable(x:long)[1] | project x, v, w = v", ["x", "v", "w"]),
        ("let v = 5; datatable(x:long)[1] | extend v", ["x", "v"]),
        ("let v = 5; datatable(x:long)[1] | distinct v", ["v"]),
        # ... except where Kusto does not do that
        ("let v = 5; datatable(x:long)[1] | summarize c = count() by v, x", ["Column1", "x", "c"]),
        ("let v = 5; print v, w = v", ["print_0", "w"]),
    ],
)
def test_a_bare_reference_is_named_as_kusto_names_it(con, kql, columns) -> None:
    assert duckdb_kql.kql(con, kql).columns == columns


def test_without_a_schema_duckdb_checks_the_assumption() -> None:
    """No schema, so translation cannot tell; the SQL assumes the binding and a
    guard stage fails the query if the input has the column after all."""
    sql = str(duckdb_kql.to_sql("let Value = 5; T | extend k = Value"))

    without = duckdb_kql.connect()
    without.execute("CREATE TABLE T AS SELECT 7::BIGINT AS x")
    assert without.execute(sql).fetchall() == [(7, 5)]

    with_it = duckdb_kql.connect()
    with_it.execute("CREATE TABLE T AS SELECT 7::BIGINT AS Value")
    with pytest.raises(duckdb.InvalidInputException, match="R23"):
        with_it.execute(sql).fetchall()
    # an empty table has the column all the same — the guard reads the shape
    with_it.execute("DELETE FROM T")
    with pytest.raises(duckdb.InvalidInputException, match="R23"):
        with_it.execute(sql).fetchall()
    # a column differing in case is a different name in KQL
    other = duckdb_kql.connect()
    other.execute("CREATE TABLE T AS SELECT 7::BIGINT AS value")
    assert other.execute(sql).fetchall() == [(7, 5)]


def test_a_parameterized_query_still_translates_without_a_schema() -> None:
    """What the refusing version broke: the CLI's whole use case."""
    sql = str(duckdb_kql.to_sql("declare query_parameters(s:string); T | where State == s"))
    assert "error(" in sql


def test_a_parameter_with_no_placeholder_left_is_not_bound(con) -> None:
    """Shadowed everywhere, a parameter vanishes from the SQL, and DuckDB
    refuses a value bound to no placeholder ("excess parameters"). That was
    already true of a parameter declared and never read, which Kusto runs
    (measured: 1) and this refused."""
    kql = "declare query_parameters(p:long); print x = 1"
    assert _rows(con, kql, parameters={"p": 5}) == [(1,)]
