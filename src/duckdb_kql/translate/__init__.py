"""Translation — IR to DuckDB SQL (pipeline stage 3).

Renders a KQL pipeline as a chain of CTEs, one per operator, per
``docs/TRANSLATION.md`` §1. The 1:1 correspondence keeps generated SQL
debuggable; DuckDB's optimizer collapses the chain, so there is no cost to it.

Every rule marked ``Rn`` below is a semantic invariant from ``TRANSLATION.md``
§4 — a place where KQL and SQL look identical and behave differently.
"""

from __future__ import annotations

from .. import ir
from ..errors import KqlUnsupportedError
from .functions import _TODATETIME, BINARY_OPERATORS, lookup

__all__ = ["to_sql", "TranslationResult"]

#: KQL type name -> DuckDB type (docs/TRANSLATION.md §2).
TYPE_MAP = {
    "bool": "BOOLEAN",
    "boolean": "BOOLEAN",
    "int": "INTEGER",
    "long": "BIGINT",
    "real": "DOUBLE",
    "double": "DOUBLE",
    "decimal": "DECIMAL(38,9)",
    "string": "VARCHAR",
    "datetime": "TIMESTAMP",
    "timespan": "INTERVAL",
    "guid": "UUID",
    "dynamic": "JSON",
}


class TranslationResult(str):
    """The generated SQL, plus the UDFs it needs (none yet in Wave 1)."""

    udfs: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# Identifiers and literals
# ---------------------------------------------------------------------------


def quote_ident(name: str) -> str:
    """Always double-quote (R7).

    KQL identifiers are case-sensitive while DuckDB folds case, so emitting a
    bare identifier risks silently merging two distinct KQL columns.
    """
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


def quote_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def render_literal(lit: ir.Literal) -> str:
    if lit.kind == "null" or lit.value is None:
        return "NULL"
    if lit.kind == "bool":
        return "TRUE" if lit.value else "FALSE"
    if lit.kind == "string":
        return quote_string(str(lit.value))
    if lit.kind in ("long", "int"):
        # KQL's default integer is 64-bit; don't let DuckDB infer INTEGER and
        # overflow at 2^31 (docs/TRANSLATION.md §2).
        return f"CAST({int(lit.value)} AS BIGINT)"
    if lit.kind in ("real", "decimal"):
        return f"CAST({lit.value} AS DOUBLE)"
    if lit.kind == "datetime":
        # A `datetime(...)` literal accepts everything todatetime() does, so it
        # must go through the same conversion (R8). `TIMESTAMP '12-02-2022'` is
        # both wrong and *loud* — DuckDB raises rather than returning the date
        # KQL would. DuckDB constant-folds this, so there is no runtime cost.
        return _TODATETIME.format(quote_string(str(lit.value)))
    if lit.kind == "timespan":
        return f"INTERVAL {quote_string(str(lit.value))}"
    if lit.kind == "guid":
        return f"UUID {quote_string(str(lit.value))}"
    if lit.kind == "dynamic":
        return f"CAST({quote_string(str(lit.value))} AS JSON)"
    raise KqlUnsupportedError(f"literal:{lit.kind}")


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------


def render_expr(node: ir.Expr) -> str:
    if isinstance(node, ir.Literal):
        return render_literal(node)

    if isinstance(node, ir.ColumnRef):
        return quote_ident(node.name)

    if isinstance(node, ir.UnaryOp):
        if node.op == "-":
            return f"(-{render_expr(node.operand)})"
        if node.op in ("not", "!"):
            return f"(NOT {render_expr(node.operand)})"
        raise KqlUnsupportedError(f"unary:{node.op}")

    if isinstance(node, ir.BinaryOp):
        spec = BINARY_OPERATORS.get(node.op)
        if spec is None:
            raise KqlUnsupportedError(
                f"operator:{node.op}", hint="no DuckDB mapping in this wave"
            )
        return spec.template.format(render_expr(node.left), render_expr(node.right))

    if isinstance(node, ir.FunctionCall):
        spec = lookup(node.name)
        if spec is None:
            raise KqlUnsupportedError(
                f"function:{node.name}",
                hint="no DuckDB mapping in this wave; see translate/functions.py",
            )
        args = [render_expr(a) for a in node.args]
        try:
            return spec.render(args)
        except ValueError as e:
            raise KqlUnsupportedError(f"function:{node.name}", hint=str(e)) from None

    raise KqlUnsupportedError(f"expression:{type(node).__name__}")


def output_name(named: ir.NamedExpr, position: int) -> str:
    """Derive a column's output name.

    An explicit ``name =`` wins; a bare column keeps its own name; anything else
    gets a positional fallback (R12 covers the ``summarize`` naming rules, which
    arrive with that operator).
    """
    if named.name:
        return named.name
    if isinstance(named.expr, ir.ColumnRef):
        return named.expr.name
    return f"Column{position}"


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def render_source(source: ir.Source) -> str:
    if isinstance(source, ir.TableRef):
        return f"SELECT * FROM {quote_ident(source.name)}"

    if isinstance(source, ir.PrintSource):
        # An unnamed `print` column is named `print_0`, `print_1`, ... by KQL --
        # not a generic positional name. Output names are user-visible.
        cols = []
        for i, e in enumerate(source.expressions):
            name = e.name or (
                e.expr.name if isinstance(e.expr, ir.ColumnRef) else f"print_{i}"
            )
            cols.append(f"{render_expr(e.expr)} AS {quote_ident(name)}")
        return "SELECT " + ", ".join(cols)

    if isinstance(source, ir.DataTable):
        return render_datatable(source)

    raise KqlUnsupportedError(f"source:{type(source).__name__}")


def render_datatable(dt: ir.DataTable) -> str:
    """Render ``datatable(...)`` as a VALUES list.

    Values are given as a flat stream and wrap across the declared columns.
    """
    arity = dt.arity
    if arity == 0:
        raise KqlUnsupportedError("datatable", hint="no columns declared")
    if len(dt.values) % arity:
        raise KqlUnsupportedError(
            "datatable",
            hint=f"{len(dt.values)} values do not divide into {arity} columns",
        )

    names = [quote_ident(n) for n, _ in dt.columns]
    types = [TYPE_MAP.get(t, "VARCHAR") for _, t in dt.columns]

    if not dt.values:
        # An empty datatable still has a schema; SELECT ... WHERE FALSE keeps it.
        cols = ", ".join(
            f"CAST(NULL AS {t}) AS {n}" for n, t in zip(names, types)
        )
        return f"SELECT {cols} WHERE FALSE"

    rows = []
    for start in range(0, len(dt.values), arity):
        cells = [
            f"CAST({render_expr(v)} AS {t})"
            for v, t in zip(dt.values[start : start + arity], types)
        ]
        rows.append("(" + ", ".join(cells) + ")")

    collist = ", ".join(names)
    return f"SELECT * FROM (VALUES {', '.join(rows)}) AS _dt({collist})"


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------


def render_operator(op: ir.Operator, prev: str) -> str:
    if isinstance(op, ir.Where):
        return f"SELECT * FROM {prev} WHERE {render_expr(op.predicate)}"

    if isinstance(op, ir.Project):
        cols = [
            f"{render_expr(e.expr)} AS {quote_ident(output_name(e, i))}"
            for i, e in enumerate(op.expressions)
        ]
        return f"SELECT {', '.join(cols)} FROM {prev}"

    if isinstance(op, ir.Extend):
        # `extend` REPLACES a column whose name already exists, and appends
        # otherwise. A plain `SELECT *, expr AS c` is silently wrong on a
        # collision — DuckDB emits two columns named `c` without complaining.
        # `EXCLUDE`/`REPLACE` would fix that but each errors in the opposite
        # case, and we have no schema here. `COLUMNS(x -> x NOT IN (...))`
        # filters dynamically, so it is correct either way.
        names = [output_name(e, i) for i, e in enumerate(op.expressions)]
        excluded = ", ".join(quote_string(n) for n in names)
        added = ", ".join(
            f"{render_expr(e.expr)} AS {quote_ident(n)}"
            for e, n in zip(op.expressions, names)
        )
        return f"SELECT COLUMNS(x -> x NOT IN ({excluded})), {added} FROM {prev}"

    if isinstance(op, ir.Take):
        # Row order is undefined without a terminal sort (R10).
        return f"SELECT * FROM {prev} LIMIT {op.count}"

    if isinstance(op, ir.Count):
        return f"SELECT count(*) AS {quote_ident(op.name)} FROM {prev}"

    if isinstance(op, ir.Distinct):
        cols = ", ".join(quote_ident(c) for c in op.columns)
        return f"SELECT DISTINCT {cols} FROM {prev}"

    if isinstance(op, ir.Sort):
        return f"SELECT * FROM {prev} ORDER BY {render_sort_keys(op.keys)}"

    raise KqlUnsupportedError(f"operator:{type(op).__name__}")


def render_sort_keys(keys: tuple[ir.SortKey, ...]) -> str:
    parts = []
    for key in keys:
        # KQL defaults to DESC — the opposite of SQL (R6) — so always emit the
        # direction explicitly rather than relying on either engine's default.
        direction = "ASC" if key.ascending else "DESC"
        # KQL puts nulls first when descending, last when ascending; state it
        # rather than inheriting DuckDB's default.
        if key.nulls_first is None:
            nulls = "NULLS FIRST" if not key.ascending else "NULLS LAST"
        else:
            nulls = "NULLS FIRST" if key.nulls_first else "NULLS LAST"
        parts.append(f"{render_expr(key.expr)} {direction} {nulls}")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def to_sql(query: ir.Query) -> TranslationResult:
    """Render an IR query as DuckDB SQL (a CTE chain)."""
    stages = [render_source(query.source)]
    for op in query.operators:
        stages.append(render_operator(op, f"_s{len(stages) - 1}"))

    if len(stages) == 1:
        return TranslationResult(stages[0])

    ctes = ",\n     ".join(f"_s{i} AS ({sql})" for i, sql in enumerate(stages))
    return TranslationResult(f"WITH {ctes}\nSELECT * FROM _s{len(stages) - 1}")
