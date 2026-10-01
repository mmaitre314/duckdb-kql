"""Trap tests for `PATCH duckdb-kql/004` — the grammar under the graph operators.

Upstream's reference grammar read a graph pattern as **one element per comma**,
so `(a)-->(b)` failed at the arrow and every documented `graph-match` example
was rejected. Because `tools/harvest_docs.py` drops a block the parser rejects,
that failure was silent twice over: the graph pages' examples never reached the
corpus, so nothing measured how much of KQL they covered.

The quieter half is in the lexer. `1..3` lexed as the real `1.` followed by
`.3`, which broke every variable-length edge range — and, unrelated to graphs,
`between (2..3)` whenever it was written without spaces. Kusto accepts both;
the measured count below is the emulator's.

The predicate that fixes it must not cost the trailing-dot real (`1.`), which
Kusto also accepts (`print y = 1.` is `1.0`).
"""

from __future__ import annotations

import duckdb
import pytest

import duckdb_kql
from duckdb_kql import validate

PARSES = [
    # One sequence with edges in it: the shape upstream rejected outright.
    "E | make-graph s --> t with_node_id=id | graph-match (a)-[e]->(b) project a.id",
    # Anonymous node and anonymous variable-length edge.
    "E | make-graph s --> t | graph-match (a)-->()-[*1..3]->(b) project a",
    # Every edge spelling, and two sequences sharing a variable.
    "E | make-graph s --> t | graph-match (a)-[p*1..3]->(b)<-[e]-(c)--(d)<--(f), (a)-->(f)"
    " project a",
    # A range bound from a `let`.
    "let k = 2; E | make-graph s --> t | graph-match (a)-[p*0..k]->(b) project a",
    # Two node tables.
    "E | make-graph s --> t with P on pid, C on cid | graph-match (a)-->(b) project a",
    # A plain partition column.
    "E | make-graph s --> t with N on id partitioned-by tenant "
    "(graph-match (a)-->(b) project a.id)",
    # Keyword-named operator parameters.
    "E | make-graph s --> t | graph-shortest-paths output=all (a)-[p*1..3]->(b) project a",
    "E | make-graph s --> t | graph-to-table nodes with_node_id=x",
]


@pytest.mark.parametrize("kql", PARSES)
def test_documented_graph_syntax_parses(kql: str) -> None:
    assert validate(kql) == []


def test_a_range_without_spaces_is_one_dotdot_two() -> None:
    # Measured: `range x from 1 to 3 step 1 | where x between (2..3) | count` is 2.
    # Before the patch it did not parse at all.
    con = duckdb.connect()
    rows = duckdb_kql.kql(
        con, "range x from 1 to 3 step 1 | where x between (2..3) | count"
    ).fetchall()
    assert rows == [(2,)]


def test_a_trailing_dot_real_is_still_a_real() -> None:
    # Measured: `print y = 1., z = 1.e2` is 1.0 and 100.0. The predicate that
    # splits `1..3` must only refuse a *second* dot.
    con = duckdb.connect()
    assert duckdb_kql.kql(con, "print a = 1., b = 1.e2, c = 1.5").fetchall() == [
        (1.0, 100.0, 1.5)
    ]
