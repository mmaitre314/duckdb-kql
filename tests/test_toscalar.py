"""Trap tests — `toscalar()`: the first column of the first row, computed once.

Reported: `print value = toscalar(print value = 1)` and the common
`let allowed = toscalar(ids | summarize make_set(id)); … | where id in
(allowed)` were refused outright. Every expectation below was measured on the
emulator, and most of them are what the obvious SQL gets wrong:

* **Not a scalar subquery.** SQL's `(SELECT …)` wants at most one row and
  says nothing of order; KQL takes the first row, in the body's order when it
  has one (`sort`, `top`), and the first column of several. An empty body is a
  null of the column's type — except a string: KQL has no null string, and it
  is `""` (`isnull` false, `strlen` 0).
* **Once.** A scalar `let` is inlined at every use (R22), so an inline
  subquery would be one evaluation per use. Kusto evaluates a `toscalar` once
  — `let x = toscalar(R | take 1); print x == x` is true — including a scalar
  argument: `toscalar(rand())` read twice is one number.
* **Outside any row.** A name the body cannot resolve is SEM0100 there; an
  inline subquery lets DuckDB resolve it against the *outer* row instead and
  answer per row. A scalar function's parameter bound to a row value is
  CRT0018 inside the body.
* **Membership.** `id in (allowed)` is membership when `allowed` is an array
  and equality when it is a scalar, and only DuckDB knows which. DuckDB also
  reads `x IN ((SELECT …))` as an IN-*subquery*, planned as a join that runs
  whatever a CASE around it says.
* **Names.** An unnamed `toscalar` column is `__scalar_` and a random GUID —
  refused — and the name reaches through `tostring`/`max` but not `+`/`iff`.

The quiet sibling: scalar `let`s never reached the body of `x in (T | …)`
either. That failed to bind, loudly — unless the outer row had a column of the
same name, which DuckDB then read instead: Kusto `[3]`, here every row.
"""

from __future__ import annotations

import duckdb
import pytest

import duckdb_kql
from duckdb_kql import ir
from duckdb_kql.errors import KqlError, KqlUnsupportedError


def rows(kql: str) -> list[tuple]:
    return duckdb_kql.query(duckdb_kql.connect(), kql).fetchall()


def names(kql: str) -> list[str]:
    cursor = duckdb_kql.query(duckdb_kql.connect(), kql)
    return [d[0] for d in cursor.description]


MEMBERSHIP = """let ids = datatable(id:string) ["a", "b"];
let allowed = toscalar(ids | summarize make_set(id));
datatable(id:string) ["a", "b", "c"]
| where id in (allowed)
| order by id asc"""


# ---------------------------------------------------------------------------
# The reproductions, through both APIs
# ---------------------------------------------------------------------------


def test_the_reported_reproductions() -> None:
    assert rows("print value = toscalar(print value = 1)") == [(1,)]
    assert rows(MEMBERSHIP) == [("a",), ("b",)]


def test_through_the_sdk_shaped_client() -> None:
    from duckdb_kql.kusto import KustoClient

    client = KustoClient(duckdb_kql.connect())
    try:
        got = [r.to_dict() for r in client.execute(None, MEMBERSHIP).primary_results[0]]
    finally:
        client.close()
    assert got == [{"id": "a"}, {"id": "b"}]


# ---------------------------------------------------------------------------
# What the value is
# ---------------------------------------------------------------------------


def test_the_first_column_of_the_first_row_in_order() -> None:
    assert rows(
        "print v = toscalar(datatable(a:long, b:string)[1,'x', 2,'y']),"
        " asc_ = toscalar(datatable(a:long)[3,1,2] | sort by a asc),"
        " desc_ = toscalar(datatable(a:long)[3,1,2] | sort by a desc),"
        " top_ = toscalar(datatable(a:long)[3,1,2] | top 1 by a)"
    ) == [(1, 1, 3, 3)]


def test_an_empty_body_is_a_typed_null_and_an_empty_string() -> None:
    assert rows(
        "print n = isnull(toscalar(datatable(x:long)[])),"
        " t = gettype(toscalar(datatable(x:long)[])),"
        " sn = isnull(toscalar(datatable(x:string)[])),"
        " sl = strlen(toscalar(datatable(x:string)[])),"
        " st = gettype(toscalar(datatable(x:string)[])),"
        " dn = isnull(toscalar(datatable(d:dynamic)[])),"
        " c = toscalar(datatable(x:long)[] | count)"
    ) == [(True, "long", False, 0, "string", True, 0)]


def test_the_columns_type_is_kept() -> None:
    assert rows(
        "print s = gettype(toscalar(print x = 's')),"
        " d = gettype(toscalar(print x = datetime(2020-01-01))),"
        " ts = gettype(toscalar(print x = 1d)), dy = gettype(toscalar(print x = dynamic([1]))),"
        " b = gettype(toscalar(print x = true)), r = gettype(toscalar(print x = 1.5)),"
        " g = gettype(toscalar(print x = guid(00000000-0000-0000-0000-000000000001)))"
    ) == [("string", "datetime", "timespan", "array", "bool", "real", "guid")]


def test_a_value_reaches_the_lambda_bodied_renderers() -> None:
    """`gettype` and a timespan's `tostring` put their operand inside a lambda,
    where DuckDB refuses a subquery: "subqueries in lambda expressions are not
    supported". So the value is read as a column, in a print and over rows."""
    assert rows("print t = tostring(toscalar(print x = 90m))") == [("01:30:00",)]
    assert rows(
        "range i from 1 to 2 step 1"
        " | extend t = gettype(toscalar(print x = 1d)), u = tostring(toscalar(print x = 1d))"
        " | project t, u"
    ) == [("timespan", "1.00:00:00"), ("timespan", "1.00:00:00")]


def test_a_scalar_argument_is_its_value() -> None:
    assert rows("let c = 5; print a = toscalar(1), b = toscalar(c + 1), d = toscalar(c)") == [
        (1, 6, 5)
    ]


def test_hint_before_the_bracket_is_accepted() -> None:
    """Measured: `toscalar kind=nooptimization (…)` answers as without it."""
    assert rows("print v = toscalar kind=nooptimization (print x = 3)") == [(3,)]


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kql", "expected"),
    [
        # Empty set: nothing is in it, everything is not in it.
        ("let a = toscalar(datatable(id:string)[] | summarize make_set(id));"
         " datatable(id:string)['a','b'] | where id in (a)", []),
        ("let a = toscalar(datatable(id:string)[] | summarize make_list(id));"
         " datatable(id:string)['a','c'] | where id !in (a)", [("a",), ("c",)]),
        # Duplicates in the list do not duplicate rows.
        ("let a = toscalar(datatable(id:string)['a','a','b','a'] | summarize make_list(id));"
         " datatable(id:string)['a','b','c'] | where id in (a)", [("a",), ("b",)]),
        ("let a = toscalar(datatable(id:string)['A','b'] | summarize make_list(id));"
         " datatable(id:string)['a','B','c'] | where id in~ (a)", [("a",), ("B",)]),
        ("let a = toscalar(datatable(id:long)[1,3] | summarize make_list(id));"
         " datatable(id:long)[1,2,3] | where id in (a)", [(1,), (3,)]),
        # A scalar: typed equality, so 2 is in (2.0).
        ("let one = toscalar(print x = 2.0); datatable(id:long)[1,2,3] | where id in (one)",
         [(2,)]),
        ("let one = toscalar(datatable(id:string)['b','c']);"
         " datatable(id:string)['a','b','c'] | where id in (one)", [("b",)]),
        ("let d = toscalar(print x = dynamic('b'));"
         " datatable(id:string)['a','b'] | where id in (d)", [("b",)]),
        # An array among other items is flattened into the list.
        ("let a = toscalar(datatable(id:string)['a'] | summarize make_list(id));"
         " datatable(id:string)['a','b','z'] | where id in (a, 'z')", [("a",), ("z",)]),
        ("let w = toscalar(datatable(w:string)['fox','dog'] | summarize make_list(w));"
         " datatable(s:string)['the quick fox', 'a cat', 'lazy dog'] | where s has_any (w)",
         [("the quick fox",), ("lazy dog",)]),
        ("let w = toscalar(datatable(w:string)['quick','fox'] | summarize make_list(w));"
         " datatable(s:string)['the quick fox', 'a quick cat'] | where s has_all (w)",
         [("the quick fox",)]),
    ],
)
def test_membership_as_measured(kql: str, expected: list[tuple]) -> None:
    assert sorted(rows(kql)) == sorted(expected)


def test_a_null_value_is_in_nothing() -> None:
    assert rows(
        "let none = toscalar(datatable(id:long)[]);"
        " datatable(id:long)[1,2] | extend a = id in (none), b = id !in (none) | project a, b"
    ) == [(False, True), (False, True)]


# ---------------------------------------------------------------------------
# Once, and outside any row
# ---------------------------------------------------------------------------


def test_one_evaluation_wherever_it_is_read() -> None:
    q = (
        "let x = toscalar(range i from 1 to 5 step 1 | summarize sum(i));"
        " let T = range j from 1 to 20 step 1 | where j <= x;"
        " T | extend a = x, b = x * 2"
    )
    sql = str(duckdb_kql.to_sql(q))
    assert sql.count("AS MATERIALIZED") == 1
    assert rows(q)[:2] == [(1, 15, 30), (2, 15, 30)]
    assert rows(
        "let x = toscalar(range i from 1 to 100000 step 1 | take 1); print same = x == x"
    ) == [(True,)]


def test_a_name_the_body_cannot_resolve_is_not_the_outer_rows() -> None:
    """Measured SEM0100. Inline, DuckDB would bind `b` to the outer `b` (5)
    and answer the correlated query; in its own CTE it cannot bind at all."""
    with pytest.raises(duckdb.BinderException):
        rows("datatable(b:long)[5] | extend m = toscalar(datatable(a:long)[1] | where a == b)")
    with pytest.raises(duckdb.BinderException):
        rows("range i from 1 to 3 step 1 | extend m = toscalar(range j from 1 to i step 1 | count)")


def test_a_parameter_bound_to_the_row_is_refused() -> None:
    """Measured CRT0018. Inlined, `k` would read the body's own `a` column."""
    with pytest.raises(KqlUnsupportedError, match="CRT0018"):
        rows(
            "let f = (k:long) { toscalar(datatable(a:long)[1,2,3] | where a > k | count) };"
            " range i from 1 to 2 step 1 | extend c = f(i)"
        )


def test_inside_the_body_a_column_shadows_a_let() -> None:
    """R23 holds in the body, which is a pipeline of its own."""
    assert rows(
        "let i = 100; range i from 1 to 2 step 1 | extend m = toscalar(print x = i) | project m"
    ) == [(100,), (100,)]
    assert rows(
        "let a = 100; range a from 1 to 2 step 1"
        " | extend m = toscalar(datatable(a:long)[7] | project a) | project m"
    ) == [(7,), (7,)]


def test_nested_places() -> None:
    assert rows(
        "let A = range i from 1 to 10 step 1;"
        " let B = A | where i > toscalar(A | summarize avg(i))"
        " | extend m = toscalar(A | summarize max(i)); B | project i, m | take 1"
    ) == [(6, 10)]
    assert rows(
        "let T = range i from 1 to 5 step 1;"
        " print v = toscalar(T | where i > toscalar(T | summarize avg(i)) | count)"
    ) == [(2,)]
    assert rows(
        "let T = datatable(k:long)[1,2,3]; let U = datatable(k:long)[2,3];"
        " print v = toscalar(T | where k in (U) | count)"
    ) == [(2,)]
    assert rows(
        "let A = datatable(k:long, a:string)[1,'x']; let B = datatable(k:long, b:string)[1,'y'];"
        " print v = toscalar(A | join kind=inner B on k | project b)"
    ) == [("y",)]
    assert rows(
        "let E = datatable(s:string, t:string)['A','B', 'B','C'];"
        " let start = toscalar(print x = 'A');"
        " E | make-graph s --> t with_node_id=id"
        " | graph-match (a)-->(b) where a.id == start project a.id, b.id"
    ) == [("A", "B")]


def test_a_let_shadowed_around_a_body_is_refused() -> None:
    """KQL scoping is lexical — measured, a function body rebinding `ids`
    still reads the outer one through an outer `toscalar`. An inlined body is
    rendered where it is used, so a second binding on the stack is refused
    rather than chosen. Built as IR: no lowering produces it today."""
    body = ir.ToScalar(ir.Query(ir.TableRef("ids"), []))
    inner = ir.Query(
        ir.PrintSource((ir.NamedExpr(body, "v"),)), [],
        [("ids", ir.Query(ir.DataTable((("id", "string"),), (ir.Literal("b", "string"),)), []))],
    )
    outer = ir.Query(
        ir.DataTable((("k", "long"),), (ir.Literal(1, "long"),)),
        [ir.Union((inner,))],
        [("ids", ir.Query(ir.DataTable((("id", "string"),), (ir.Literal("a", "string"),)), []))],
    )
    from duckdb_kql.translate import to_sql

    with pytest.raises(KqlUnsupportedError, match="bound by a let here and by another"):
        to_sql(outer)


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kql",
    [
        "datatable(a:long)[1] | extend toscalar(print x = 2)",
        "datatable(a:long)[1] | project toscalar(print x = 2)",
        "datatable(a:long)[1] | extend tostring(toscalar(print x = 2))",
        "range i from 1 to 3 step 1 | distinct toscalar(print x = 5)",
        "range i from 1 to 3 step 1 | summarize count() by toscalar(print x = 5)",
        "range i from 1 to 4 step 1 | summarize max(toscalar(print x = 3))",
    ],
)
def test_a_name_kusto_draws_at_random_is_refused(kql: str) -> None:
    with pytest.raises(KqlUnsupportedError, match="random GUID"):
        duckdb_kql.to_sql(kql)


def test_names_that_do_not_reach_the_toscalar() -> None:
    assert names("datatable(a:long)[1] | extend toscalar(print x = 2) + 1") == ["a", "Column1"]
    assert names("datatable(a:long)[1] | extend iff(toscalar(print x = true), 1, 2)") == [
        "a", "Column1"
    ]
    assert names("range i from 1 to 4 step 1 | summarize sum(i * toscalar(print x = 10))") == [
        "sum_"
    ]
    assert names("print toscalar(print x = 1)") == ["print_0"]
    assert names("let s = toscalar(print x = 5); datatable(a:long)[1] | project s") == ["s"]


# ---------------------------------------------------------------------------
# The quiet sibling: scalar lets in an `in (T | …)` body
# ---------------------------------------------------------------------------


def test_a_let_reaches_an_in_subquery_body() -> None:
    """Measured `[3]`; here it was every row, `k` read from the outer row."""
    assert rows(
        "let T = datatable(b:long)[1,2,3]; let k = 2;"
        " datatable(a:long, k:long)[1,0, 2,0, 3,0] | where a in (T | where b > k)"
    ) == [(3, 0)]
    assert rows(
        "let T = datatable(b:long)[1,2,3]; let k = 2;"
        " datatable(a:long)[1, 2, 3] | where a in (T | where b > k)"
    ) == [(3,)]


def test_a_let_reaches_a_has_any_subquery_body() -> None:
    """Measured `the fox`; here it was `a dog`, `w` read from the outer row."""
    assert rows(
        "let W = datatable(t:string)['fox','dog']; let w = 'fox';"
        " datatable(s:string, w:string)['the fox', 'dog', 'a dog', 'dog']"
        " | where s has_any (W | where t == w) | project s"
    ) == [("the fox",)]


def test_refusals_are_errors_not_answers() -> None:
    with pytest.raises(KqlError):
        duckdb_kql.to_sql("range i from 1 to 10 step 1 | take toscalar(print x = 3)")


def test_a_let_read_twice_in_a_body_is_not_bound_around_it() -> None:
    """The body renders behind the sharing barrier. Walked into, its `let`
    read twice was planned as a column of the *outer* stage, unresolved."""
    assert rows(
        "let x = 3; print v = toscalar(range i from 1 to 10 step 1"
        " | where i > x | where i < x * 3 | count)"
    ) == [(5,)]


def test_membership_without_a_schema() -> None:
    """With no schema nothing is bound to a column (`sharing.bind_repeats`),
    so the value stays a subquery — and DuckDB plans `x IN ((SELECT …))` as a
    join that runs whatever the CASE says: over a list it failed to bind."""
    con = duckdb.connect()
    con.execute("CREATE TABLE Ids AS SELECT * FROM (VALUES ('a'), ('b'), ('c')) t(id)")
    sql = str(duckdb_kql.to_sql(
        "let allowed = toscalar(datatable(id:string)['a','b'] | summarize make_set(id));"
        " Ids | where id in (allowed)"
    ))
    assert sorted(con.sql(sql).fetchall()) == [("a",), ("b",)]
    sql = str(duckdb_kql.to_sql(
        "let one = toscalar(print x = 'b'); Ids | where id in (one)"
    ))
    assert con.sql(sql).fetchall() == [("b",)]
