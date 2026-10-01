"""Trap tests for graph semantics — R25 (docs/graph-proposal.md).

Every expectation here was measured on the Kusto Emulator; each test says what
the obvious translation answers instead. The graph proposal's §2 holds the
full measurement tables, and `docs/TRANSLATION.md` R25 the rule.

The traps fall into four families:

* **identity** — a null id is a node that joins; every edge row is an edge;
  an undirected edge is one edge in two orientations;
* **cycles** — what `unique_edges`, `all` and `none` each forbid;
* **shapes** — bags drop null *and empty-string* properties, and output names
  follow spellings that no single rule predicts;
* **paths** — a null condition fails `all` and `any` alike, and
  `graph-shortest-paths` is simple paths, range-respecting, filter-first.
"""

from __future__ import annotations

import json
from collections import Counter

import duckdb
import pytest

import duckdb_kql
from duckdb_kql.errors import KqlUnsupportedError


def rows(kql: str) -> list[tuple]:
    return duckdb_kql.kql(duckdb.connect(), kql).fetchall()


def columns(kql: str) -> list[str]:
    cursor = duckdb_kql.kql(duckdb.connect(), kql)
    return [d[0] for d in cursor.description]


def bag(value: str) -> object:
    return json.loads(value)


E2 = 'let E = datatable(s:string, t:string)["A","B", "B","A"];'


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_a_null_id_is_a_node_and_it_joins() -> None:
    """Measured: `4 → null → 3` is a path. With `=` instead of
    `IS NOT DISTINCT FROM` the null node matches nothing and the path is lost."""
    q = (
        "let E = datatable(s:long, t:long)[1,2, long(null),3, 4,long(null)];"
        " E | make-graph s --> t with_node_id=id"
        " | graph-match (a)-->(b)-->(c) project a.id, b.id, c.id"
    )
    assert rows(q) == [(4, None, 3)]


def test_an_edge_only_null_id_is_one_node() -> None:
    """Measured: five nodes, one null. `NOT IN` would drop it: a null on the
    left of NOT IN is never true."""
    q = (
        "let E = datatable(s:long, t:long)[1,2, long(null),3, 4,long(null)];"
        " E | make-graph s --> t with_node_id=id | graph-match (n) project n.id"
    )
    assert Counter(r[0] for r in rows(q)) == Counter([1, 2, 3, 4, None])


def test_identical_edge_rows_are_two_edges() -> None:
    """Measured: twice. A `DISTINCT` on the edges answers once."""
    q = (
        'let E = datatable(s:string, t:string)["A","B", "A","B"];'
        " E | make-graph s --> t with_node_id=id | graph-match (a)-->(b) project a.id, b.id"
    )
    assert rows(q) == [("A", "B"), ("A", "B")]


def test_an_undirected_self_loop_matches_twice() -> None:
    """Measured: `(a)--(b)` over `A→A` is (A, A) twice — the edge in each
    orientation. A `UNION` of the orientations answers once."""
    q = (
        'let E = datatable(s:string, t:string)["A","A"];'
        " E | make-graph s --> t with_node_id=id | graph-match (a)--(b) project a.id, b.id"
    )
    assert rows(q) == [("A", "A"), ("A", "A")]


def test_an_undirected_edge_is_one_edge_for_unique_edges() -> None:
    """Measured: `(a)--(b)--(c)` over a single edge is empty by default and
    walks it back with `cycles=all`. Numbering the reversed copy as a second
    edge would let the default answer A–B–A."""
    base = (
        'let E = datatable(s:string, t:string)["A","B"];'
        " E | make-graph s --> t with_node_id=id | graph-match {} (a)--(b)--(c)"
        " project a.id, b.id, c.id"
    )
    assert rows(base.format("")) == []
    assert sorted(rows(base.format("cycles=all"))) == [("A", "B", "A"), ("B", "A", "B")]


def test_a_node_in_two_node_tables_is_one_tables_row_whole() -> None:
    """Measured, twice, with *different* answers: Kusto keeps one table's row
    whole and the choice is arbitrary — the documentation says the properties
    are merged, and neither run merged them. So: one row, never a mix."""
    q = (
        'let E = datatable(s:string, t:string)["A","X"];'
        ' let P = datatable(pid:string, v:long)["A",10];'
        ' let C = datatable(cid:string, size:long)["A",99];'
        " E | make-graph s --> t with P on pid, C on cid | graph-match (a)-->(b) project a"
    )
    (value,), = rows(q)
    assert bag(value) in ({"pid": "A", "v": 10}, {"cid": "A", "size": 99})


def test_mismatched_id_types_are_refused_not_cast() -> None:
    """Measured: SEM1079. Left alone, DuckDB casts and matches `'1'` with `1`."""
    q = (
        "let E = datatable(s:long, t:long)[1,2];"
        ' let N = datatable(id:string, age:long)["1",10];'
        " E | make-graph s --> t with N on id | graph-match (n) project n.id, n.age"
    )
    with pytest.raises(duckdb.InvalidInputException, match="SEM1079"):
        rows(q)


def test_edge_identity_is_materialized() -> None:
    """`row_number() OVER ()` is each edge's identity. If DuckDB inlined the
    CTE, two references could number the same row differently and
    `unique_edges` would compare unrelated ids — so the SQL must say so."""
    sql = duckdb_kql.to_sql(
        "datatable(s:string, t:string)['A','B'] | make-graph s --> t"
        " | graph-match (a)-[e]->(b)-[f]->(c) project a"
    )
    assert '"_g0_e" AS MATERIALIZED (SELECT row_number() OVER () AS eid' in sql


# ---------------------------------------------------------------------------
# cycles=
# ---------------------------------------------------------------------------


def test_unique_edges_spans_fixed_and_variable_edges() -> None:
    """Measured: two rows. Uniqueness within each edge alone allows four."""
    q = (
        'let E = datatable(s:string, t:string, w:long)["A","B",1, "B","A",2];'
        " E | make-graph s --> t with_node_id=id"
        " | graph-match (a)-[e]->(b)-[p*1..3]->(c) project a.id, e.w, c.id"
    )
    assert sorted(rows(q)) == [("A", 1, "A"), ("B", 2, "B")]


def test_cycles_none_distinct_variables_distinct_nodes() -> None:
    """Measured: the two-cycle and a self-loop are excluded, while a triangle
    that repeats a *variable* is kept."""
    assert rows(
        E2 + " E | make-graph s --> t with_node_id=id"
        " | graph-match cycles=none (a)-->(b)-->(c) project a.id"
    ) == []
    triangle = (
        'let E = datatable(s:string, t:string)["A","B", "B","C", "C","A"];'
        " E | make-graph s --> t with_node_id=id"
        " | graph-match cycles=none (a)-->(b)-->(c)-->(a) project a.id"
    )
    assert sorted(rows(triangle)) == [("A",), ("B",), ("C",)]


def test_cycles_none_paths_are_simple_and_avoid_pattern_nodes() -> None:
    """Measured: `(a)-[p*1..3]->(a)` is empty although `(a)-->(b)-->(a)` is
    not, and an inner node may not be another pattern node."""
    assert rows(
        E2 + " E | make-graph s --> t with_node_id=id"
        " | graph-match cycles=none (a)-[p*1..3]->(a) project a.id"
    ) == []
    inner = (
        'let E = datatable(s:string, t:string)["X","A", "A","X", "X","B"];'
        " E | make-graph s --> t with_node_id=id"
        " | graph-match {} (x)-->(a)-[p*2..2]->(b) project x.id, b.id"
    )
    assert rows(inner.format("cycles=none")) == []
    assert rows(inner.format("")) == [("X", "B")]


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


def test_a_bag_drops_null_and_empty_string_properties() -> None:
    """Measured: `{"id":"A","z":0,"b":false}` — the empty `nm` is gone, zero
    and false stay. `json_object()` keeps all three."""
    q = (
        'let E = datatable(s:string, t:string)["A","B"];'
        " let N = datatable(id:string, nm:string, z:long, b:bool, age:long)"
        '["A","",0,false,long(null)];'
        " E | make-graph s --> t with N on id | graph-match (a)-[e]->(b) project a, e, b"
    )
    (a, e, b), = rows(q)
    assert bag(a) == {"id": "A", "z": 0, "b": False}
    assert bag(e) == {"s": "A", "t": "B"}
    assert bag(b) == {"id": "B"}


def test_an_edge_only_node_carries_its_id_under_every_key() -> None:
    """Measured: `{"pid":"Z","cid":"Z"}` with two node tables."""
    q = (
        'let E = datatable(s:string, t:string)["A","Z"];'
        ' let P = datatable(pid:string, v:long)["A",10];'
        ' let C = datatable(cid:string, size:long)["X",99];'
        " E | make-graph s --> t with P on pid, C on cid | graph-match (a)-->(b) project b"
    )
    (value,), = rows(q)
    assert bag(value) == {"pid": "Z", "cid": "Z"}


def test_output_names_are_the_measured_spellings() -> None:
    q = (
        'let E = datatable(s:string, t:string, w:long)["A","B",1, "B","C",2];'
        ' let N = datatable(id:string, age:long)["A",10,"B",20,"C",30];'
        " E | make-graph s --> t with N on id | graph-match (a)-[p*1..2]->(b)"
        " project tostring(a.id), a.age + 1, node_degree_in(a), any(p, w > 0),"
        " all(inner_nodes(p), age < 40), map(inner_nodes(p), node_id()), p,"
        " map(p, w), a.age, a.age"
    )
    assert columns(q) == [
        "Column1", "Column2", "node_degree_in_a", "p_Column", "inner_nodes_p_Column",
        "inner_nodes_p_node_id", "p", "p_w", "a_age", "a_age1",
    ]


def test_a_propertyless_node_projected_whole_is_positional() -> None:
    """Measured: `project a, b` over `make-graph s --> t` is Column1, Column2."""
    q = (
        'let E = datatable(s:string, t:string)["A","B"];'
        " E | make-graph s --> t | graph-match (a)-->(b) project a, b"
    )
    assert columns(q) == ["Column1", "Column2"]


def test_an_unmeasured_name_is_refused() -> None:
    """Measured: `strlen(a.id)` is named `strlen_`, which no rule predicts —
    so an unaliased call whose name is unmeasured is refused, not guessed."""
    with pytest.raises(KqlUnsupportedError, match="name this column"):
        duckdb_kql.to_sql(
            "datatable(s:string, t:string)['A','B'] | make-graph s --> t with_node_id=id"
            " | graph-match (a)-->(b) project strlen(a.id)"
        )


def test_a_pattern_variable_wins_over_a_let() -> None:
    """Measured: `let a = 5; … (a)-->(b) project a.id` reads the node."""
    q = (
        'let a = 5; let E = datatable(s:string, t:string)["A","B"];'
        " E | make-graph s --> t with_node_id=id | graph-match (a)-->(b) project a.id"
    )
    assert rows(q) == [("A",)]


def test_inside_a_lambda_a_property_wins_and_a_let_still_reaches() -> None:
    """Measured: with `let w = 100`, `map(p, w)` maps the property; with
    `let lim = 2`, `all(p, w < lim)` reads the `let`."""
    q = (
        'let w = 100; let lim = 2;'
        ' let E = datatable(s:string, t:string, w:long)["A","B",1, "B","C",2];'
        " E | make-graph s --> t with_node_id=id | graph-match (a)-[p*1..2]->(b)"
        " project a.id, b.id, x = map(p, w), y = all(p, w < lim)"
    )
    got = {(a, b): (json.loads(x), y) for a, b, x, y in rows(q)}
    assert got == {
        ("A", "B"): ([1], True), ("B", "C"): ([2], False), ("A", "C"): ([1, 2], False),
    }


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def test_a_null_condition_fails_all_and_any() -> None:
    """Measured: with `w` null, all three are false. `list_bool_and` skips
    nulls and would answer true for `all`."""
    q = (
        'let E = datatable(s:string, t:string, w:long)["A","B",long(null), "B","C",1];'
        " E | make-graph s --> t with_node_id=id | graph-match (a)-[p*1..2]->(b)"
        " project a.id, b.id, al = all(p, w > 0), an = any(p, w > 0),"
        " nal = all(p, not(w > 0)), m = map(p, w)"
    )
    got = {(a, b): (al, an, nal, json.loads(m)) for a, b, al, an, nal, m in rows(q)}
    assert got == {
        ("A", "B"): (False, False, False, [None]),
        ("B", "C"): (True, True, False, [1]),
        ("A", "C"): (False, True, False, [None, 1]),
    }


def test_a_zero_length_path() -> None:
    """Measured: every node, isolated ones too; `all` true, `any` false, and
    `map` empty. `list_bool_and([])` is null, not true."""
    q = (
        'let E = datatable(s:string, t:string, w:long)["A","B",1];'
        ' let N = datatable(id:string)["A","B","Q"];'
        " E | make-graph s --> t with N on id | graph-match (a)-[p*0..0]->(b)"
        " project a.id, x = all(p, w > 5), y = any(p, w > 0), m = map(p, w)"
    )
    assert sorted(rows(q)) == [
        ("A", True, False, "[]"), ("B", True, False, "[]"), ("Q", True, False, "[]"),
    ]


def test_shortest_paths_are_simple_whatever_cycles_says() -> None:
    """Measured: on `A→B, B→A, A→C`, `*3..5` from A to C finds nothing, though
    graph-match finds `A→B→A→C`."""
    q = (
        'let E = datatable(s:string, t:string)["A","B", "B","A", "A","C"];'
        " E | make-graph s --> t with_node_id=id"
        " | graph-{} (a)-[p*3..5]->(b) where a.id == 'A' and b.id == 'C'"
        " project l = array_length(p)"
    )
    assert rows(q.format("shortest-paths")) == []
    assert rows(q.format("match")) == [(3,)]


def test_shortest_paths_respect_the_range_and_filter_first() -> None:
    """Measured: with a direct `A→C`, `*2..5` returns the length-2 path; and
    when the short route fails `all(p, ok)` the longer one is returned, not
    nothing."""
    ranged = (
        'let E = datatable(s:string, t:string)["A","B", "B","C", "A","C"];'
        " E | make-graph s --> t with_node_id=id | graph-shortest-paths (a)-[p*2..5]->(b)"
        " where a.id == 'A' and b.id == 'C' project l = array_length(p)"
    )
    assert rows(ranged) == [(2,)]
    filtered = (
        "let E = datatable(s:string, t:string, ok:bool)"
        '["A","B",false, "B","D",true, "A","C",true, "C","E",true, "E","D",true];'
        " E | make-graph s --> t with_node_id=id | graph-shortest-paths (a)-[p*1..5]->(b)"
        " where a.id == 'A' and b.id == 'D' and all(p, ok)"
        " project l = array_length(p), n = map(inner_nodes(p), id)"
    )
    assert rows(filtered) == [(3, '["C","E"]')]


def test_shortest_paths_any_and_all() -> None:
    """Measured: `all` returns both tied paths over parallel edges, `any` one."""
    q = (
        'let E = datatable(s:string, t:string, k:long)["A","B",1, "A","B",2];'
        " E | make-graph s --> t with_node_id=id"
        " | graph-shortest-paths output={} (a)-[p*1..5]->(b) project k = p.k"
    )
    assert sorted(rows(q.format("all"))) == [("[1]",), ("[2]",)]
    assert len(rows(q.format("any"))) == 1


def test_shortest_paths_refuse_comparing_two_elements() -> None:
    """Measured: CRT0001, "dynamic filters are not supported"."""
    with pytest.raises(KqlUnsupportedError, match="CRT0001"):
        duckdb_kql.to_sql(
            "datatable(s:string, t:string)['A','B'] | make-graph s --> t with_node_id=id"
            " | graph-shortest-paths (a)-[p*1..5]->(b) where a.id == b.id project a.id"
        )


def test_node_degrees_count_edges_and_a_self_loop_each_way() -> None:
    """Measured: A with `A→B` twice, `A→A` and `C→A` is in 2, out 3."""
    q = (
        'let E = datatable(s:string, t:string)["A","B", "A","B", "A","A", "C","A"];'
        " E | make-graph s --> t with_node_id=id"
        " | graph-match (n) project n.id, i = node_degree_in(n), o = node_degree_out(n)"
    )
    assert sorted(rows(q)) == [("A", 2, 3), ("B", 2, 0), ("C", 0, 1)]


# ---------------------------------------------------------------------------
# graph-to-table, graph-mark-components, partitioned-by
# ---------------------------------------------------------------------------


def test_weak_components_are_numbered_by_first_appearance() -> None:
    """Measured: node-table rows first, then edges in order."""
    q = (
        'let E = datatable(s:string, t:string)["Z","Y", "A","B"];'
        ' let N = datatable(id:string)["A","B","Q","Z","Y"];'
        " E | make-graph s --> t with N on id | graph-mark-components | graph-to-table nodes"
    )
    assert rows(q) == [("A", 0), ("B", 0), ("Q", 1), ("Z", 2), ("Y", 2)]


def test_strong_components_partition_the_nodes() -> None:
    """Kusto's strong numbering is arbitrary (measured 1, 0, 3, 2 here), so
    only the partition is compared."""
    q = (
        'let E = datatable(s:string, t:string)["A","B", "B","A", "B","C", "D","E"];'
        " E | make-graph s --> t with_node_id=id | graph-mark-components kind=strong"
        " | graph-to-table nodes"
    )
    groups: dict[int, set[str]] = {}
    for node, component in rows(q):
        groups.setdefault(component, set()).add(node)
    assert sorted(map(sorted, groups.values())) == [["A", "B"], ["C"], ["D"], ["E"]]


def test_a_component_name_collision_is_refused() -> None:
    """Measured: Kusto returns two columns called ComponentId."""
    with pytest.raises(KqlUnsupportedError, match="ComponentId"):
        duckdb_kql.to_sql(
            "let N = datatable(id:string, ComponentId:string)['A','x'];"
            " datatable(s:string, t:string)['A','B'] | make-graph s --> t with N on id"
            " | graph-mark-components | graph-to-table nodes"
        )


def test_partitions_are_separate_graphs() -> None:
    """Measured: per-tenant degrees and paths. The first version tied a path's
    end to no partition and answered every row twice."""
    q = (
        "let E = datatable(s:string, t:string, tenant:string)"
        '["A","B","x", "B","C","x", "A","B","y", "C","A","y"];'
        " let N = datatable(id:string, tenant:string)"
        '["A","x","B","x","C","x","A","y","B","y","C","y"];'
        " E | make-graph s --> t with N on id partitioned-by tenant"
        " (graph-match (a)-[p*1..3]->(b) project a.id, b.id, a.tenant,"
        " d = node_degree_out(a), l = array_length(p))"
    )
    assert sorted(rows(q)) == sorted([
        ("A", "B", "y", 1, 1), ("C", "A", "y", 1, 1), ("C", "B", "y", 1, 2),
        ("A", "B", "x", 1, 1), ("B", "C", "x", 1, 1), ("A", "C", "x", 1, 2),
    ])


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kql", "match"),
    [
        # SEM0456 on the emulator: nothing to measure against.
        ("E | make-graph s -- t | graph-match (a)-->(b) project a", "undirected"),
        # KQL's hash(): xxhash64, which DuckDB lacks.
        ("E | make-graph s --> t | graph-to-table nodes with_node_id=X", "hash"),
        # Several result tables.
        ("E | make-graph s --> t | graph-to-table nodes as N, edges as X; N", "statement"),
        # SEM0001.
        ("E | make-graph s --> t | graph-match (a)-->(b)", "without project"),
        # SEM1011.
        ("E | make-graph s --> t | graph-match (a)-->(b), (c)-->(d) project a", "SEM1011"),
        # SEM0104.
        ("E | make-graph s --> t | count", "SEM0104"),
        # The emulator fails internally on these.
        ("E | make-graph s --> t with N on t partitioned-by s (graph-to-table nodes)",
         "partitioned-by with graph-to-table"),
    ],
)
def test_refusals(kql: str, match: str) -> None:
    prefix = "let E = datatable(s:string, t:string)['A','B']; let N = E; "
    with pytest.raises(KqlUnsupportedError, match=match):
        duckdb_kql.to_sql(prefix + kql)


@pytest.mark.parametrize(
    ("project", "where", "match"),
    [
        ("a.nope", None, "SEM1040"),
        ("a.id", "s == 'A'", "SEM0100"),
        ("a.id", "p.w > 0", "SEM1054"),
    ],
)
def test_render_time_refusals(project: str, where: str | None, match: str) -> None:
    q = (
        "datatable(s:string, t:string, w:long)['A','B',1] | make-graph s --> t with_node_id=id"
        f" | graph-match (a)-[p*1..2]->(b) {'where ' + where if where else ''} project {project}"
    )
    with pytest.raises(KqlUnsupportedError, match=match):
        duckdb_kql.to_sql(q)


def test_a_graph_let_is_spliced_where_it_is_used() -> None:
    q = (
        "let G = datatable(s:string, t:string)['A','B', 'B','C'] | make-graph s --> t"
        " with_node_id=id;"
        " G | graph-match (a)-->(b) project a.id"
        " | join kind=inner (G | graph-match (a)-->(b) project x = b.id) on $left.a_id == $right.x"
    )
    assert rows(q) == [("B", "B")]
