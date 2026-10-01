"""Trap tests — what a graph's relations carry, and which side a walk's join builds on.

Reported: on a 2M-node / 8M-edge graph, a five-hop match from one node
(`(start)-[path*5..5]->(finish)`, both ends named, in a six-node component no
other edge touches) did not finish in 60 seconds. With a 2 GB budget and 8 GB
of spill it ran out of both after 79 s, while five plain joins answered in a
fraction of a second. The walk itself was seeded. The cost was elsewhere:

* **Everything was carried.** The raw edges were materialized, then numbered
  into a second materialized copy, each with every column — a 256-byte
  payload among them — for a query that reads `EdgeId`. The nodes were
  deduplicated, materialized, sorted to number them and copied again, though
  only `graph-mark-components` and `graph-to-table nodes` observe the order.
* **Every hop rebuilt a hash table of every edge.** A CTE has no statistics,
  so DuckDB estimated a walk anchored on its first edge as a cross product of
  the edges, 3×10¹⁵ rows, and built each step's hash table on the 8M edges
  rather than on the one walk: five hash builds of 8M rows to extend one path.

Now a relation carries the properties the consumer reads and no others; the
raw edges are read once, by the numbered copy; nodes are numbered only for an
operator that can see the order; a step reads the node relation only for an
inner node's properties; and a walk starts as a zero-length stub at a node,
so DuckDB builds on the walks and probes the edges. At full scale the query
answers in about 9 s, of which about 5 s is the KQL's own key expression,
`tostring(pack_array(...))` over every edge.

The traps, every expectation measured on the emulator:

* **A stub's first step leaves the start, which is not an inner node** — not
  in `inner_nodes(p)`, and not tested by `all(inner_nodes(p), …)`, which is
  why a start whose tag fails that test still starts walks below.
* **A walk from a stub must be one row per edge**, duplicates included: the
  node relation holds each id once, so a stub cannot multiply its edges.
* **The guard's type probes must not run the edge query**, now that nothing
  materializes it for them: `typeof` is not folded, so they read through a
  filter that is false.
* **Not carrying something must never change what is read.** A whole node,
  edge or path still reads every property.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

import duckdb_kql
from duckdb_kql.engine import schema

#: Measured with this fixture: the previous SQL needs between 256 and 512 MB,
#: this between 48 and 64 MB.
BUDGET = "128MB"

KQL = """
set query_results_cache_max_age = time(0s);
let VertexRows = Nodes
    | extend NodeKey=tostring(pack_array(GroupName, Kind, Id));
Edges
| extend SourceKey=tostring(pack_array(GroupName, Kind, Source)),
         TargetKey=tostring(pack_array(GroupName, Kind, Target))
| make-graph SourceKey --> TargetKey with VertexRows on NodeKey
| graph-match cycles=none (start)-[path*5..5]->(finish)
    where start.Id == 'node-0' and finish.Id == 'node-5'
    project Start=start.Id, End=finish.Id, EdgeIds=map(path, EdgeId)
| extend Hops=array_length(EdgeIds)
"""


def _fixture(con: duckdb.DuckDBPyConnection, nodes: int, edges: int) -> None:
    """The reporter's generator: a chain node-0 → … → node-5 and a disjoint
    background, every row with a 256-character payload nothing reads."""
    con.execute(
        "CREATE TABLE Nodes AS SELECT 'node-' || i AS Id, 'group' AS GroupName,"
        " 'item' AS Kind, repeat(md5(CAST(i AS VARCHAR)), 8) AS Payload FROM range(?) t(i)",
        [nodes],
    )
    con.execute(
        "CREATE TABLE Edges AS SELECT 'edge-' || i AS EdgeId, 'group' AS GroupName,"
        " 'item' AS Kind,"
        " 'node-' || CASE WHEN i < 5 THEN i ELSE 6 + (i - 5) % ($n - 10) END AS Source,"
        " 'node-' || CASE WHEN i < 5 THEN i + 1"
        "   ELSE 7 + (i - 5) % ($n - 10) + ((i - 5) // ($n - 10)) % 4 END AS Target,"
        " repeat(md5(CAST(i AS VARCHAR)), 8) AS Payload FROM range($e) t(i)",
        {"n": nodes, "e": edges},
    )


@pytest.fixture(scope="module")
def budgeted(tmp_path_factory: pytest.TempPathFactory):
    """Disk-backed, as reported, so the tables themselves are outside the
    budget: what it limits is the query."""
    con = duckdb_kql.connect(str(tmp_path_factory.mktemp("graph") / "fixture.duckdb"))
    _fixture(con, 50_000, 200_000)
    con.execute("CHECKPOINT")
    con.execute(f"SET memory_limit='{BUDGET}'")
    con.execute("SET max_temp_directory_size='0B'")
    con.execute("SET threads=1")
    yield con
    con.close()


EXPECTED = [("node-0", "node-5", [f"edge-{i}" for i in range(5)], 5)]


def test_the_reported_query_fits_the_budget(budgeted) -> None:
    rows = duckdb_kql.query(budgeted, KQL).fetchall()
    assert [(s, e, json.loads(ids), h) for s, e, ids, h in rows] == EXPECTED


def test_through_the_sdk_shaped_client(budgeted) -> None:
    from duckdb_kql.kusto import KustoClient

    (row,) = list(KustoClient(budgeted).execute(None, KQL).primary_results[0])
    assert (row["Start"], row["End"], list(row["EdgeIds"]), row["Hops"]) == EXPECTED[0]


def test_each_step_builds_on_the_walks_and_probes_the_edges(tmp_path: Path) -> None:
    """The second failure, pinned where it happens: in DuckDB's plan the
    build side of a hash join is its second child. Anchored on an edge, that
    child was the edge relation, at every one of the five hops."""
    con = duckdb_kql.connect()
    _fixture(con, 2_000, 8_000)
    sql = str(duckdb_kql.to_sql(KQL, schema=schema(con)))
    profile = tmp_path / "profile.json"
    con.execute("PRAGMA enable_profiling='json'")
    con.execute(f"PRAGMA profiling_output='{profile}'")
    con.sql(sql).fetchall()
    con.execute("PRAGMA disable_profiling")

    def named(node: dict, name: str) -> dict | None:
        if (node.get("operator_name") or "").strip() == name:
            return node
        for child in node.get("children", []):
            found = named(child, name)
            if found is not None:
                return found
        return None

    recursion = named(json.loads(profile.read_text()), "REC_CTE")
    assert recursion is not None, "no recursive CTE in the plan"
    step = named(recursion["children"][1], "HASH_JOIN")
    assert step is not None, "the recursive step has no hash join"
    build = step["children"][1]
    assert named(build, "REC_CTE_SCAN") is not None, "the step builds on the edges"


# ---------------------------------------------------------------------------
# What the relations carry
# ---------------------------------------------------------------------------


def _sql(kql: str) -> str:
    con = duckdb_kql.connect()
    _fixture(con, 20, 20)
    return str(duckdb_kql.to_sql(kql, schema=schema(con)))


def test_only_what_is_read_is_carried() -> None:
    sql = _sql(KQL)
    assert 'struct_pack("EdgeId" := x."EdgeId") AS props' in sql
    assert 'AS MATERIALIZED (SELECT "Id", "NodeKey" FROM' in sql
    assert "Payload" not in sql.split('"_g1_t0"', 1)[1]


def test_the_raw_edges_are_read_once_and_probed_without_rows() -> None:
    sql = _sql(KQL)
    assert '"_g1_ed" AS NOT MATERIALIZED' in sql
    assert 'FROM "_g1_ed" x WHERE false))' in sql
    assert '"_g1_e" AS MATERIALIZED (SELECT row_number() OVER () AS eid' in sql


def test_nodes_are_numbered_only_where_the_order_is_observed() -> None:
    graph = "Edges | make-graph Source --> Target with Nodes on Id"
    assert "AS ix" not in _sql(f"{graph} | graph-match (a)-->(b) project a.Id")
    assert "AS ix" in _sql(f"{graph} | graph-to-table nodes")
    assert "AS ix" in _sql(f"{graph} | graph-mark-components | graph-to-table nodes")


def test_a_step_reads_the_node_relation_only_for_properties() -> None:
    graph = "Edges | make-graph Source --> Target with Nodes on Id | graph-match "
    ids = _sql(graph + "cycles=none (a)-[p*1..3]->(b) project i = map(inner_nodes(p), node_id())")
    props = _sql(graph + "(a)-[p*1..3]->(b) project i = map(inner_nodes(p), Kind)")
    assert 'JOIN "_g0_n" n ON' not in ids.split('"_g0_p0" AS (', 1)[1]
    assert 'JOIN "_g0_n" n ON' in props.split('"_g0_p0" AS (', 1)[1]


# ---------------------------------------------------------------------------
# Exactness: each answer as the emulator gave it
# ---------------------------------------------------------------------------

#: Duplicate edges (A→B twice), a duplicate node row (B), an edge-only node
#: (D), a cycle (A→B→C→A) and a self-loop (D→D).
_G = (
    'let E = datatable(s:string, t:string, w:long)["A","B",1, "A","B",2, "B","C",3,'
    ' "C","A",4, "C","D",5, "D","D",6];'
    ' let N = datatable(id:string, tag:string, v:long)["A","n",1, "B","y",2, "B","z",20,'
    ' "C","y",3];'
    " E | make-graph s --> t with N on id | graph-match "
)
#: A null id in the middle of a chain.
_Z = (
    "let E = datatable(s:long, t:long, w:long)[1,long(null),7, long(null),3,8, 3,4,9];"
    " E | make-graph s --> t with_node_id=id | graph-match "
)


def rows(kql: str) -> list[tuple]:
    got = duckdb_kql.kql(duckdb.connect(), kql).fetchall()
    return sorted(
        (tuple(json.loads(v) if isinstance(v, str) and v[:1] in "[{" else v for v in r)
         for r in got),
        key=repr,
    )


def ordered(expected: list[tuple]) -> list[tuple]:
    return sorted(expected, key=repr)


@pytest.mark.parametrize(
    ("match", "expected"),
    [
        # Duplicate edges are two walks each; the duplicate node row is one node.
        ("(a)-[p*1..2]->(b) where a.id == 'A' project b.id, m = map(p, w)",
         [("B", [1]), ("B", [2]), ("C", [1, 3]), ("C", [2, 3])]),
        ("(a)-[p*1..1]->(b) where b.id == 'B' project a.id, m = map(p, w)",
         [("A", [1]), ("A", [2])]),
        ("(a)-[p*3..3]->(b) where a.id == 'A' project b.id, m = map(p, w)",
         [("A", [1, 3, 4]), ("A", [2, 3, 4]), ("D", [1, 3, 5]), ("D", [2, 3, 5])]),
        ("cycles=none (a)-[p*1..4]->(b) where a.id == 'A' project b.id, m = map(p, w)",
         [("B", [1]), ("B", [2]), ("C", [1, 3]), ("C", [2, 3]), ("D", [1, 3, 5]),
          ("D", [2, 3, 5])]),
        ("(a)-[p*1..2]->(b) where a.id == 'D' project b.id, m = map(p, w)", [("D", [6])]),
        # Zero-length paths: every node unseeded, the seed alone seeded.
        ("(a)-[p*0..0]->(b) project a.id, b.id, l = array_length(map(p, w))",
         [("A", "A", 0), ("B", "B", 0), ("C", "C", 0), ("D", "D", 0)]),
        ("(a)-[p*0..1]->(b) where a.id == 'C' project b.id, m = map(p, w),"
         " i = map(inner_nodes(p), id)",
         [("A", [4], []), ("C", [], []), ("D", [5], [])]),
        # The start fails the inner-node test; one-edge walks have no inner node.
        ("(a)-[p*1..3]->(b) where a.id == 'A' and all(inner_nodes(p), tag == 'y')"
         " project b.id, m = map(p, w)",
         [("A", [1, 3, 4]), ("A", [2, 3, 4]), ("B", [1]), ("B", [2]), ("C", [1, 3]),
          ("C", [2, 3]), ("D", [1, 3, 5]), ("D", [2, 3, 5])]),
        ("(a)-[p*1..3]->(b) where all(inner_nodes(p), node_id() != 'B') and a.id == 'A'"
         " project b.id, m = map(p, w)",
         [("B", [1]), ("B", [2])]),
        ("(a)-[p*1..3]->(b) where b.id == 'D' and all(inner_nodes(p), tag == 'y')"
         " project a.id, m = map(p, w)",
         [("A", [1, 3, 5]), ("A", [2, 3, 5]), ("B", [3, 5]), ("C", [5]), ("D", [6])]),
        # Inner nodes read through the node relation, forwards and backwards.
        ("(a)-[p*1..2]->(b) where a.id == 'A' project b.id,"
         " d = map(inner_nodes(p), node_degree_out()), m = map(p, w)",
         [("B", [], [1]), ("B", [], [2]), ("C", [1], [1, 3]), ("C", [1], [2, 3])]),
        ("(a)-[p*1..2]->(b) where b.id == 'C' project a.id, i = map(inner_nodes(p), v),"
         " m = map(p, w)",
         [("A", [2], [1, 3]), ("A", [2], [2, 3]), ("B", [], [3])]),
        # Nothing read at all, and only lengths read.
        ("(a)-[p*1..2]->(b) where a.id == 'A' project c = 1", [(1,), (1,), (1,), (1,)]),
        ("(a)-[p*1..2]->(b) where a.id == 'A' project c = map(p, 1),"
         " i = map(inner_nodes(p), 2)",
         [([1, 1], [2]), ([1, 1], [2]), ([1], []), ([1], [])]),
        # A whole path still reads every property.
        ("(a)-[p*1..2]->(b) where a.id == 'C' project p",
         [([{"s": "C", "t": "A", "w": 4}, {"s": "A", "t": "B", "w": 1}],),
          ([{"s": "C", "t": "A", "w": 4}, {"s": "A", "t": "B", "w": 2}],),
          ([{"s": "C", "t": "A", "w": 4}],),
          ([{"s": "C", "t": "D", "w": 5}, {"s": "D", "t": "D", "w": 6}],),
          ([{"s": "C", "t": "D", "w": 5}],)]),
    ],
)
def test_as_measured(match: str, expected: list[tuple]) -> None:
    assert rows(_G + match) == ordered(expected)


@pytest.mark.parametrize(
    ("match", "expected"),
    [
        ("(a)-[p*2..2]->(b) project a.id, b.id, m = map(p, w), i = map(inner_nodes(p), id)",
         [(1, 3, [7, 8], [None]), (None, 4, [8, 9], [3])]),
        ("(a)-[p*1..2]->(b) where isnull(a.id) project b.id, m = map(p, w)",
         [(3, [8]), (4, [8, 9])]),
        ("(a)-[p*1..2]->(b) where isnull(b.id) project a.id, m = map(p, w)", [(1, [7])]),
    ],
)
def test_a_null_id_seeds_and_ends_a_walk(match: str, expected: list[tuple]) -> None:
    assert rows(_Z + match) == ordered(expected)


def test_a_whole_node_is_one_of_its_rows_whole() -> None:
    """B has two rows; Kusto returned the first, and either one whole is right."""
    got = rows(_G + "(a)-[p*1..1]->(b) where a.id == 'A' project b, m = map(p, w)")
    assert len(got) == 2
    assert {json.dumps(b, sort_keys=True) for b, _ in got} <= {
        json.dumps({"id": "B", "tag": "y", "v": 2}, sort_keys=True),
        json.dumps({"id": "B", "tag": "z", "v": 20}, sort_keys=True),
    }
    assert sorted(m for _, m in got) == [[1], [2]]


def test_a_mismatched_id_type_is_still_refused() -> None:
    """The guard reads types through a false filter now; it must still fire."""
    q = (
        "let E = datatable(s:long, t:long)[1,2];"
        ' let N = datatable(id:string, age:long)["1",10];'
        " E | make-graph s --> t with N on id | graph-match (a)-[p*1..2]->(b) project a.age"
    )
    with pytest.raises(duckdb.InvalidInputException, match="SEM1079"):
        duckdb_kql.kql(duckdb.connect(), q).fetchall()


def test_two_node_tables_keep_the_columns_the_guard_compares() -> None:
    """The guard compares the types of every column the two tables share, so
    those columns are kept even when nothing reads them; dropped, the guard's
    SQL names a column that is not there. Measured, with `v` read by neither."""
    q = (
        'let E = datatable(s:string, t:string)["A","B", "B","C"];'
        ' let P = datatable(pid:string, v:long, x:string)["A",10,"p"];'
        ' let C = datatable(cid:string, v:long, y:string)["B",20,"q", "C",30,"r"];'
        " E | make-graph s --> t with P on pid, C on cid"
        " | graph-match (a)-[p*1..2]->(b) project a.pid, b.cid, m = map(inner_nodes(p), y)"
    )
    # Kusto prints the null string as ''.
    assert rows(q) == ordered([("A", "B", []), (None, "C", []), ("A", "C", ["q"])])
