"""Columns and tables named with KQL keywords, and `['escaped names']`.

`identifierOrKeywordOrEscapedName: identifierName | keywordName | escapedName`
— most of KQL's keywords are legal names, and `['...']` exists precisely to name
things a plain identifier cannot. Neither worked: a column called `id`, `count`,
`by` or `range` reached the lowerer as `KeywordName` and came back as
"unsupported KQL construct 'expression:KeywordName'" — a message about the
language when the problem was one column's name.

Every expectation below was **measured on the Kusto Emulator** against the same
datatable, and every one of them is a query Kusto runs:

    datatable(id:long, ['count']:long, ['by']:string, ['my col']:string)
    [1, 2, 'a', 'b', 3, 4, 'c', 'd']

Reproduce with:

    docker compose up -d kusto
    curl -s localhost:8080/v1/rest/query -H 'Content-Type: application/json' \
      -d '{"db":"NetDefaultDB","csl":"<query>"}'
"""

from __future__ import annotations

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")

TABLE = (
    "datatable(id:long, ['count']:long, ['by']:string, ['my col']:string)"
    "[1,2,'a','b', 3,4,'c','d']"
)

#: `(tail, columns, rows)` — the emulator's answer for `TABLE | tail`.
MEASURED: list[tuple[str, list[str], list[tuple]]] = [
    ("where id > 0", ["id", "count", "by", "my col"], [(1, 2, "a", "b"), (3, 4, "c", "d")]),
    ("project id", ["id"], [(1,), (3,)]),
    ("extend y = id", ["id", "count", "by", "my col", "y"],
     [(1, 2, "a", "b", 1), (3, 4, "c", "d", 3)]),
    ("summarize c = count() by id", ["id", "c"], [(1, 1), (3, 1)]),
    ("sort by id asc", ["id", "count", "by", "my col"],
     [(1, 2, "a", "b"), (3, 4, "c", "d")]),
    ("project-away id", ["count", "by", "my col"], [(2, "a", "b"), (4, "c", "d")]),
    ("project-rename z = id", ["z", "count", "by", "my col"],
     [(1, 2, "a", "b"), (3, 4, "c", "d")]),
    ("distinct id", ["id"], [(1,), (3,)]),
    ("project ['my col']", ["my col"], [("b",), ("d",)]),
    ("where ['by'] == 'a'", ["id", "count", "by", "my col"], [(1, 2, "a", "b")]),
    ("extend ['a b'] = 1 | project ['a b']", ["a b"], [(1,), (1,)]),
    ("project ['count']", ["count"], [(2,), (4,)]),
    ("summarize s = sum(['count'])", ["s"], [(6,)]),
    ("project by = id", ["by"], [(1,), (3,)]),
    ("extend range = 1 | project range", ["range"], [(1,), (1,)]),
    ("summarize m = max(['count']) by ['by']", ["by", "m"], [("a", 2), ("c", 4)]),
]


@pytest.fixture(scope="module")
def con():
    return duckdb_kql.connect()


@pytest.mark.parametrize("tail,columns,rows", MEASURED, ids=[m[0] for m in MEASURED])
def test_it_matches_the_emulator(con, tail, columns, rows) -> None:
    rel = duckdb_kql.kql(con, f"{TABLE} | {tail}")
    assert list(rel.columns) == columns
    assert sorted(map(str, (tuple(r) for r in rel.fetchall()))) == sorted(map(str, rows))


def test_a_keyword_names_a_table(con) -> None:
    """`id | count` — the source position, not just expressions."""
    con.execute("CREATE OR REPLACE TABLE id(x BIGINT); INSERT INTO id VALUES (1), (2)")
    assert duckdb_kql.kql(con, "id | count").fetchall() == [(2,)]


def test_an_escaped_name_names_a_table(con) -> None:
    """The one thing a bare keyword cannot do.

    `count | project a` does not parse — at the start of a query `count` is the
    operator, in Kusto as here. `['count']` is how KQL says "the table".
    """
    con.execute("CREATE OR REPLACE TABLE \"count\"(a BIGINT); INSERT INTO \"count\" VALUES (7)")
    assert duckdb_kql.kql(con, "['count'] | project a").fetchall() == [(7,)]


def test_an_escaped_name_may_contain_a_space(con) -> None:
    con.execute('CREATE OR REPLACE TABLE "my table"(b BIGINT); INSERT INTO "my table" VALUES (9)')
    assert duckdb_kql.kql(con, "['my table'] | project b").fetchall() == [(9,)]


def test_a_function_name_is_not_read_as_a_column(con) -> None:
    """The trap in widening this: `bin` is a `KeywordName` too.

    `summarize ... by bin(t, 1h)` has one in *function* position, so a blanket
    "any keyword is a name" would have collected `bin` as a group key. The name
    lookup is only used where the grammar allows nothing but names.
    """
    sql = str(duckdb_kql.to_sql("datatable(t:long)[1] | summarize c = count() by bin(t, 2)"))
    assert "floor" in sql  # bin() was translated as a function
    assert '"bin"' not in sql  # ... not quoted as a column


def test_escaped_names_are_unescaped_exactly_once(con) -> None:
    """`getText()` on an escaped name yields `['my col']`, brackets and all.

    That produced a *column literally called* `['my col']` — which then failed to
    bind, or worse, matched nothing. The name is the string's value.
    """
    rel = duckdb_kql.kql(con, "datatable(['my col']:long)[1] | project ['my col']")
    assert list(rel.columns) == ["my col"]


def test_a_let_may_be_named_with_a_keyword(con) -> None:
    assert duckdb_kql.kql(con, "let id = 7; print y = id").fetchall() == [(7,)]


def test_a_let_may_be_named_with_an_escaped_name(con) -> None:
    """Also the regression this uncovered: the value has to reach a `range`
    bound, which the substitution pass used to skip."""
    assert duckdb_kql.kql(con, "let ['my let'] = 5; print x = ['my let']").fetchall() == [(5,)]
    rows = duckdb_kql.kql(
        con, "let ['some number'] = 20;\nrange y from 0 to ['some number'] step 5"
    ).fetchall()
    assert [r[0] for r in rows] == [0, 5, 10, 15, 20]


def test_range_may_be_named_with_an_escaped_name(con) -> None:
    rel = duckdb_kql.kql(con, "range ['my idx'] from 1 to 3 step 1")
    assert list(rel.columns) == ["my idx"]
    assert [r[0] for r in rel.fetchall()] == [1, 2, 3]


def test_a_join_key_may_be_a_keyword(con) -> None:
    left = "datatable(id:long, v:string)[1,'a', 2,'b']"
    right = "datatable(id:long, w:string)[1,'z']"
    rows = duckdb_kql.kql(con, f"{left} | join kind=inner ({right}) on id | project w").fetchall()
    assert rows == [("z",)]
