"""Lowering for graph semantics: `make-graph` and the operators after it.

A graph exists only at translation time (docs/graph-proposal.md §3.1), so a
pipeline ``Edges | make-graph … | graph-mark-components … | graph-match …``
lowers to **one** source, :class:`ir.GraphSource`, and whatever tabular
operators follow the graph operator stay an ordinary pipeline after it.

Expressions in `where` and `project` are lowered by the ordinary expression
lowerer and then rewritten: a name that is a pattern variable becomes
:class:`ir.GraphVar`, ``v.prop`` becomes :class:`ir.GraphProperty`, and the
graph functions become :class:`ir.GraphCall`. A variable is rewritten *before*
the `let` substitution pass runs, which is what makes it win over a `let` of
the same name — measured: ``let a = 5; … graph-match (a)-->(b) project a.id``
reads the node.

Refusals here are the ones that need nothing but the query text. Anything that
needs the inputs' columns is refused while rendering (translate/graph.py).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from . import ir
from . import lower as L
from .errors import KqlUnsupportedError

#: The graph operators that consume a graph and produce a table.
_CONSUMERS = ("GraphMatchOperator", "GraphShortestPathsOperator", "GraphToTableOperator")

#: Graph functions taking one node or edge variable (or none inside a lambda).
_ELEMENT_FUNCTIONS = ("node_degree_in", "node_degree_out", "labels", "node_id")

#: Graph functions taking a variable-length edge and a per-element body.
_PATH_FUNCTIONS = ("map", "all", "any")


def operator_node(node: Any) -> Any:
    """The operator under a `| op` wrapper, as `lower._lower_operator` sees it."""
    while L._cls(node) in ("PipedOperator", "AfterPipeOperator"):
        rules = L._rule_children(node)
        if not rules:
            break
        node = rules[0]
    return L._collapse(node)


def make_graph_index(rest: list[Any]) -> int | None:
    """The position of the first `make-graph` among piped operators, if any."""
    for i, node in enumerate(rest):
        if L._cls(operator_node(node)) == "MakeGraphOperator":
            return i
    return None


def is_graph_value(node: Any) -> bool:
    """Whether a `let` value is a graph: a pipeline ending in `make-graph` or
    `graph-mark-components` (``let G = E | make-graph s --> t;``)."""
    node = L._unparenthesize(node)
    if L._cls(node) != "PipeExpression":
        return False
    parts = L._rule_children(node)
    if len(parts) < 2:
        return False
    last = L._cls(operator_node(parts[-1]))
    return last in ("MakeGraphOperator", "GraphMarkComponentsOperator") and (
        make_graph_index(parts[1:]) is not None
    )


def lower_graph_head(
    head: Any, before: list[Any], make_graph: Any, after: list[Any]
) -> ir.Query:
    """``head | before… | make-graph … | after…`` as a graph source and the
    tabular operators that follow its graph operator."""
    edges = L._lower_head(head, before)
    node = operator_node(make_graph)
    graph = _lower_make_graph(node, edges)

    partition = getattr(node, "PartitionedByClause", None)
    if partition is not None:
        if after:
            raise L._unsupported(
                operator_node(after[0]),
                "partitioned-by: the graph operator goes inside its parentheses",
            )
        # `( graph-match … | … )`: a pipe sub-expression whose first element
        # is the graph operator itself.
        inner = L._rule_children(partition.SubQuery)
        after = L._rule_children(inner[0]) if inner else []
        graph = dataclasses.replace(graph, partition=_simple_name(partition.Column))

    components: list[ir.MarkComponents] = []
    for index, raw in enumerate(after):
        op = operator_node(raw)
        kind = L._cls(op)
        if kind == "GraphMarkComponentsOperator":
            components.append(_lower_mark_components(op))
            continue
        if kind not in _CONSUMERS:
            raise L._unsupported(
                op,
                f"{kind}: a graph must be followed by a graph operator — "
                "graph-match, graph-shortest-paths, graph-to-table or "
                "graph-mark-components (Kusto: SEM0104)",
            )
        rest = after[index + 1:]
        if graph.partition is not None:
            if components:
                raise L._unsupported(op, "partitioned-by with graph-mark-components")
            if kind == "GraphToTableOperator":
                raise L._unsupported(op, "partitioned-by with graph-to-table")
            if rest:
                raise L._unsupported(
                    operator_node(rest[0]),
                    "an operator after the graph operator inside partitioned-by",
                )
        graph = dataclasses.replace(graph, components=tuple(components))
        consumer = (
            _lower_graph_to_table(op)
            if kind == "GraphToTableOperator"
            else _lower_graph_match(op, shortest=kind == "GraphShortestPathsOperator")
        )
        return ir.Query(ir.GraphSource(graph, consumer), L._lower_operators(rest))

    raise L._unsupported(
        node,
        "make-graph: a graph must be followed by graph-match, "
        "graph-shortest-paths or graph-to-table (Kusto: SEM0104)",
    )


# ---------------------------------------------------------------------------
# make-graph
# ---------------------------------------------------------------------------


def _simple_name(node: Any) -> str:
    names = L._find_names(node)
    if not names:
        raise L._unsupported(node, "name")
    return names[0]


def _lower_make_graph(node: Any, edges: ir.Query) -> ir.MakeGraph:
    parameters = getattr(node, "Parameters", None) or []
    if parameters:
        raise L._unsupported(parameters[0], "make-graph parameter")
    if node.Direction.text != "-->":
        # Measured: SEM0456 "KQL undirected graphs feature is not enabled". The
        # emulator cannot say what it means, so neither can this.
        raise L._unsupported(node, "make-graph with an undirected edge (--)")
    graph = ir.MakeGraph(
        edges, _simple_name(node.SourceColumn), _simple_name(node.TargetColumn)
    )
    if getattr(node, "IdClause", None) is not None:
        return dataclasses.replace(graph, node_id=_simple_name(node.IdClause.Name))
    clause = getattr(node, "TablesAndKeysClause", None)
    if clause is not None:
        tables = tuple(
            ir.NodeTable(L._lower_query_node(entry.Table), _simple_name(entry.Column))
            for entry in clause.Tables
        )
        return dataclasses.replace(graph, nodes=tables)
    return graph


def _parameters(node: Any, attribute: str = "Parameters") -> dict[str, str]:
    """``name=value`` operator parameters, names lower-cased, values as written."""
    out: dict[str, str] = {}
    for parameter in getattr(node, attribute, None) or []:
        name, _, value = parameter.getText().partition("=")
        out[name.strip().lower()] = value.strip()
    return out


def _lower_mark_components(node: Any) -> ir.MarkComponents:
    # The grammar spells this label `Parametems`.
    parameters = _parameters(node, "Parametems")
    kind = parameters.pop("kind", "weak").lower()
    name = parameters.pop("with_component_id", "ComponentId")
    if parameters or kind not in ("weak", "strong"):
        raise L._unsupported(node, "graph-mark-components parameter")
    return ir.MarkComponents(kind, name)


def _lower_graph_to_table(node: Any) -> ir.GraphToTable:
    outputs = list(node.Outputs)
    if len(outputs) != 1 or getattr(outputs[0], "AsClause", None) is not None:
        # `nodes as N, edges as E; N; E` is several result tables, which no
        # other operator here produces either (cf. `fork`).
        raise L._unsupported(node, "graph-to-table with several outputs or `as`")
    output = outputs[0]
    if _parameters(output):
        # with_node_id= / with_source_id= / with_target_id= are KQL's hash()
        # of the id — xxhash64, which DuckDB lacks (graph-proposal §3.7).
        raise L._unsupported(
            output,
            "graph-to-table id columns (with_node_id=, with_source_id=, "
            "with_target_id=): they are KQL's hash(), xxhash64, which DuckDB lacks",
        )
    return ir.GraphToTable(output.Keyword.text.lower())


# ---------------------------------------------------------------------------
# graph-match / graph-shortest-paths
# ---------------------------------------------------------------------------


def _lower_graph_match(node: Any, *, shortest: bool) -> ir.GraphMatch:
    keyword = "graph-shortest-paths" if shortest else "graph-match"
    parameters = _parameters(node)
    cycles = parameters.pop("cycles", "unique_edges").lower()
    output = parameters.pop("output", "any").lower() if shortest else None
    if parameters:
        raise L._unsupported(node, f"{keyword} parameter:{next(iter(parameters))}")
    if cycles not in ("unique_edges", "all", "none"):
        raise L._unsupported(node, f"{keyword} cycles={cycles}")
    if output not in (None, "any", "all"):
        raise L._unsupported(node, f"{keyword} output={output}")

    counter = iter(range(1_000_000))
    patterns = tuple(_lower_pattern(p, counter) for p in node.Patterns)
    variables = _variables(patterns, node)
    _refuse_disconnected(patterns, node)

    if getattr(node, "ProjectClause", None) is None:
        raise L._unsupported(node, f"{keyword} without project (Kusto: SEM0001)")
    where = None
    if getattr(node, "WhereClause", None) is not None:
        expression = L._collapse(node.WhereClause.Expression)
        if L._cls(expression) == "PipeExpression":
            raise L._unsupported(expression, f"{keyword} where")
        where = graphify(L._lower_expr(expression), variables)
    project = tuple(
        dataclasses.replace(named, expr=graphify(named.expr, variables))
        for named in (L._lower_named(e) for e in node.ProjectClause.Expressions)
    )
    return ir.GraphMatch(patterns, project, where, cycles, output)


def _lower_pattern(node: Any, counter: Any) -> ir.Pattern:
    nodes = []
    for element in node.Nodes:
        name = getattr(element, "Name", None)
        if name is None:
            nodes.append(ir.PatternNode(f"\x00node{next(counter)}", anonymous=True))
        else:
            nodes.append(ir.PatternNode(_name(name)))
    edges = []
    for element in node.Edges:
        edges.append(_lower_pattern_edge(element, counter))
    return ir.Pattern(tuple(nodes), tuple(edges))


def _name(node: Any) -> str:
    names = L._find_names(node)
    return names[0] if names else node.getText()


def _lower_pattern_edge(node: Any, counter: Any) -> ir.PatternEdge:
    unnamed = getattr(node, "UnnamedEdge", None)
    if unnamed is not None:
        direction = {"-->": "out", "<--": "in", "--": "any"}[unnamed.Direction.text]
        return ir.PatternEdge(f"\x00edge{next(counter)}", direction, anonymous=True)
    named = node.NamedEdge
    opening, closing = named.OpenBracket.text, named.CloseBracket.text
    if opening == "<-[" and closing == "]-":
        direction = "in"
    elif opening == "-[" and closing == "]->":
        direction = "out"
    elif opening == "-[" and closing == "]-":
        direction = "any"
    else:
        # `<-[e]->` is no direction Kusto documents.
        raise L._unsupported(named, "graph edge direction")
    name = getattr(named, "Name", None)
    edge = ir.PatternEdge(
        _name(name) if name is not None else f"\x00edge{next(counter)}",
        direction,
        anonymous=name is None,
    )
    bounds = getattr(named, "Range", None)
    if bounds is not None:
        edge = dataclasses.replace(
            edge,
            low=L._lower_expr(bounds.LowerBound),
            high=L._lower_expr(bounds.UpperBound),
        )
    return edge


def _variables(patterns: tuple[ir.Pattern, ...], node: Any) -> dict[str, str]:
    """Variable name -> ``node``, ``edge`` or ``path``.

    A name used for a node and an edge is refused. The emulator accepts
    ``(a)-[a]->(b)`` and reads `a.id` from the node, but that is one reading
    measured, not a rule, and the query is almost certainly a typo.
    """
    kinds: dict[str, str] = {}
    # A repeated *node* variable is the same node, measured. A repeated edge
    # variable is not measured, so it is refused with the collisions.
    for pattern in patterns:
        for n in pattern.nodes:
            kinds[n.name] = "node"
    for pattern in patterns:
        for e in pattern.edges:
            if e.name in kinds:
                raise L._unsupported(
                    node, f"graph pattern variable {e.name!r} names two elements"
                )
            kinds[e.name] = "path" if e.variable else "edge"
    return kinds


def _refuse_disconnected(patterns: tuple[ir.Pattern, ...], node: Any) -> None:
    """Kusto: SEM1011, "exactly one connected component supported"."""
    groups = [{n.name for n in p.nodes} for p in patterns]
    merged = groups[0]
    pending = groups[1:]
    while pending:
        joined = [g for g in pending if g & merged]
        if not joined:
            raise L._unsupported(
                node, "graph patterns with no variable in common (Kusto: SEM1011)"
            )
        for g in joined:
            merged |= g
            pending.remove(g)


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------


def graphify(expr: ir.Expr, variables: dict[str, str], element: bool = False) -> ir.Expr:
    """Rewrite an ordinarily-lowered expression in terms of pattern variables.

    *element* is true inside a ``map``/``all``/``any`` body, where a bare name
    is the current edge's or inner node's property.
    """

    def rewrite(node: ir.Expr) -> ir.Expr:
        if isinstance(node, ir.ColumnRef):
            if node.name in variables and not element:
                return ir.GraphVar(node.name)
            if element:
                return ir.GraphElement(node.name)
            return node
        if isinstance(node, ir.PathAccess) and isinstance(node.base, ir.ColumnRef):
            base = node.base.name
            if base in variables and not element:
                first, *rest = node.steps
                if first.name is None:
                    raise KqlUnsupportedError(
                        f"graph:{base}[…]", hint="index a property, not a variable"
                    )
                prop: ir.Expr = ir.GraphProperty(base, first.name)
                return ir.PathAccess(prop, tuple(rest)) if rest else prop
        if isinstance(node, ir.FunctionCall):
            name = node.name
            if name == "inner_nodes":
                raise KqlUnsupportedError(
                    "function:inner_nodes",
                    hint="inner_nodes() is only the first argument of map(), "
                    "all() or any() (Kusto: SEM0207)",
                )
            if name in _PATH_FUNCTIONS and len(node.args) == 2:
                if element:
                    raise KqlUnsupportedError(
                        f"function:{name}", hint="a graph function inside another"
                    )
                target, body = node.args
                inner = False
                if isinstance(target, ir.FunctionCall) and target.name == "inner_nodes":
                    if len(target.args) != 1:
                        raise KqlUnsupportedError("function:inner_nodes")
                    target, inner = target.args[0], True
                if not (
                    isinstance(target, ir.ColumnRef)
                    and variables.get(target.name) == "path"
                ):
                    raise KqlUnsupportedError(
                        f"function:{name}",
                        hint=f"{name}() takes a variable-length edge of the pattern",
                    )
                return ir.GraphCall(
                    name, target.name, inner, graphify(body, variables, element=True)
                )
            if name in _ELEMENT_FUNCTIONS:
                if not node.args and element:
                    return ir.GraphCall(name)
                if (
                    len(node.args) == 1
                    and isinstance(node.args[0], ir.ColumnRef)
                    and node.args[0].name in variables
                    and not element
                ):
                    var = node.args[0].name
                    if name.startswith("node_") and variables[var] != "node":
                        raise KqlUnsupportedError(
                            f"function:{name}", hint=f"{name}() takes a node"
                        )
                    if variables[var] == "path":
                        raise KqlUnsupportedError(
                            f"function:{name}", hint=f"{name}() of a path"
                        )
                    return ir.GraphCall(name, var)
                raise KqlUnsupportedError(
                    f"function:{name}",
                    hint=f"{name}() takes a pattern variable, or nothing inside "
                    "map(), all() or any()",
                )
        mapped: ir.Expr = _map_children(node, rewrite)
        return mapped

    return rewrite(expr)


def _map_children(node: Any, fn: Callable[[ir.Expr], ir.Expr]) -> Any:
    """*node* with *fn* applied to each direct child expression."""
    if not dataclasses.is_dataclass(node) or isinstance(node, type):
        return node
    if isinstance(node, ir.Query):
        return node
    changes = {}
    for field in dataclasses.fields(node):
        value = getattr(node, field.name)
        new = _map_value(value, fn)
        if new is not value:
            changes[field.name] = new
    return dataclasses.replace(node, **changes) if changes else node


def _map_value(value: Any, fn: Callable[[ir.Expr], ir.Expr]) -> Any:
    if isinstance(value, ir.Expr):
        return fn(value)
    if isinstance(value, tuple):
        items = tuple(_map_value(v, fn) for v in value)
        return value if all(a is b for a, b in zip(items, value, strict=True)) else items
    if isinstance(value, (ir.PathStep, ir.NamedExpr, ir.SortKey)):
        return _map_children(value, fn)
    return value


# ---------------------------------------------------------------------------
# Passes over the IR that must reach inside a graph source
# ---------------------------------------------------------------------------


def map_queries(source: ir.GraphSource, fn: Callable[[ir.Query], ir.Query]) -> ir.GraphSource:
    """*source* with *fn* applied to the edges query and every node table."""
    graph = source.graph
    graph = dataclasses.replace(
        graph,
        edges=fn(graph.edges),
        nodes=tuple(dataclasses.replace(t, query=fn(t.query)) for t in graph.nodes),
    )
    return dataclasses.replace(source, graph=graph)


def queries(source: ir.GraphSource) -> list[ir.Query]:
    return [source.graph.edges, *(t.query for t in source.graph.nodes)]


def map_expressions(source: ir.GraphSource, fn: Callable[[Any], Any]) -> ir.GraphSource:
    """*source* with *fn* applied to every expression of its graph operator."""
    consumer = source.consumer
    if not isinstance(consumer, ir.GraphMatch):
        return source
    patterns = tuple(
        dataclasses.replace(
            p,
            edges=tuple(
                dataclasses.replace(
                    e,
                    low=None if e.low is None else fn(e.low),
                    high=None if e.high is None else fn(e.high),
                )
                for e in p.edges
            ),
        )
        for p in consumer.patterns
    )
    consumer = dataclasses.replace(
        consumer,
        patterns=patterns,
        where=None if consumer.where is None else fn(consumer.where),
        project=tuple(fn(e) for e in consumer.project),
    )
    return dataclasses.replace(source, consumer=consumer)
