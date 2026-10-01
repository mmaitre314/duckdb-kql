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
    #: The edge columns the consumer reads, packed into each edge's `props`;
    #: `edge_cols` stays the full list, which is what names are checked against.
    edge_fields: list[str] | None = None
    #: Whether the nodes are numbered (`ix`): only an operator that can observe
    #: the order needs it, and the numbering is a sort of every node.
    numbered: bool = True

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
    consumer = source.consumer
    edge_reads, node_reads = _reads(consumer)
    base_props = [
        p for p in props
        if p not in {m.name for m in graph.components} and (node_reads is None or p in node_reads)
    ]
    g = _Graph(_prefix(), edge_cols, props, graph.partition)
    g.edge_fields = [c for c in edge_cols if edge_reads is None or c in edge_reads]
    g.numbered = bool(graph.components) or (
        isinstance(consumer, ir.GraphToTable) and consumer.kind == "nodes"
    )
    if isinstance(consumer, ir.GraphMatch):
        g.degrees = _uses_degrees(consumer)

    # The edges are read once, by the numbered relation below, which is the
    # copy that holds still; the guard's probes evaluate no row of them.
    ctes = [f"{g.name('ed')} AS NOT MATERIALIZED ({to_sql(graph.edges, schema)})"]
    for i, (table, cols) in enumerate(tables):
        kept = _node_columns(graph, table, cols, tables, base_props)
        ctes.append(
            f"{g.name(f't{i}')} AS MATERIALIZED (SELECT {', '.join(map(_quote, kept))} "
            f"FROM ({to_sql(table.query, schema)}) x)"
        )
    ctes.append(f"{g.name('chk')} AS MATERIALIZED ({_guard(g, graph, tables)})")
    ctes.append(f"{g.name('e')} AS MATERIALIZED ({_edges(g, graph)})")
    ctes.append(f"{g.name('nr')} AS ({_raw_nodes(g, graph, tables, base_props)})")
    if g.numbered:
        ctes.append(f"{g.name('n0')} AS MATERIALIZED ({_nodes(g)})")
        nodes = "n0"
        for i, marked in enumerate(graph.components):
            ctes.extend(_components(g, nodes, i, marked, has_props=bool(base_props) or i > 0))
            nodes = f"n{i + 1}"
        ctes.append(f"{g.name('n')} AS MATERIALIZED (SELECT * FROM {g.name(nodes)})")
    else:
        ctes.append(f"{g.name('n')} AS MATERIALIZED ({_nodes(g)})")

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


def _reads(
    consumer: ir.GraphMatch | ir.GraphToTable,
) -> tuple[set[str] | None, set[str] | None]:
    """The edge and node properties *consumer* can observe — None for all.

    Reported: a five-hop match on a graph of 8M edges carried every column of
    every edge, a 256-byte payload among them, through every relation it built,
    to read one: `EdgeId`. What a relation leaves out here, nothing downstream
    can ask for — every way of reading a property is one of these:

    * a whole node, edge or path (`project a`, `p`) reads all of its kind;
    * `a.x` reads `x` — of an edge for an edge or path variable;
    * a name inside `map`/`all`/`any` reads it from each edge, or from each
      inner node under `inner_nodes()`;
    * `graph-to-table` reads every property of what it returns.

    Partition keys and ids travel in their own columns, never in `props`.
    """
    if isinstance(consumer, ir.GraphToTable):
        return (None, set()) if consumer.kind == "edges" else (set(), None)
    kinds: dict[str, str] = {}
    for pattern in consumer.patterns:
        for node in pattern.nodes:
            kinds[node.name] = "node"
        for edge in pattern.edges:
            kinds[edge.name] = "edge"
    edges: set[str] | None = set()
    nodes: set[str] | None = set()

    def read(kind: str, name: str | None) -> None:
        nonlocal edges, nodes
        if kind == "node":
            nodes = None if name is None or nodes is None else nodes | {name}
        else:
            edges = None if name is None or edges is None else edges | {name}

    def visit(node: object, element: str | None) -> None:
        if isinstance(node, ir.GraphVar) and node.name in kinds:
            read(kinds[node.name], None)
        elif isinstance(node, ir.GraphProperty) and node.var in kinds:
            read(kinds[node.var], node.prop)
        elif isinstance(node, ir.GraphElement) and element is not None:
            read(element, node.name)
        if isinstance(node, ir.GraphCall) and node.var is not None and node.body is not None:
            visit(node.body, "node" if node.inner else "edge")
            return
        if dataclasses.is_dataclass(node) and not isinstance(node, (type, ir.Query)):
            for f in dataclasses.fields(node):
                visit(getattr(node, f.name), element)
        elif isinstance(node, tuple):
            for item in node:
                visit(item, element)

    visit(consumer.where, None)
    visit(consumer.project, None)
    return edges, nodes


def _node_columns(
    graph: ir.MakeGraph,
    table: ir.NodeTable,
    cols: list[str],
    tables: list[tuple[ir.NodeTable, list[str]]],
    props: list[str],
) -> list[str]:
    """The columns of a node table the relations read: its key, the partition,
    the properties that are carried, and — with two tables — the columns they
    share, whose types the guard compares."""
    shared = set(tables[0][1]) & set(tables[1][1]) if len(tables) == 2 else set()
    wanted = {table.key, graph.partition, *props, *shared}
    return [c for c in cols if c in wanted]


def _typeof(cte: str, column: str) -> str:
    """The column's type, known even when the table is empty — and read
    without evaluating a row: `typeof` is not folded, so `LIMIT 1` would run
    the query under it, and a filter that is false prunes it instead."""
    return f"typeof((SELECT x.{_quote(column)} FROM {cte} x WHERE false))"


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
    names = g.edge_cols if g.edge_fields is None else g.edge_fields
    fields = ", ".join(f"{_quote(c)} := x.{_quote(c)}" for c in names)
    props = f"struct_pack({fields})" if fields else "NULL"
    return (
        f"SELECT row_number() OVER () AS eid, x.{_quote(graph.source)} AS src, "
        f"x.{_quote(graph.target)} AS dst{_part('x', g)}, {props} AS props "
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
        order = f", {i} AS grp, row_number() OVER () AS ord" if g.numbered else ""
        branches.append(
            f"SELECT x.{_quote(table.key)} AS nid{_part('x', g)}, "
            f"{f'struct_pack({fields})' if fields else 'NULL'} AS props{order} "
            f"FROM {g.name(f't{i}')} x WHERE true{earlier} "
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
    part = ", part" if g.partitioned else ""
    # First appearance: an end's position among the edges, sources first.
    first, src_ord, dst_ord = (
        (f", {len(tables)} AS grp, min(x.ord) AS ord", ", eid * 2 AS ord", ", eid * 2 + 1")
        if g.numbered else ("", "", "")
    )
    branches.append(
        f"SELECT x.nid{', x.part' if g.partitioned else ''}, {packed} AS props{first} FROM ("
        f"SELECT src AS nid{part}{src_ord} FROM {g.name('e')} "
        f"UNION ALL SELECT dst{part}{dst_ord} FROM {g.name('e')}"
        f") x WHERE true{seen} GROUP BY x.nid{group}"
    )
    return "\nUNION ALL ".join(branches)


def _nodes(g: _Graph) -> str:
    """Numbered by first appearance: node-table order, then edge order.

    Measured: that is the order `graph-mark-components` numbers components in,
    and `graph-to-table nodes` returns them in. Nothing else observes it, so
    for anything else the nodes are not numbered at all.
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
    ix = ", row_number() OVER (ORDER BY grp, ord) AS ix" if g.numbered else ""
    return f"SELECT nid{part}, props{ix}{degrees} FROM {g.name('nr')} r"


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


@dataclasses.dataclass
class _PathSpec:
    """A variable-length edge of the pattern, before its CTE is rendered."""

    index: int
    edge: ir.PatternEdge
    left: str  # pattern variable at the path's start (pattern order)
    right: str  # … and at its end
    alias: str
    high: int
    simple: bool

    def cte(self, g: _Graph, suffix: str = "") -> str:
        return g.name(f"p{self.index}{suffix}")


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
    specs: list[_PathSpec] = []
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
            left_var, right_var = pattern.nodes[index].name, pattern.nodes[index + 1].name
            left, right = aliases[left_var], aliases[right_var]
            alias = f"w{len(aliases)}"
            aliases[edge.name] = alias
            if edge.variable:
                kinds[edge.name] = "path"
                low = _bound(edge.low, "lower")
                high = _bound(edge.high, "upper")
                if low < 0 or low > high:
                    raise _refuse("graph-match", "invalid variable length range (Kusto: SEM1013)")
                simple = shortest or match.cycles == "none"
                spec = _PathSpec(len(specs), edge, left_var, right_var, alias, high, simple)
                specs.append(spec)
                tables.append(f"{spec.cte(g)} {alias}")
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
    conjuncts = _conjuncts(match.where)
    with _context(state):
        state.clause = "where"
        for spec in specs:
            ctes.extend(_path_ctes(state, spec, match, conjuncts))
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


# ---------------------------------------------------------------------------
# Variable-length edges: what the recursion is seeded from and carries
# ---------------------------------------------------------------------------


def _conjuncts(expr: ir.Expr | None) -> list[ir.Expr]:
    """The `where` clause split at its top-level `and`s.

    The outer `WHERE` keeps a match only when **every** conjunct is true —
    under KQL's null logic as under SQL's — so a conjunct may also be checked
    earlier, on any part of the match it alone decides, without changing the
    answer.
    """
    if expr is None:
        return []
    if isinstance(expr, ir.BinaryOp) and expr.op == "and":
        return _conjuncts(expr.left) + _conjuncts(expr.right)
    return [expr]


def _mentioned(node: object) -> set[str]:
    """The pattern variables *node* reads."""
    found: set[str] = set()
    if isinstance(node, ir.GraphVar):
        found.add(node.name)
    elif isinstance(node, ir.GraphProperty):
        found.add(node.var)
    elif isinstance(node, ir.GraphCall) and node.var is not None:
        found.add(node.var)
    if dataclasses.is_dataclass(node) and not isinstance(node, (type, ir.Query)):
        for f in dataclasses.fields(node):
            found |= _mentioned(getattr(node, f.name))
    elif isinstance(node, tuple):
        for item in node:
            found |= _mentioned(item)
    return found


def _uses(match: ir.GraphMatch, path: str) -> tuple[set[str] | None, set[str] | None]:
    """The edge and inner-node fields the query reads from *path*.

    Each is None when nothing reads that list at all — it is then not carried
    through the recursion — or the full set when the whole of it is read
    (`project p`, `array_length(p)`). Inner-node fields are `nid`, `indeg`,
    `outdeg`, and property names under `props`.
    """
    edge: set[str] | None = None
    inner: set[str] | None = None

    def add(target: set[str] | None, names: set[str]) -> set[str]:
        return (target or set()) | names

    def body_fields(body: ir.Expr) -> set[str]:
        names: set[str] = set()

        def walk(node: object) -> None:
            if isinstance(node, ir.GraphElement):
                names.add(f"props.{node.name}")
            elif isinstance(node, ir.GraphCall) and node.var is None:
                names.add({"node_id": "nid", "node_degree_in": "indeg",
                           "node_degree_out": "outdeg"}.get(node.name, ""))
            if dataclasses.is_dataclass(node) and not isinstance(node, (type, ir.Query)):
                for f in dataclasses.fields(node):
                    walk(getattr(node, f.name))
            elif isinstance(node, tuple):
                for item in node:
                    walk(item)

        walk(body)
        names.discard("")
        return names

    everything = {"*"}

    def visit(node: object) -> None:
        nonlocal edge, inner
        if isinstance(node, ir.GraphVar) and node.name == path:
            edge = everything
        elif isinstance(node, ir.GraphProperty) and node.var == path:
            edge = add(edge, {f"props.{node.prop}"})
        elif isinstance(node, ir.GraphCall) and node.var == path and node.body is not None:
            fields = body_fields(node.body)
            if node.inner:
                inner = add(inner, fields)
            else:
                # An edge has no `nid`/degrees; those are refused when rendered.
                edge = add(edge, {f for f in fields if f.startswith("props.")})
        if dataclasses.is_dataclass(node) and not isinstance(node, (type, ir.Query)):
            for f in dataclasses.fields(node):
                visit(getattr(node, f.name))
        elif isinstance(node, tuple):
            for item in node:
                visit(item)

    visit(match.where)
    visit(match.project)
    return edge, inner


def _edge_item(alias: str, fields: set[str], g: _Graph) -> str:
    """What one edge contributes to a path's `edges` list."""
    if "*" in fields:
        return f"{alias}.props"
    names = [c for c in g.edge_cols if f"props.{c}" in fields]
    if not names:
        # Read only through something with no fields of its own (`map(p, 1)`):
        # the length is what matters, and the id is the cheapest typed item.
        return f"{alias}.eid"
    packed = ", ".join(f"{_quote(c)} := {alias}.props.{_quote(c)}" for c in names)
    return f"struct_pack({packed})"


def _reads_node(fields: set[str], g: _Graph) -> bool:
    """Whether an inner node's *fields* need more than its id — the one thing
    every step already has, as the end it grows from."""
    return any(f.startswith("props.") and f[6:] in g.node_props for f in fields) or (
        g.degrees and bool({"indeg", "outdeg"} & fields)
    )


def _node_item(alias: str, fields: set[str], g: _Graph, nid: str | None = None) -> str:
    """What one inner node contributes to a path's `inn` list — the fields of
    the node relation that are read, under the same names, so the expression
    renderer reads it as it would read the relation."""
    parts = ["nid := " + (nid or f"{alias}.nid")]
    names = [c for c in g.node_props if f"props.{c}" in fields]
    if names:
        packed = ", ".join(f"{_quote(c)} := {alias}.props.{_quote(c)}" for c in names)
        parts.append(f"props := struct_pack({packed})")
    if g.degrees and "indeg" in fields:
        parts.append(f"indeg := {alias}.indeg")
    if g.degrees and "outdeg" in fields:
        parts.append(f"outdeg := {alias}.outdeg")
    return f"struct_pack({', '.join(parts)})"


def _path_ctes(
    state: _Match, spec: _PathSpec, match: ir.GraphMatch, conjuncts: list[ir.Expr]
) -> list[str]:
    """A variable-length edge's CTEs: its seed set, if any, and the walk itself.

    **Seeding.** The obvious recursion starts from every edge in the graph and
    lets the outer `WHERE` discard what does not match — so a one-path answer
    could cost every walk of the graph, and a disconnected component of a few
    hundred edges was enough to exhaust a 1 GB budget. A conjunct that reads
    only the path's start node restricts where the walk may begin, which is
    exact because a path's start never changes as it grows. When only the
    *end* node is constrained, the path is built backwards from there instead.

    **Pruning.** A conjunct `all(p, c)` / `all(inner_nodes(p), c)` holds for a
    path only if it holds for every prefix, so a prefix that fails it is not
    extended. The outer `WHERE` still checks all of them: this changes how
    much is enumerated, never what is returned.
    """
    g = state.graph
    conjuncts = [c for c in conjuncts if not _volatile(c)]
    start_only = [c for c in conjuncts if _mentioned(c) == {spec.left}]
    end_only = [c for c in conjuncts if _mentioned(c) == {spec.right}]
    every_edge: list[ir.GraphCall] = []
    every_inner: list[ir.GraphCall] = []
    for c in conjuncts:
        if (
            isinstance(c, ir.GraphCall) and c.name == "all" and c.var == spec.edge.name
            and c.body is not None and not _mentioned(c.body)
        ):
            (every_inner if c.inner else every_edge).append(c)

    ctes: list[str] = []
    backward = not start_only and bool(end_only)
    anchor_tests = end_only if backward else start_only
    seeds = None
    if anchor_tests:
        # DISTINCT: a seed may match a path's end once, whatever the node
        # relation holds, so the join never multiplies a path.
        seeds = spec.cte(g, "s")
        alias = state.aliases[spec.right if backward else spec.left]
        tests = " AND ".join(f"({_render(c)})" for c in anchor_tests)
        part = f", {alias}.part" if g.partitioned else ""
        ctes.append(
            f"{seeds} AS (SELECT DISTINCT {alias}.nid{part} "
            f"FROM {g.name('n')} {alias} WHERE {tests})"
        )

    def element_test(tests: list[ir.GraphCall], kind: str, elem: str) -> str:
        if not tests:
            return ""
        saved, state.element = state.element, (kind, elem)
        try:
            parts = [_holds(_render(t.body)) for t in tests if t.body is not None]
        finally:
            state.element = saved
        return " AND " + " AND ".join(parts)

    edge_ok = element_test(every_edge, "edge", "e.props")
    inner_ok = element_test(every_inner, "node", "n")
    edge_fields, inner_fields = _uses(match, spec.edge.name)
    if match.cycles == "none":
        # The outer query checks that no inner node is a pattern node.
        inner_fields = (inner_fields or set()) | {"nid"}
    body = _path(g, spec, match, seeds, backward, edge_fields, inner_fields, edge_ok, inner_ok)
    ctes.append(f"{spec.cte(g)} AS ({body})")
    return ctes


def _volatile(expr: ir.Expr) -> bool:
    """Whether *expr* may answer differently when evaluated twice — then a
    second, earlier evaluation is not the same test."""
    from .sharing import VOLATILE

    def visit(node: object) -> bool:
        if isinstance(node, ir.FunctionCall) and node.name in VOLATILE:
            return True
        if dataclasses.is_dataclass(node) and not isinstance(node, (type, ir.Query)):
            return any(visit(getattr(node, f.name)) for f in dataclasses.fields(node))
        if isinstance(node, tuple):
            return any(visit(item) for item in node)
        return False

    return visit(expr)


def _path(
    g: _Graph,
    spec: _PathSpec,
    match: ir.GraphMatch,
    seeds: str | None,
    backward: bool,
    edge_fields: set[str] | None,
    inner_fields: set[str] | None,
    edge_ok: str,
    inner_ok: str,
) -> str:
    """A variable-length edge: every walk of up to `high` edges, with its path.

    Columns: ``st``/``en`` (the ends, in pattern order), ``eids``, ``plen``,
    and only when the query reads them ``edges`` (edge properties, only those
    read) and ``inn`` (inner nodes, likewise); for a simple path ``vis``, the
    nodes visited. Measured, shortest paths are **simple** whatever `cycles=`
    says, so they never revisit a node, the start included; `graph-match`
    paths under `unique_edges` may, and only their edges must differ.

    *backward* builds each path from its end: the same rows, the lists
    prepended to rather than appended, so the edges stay in pattern order.
    """
    edge, high, simple, cte = spec.edge, spec.high, spec.simple, spec.cte(g)
    relation = g.name("eu" if edge.direction == "any" else "e")
    near, far = ("dst", "src") if edge.direction == "in" else ("src", "dst")
    carry_edges, carry_inner = edge_fields is not None, inner_fields is not None
    edge_item = _edge_item("e", edge_fields or set(), g)
    node_item = _node_item("n", inner_fields or set(), g)

    # Typed empty lists, for the rows with no edge to take a type from. The
    # NULL row makes the subquery answer even for a graph with no edges.
    empty_edges = (
        f"(SELECT list_filter([{edge_item}], {_ELEM} -> false) FROM "
        f"(SELECT * FROM {g.name('e')} UNION ALL BY NAME SELECT NULL AS eid) e LIMIT 1)"
    )
    empty_inner = (
        f"(SELECT list_filter([{node_item}], {_ELEM} -> false) FROM "
        f"(SELECT * FROM {g.name('n')} UNION ALL BY NAME SELECT NULL AS nid) n LIMIT 1)"
    )

    lists = ""
    if carry_edges:
        lists += f", {empty_edges} AS edges"
    if carry_inner:
        lists += f", {empty_inner} AS inn"

    # Every walk starts as a zero-length stub at a node — a seed when the
    # `where` names some, every node otherwise — and each step adds one edge,
    # the first included. Reported: anchored on its first edge instead, the
    # recursion's working table is estimated as a cross product of the edges
    # (a CTE has no statistics), DuckDB builds each step's hash table on the
    # edges rather than on the walks, and a five-hop walk from one node read
    # the 8M-edge relation into a hash table five times. Anchored on nodes,
    # the estimate is the stubs', and the edges are probed instead. The rows
    # are the same: each edge's start is exactly one node, so each one-edge
    # path is one stub plus one step; the stubs are dropped by `plen >= lo`
    # unless the range starts at zero, where they are the zero-length paths.
    starts = seeds or g.name("n")
    vis = ", [s.nid] AS vis" if simple else ""
    part_s = ", s.part" if g.partitioned else ""
    rows = [
        f"SELECT s.nid AS st, s.nid AS en{part_s}, []::BIGINT[] AS eids{lists}, "
        f"0 AS plen{vis} FROM {starts} s"
    ]

    # The walk grows at `en` (forward) or at `st` (backward); the node passed
    # through becomes an inner node, and `step` is the edge's other end.
    grow, joint = ("st", far) if backward else ("en", near)
    step = near if backward else far

    def extend(lst: str, item: str) -> str:
        return f"list_prepend({item}, p.{lst})" if backward else f"list_append(p.{lst}, {item})"

    if match.cycles == "unique_edges" and not simple:
        fresh = " AND NOT list_contains(p.eids, e.eid)"
    elif simple:
        fresh = (
            f" AND len(list_filter(p.vis, {_ELEM} -> "
            f"({_ELEM} IS NOT DISTINCT FROM e.{step}))) = 0"
        )
    else:
        fresh = ""
    # The node relation holds each (partition, id) once and every edge end is
    # in it, so joining it at the end a step grows from neither drops nor
    # repeats a path: it is there only to read the node, when that is needed.
    # A pushed-down `all(inner_nodes(p), …)` is rendered against `n` itself.
    joined = bool(inner_ok) or (carry_inner and _reads_node(inner_fields or set(), g))
    if carry_inner and not joined:
        node_item = _node_item("n", inner_fields or set(), g, nid=f"p.{grow}")
    node_join = f"JOIN {g.name('n')} n ON {_same('n.nid', 'p.' + grow)} " if joined else ""
    same_part = (
        f" AND {_same('e.part', 'p.part')}"
        + (f" AND {_same('n.part', 'p.part')}" if joined else "")
        if g.partitioned else ""
    )
    ends = f"e.{step}, p.en" if backward else f"p.st, e.{step}"
    carried = ""
    if carry_edges:
        carried += f", {extend('edges', edge_item)}"
    if carry_inner:
        # The node a stub's first step leaves is the path's end, not inner.
        carried += f", CASE WHEN p.plen = 0 THEN p.inn ELSE {extend('inn', node_item)} END"
    if inner_ok:
        inner_ok = f" AND (p.plen = 0 OR ({inner_ok.removeprefix(' AND ')}))"
    vis = f", {extend('vis', 'e.' + step)}" if simple else ""
    step_sql = (
        f"SELECT {ends}{', p.part' if g.partitioned else ''}, {extend('eids', 'e.eid')}"
        f"{carried}, p.plen + 1{vis} "
        f"FROM {cte} p "
        f"JOIN {relation} e ON {_same('e.' + joint, 'p.' + grow)} "
        f"{node_join}"
        f"WHERE p.plen < {high}{fresh}{same_part}{edge_ok}{inner_ok}"
    )
    return "\nUNION ALL ".join(rows) + "\nUNION ALL " + step_sql


def _has_node(inner: str, nid: str) -> str:
    """Whether the inner-node list *inner* holds the node *nid* — null-safe."""
    return f"(len(list_filter({inner}, {_ELEM} -> ({_ELEM}.nid IS NOT DISTINCT FROM {nid}))) > 0)"


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
    test = f"list_transform({items}, {_ELEM} -> {_holds(body)})"
    if node.name == "all":
        return f"coalesce(list_bool_and({test}), true)"
    return f"coalesce(list_bool_or({test}), false)"


def _holds(condition: str) -> str:
    """A graph function's condition as `all` and `any` count it."""
    return f"coalesce(({condition}), false)"
