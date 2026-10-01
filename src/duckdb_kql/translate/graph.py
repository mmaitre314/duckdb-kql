"""Rendering for graph semantics (docs/graph-proposal.md §3, TRANSLATION.md R25).

A graph is two relations that exist only inside the SQL of the operator that
consumes it:

* **edges** — one row per edge row, keyed by ``eid`` (its row number), with
  ``src``, ``dst`` and every edge column in a ``props`` struct;
* **nodes** — one row per node id, keyed by ``nid``, with the node properties
  in a ``props`` struct and ``ix``, the node's order of first appearance.

A fixed-length pattern is a join over those, a variable-length edge is a
bounded recursive CTE carrying its path, and the result is a plain table.

Each line of the node and edge relations is there because the obvious
alternative is a wrong answer — the docstrings below say which.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from .. import ir
from ..errors import KqlUnsupportedError

if TYPE_CHECKING:
    from ..schema import Schema

#: Unaliased functions measured to be named ``ColumnN`` in `graph-match`'s
#: `project`. Anything else unaliased and unmeasured is refused: `strlen(a.id)`
#: was measured as ``strlen_``, which no rule here would have guessed.
_POSITIONAL = frozenset({
    "tostring", "toupper", "tolong", "bin", "isnull", "isempty", "array_length",
})

#: The lambda parameters. Never a column: they only ever appear bound.
_ELEM = "_gz"
_KEY = "_gk"


@dataclasses.dataclass
class _Graph:
    """What the renderer knows about one graph while rendering it."""

    prefix: str
    edge_cols: list[str]
    node_props: list[str]
    #: The `partitioned-by` column, or None.
    partition: str | None
    degrees: bool = False

    @property
    def partitioned(self) -> bool:
        return self.partition is not None

    def name(self, suffix: str) -> str:
        return f'"{self.prefix}{suffix}"'


@dataclasses.dataclass
class _Match:
    """The pattern being rendered: variable -> (kind, SQL alias)."""

    graph: _Graph
    kinds: dict[str, str]
    aliases: dict[str, str]
    #: For a path variable: whether its elements are inner nodes (`inner_nodes`).
    clause: str = "project"
    #: Inside a map()/all()/any() body: ("edge"|"node", element SQL).
    element: tuple[str, str] | None = None


_CTX: _Match | None = None


@contextmanager
def _context(match: _Match) -> Iterator[None]:
    global _CTX
    saved, _CTX = _CTX, match
    try:
        yield
    finally:
        _CTX = saved


def _quote(name: str) -> str:
    from . import quote_ident

    return quote_ident(name)


def _string(value: str) -> str:
    from . import quote_string

    return quote_string(value)


def _refuse(construct: str, hint: str) -> KqlUnsupportedError:
    return KqlUnsupportedError(construct, hint=hint)


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------


def output_columns(source: ir.GraphSource, schema: Schema | None) -> list[str]:
    """The columns *source* produces, in order."""
    consumer = source.consumer
    graph = source.graph
    if isinstance(consumer, ir.GraphMatch):
        return column_names(consumer.project, _bare_nodes(consumer, graph, schema))
    if consumer.kind == "edges":
        from ..schema import output_columns as query_columns

        return query_columns(graph.edges, schema)
    return _node_properties(graph, schema)


def _node_properties(graph: ir.MakeGraph, schema: Schema | None) -> list[str]:
    """Node property names, in order: the node tables' columns (first table
    first), or the `with_node_id` name, then each component id."""
    from ..schema import output_columns as query_columns

    props: list[str] = []
    if graph.node_id is not None:
        props.append(graph.node_id)
    for table in graph.nodes:
        for column in query_columns(table.query, schema):
            if column not in props:
                props.append(column)
    for marked in graph.components:
        if marked.name in props:
            # Measured: graph-to-table then returns two columns of that name.
            raise _refuse(
                "graph-mark-components",
                f"the nodes already have a property {marked.name!r}; Kusto then "
                "returns two columns of that name, which cannot be reproduced",
            )
        props.append(marked.name)
    return props


def _bare_nodes(
    match: ir.GraphMatch, graph: ir.MakeGraph, schema: Schema | None
) -> frozenset[str]:
    """The node variables whose bag is empty: all of them, in a graph whose
    nodes have no properties at all."""
    if _node_properties(graph, schema):
        return frozenset()
    return frozenset(n.name for p in match.patterns for n in p.nodes)


def column_names(
    project: tuple[ir.NamedExpr, ...], bare: frozenset[str] = frozenset()
) -> list[str]:
    """`graph-match`'s output names — measured, spelling by spelling.

    ``a.age`` is ``a_age``, ``map(inner_nodes(p), id)`` is
    ``inner_nodes_p_id``, a computed expression is ``Column1``, ``Column2``…
    counted among the unnamed only, and a repeated name gets ``1``, ``2``…
    A node with no properties at all, projected whole, is positional too:
    measured, `project a, b` over `make-graph s --> t` is `Column1, Column2`.
    """
    from ..schema import disambiguate

    names: list[str] = []
    unnamed = 0
    for named in project:
        expr = named.expr
        if named.name is None and isinstance(expr, ir.GraphVar) and expr.name in bare:
            name = None
        else:
            name = named.name or _auto_name(expr)
        if name is None:
            unnamed += 1
            name = f"Column{unnamed}"
        names.append(disambiguate(name, names))
    return names


def _auto_name(expr: ir.Expr) -> str | None:
    """The measured name of an unaliased expression, or None for ``ColumnN``."""
    if isinstance(expr, ir.GraphVar):
        return expr.name
    if isinstance(expr, ir.GraphProperty):
        return f"{expr.var}_{expr.prop}"
    if isinstance(expr, ir.PathAccess) and isinstance(expr.base, ir.GraphProperty):
        parts = [expr.base.var, expr.base.prop]
        for step in expr.steps:
            if step.name is None:
                raise _unnamed()
            parts.append(step.name)
        return "_".join(parts)
    if isinstance(expr, ir.GraphCall):
        if expr.body is None:
            return f"{expr.name}_{expr.var}"
        prefix = ("inner_nodes_" if expr.inner else "") + f"{expr.var}_"
        body = expr.body
        if isinstance(body, ir.GraphElement):
            return prefix + body.name
        if isinstance(body, ir.GraphCall) and body.name == "node_id" and body.var is None:
            return prefix + "node_id"
        return prefix + "Column"
    if isinstance(expr, (ir.BinaryOp, ir.UnaryOp)):
        return None
    if isinstance(expr, ir.FunctionCall) and expr.name in _POSITIONAL:
        return None
    raise _unnamed()


def _unnamed() -> KqlUnsupportedError:
    return _refuse(
        "graph-match project",
        "name this column (`project x = …`): the name Kusto gives this "
        "expression has not been measured, and a wrong column name is a wrong "
        "answer to everything downstream",
    )


# ---------------------------------------------------------------------------
# The two relations
# ---------------------------------------------------------------------------


def render(source: ir.GraphSource, schema: Schema | None) -> str:
    """The SQL of a graph source: one SELECT, its CTEs nested inside it."""
    from ..schema import output_columns as query_columns
    from . import to_sql

    graph = source.graph
    edge_cols = query_columns(graph.edges, schema)
    tables = [(t, query_columns(t.query, schema)) for t in graph.nodes]
    _check_columns(graph, edge_cols, tables)
    props = _node_properties(graph, schema)
    base_props = [p for p in props if p not in {m.name for m in graph.components}]
    g = _Graph(_prefix(), edge_cols, props, graph.partition)
    consumer = source.consumer
    if isinstance(consumer, ir.GraphMatch):
        g.degrees = _uses_degrees(consumer)

    ctes = [f"{g.name('ed')} AS MATERIALIZED ({to_sql(graph.edges, schema)})"]
    for i, (table, _) in enumerate(tables):
        ctes.append(f"{g.name(f't{i}')} AS MATERIALIZED ({to_sql(table.query, schema)})")
    ctes.append(f"{g.name('chk')} AS MATERIALIZED ({_guard(g, graph, tables)})")
    ctes.append(f"{g.name('e')} AS MATERIALIZED ({_edges(g, graph)})")
    ctes.append(f"{g.name('nr')} AS ({_raw_nodes(g, graph, tables, base_props)})")
    ctes.append(f"{g.name('n0')} AS MATERIALIZED ({_nodes(g)})")
    nodes = "n0"
    for i, marked in enumerate(graph.components):
        ctes.extend(_components(g, nodes, i, marked, has_props=bool(base_props) or i > 0))
        nodes = f"n{i + 1}"
    ctes.append(f"{g.name('n')} AS MATERIALIZED (SELECT * FROM {g.name(nodes)})")

    if isinstance(consumer, ir.GraphToTable):
        body = _graph_to_table(g, consumer)
    else:
        ctes.append(f"{g.name('eu')} AS ({_undirected(g)})")
        body, extra = _match(g, consumer)
        ctes.extend(extra)
    return "SELECT * FROM (WITH RECURSIVE " + ",\n".join(ctes) + f"\n{body})"


def _prefix() -> str:
    """``_g0_``, ``_g1_``… — free of every name the statement reads."""
    from . import _NAMES

    if _NAMES is None:
        return "_g0_"
    index = _NAMES.counter
    _NAMES.counter += 1
    prefix = "_g"
    while any(name.startswith(f"{prefix}{index}_") for name in _NAMES.taken):
        prefix = "_" + prefix
    return f"{prefix}{index}_"


def _check_columns(
    graph: ir.MakeGraph, edge_cols: list[str], tables: list[tuple[ir.NodeTable, list[str]]]
) -> None:
    for column in (graph.source, graph.target):
        if column not in edge_cols:
            raise _refuse(
                "make-graph", f"the edges have no column {column!r} (Kusto: SEM0100)"
            )
    for table, cols in tables:
        if table.key not in cols:
            raise _refuse(
                "make-graph", f"the node table has no column {table.key!r} (Kusto: SEM0100)"
            )
    if graph.partition is not None:
        # Measured: with `with_node_id` the partition column does not resolve,
        # and the documentation requires it in the edges and every node table.
        if not tables or graph.partition not in edge_cols or any(
            graph.partition not in cols for _, cols in tables
        ):
            raise _refuse(
                "make-graph partitioned-by",
                f"{graph.partition!r} must be a column of the edges and of every "
                "node table (Kusto: SEM0100)",
            )


def _typeof(cte: str, column: str) -> str:
    """The column's type, known even when the table is empty."""
    return f"typeof((SELECT x.{_quote(column)} FROM {cte} x LIMIT 1))"


def _guard(g: _Graph, graph: ir.MakeGraph, tables: list[tuple[ir.NodeTable, list[str]]]) -> str:
    """Kusto's compile-time type refusals, made at run time.

    The schema carries names, not types, and left alone DuckDB would compare a
    VARCHAR id with a BIGINT one by casting — matching `'1'` with `1` where
    Kusto refuses the query (SEM1079). `typeof` of a scalar subquery is the
    column's declared type even when the table is empty.
    """
    src = _typeof(g.name("ed"), graph.source)
    dst = _typeof(g.name("ed"), graph.target)
    checks = [
        (f"{src} <> {dst}",
         "make-graph: source and target must be of the same data type (Kusto: SEM1019)"),
        (f"{src} = 'JSON'",
         "make-graph: source, target and node id columns can't be of type dynamic "
         "(Kusto: SEM1006)"),
    ]
    for i, (table, _) in enumerate(tables):
        checks.append((
            f"{_typeof(g.name(f't{i}'), table.key)} <> {src}",
            "make-graph: node id columns must be of the same data type as the "
            "edges' source and target (Kusto: SEM1079)",
        ))
    if len(tables) == 2:
        shared = [c for c in tables[0][1] if c in tables[1][1]]
        for column in shared:
            checks.append((
                f"{_typeof(g.name('t0'), column)} <> {_typeof(g.name('t1'), column)}",
                f"make-graph: node property {column!r} has a different type in each "
                "node table, and Kusto then drops it (SEM1040 on use)",
            ))
    whens = " ".join(f"WHEN {cond} THEN error({_string(msg)})" for cond, msg in checks)
    return f"SELECT CASE {whens} END AS ok"


def _part(alias: str, g: _Graph) -> str:
    """``, x."tenant" AS part`` when partitioned: the partition key, read from
    the edges or a node table, under one name."""
    return f", {alias}.{_quote(g.partition)} AS part" if g.partition is not None else ""


def _edges(g: _Graph, graph: ir.MakeGraph) -> str:
    """One row per edge row — duplicates included, measured — numbered once.

    ``MATERIALIZED`` is not an optimisation: ``row_number() OVER ()`` is the
    edge's identity, and if DuckDB inlined this CTE two references to it could
    number the same row differently, and `unique_edges` would compare
    unrelated ids.
    """
    fields = ", ".join(f"{_quote(c)} := x.{_quote(c)}" for c in g.edge_cols)
    return (
        f"SELECT row_number() OVER () AS eid, x.{_quote(graph.source)} AS src, "
        f"x.{_quote(graph.target)} AS dst{_part('x', g)}, struct_pack({fields}) AS props "
        f"FROM {g.name('ed')} x, {g.name('chk')} c"
    )


def _same(left: str, right: str) -> str:
    return f"{left} IS NOT DISTINCT FROM {right}"


def _same_part(left: str, right: str, g: _Graph) -> str:
    return f" AND {_same(f'{left}.part', f'{right}.part')}" if g.partitioned else ""


def _raw_nodes(
    g: _Graph,
    graph: ir.MakeGraph,
    tables: list[tuple[ir.NodeTable, list[str]]],
    props: list[str],
) -> str:
    """Every node once: node-table rows first, then ids seen only on edges.

    * **A null id is a key** — measured, `4 → null → 3` is a path — so every
      comparison of ids is ``IS NOT DISTINCT FROM`` and every anti-join is
      ``NOT EXISTS``. ``NOT IN`` would lose the null node twice over.
    * **A node is one whole row** of its table (``QUALIFY row_number()``), never
      a mix of columns from several.
    * **The first node table wins, whole** — measured, contradicting the
      documentation's "merged" — which the anti-join on the second gives.
    * An edge-only node has its id under every node table's key column, and
      under the `with_node_id` name: measured, `{"pid":"Z","cid":"Z"}`.
    """
    branches = []
    for i, (table, cols) in enumerate(tables):
        fields = ", ".join(
            f"{_quote(p)} := " + (f"x.{_quote(p)}" if p in cols else "NULL") for p in props
        )
        partition = f"{_quote(graph.partition)}, " if graph.partition else ""
        earlier = "".join(
            f" AND NOT EXISTS (SELECT 1 FROM {g.name(f't{j}')} y WHERE "
            f"{_same(f'y.{_quote(other.key)}', f'x.{_quote(table.key)}')}"
            + (
                f" AND {_same(f'y.{_quote(graph.partition)}', f'x.{_quote(graph.partition)}')}"
                if graph.partition else ""
            )
            + ")"
            for j, (other, _) in enumerate(tables[:i])
        )
        branches.append(
            f"SELECT x.{_quote(table.key)} AS nid{_part('x', g)}, struct_pack({fields}) AS props, "
            f"{i} AS grp, row_number() OVER () AS ord FROM {g.name(f't{i}')} x "
            f"WHERE true{earlier} "
            f"QUALIFY row_number() OVER (PARTITION BY {partition}x.{_quote(table.key)}) = 1"
        )
    keys = {t.key for t, _ in tables}
    if graph.node_id is not None:
        keys.add(graph.node_id)
    fields = ", ".join(
        f"{_quote(p)} := " + ("x.nid" if p in keys else "NULL") for p in props
    )
    packed = f"struct_pack({fields})" if props else "NULL"
    seen = "".join(
        f" AND NOT EXISTS (SELECT 1 FROM {g.name(f't{i}')} y WHERE "
        f"{_same(f'y.{_quote(t.key)}', 'x.nid')}"
        + (f" AND {_same(f'y.{_quote(graph.partition)}', 'x.part')}" if graph.partition else "")
        + ")"
        for i, (t, _) in enumerate(tables)
    )
    group = ", x.part" if g.partitioned else ""
    branches.append(
        f"SELECT x.nid{', x.part' if g.partitioned else ''}, {packed} AS props, "
        f"{len(tables)} AS grp, min(x.ord) AS ord FROM ("
        f"SELECT src AS nid{', part' if g.partitioned else ''}, eid * 2 AS ord FROM {g.name('e')} "
        f"UNION ALL SELECT dst{', part' if g.partitioned else ''}, eid * 2 + 1 FROM {g.name('e')}"
        f") x WHERE true{seen} GROUP BY x.nid{group}"
    )
    return "\nUNION ALL ".join(branches)


def _nodes(g: _Graph) -> str:
    """Numbered by first appearance: node-table order, then edge order.

    Measured: that is the order `graph-mark-components` numbers components in.
    """
    part = ", part" if g.partitioned else ""
    degrees = ""
    if g.degrees:
        degrees = (
            f", (SELECT count(*) FROM {g.name('e')} e WHERE {_same('e.dst', 'r.nid')}"
            f"{_same_part('e', 'r', g)}) AS indeg"
            f", (SELECT count(*) FROM {g.name('e')} e WHERE {_same('e.src', 'r.nid')}"
            f"{_same_part('e', 'r', g)}) AS outdeg"
        )
    return (
        f"SELECT nid{part}, props, row_number() OVER (ORDER BY grp, ord) AS ix{degrees} "
        f"FROM {g.name('nr')} r"
    )


def _undirected(g: _Graph) -> str:
    """Each edge once in each orientation, **under the same eid**.

    `UNION ALL`, so a self-loop appears twice and matches twice — measured.
    One eid for both copies is what stops `unique_edges` walking an edge back:
    measured, `(a)--(b)--(c)` over a single edge is empty.
    """
    part = ", part" if g.partitioned else ""
    return (
        f"SELECT eid, src, dst{part}, props FROM {g.name('e')} "
        f"UNION ALL SELECT eid, dst, src{part}, props FROM {g.name('e')}"
    )


def _components(
    g: _Graph, nodes: str, i: int, marked: ir.MarkComponents, *, has_props: bool
) -> list[str]:
    """`graph-mark-components`: a reachability closure, then a dense numbering.

    A component is numbered by its earliest node, so the numbering follows
    first appearance — measured for weak components. Kusto's numbering is
    documented as arbitrary, and its strong numbering is not first appearance,
    so a test compares the partition, never the numbers.
    """
    src, part = g.name(nodes), ", part" if g.partitioned else ""
    pairs = (
        f"SELECT a.ix AS x, b.ix AS y FROM {g.name('e')} e "
        f"JOIN {src} a ON {_same('a.nid', 'e.src')}{_same_part('a', 'e', g)} "
        f"JOIN {src} b ON {_same('b.nid', 'e.dst')}{_same_part('b', 'e', g)}"
    )
    if marked.kind == "weak":
        pairs += f" UNION ALL SELECT y, x FROM ({pairs})"
    reach = (
        f"SELECT ix AS x, ix AS y FROM {src} UNION "
        f"SELECT r.x, s.y FROM {g.name(f'r{i}')} r JOIN {g.name(f's{i}')} s ON s.x = r.y"
    )
    if marked.kind == "weak":
        root = f"SELECT x AS ix, min(y) AS root FROM {g.name(f'r{i}')} GROUP BY x"
    else:
        root = (
            f"SELECT a.x AS ix, min(a.y) AS root FROM {g.name(f'r{i}')} a "
            f"JOIN {g.name(f'r{i}')} b ON b.x = a.y AND b.y = a.x GROUP BY a.x"
        )
    over = "PARTITION BY n.part " if g.partitioned else ""
    number = f"dense_rank() OVER ({over}ORDER BY c.root) - 1"
    props = (
        f"struct_insert(n.props, {_quote(marked.name)} := {number})"
        if has_props
        else f"struct_pack({_quote(marked.name)} := {number})"
    )
    degrees = ", n.indeg, n.outdeg" if g.degrees else ""
    return [
        f"{g.name(f's{i}')} AS ({pairs})",
        f"{g.name(f'r{i}')}(x, y) AS ({reach})",
        f"{g.name(f'c{i}')} AS ({root})",
        f"{g.name(f'n{i + 1}')} AS MATERIALIZED (SELECT n.nid{part.replace(', ', ', n.')}, "
        f"{props} AS props, n.ix{degrees} FROM {src} n JOIN {g.name(f'c{i}')} c ON c.ix = n.ix)",
    ]


# ---------------------------------------------------------------------------
# graph-to-table
# ---------------------------------------------------------------------------


def _graph_to_table(g: _Graph, consumer: ir.GraphToTable) -> str:
    if consumer.kind == "edges":
        return f"SELECT props.* FROM {g.name('e')} ORDER BY eid"
    if not g.node_props:
        # Measured: zero columns. A DuckDB result cannot have none.
        raise _refuse(
            "graph-to-table nodes",
            "the graph's nodes have no properties (no node table and no "
            "with_node_id), so Kusto returns zero columns",
        )
    return f"SELECT props.* FROM {g.name('n')} ORDER BY ix"


# ---------------------------------------------------------------------------
# graph-match and graph-shortest-paths
# ---------------------------------------------------------------------------


def _uses_degrees(match: ir.GraphMatch) -> bool:
    found = False

    def visit(node: object) -> None:
        nonlocal found
        if isinstance(node, ir.GraphCall) and node.name.startswith("node_degree"):
            found = True
        if dataclasses.is_dataclass(node) and not isinstance(node, (type, ir.Query)):
            for f in dataclasses.fields(node):
                visit(getattr(node, f.name))
        elif isinstance(node, tuple):
            for item in node:
                visit(item)

    visit(match.where)
    visit(match.project)
    return found


def _bound(expr: ir.Expr | None, what: str) -> int:
    if isinstance(expr, ir.Literal) and isinstance(expr.value, int) and not isinstance(
        expr.value, bool
    ):
        return expr.value
    raise _refuse("graph-match", f"the {what} bound of a variable-length edge must be "
                  "an integer constant")


def _match(g: _Graph, match: ir.GraphMatch) -> tuple[str, list[str]]:
    """The SELECT of a `graph-match` or `graph-shortest-paths`, and its path CTEs."""
    _refuse_columns(match)
    kinds: dict[str, str] = {}
    aliases: dict[str, str] = {}
    tables: list[str] = []
    conditions: list[str] = []
    ctes: list[str] = []
    fixed: list[str] = []
    paths: list[str] = []
    nodes_seen: list[str] = []
    shortest = match.shortest is not None

    if shortest:
        _refuse_shortest_shape(match)

    for pattern in match.patterns:
        for node in pattern.nodes:
            if node.name not in aliases:
                alias = f"v{len(aliases)}"
                aliases[node.name] = alias
                kinds[node.name] = "node"
                tables.append(f"{g.name('n')} {alias}")
                nodes_seen.append(alias)
        for index, edge in enumerate(pattern.edges):
            left = aliases[pattern.nodes[index].name]
            right = aliases[pattern.nodes[index + 1].name]
            alias = f"w{len(aliases)}"
            aliases[edge.name] = alias
            if edge.variable:
                kinds[edge.name] = "path"
                low = _bound(edge.low, "lower")
                high = _bound(edge.high, "upper")
                if low < 0 or low > high:
                    raise _refuse("graph-match", "invalid variable length range (Kusto: SEM1013)")
                simple = shortest or match.cycles == "none"
                cte = g.name(f"p{len(ctes)}")
                ctes.append(f"{cte} AS ({_path(g, edge, high, match, simple, cte)})")
                tables.append(f"{cte} {alias}")
                conditions.append(f"{alias}.plen >= {low}")
                conditions.append(_same(f"{alias}.st", f"{left}.nid"))
                conditions.append(_same(f"{alias}.en", f"{right}.nid"))
                if g.partitioned:
                    conditions.append(_same(f"{alias}.part", f"{left}.part"))
                    conditions.append(_same(f"{alias}.part", f"{right}.part"))
                paths.append(alias)
            else:
                kinds[edge.name] = "edge"
                relation = g.name("eu" if edge.direction == "any" else "e")
                tables.append(f"{relation} {alias}")
                start, end = (left, right) if edge.direction != "in" else (right, left)
                conditions.append(_same(f"{alias}.src", f"{start}.nid"))
                conditions.append(_same(f"{alias}.dst", f"{end}.nid"))
                if g.partitioned:
                    conditions.append(_same(f"{alias}.part", f"{start}.part"))
                    conditions.append(_same(f"{alias}.part", f"{end}.part"))
                fixed.append(alias)

    if match.cycles == "unique_edges" and not shortest:
        for i, a in enumerate(fixed):
            conditions.extend(f"{a}.eid <> {b}.eid" for b in fixed[i + 1:])
            conditions.extend(f"NOT list_contains({p}.eids, {a}.eid)" for p in paths)
        for i, p in enumerate(paths):
            conditions.extend(f"NOT list_has_any({p}.eids, {q}.eids)" for q in paths[i + 1:])
    elif match.cycles == "none":
        # Measured: distinct variables bind distinct nodes, while a repeated one
        # is the same node — the triangle `(a)-->(b)-->(c)-->(a)` is kept. A
        # variable-length edge is a simple path of its own, so `(a)-[p]->(a)`
        # is empty, and its inner nodes are none of the pattern's nodes.
        for i, a in enumerate(nodes_seen):
            conditions.extend(f"{a}.nid IS DISTINCT FROM {b}.nid" for b in nodes_seen[i + 1:])
        for i, p in enumerate(paths):
            for a in nodes_seen:
                conditions.append(f"NOT {_has_node(f'{p}.inn', f'{a}.nid')}")
            for q in paths[i + 1:]:
                conditions.append(
                    f"len(list_filter({p}.inn, _gy -> {_has_node(f'{q}.inn', '_gy.nid')})) = 0"
                )

    state = _Match(g, kinds, aliases)
    with _context(state):
        state.clause = "where"
        if match.where is not None:
            conditions.append(f"({_render(match.where)})")
        state.clause = "project"
        bare = frozenset() if g.node_props else frozenset(
            v for v, kind in kinds.items() if kind == "node"
        )
        names = column_names(match.project, bare)
        selected = ", ".join(
            f"{_render(named.expr)} AS {_quote(name)}"
            for named, name in zip(match.project, names, strict=True)
        )

    where = " AND ".join(conditions) if conditions else "true"
    body = f"SELECT {selected} FROM {', '.join(tables)} WHERE {where}"
    if shortest:
        start, end = nodes_seen[0], nodes_seen[-1]
        path = paths[0]
        part = f"{start}.part, " if g.partitioned else ""
        # `any` returns one of the tied paths. Kusto's choice is arbitrary; the
        # earliest edges is ours, which matches the documentation's examples.
        rank, ties = ("rank()", "") if match.shortest == "all" else (
            "row_number()", f", {path}.eids"
        )
        body += (
            f" QUALIFY {rank} OVER (PARTITION BY {part}{start}.nid, {end}.nid "
            f"ORDER BY {path}.plen{ties}) = 1"
        )
    return body, ctes


def _path(
    g: _Graph, edge: ir.PatternEdge, high: int, match: ir.GraphMatch, simple: bool, cte: str
) -> str:
    """A variable-length edge: every walk of up to *high* edges, with its path.

    Columns: ``st``/``en`` (the ends, in pattern order), ``eids``, ``edges``
    (edge props), ``inn`` (inner nodes as structs), ``plen``, and for
    `graph-shortest-paths` ``vis``, the nodes visited. Measured, shortest paths
    are **simple** whatever `cycles=` says, so they never revisit a node, the
    start included; `graph-match` paths under `unique_edges` may, and only
    their edges must differ.
    """
    relation = g.name("eu" if edge.direction == "any" else "e")
    near, far = ("dst", "src") if edge.direction == "in" else ("src", "dst")
    part_e, part_n = (", e.part", ", n.part") if g.partitioned else ("", "")
    degrees = ", indeg := n.indeg, outdeg := n.outdeg" if g.degrees else ""
    element = f"struct_pack(nid := n.nid, props := n.props{degrees})"
    # Typed empty lists, for the rows with no edge to take a type from. The
    # NULL row makes the subquery answer even for a graph with no edges.
    empty_edges = (
        f"(SELECT list_filter([props], {_ELEM} -> false) FROM "
        f"(SELECT props FROM {g.name('e')} UNION ALL SELECT NULL) LIMIT 1)"
    )
    empty_inner = (
        f"(SELECT list_filter([{element}], {_ELEM} -> false) FROM "
        f"(SELECT * FROM {g.name('n')} UNION ALL BY NAME SELECT NULL AS nid) n LIMIT 1)"
    )
    vis_zero = ", [n.nid] AS vis" if simple else ""
    vis_one = f", [e.{near}, e.{far}] AS vis" if simple else ""
    vis_step = f", list_append(p.vis, e.{far})" if simple else ""
    rows = []
    if _bound(edge.low, "lower") == 0:
        rows.append(
            f"SELECT n.nid AS st, n.nid AS en{part_n}, []::BIGINT[] AS eids, "
            f"{empty_edges} AS edges, {empty_inner} AS inn, 0 AS plen{vis_zero} "
            f"FROM {g.name('n')} n"
        )
    one_edge_simple = f" AND {_differ('e.' + near, 'e.' + far)}" if simple else ""
    rows.append(
        f"SELECT e.{near} AS st, e.{far} AS en{part_e}, [e.eid] AS eids, [e.props] AS edges, "
        f"{empty_inner} AS inn, 1 AS plen{vis_one} "
        f"FROM {relation} e WHERE {high} >= 1{one_edge_simple}"
    )
    if match.cycles == "unique_edges" and not simple:
        fresh = " AND NOT list_contains(p.eids, e.eid)"
    elif simple:
        fresh = (
            f" AND len(list_filter(p.vis, {_ELEM} -> "
            f"({_ELEM} IS NOT DISTINCT FROM e.{far}))) = 0"
        )
    else:
        fresh = ""
    same_part = (
        f" AND {_same('e.part', 'p.part')} AND {_same('n.part', 'p.part')}"
        if g.partitioned else ""
    )
    step = (
        f"SELECT p.st, e.{far}{', p.part' if g.partitioned else ''}, list_append(p.eids, e.eid), "
        f"list_append(p.edges, e.props), list_append(p.inn, {element}), p.plen + 1{vis_step} "
        f"FROM {cte} p "
        f"JOIN {relation} e ON {_same('e.' + near, 'p.en')} "
        f"JOIN {g.name('n')} n ON {_same('n.nid', 'p.en')} "
        f"WHERE p.plen >= 1 AND p.plen < {high}{fresh}{same_part}"
    )
    return "\nUNION ALL ".join(rows) + "\nUNION ALL " + step


def _has_node(inner: str, nid: str) -> str:
    """Whether the inner-node list *inner* holds the node *nid* — null-safe."""
    return f"(len(list_filter({inner}, {_ELEM} -> ({_ELEM}.nid IS NOT DISTINCT FROM {nid}))) > 0)"


def _differ(left: str, right: str) -> str:
    return f"{left} IS DISTINCT FROM {right}"


def _refuse_shortest_shape(match: ir.GraphMatch) -> None:
    """Phase 1 supports the documented shape, `(a)-[p*lo..hi]->(b)`, only.

    The emulator accepts more than the documentation allows — a pattern with
    no variable-length edge, or two — and refusing those is never a wrong
    answer (graph-proposal §2.9).
    """
    if (
        len(match.patterns) != 1
        or len(match.patterns[0].edges) != 1
        or not match.patterns[0].edges[0].variable
    ):
        raise _refuse(
            "graph-shortest-paths",
            "only a single variable-length edge between two nodes, "
            "`(a)-[p*lo..hi]->(b)`, is supported",
        )
    if match.where is not None:
        _refuse_cross_element(match.where)


def _refuse_cross_element(expr: ir.Expr) -> None:
    """A comparison between two pattern elements — Kusto: CRT0001, "dynamic
    filters are not supported". Refusing every comparison that mentions two
    elements is a superset of that, and a superset is safe."""

    def mentioned(node: object) -> set[str]:
        found: set[str] = set()
        if isinstance(node, (ir.GraphVar,)):
            found.add(node.name)
        elif isinstance(node, ir.GraphProperty):
            found.add(node.var)
        elif isinstance(node, ir.GraphCall) and node.var is not None:
            found.add(node.var)
        if dataclasses.is_dataclass(node) and not isinstance(node, (type, ir.Query)):
            for f in dataclasses.fields(node):
                found |= mentioned(getattr(node, f.name))
        elif isinstance(node, tuple):
            for item in node:
                found |= mentioned(item)
        return found

    def visit(node: object) -> None:
        if isinstance(node, ir.BinaryOp) and node.op not in ("and", "or"):
            if len(mentioned(node)) > 1:
                raise _refuse(
                    "graph-shortest-paths where",
                    "a condition relating two pattern elements (Kusto: CRT0001, "
                    "dynamic filters are not supported)",
                )
        if dataclasses.is_dataclass(node) and not isinstance(node, (type, ir.Query)):
            for f in dataclasses.fields(node):
                visit(getattr(node, f.name))
        elif isinstance(node, tuple):
            for item in node:
                visit(item)

    visit(expr)


def _refuse_columns(match: ir.GraphMatch) -> None:
    """A bare name in `where`/`project` that is no pattern variable.

    Measured: the edge table's columns are not in scope (SEM0100). Left alone,
    the name would reach DuckDB and could bind to one of this SQL's own columns.
    """

    def visit(node: object) -> None:
        if isinstance(node, ir.ColumnRef):
            raise _refuse(
                f"graph-match:{node.name}",
                f"{node.name!r} is not a pattern variable; graph-match sees only "
                "its pattern's variables and the query's lets (Kusto: SEM0100)",
            )
        if dataclasses.is_dataclass(node) and not isinstance(node, (type, ir.Query)):
            for f in dataclasses.fields(node):
                visit(getattr(node, f.name))
        elif isinstance(node, tuple):
            for item in node:
                visit(item)

    visit(match.where)
    visit(match.project)


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------


def _render(expr: ir.Expr) -> str:
    from . import render_expr

    return render_expr(expr)


def render_graph_expr(node: ir.Expr) -> str:
    """`render_expr` for the graph expression nodes."""
    if isinstance(node, _Sql):
        return node.sql
    ctx = _CTX
    if ctx is None:
        raise _refuse("graph", "a graph expression outside a graph operator")
    if isinstance(node, ir.GraphVar):
        return _whole(ctx, node.name)
    if isinstance(node, ir.GraphProperty):
        return _property(ctx, node)
    if isinstance(node, ir.GraphElement):
        return _element(ctx, node)
    if isinstance(node, ir.GraphCall):
        return _call(ctx, node)
    raise _refuse(f"expression:{type(node).__name__}", "not a graph expression")


def _bag(struct: str, fields: list[str]) -> str:
    """A node's or edge's dynamic bag: its properties, **minus null and
    empty-string ones** — measured, `{"id":"A"}` when `age` is null, while
    zero and `false` stay. `json_object()` is the obvious choice and keeps them.
    """
    if not fields:
        return "'{}'::JSON"
    entries = ", ".join(
        f"{{'k': {_string(f)}, 'v': to_json({struct}.{_quote(f)})}}" for f in fields
    )
    keep = f"{_KEY} -> ({_KEY}.v IS NOT NULL AND {_KEY}.v <> '\"\"')"
    return f"to_json(map_from_entries(list_filter([{entries}], {keep})))"


def _whole(ctx: _Match, name: str) -> str:
    kind, alias = ctx.kinds[name], ctx.aliases[name]
    if kind == "node":
        return _bag(f"{alias}.props", ctx.graph.node_props)
    if kind == "edge":
        return _bag(f"{alias}.props", ctx.graph.edge_cols)
    return f"to_json(list_transform({alias}.edges, {_ELEM} -> {_bag(_ELEM, ctx.graph.edge_cols)}))"


def _known(ctx: _Match, kind: str, var: str, prop: str) -> None:
    fields = ctx.graph.node_props if kind == "node" else ctx.graph.edge_cols
    if prop not in fields:
        what = "node" if kind == "node" else "edge"
        raise _refuse(
            f"graph:{var}.{prop}",
            f"{what} {var!r} doesn't have property {prop!r} (Kusto: SEM1040)",
        )


def _property(ctx: _Match, node: ir.GraphProperty) -> str:
    kind, alias = ctx.kinds[node.var], ctx.aliases[node.var]
    if kind == "path":
        if ctx.clause == "where":
            raise _refuse(
                f"graph:{node.var}.{node.prop}",
                "a variable-length edge's property in `where` must go through "
                "map(), all() or any() (Kusto: SEM1054)",
            )
        _known(ctx, "edge", node.var, node.prop)
        return f"to_json(list_transform({alias}.edges, {_ELEM} -> {_ELEM}.{_quote(node.prop)}))"
    _known(ctx, kind, node.var, node.prop)
    return f"{alias}.props.{_quote(node.prop)}"


def _element(ctx: _Match, node: ir.GraphElement) -> str:
    if ctx.element is None:
        raise _refuse(f"graph:{node.name}", "an element name outside map(), all() or any()")
    kind, elem = ctx.element
    fields = ctx.graph.edge_cols if kind == "edge" else ctx.graph.node_props
    if node.name in fields:
        return f"{elem}.{_quote(node.name)}" if kind == "edge" else (
            f"{elem}.props.{_quote(node.name)}"
        )
    if node.fallback is not None:
        # Measured: a name that is no property reaches the `let`.
        saved, ctx.element = ctx.element, None
        try:
            return _render(node.fallback)
        finally:
            ctx.element = saved
    what = "edge" if kind == "edge" else "inner node"
    raise _refuse(
        f"graph:{node.name}",
        f"the {what}s of the path don't have property {node.name!r} (Kusto: SEM1040)",
    )


def _call(ctx: _Match, node: ir.GraphCall) -> str:
    if node.body is not None:
        return _path_call(ctx, node)
    if node.var is None:
        if ctx.element is None:
            raise _refuse(f"function:{node.name}", "needs a pattern variable")
        kind, elem = ctx.element
        if node.name == "labels":
            return "'[]'::JSON"
        if kind == "edge":
            raise _refuse(
                f"function:{node.name}",
                f"{node.name}() of an edge: use inner_nodes() to reach the nodes",
            )
        if node.name == "node_id":
            return _node_id(f"{elem}.nid")
        return f"{elem}.{'indeg' if node.name == 'node_degree_in' else 'outdeg'}"
    kind, alias = ctx.kinds[node.var], ctx.aliases[node.var]
    if node.name == "labels":
        # A make-graph graph has no labels: measured, `[]` for nodes and edges.
        return "'[]'::JSON"
    if node.name == "node_id":
        return _node_id(f"{alias}.nid")
    return f"{alias}.{'indeg' if node.name == 'node_degree_in' else 'outdeg'}"


def _node_id(sql: str) -> str:
    """`node_id()` is the id's **string** form — measured, `'1'` for a long."""
    from . import render_kql_tostring

    return render_kql_tostring(_Sql(sql))


@dataclasses.dataclass(frozen=True)
class _Sql(ir.Expr):
    """Already-rendered SQL, for helpers that take an expression."""

    sql: str


def _path_call(ctx: _Match, node: ir.GraphCall) -> str:
    """``map``/``all``/``any`` over a path's edges or inner nodes.

    Measured: a zero-length path maps to ``[]``, ``all`` is true and ``any``
    false; and **a null condition counts as unsatisfied in both** —
    ``all(p, w > 0)`` and ``any(p, w > 0)`` are both false when `w` is null.
    Hence the inner `coalesce`; the outer one is the zero-length rule, because
    ``list_bool_and([])`` is null rather than true.
    """
    assert node.var is not None and node.body is not None
    if ctx.element is not None:
        raise _refuse(f"function:{node.name}", "a graph function inside another")
    alias = ctx.aliases[node.var]
    items = f"{alias}.inn" if node.inner else f"{alias}.edges"
    saved = ctx.element
    ctx.element = ("node" if node.inner else "edge", _ELEM)
    try:
        body = _render(node.body)
    finally:
        ctx.element = saved
    if node.name == "map":
        return f"to_json(list_transform({items}, {_ELEM} -> ({body})))"
    test = f"list_transform({items}, {_ELEM} -> coalesce(({body}), false))"
    if node.name == "all":
        return f"coalesce(list_bool_and({test}), true)"
    return f"coalesce(list_bool_or({test}), false)"
