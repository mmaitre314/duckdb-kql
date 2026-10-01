"""Trap tests — what a variable-length edge's recursion starts from and carries.

Reported: on a graph with a disconnected 6×12 layered component (83 nodes, 730
edges), the one-row match

    graph-match cycles=none (start)-[path*5..5]->(finish)
        where start.Id == 'main-0' and finish.Id == 'main-5'

exhausted a 1 GB budget with spilling disabled. The recursion was seeded from
**every edge in the graph** and carried every property of every edge and inner
node; the endpoint tests ran only in the outer `WHERE`, once all three million
walks of the unrelated component had been built. Not a wrong answer — an
answer that never came.

Now a conjunct that reads only a path's start node restricts where its walk
begins; when only the *end* is constrained, the walk is built backwards from
there; a top-level `all(p, …)` or `all(inner_nodes(p), …)` stops extending a
prefix that fails it; and only the properties the query reads are carried.

The traps, every expectation below measured on the emulator:

* **An early test is a second copy of one the outer `WHERE` still applies, so
  it is exact only if it never rejects what the outer one keeps.** A conjunct qualifies
  and nothing else does: one side of an `or` decides nothing alone, and
  `a.id == 'A' or b.id == 'A'` seeded from A would lose the walks that end
  there.
* **A walk built backwards must keep its edges in pattern order** — `map(p, w)`
  is an ordered list — under `<-[…]-` and `-[…]-` as well as `-[…]->`, and
  with a zero-length path among the rows.
* **A seed is a node in a partition.** `b.id == 'A'` names a node in each
  partition; joined on the id alone, every walk to A would be built twice.
* **The early `all` counts a null as the outer one does — unsatisfied.** The
  two share one rendering of that rule, so they cannot drift.

What remains: a pattern that no conjunct constrains still enumerates every walk
of the graph. That limit is pinned here too, so that the budget is known to be
one the full expansion exceeds (TRANSLATION.md R25).
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import duckdb
import pytest

import duckdb_kql
from duckdb_kql.kusto import KustoClient

#: Enough for DuckDB itself with room to spare; measured, the reported query
#: needed several times this before the fix.
BUDGET = "256MB"


def _budgeted() -> duckdb.DuckDBPyConnection:
    """The reporter's fixture, under an explicit budget with nowhere to spill."""
    con = duckdb_kql.connect()
    con.execute(f"SET memory_limit='{BUDGET}'")
    con.execute("SET max_temp_directory_size='0B'")
    con.execute("SET threads=1")
    con.execute("CREATE TABLE Nodes(Id VARCHAR, Bucket VARCHAR)")
    con.execute(
        "CREATE TABLE Edges(Source VARCHAR, Target VARCHAR, EdgeId VARCHAR, Bucket VARCHAR)"
    )
    nodes = [(f"main-{i}", "selected") for i in range(11)]
    nodes += [(f"noise-{layer}-{c}", "other") for layer in range(6) for c in range(12)]
    edges = [(f"main-{i}", f"main-{i + 1}", f"edge-{i}", "selected") for i in range(10)]
    edges += [
        (f"noise-{layer}-{s}", f"noise-{layer + 1}-{t}", f"noise-edge-{layer}-{s}-{t}", "other")
        for layer in range(5) for s in range(12) for t in range(12)
    ]
    con.executemany("INSERT INTO Nodes VALUES (?, ?)", nodes)
    con.executemany("INSERT INTO Edges VALUES (?, ?, ?, ?)", edges)
    return con


def _query(where: str, low: int = 5, high: int = 5) -> str:
    return (
        "Edges | make-graph Source --> Target with Nodes on Id"
        f" | graph-match cycles=none (start)-[path*{low}..{high}]->(finish)"
        + (f" where {where}" if where else "")
        + " project Start=start.Id, End=finish.Id, EdgeIds=map(path, EdgeId)"
    )


@pytest.fixture(scope="module")
def budgeted() -> Iterator[duckdb.DuckDBPyConnection]:
    con = _budgeted()
    yield con
    con.close()


@pytest.mark.parametrize(
    "where",
    [
        "start.Id == 'main-0' and finish.Id == 'main-5'",  # as reported
        "finish.Id == 'main-5'",                           # built backwards
        "start.Id == 'main-0'",
        "finish.Id == 'main-5' and start.Bucket == 'selected'",
    ],
)
def test_a_selective_match_fits_the_budget(budgeted, where: str) -> None:
    rows = duckdb_kql.query(budgeted, _query(where)).fetchall()
    assert [(s, e, json.loads(ids)) for s, e, ids in rows] == [
        ("main-0", "main-5", [f"edge-{i}" for i in range(5)])
    ]


@pytest.mark.parametrize(("low", "high", "finish"), [(10, 10, 10), (5, 10, 10), (5, 10, 7)])
def test_ranges_keep_the_exact_ordered_path(budgeted, low: int, high: int, finish: int) -> None:
    where = f"start.Id == 'main-0' and finish.Id == 'main-{finish}'"
    rows = duckdb_kql.query(budgeted, _query(where, low, high)).fetchall()
    assert [json.loads(ids) for _, _, ids in rows] == [[f"edge-{i}" for i in range(finish)]]


def test_through_the_sdk_shaped_client(budgeted) -> None:
    """`KustoClient` over the same budgeted connection answers the same row."""
    client = KustoClient(budgeted)
    table = client.execute(
        "db", _query("start.Id == 'main-0' and finish.Id == 'main-5'")
    ).primary_results[0]
    (row,) = list(table)
    assert (row["Start"], row["End"]) == ("main-0", "main-5")
    assert list(row["EdgeIds"]) == [f"edge-{i}" for i in range(5)]


def test_the_budget_is_one_full_expansion_exceeds() -> None:
    """The limit that remains, and the proof that the budget above is real:
    with nothing to start from, every walk of the graph is built — about three
    million in the unrelated component — and DuckDB has nowhere to spill them.
    Its own connection, so the shared one never runs out."""
    con = _budgeted()
    try:
        with pytest.raises(duckdb.OutOfMemoryException):
            duckdb_kql.query(con, _query("") + " | count").fetchall()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Exactness: each answer as the emulator gave it, row multiset and edge order
# ---------------------------------------------------------------------------

_G = (
    'let E = datatable(s:string, t:string, w:long, c:string)["A","B",1,"r", "B","C",2,"r",'
    ' "C","A",3,"b", "B","D",long(null),"r", "D","C",4,"", "A","C",5,"b", "C","D",6,"r",'
    ' "A","B",7,"b"];'
    ' let N = datatable(id:string, k:long, tag:string)["A",1,"x", "B",2,"y", "C",long(null),"x",'
    ' "D",4,""];'
    " E | make-graph s --> t with N on id"
)
_P = (
    'let E = datatable(s:string, t:string, tenant:string, w:long)["A","B","x",1, "B","C","x",2,'
    ' "A","B","y",3, "C","A","y",4, "B","A","y",5];'
    ' let N = datatable(id:string, tenant:string, k:long)["A","x",1,"B","x",2,"C","x",3,'
    ' "A","y",4,"B","y",5,"C","y",6];'
    " E | make-graph s --> t with N on id partitioned-by tenant"
)


def _rows(graph: str, match: str) -> list[tuple]:
    """The rows of `graph | graph-match <match>`, in a fixed order, lists decoded."""
    kql = f"{graph} | graph-match {match}" if graph is _G else f"{graph} (graph-match {match})"
    got = duckdb_kql.kql(duckdb.connect(), kql).fetchall()
    return _sorted(
        [tuple(json.loads(v) if isinstance(v, str) and v[:1] == "[" else v for v in r) for r in got]
    )


def _sorted(rows: list[tuple]) -> list[tuple]:
    return sorted(rows, key=repr)


def test_backwards_against_the_arrow() -> None:
    """End-only under `<-[…]-`: built from A, the edges still in pattern order."""
    assert _rows(_G, "(a)<-[p*1..3]-(b) where b.id == 'A' project a.id, m = map(p, w)") == _sorted([
        ("A", [3, 2, 1]), ("A", [3, 2, 7]), ("A", [3, 5]), ("B", [1]), ("B", [1, 3, 5]),
        ("B", [7]), ("B", [7, 3, 5]), ("C", [2, 1]), ("C", [2, 7]), ("C", [4, None, 1]),
        ("C", [4, None, 7]), ("C", [4, 6, 5]), ("C", [5]), ("D", [None, 1]), ("D", [None, 7]),
        ("D", [6, 2, 1]), ("D", [6, 2, 7]), ("D", [6, 5]),
    ])


def test_backwards_along_an_undirected_edge() -> None:
    """`-[…]-` walks an edge either way; built from D, each list still starts at `a`."""
    assert _rows(
        _G, "(a)-[p*1..2]-(b) where b.id == 'D' project a.id, m = map(p, w),"
        " i = map(inner_nodes(p), id)"
    ) == _sorted([
        ("A", [1, None], ["B"]), ("A", [3, 4], ["C"]), ("A", [3, 6], ["C"]),
        ("A", [5, 4], ["C"]), ("A", [5, 6], ["C"]), ("A", [7, None], ["B"]),
        ("B", [2, 4], ["C"]), ("B", [2, 6], ["C"]), ("B", [None], []),
        ("C", [2, None], ["B"]), ("C", [4], []), ("C", [6], []), ("D", [4, 6], ["C"]),
        ("D", [6, 4], ["C"]),
    ])


def test_backwards_with_a_zero_length_path() -> None:
    got = _rows(_G, "(a)<-[p*0..2]-(b) where b.tag == 'y' project a.id, m = map(p, w)")
    assert got == _sorted([
        ("A", [3, 2]), ("B", []), ("C", [2]), ("C", [4, None]), ("D", [None]), ("D", [6, 2]),
    ])


def test_forwards_with_a_zero_length_path_and_inner_nodes() -> None:
    assert _rows(
        _G, "(a)-[p*0..2]->(b) where a.id == 'C' project b.id, m = map(p, w),"
        " i = map(inner_nodes(p), id)"
    ) == _sorted([
        ("A", [3], []), ("B", [3, 1], ["A"]), ("B", [3, 7], ["A"]), ("C", [], []),
        ("C", [3, 5], ["A"]), ("C", [6, 4], ["D"]), ("D", [6], []),
    ])


def test_all_cuts_a_prefix_at_a_null() -> None:
    """`B -[w=null]-> D` fails `w > 1`, so no walk from B goes through D first —
    and `a.k > 1` drops C, whose `k` is null."""
    assert _rows(
        _G, "(a)-[p*1..3]->(b) where a.k > 1 and all(p, w > 1) project a.id, b.id, m = map(p, w)"
    ) == _sorted([
        ("B", "A", [2, 3]), ("B", "B", [2, 3, 7]), ("B", "C", [2]), ("B", "C", [2, 3, 5]),
        ("B", "C", [2, 6, 4]), ("B", "D", [2, 6]), ("D", "A", [4, 3]), ("D", "B", [4, 3, 7]),
        ("D", "C", [4]), ("D", "C", [4, 3, 5]), ("D", "D", [4, 6]),
    ])


def test_all_over_inner_nodes() -> None:
    """No walk passes through B, the one node tagged `y`, or D, tagged ''."""
    assert _rows(
        _G, "(a)-[p*1..3]->(b) where all(inner_nodes(p), tag == 'x')"
        " project a.id, b.id, m = map(p, w)"
    ) == _sorted([
        ("A", "A", [5, 3]), ("A", "B", [1]), ("A", "B", [5, 3, 1]), ("A", "B", [5, 3, 7]),
        ("A", "B", [7]), ("A", "C", [5]), ("A", "D", [5, 6]), ("B", "A", [2, 3]),
        ("B", "B", [2, 3, 1]), ("B", "B", [2, 3, 7]), ("B", "C", [2, 3, 5]), ("B", "C", [2]),
        ("B", "D", [2, 6]), ("B", "D", [None]), ("C", "A", [3]), ("C", "B", [3, 1]),
        ("C", "B", [3, 7]), ("C", "C", [3, 5]), ("C", "D", [3, 5, 6]), ("C", "D", [6]),
        ("D", "A", [4, 3]), ("D", "B", [4, 3, 1]), ("D", "B", [4, 3, 7]), ("D", "C", [4, 3, 5]),
        ("D", "C", [4]), ("D", "D", [4, 6]),
    ])


def test_one_side_of_an_or_is_not_pushed() -> None:
    """Seeding from A would lose the last five rows, the walks that end there."""
    assert _rows(
        _G, "(a)-[p*1..3]->(b) where a.id == 'A' or b.id == 'A' project a.id, b.id, m = map(p, w)"
    ) == _sorted([
        ("A", "A", [1, 2, 3]), ("A", "A", [5, 3]), ("A", "A", [7, 2, 3]), ("A", "B", [1]),
        ("A", "B", [5, 3, 1]), ("A", "B", [5, 3, 7]), ("A", "B", [7]), ("A", "C", [1, 2]),
        ("A", "C", [1, None, 4]), ("A", "C", [5, 6, 4]), ("A", "C", [5]), ("A", "C", [7, 2]),
        ("A", "C", [7, None, 4]), ("A", "D", [1, 2, 6]), ("A", "D", [1, None]),
        ("A", "D", [5, 6]), ("A", "D", [7, 2, 6]), ("A", "D", [7, None]),
        ("B", "A", [2, 3]), ("B", "A", [None, 4, 3]), ("C", "A", [3]), ("C", "A", [6, 4, 3]),
        ("D", "A", [4, 3]),
    ])


def test_a_seed_is_a_node_in_its_partition() -> None:
    """`b.id == 'A'` names a node in both partitions; only `y` has paths to it."""
    assert _rows(
        _P, "(a)-[p*1..3]->(b) where b.id == 'A'"
        " project a.tenant, a.id, m = map(p, w), i = map(inner_nodes(p), k)"
    ) == _sorted([
        ("y", "A", [3, 5], [5]), ("y", "B", [5], []), ("y", "C", [4], []),
        ("y", "C", [4, 3, 5], [4, 5]),
    ])


def test_the_same_variable_at_both_ends() -> None:
    got = _rows(_G, "(a)-[p*1..2]->(a) where a.tag == 'x' project a.id, m = map(p, w)")
    assert got == _sorted([
        ("A", [5, 3]), ("C", [3, 5]), ("C", [6, 4]),
    ])


# ---------------------------------------------------------------------------
# What is carried
# ---------------------------------------------------------------------------


def test_only_the_properties_read_are_carried() -> None:
    """`map(p, w)` reads one edge property and no inner node: the walk carries
    `w` alone, where it carried both properties and every inner node."""
    sql = duckdb_kql.to_sql(
        f"{_G} | graph-match (a)-[p*1..3]->(b) where a.id == 'A' project m = map(p, w)"
    )
    path = sql[sql.index('_p0" AS ('):]
    assert 'list_append(p.edges, struct_pack("w" := e.props."w"))' in path
    assert 'e.props."c"' not in path and " AS inn" not in path
