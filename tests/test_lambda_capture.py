"""Trap tests — a DuckDB lambda must not capture the user's column of the same name.

Measured on DuckDB 2.0: inside `lambda t: …`, an unqualified `t` is the lambda's
parameter whatever column of that name is in scope, and however it is spelled
— `t`, `"t"` and `"T"` all resolve to the parameter. Six renderings embedded a
user expression in a lambda body under a fixed parameter name, so a column
named like the parameter was silently replaced by it. Each answer below was
measured on the emulator (2026-11-08); here, before the fix:

* `t has_any (p)` and `t has_any (toscalar(…))` tested each needle against
  *itself*, so a column `t` or `T` matched every row — 1 where Kusto has 0, and
  `has_all` likewise. `t` is about as common a column name as there is.
* `gettype(_k)` of a dynamic array answered `'string'`, Kusto `'array'`.
* `mv-expand k` of a bag answered `{"1":null}` for `{"1":5}` — wherever a key
  was itself valid JSON (`1`, `true`, `null`); any other key raised.
* `countof(i, …)` and `zip(i, …)` leaked a raw DuckDB error.

**Why the obvious fix is wrong.** Renaming the parameter to something unlikely
(`_kql_t`) only moves the collision: a KQL column can be named anything at all
(`['_kql_t']`), and DuckDB folds case, so the name must be chosen against what
the body actually embeds. `translate.lambdas.parameter` does that, lengthening
the name as `sharing._prefix` does for its slots.

The opposite rule holds for a *qualified* reference: `t.v` prefers a column
`t` in scope over the parameter. Numeric `bin` read its operands as `_bin.v`
and `_bin.r`, so `bin(x, 5)` beside a column `_bin` leaked a binder error —
and beside a DuckDB STRUCT column `_bin` with those fields, which `kql()` over
a user's own table can see, answered from the column: `bin(7, 5)` was 100. It
reads `_bin['v']` now. graph-match reads `_gz.nid` and the like as well, but
its scope never holds a user column: properties and pattern variables named
`_gz`, `_gk` or `_gy` were measured to agree.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.translate.lambdas import names_in, parameter

duckdb = pytest.importorskip("duckdb")


def _rows(query: str, **kwargs: object) -> list[tuple]:
    return duckdb_kql.kql(duckdb_kql.connect(), query, **kwargs).fetchall()


def _has(op: str, needles: str) -> str:
    """A template over one string column `{c}`, needles from a `toscalar`."""
    return (
        "datatable({c}:string)['hello world'] "
        f"| where {{c}} {op} (toscalar(print dynamic({needles}))) | count"
    )


#: `(template, the names that collided, Kusto's answer)` — every one measured.
MEASURED = [
    (_has("has_any", "['xyz']"), ["t", "T"], [(0,)]),
    (_has("has_all", "['hello', 'xyz']"), ["t", "T"], [(0,)]),
    (_has("has_all", "['hello', 'world']"), ["t"], [(1,)]),
    ("datatable({c}:dynamic)[dynamic([1])] | project g = gettype({c})", ["_k", "_K"], [("array",)]),
    ("datatable({c}:dynamic)[dynamic({{'1':5,'true':6,'null':7}})] | mv-expand {c} "
     "| project v = tostring({c})", ["k", "K"], [('{"1":5}',), ('{"true":6}',), ('{"null":7}',)]),
    ("datatable({c}:dynamic)[dynamic({{'a':5}})] | mv-expand kind=array {c} "
     "| project v = tostring({c})", ["k", "K"], [('["a",5]',)]),
    ("datatable({c}:string)['abcabc'] | project n = countof({c}, 'bc')", ["i", "I"], [(2,)]),
    ("datatable({c}:dynamic)[dynamic([1,2])] | project z = tostring(zip({c}, dynamic(['a','b'])))",
     ["i"], [('[[1,"a"],[2,"b"]]',)]),
]


@pytest.mark.parametrize(
    ("template", "column", "expected"),
    [(t, c, e) for t, names, e in MEASURED for c in [*names, "s"]],
)
def test_a_column_named_like_the_parameter_is_still_the_column(
    template: str, column: str, expected: list[tuple]
) -> None:
    assert _rows(template.format(c=column)) == expected


def test_a_query_parameter_list_is_matched_against_the_column() -> None:
    query = "declare query_parameters(p:dynamic); datatable(t:string)['hello world'] " \
            "| where t has_any (p) | count"
    assert _rows(query, parameters={"p": ["xyz"]}) == [(0,)]
    assert _rows(query, parameters={"p": ["world"]}) == [(1,)]


def test_the_parameter_steps_around_its_own_lengthened_name() -> None:
    """A body reading both `t` and `_t` gets `__t`, and both are still columns."""
    query = (
        "datatable(t:string, _t:string)['hello', 'world'] "
        "| where strcat(t, ' ', _t) has_all (toscalar(print dynamic(['hello', 'world']))) | count"
    )
    assert _rows(query) == [(1,)]
    assert "lambda __t:" in str(duckdb_kql.to_sql(query))


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("datatable(_bin:long)[7] | project b = bin(_bin, 5)", [(5,)]),
        ("datatable(x:long, _bin:long)[7, 1] | project b = bin(x, 5)", [(5,)]),
        ("datatable(x:real, _BIN:long)[7.5, 1] | project b = bin(x, 2.0)", [(6.0,)]),
        ("datatable(x:long, _bin:long)[7, 1, -7, 2] "
         "| summarize c = count() by b = bin(x, 5) | order by b asc", [(-10, 1), (5, 1)]),
    ],
)
def test_bin_beside_a_column_named_like_its_parameter(query: str, expected: list[tuple]) -> None:
    """Measured. Each leaked `Cannot extract field 'v' from expression "_bin"`."""
    assert _rows(query) == expected


def test_bin_does_not_read_a_struct_column_for_its_operands() -> None:
    con = duckdb_kql.connect()
    con.execute("CREATE TABLE T AS SELECT 7::BIGINT AS x, {'v': 100, 'r': 1} AS _bin")
    assert duckdb_kql.kql(con, "T | project b = bin(x, 5)").fetchall() == [(5,)]


def test_the_unqualified_name_is_the_parameter_in_duckdb() -> None:
    """The witness: what made every one of the above wrong."""
    con = duckdb.connect()
    for spelling in ("s", '"s"', '"S"'):
        sql = f"SELECT list_transform([1], lambda s: {spelling} + 1) FROM (SELECT 5 AS s)"
        assert con.sql(sql).fetchall() == [([2],)]


def test_a_qualified_reference_prefers_the_column() -> None:
    """The opposite rule, silently: a struct column `s` answers for `s.v`."""
    con = duckdb.connect()
    sql = "SELECT list_transform([{'v': 1}], lambda s: s.v) FROM (SELECT {'v': 9} AS s)"
    assert con.sql(sql).fetchall() == [([9],)]
    sql = "SELECT list_transform([{'v': 1}], lambda s: s['v']) FROM (SELECT {'v': 9} AS s)"
    assert con.sql(sql).fetchall() == [([1],)]


@pytest.mark.parametrize(
    ("sql", "names"),
    [
        ('"t" || x', {"t", "x"}),
        ('"T"', {"t"}),
        ('"a""b"', {'a"b'}),
        ("'t' || \"u\"", {"u"}),  # a string literal names nothing
        ("'it''s \"t\"'", set()),
        ("CAST($p AS VARCHAR)", {"cast", "as", "varchar"}),  # `$p` is a placeholder
        ("1e5 + y1", {"y1"}),
    ],
)
def test_names_in_reads_what_duckdb_would_resolve(sql: str, names: set[str]) -> None:
    assert names_in(sql) == names


def test_parameter_keeps_the_name_unless_something_resolves_to_it() -> None:
    assert parameter("t", '"s"', "'t'") == "t"
    assert parameter("t", '"T"') == "_t"
    assert parameter("t", '"t" || "_t"') == "__t"
