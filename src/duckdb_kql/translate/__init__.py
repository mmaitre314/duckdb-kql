"""Translation — IR to DuckDB SQL (pipeline stage 3).

Renders a KQL pipeline as a chain of CTEs, one per operator, per
``docs/TRANSLATION.md`` §1. The 1:1 correspondence keeps generated SQL
debuggable; DuckDB's optimizer collapses the chain, so there is no cost to it.

Every rule marked ``Rn`` below is a semantic invariant from ``TRANSLATION.md``
§4 — a place where KQL and SQL look identical and behave differently.
"""

from __future__ import annotations

import dataclasses
import json
import re
from typing import TYPE_CHECKING, Any

from .. import ir
from ..errors import KqlUnsupportedError
from .functions import (
    _TODATETIME,
    _TOTIMESPAN,
    BINARY_OPERATORS,
    lookup,
    lookup_aggregate,
    term_match_sql,
)
from .regexfrag import neutralise_groups

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ..params import ParameterDeclaration
    from ..schema import Schema

__all__ = ["to_sql", "TranslationResult"]

#: Placeholder slot -> the value bound to it. See duckdb_kql.params.
Parameters = dict[str, Any]

#: KQL type name -> DuckDB type (docs/TRANSLATION.md §2).
#:
#: Aliases included. KQL accepts several spellings for most types and they are
#: not decoration — the documentation's own examples use them, and `parse … t:
#: date` in the corpus is one. Transcribed from `Symbols/ScalarTypes.cs`
#: (`PrimitiveSymbol`'s second argument) rather than guessed, so the set is the
#: same one a cluster accepts. Note `int` and its unsigned/narrow aliases all
#: map to INTEGER: KQL has no unsigned or narrow integer *type*, only names
#: that mean `int`.
TYPE_MAP = {
    "bool": "BOOLEAN",
    "boolean": "BOOLEAN",
    "int": "INTEGER",
    "int32": "INTEGER", "uint": "INTEGER", "uint32": "INTEGER",
    "int8": "INTEGER", "uint8": "INTEGER", "int16": "INTEGER", "uint16": "INTEGER",
    "long": "BIGINT",
    "int64": "BIGINT", "ulong": "BIGINT", "uint64": "BIGINT",
    "real": "DOUBLE",
    "double": "DOUBLE",
    "float": "DOUBLE",
    "decimal": "DECIMAL(38,9)",
    "string": "VARCHAR",
    "datetime": "TIMESTAMP",
    "date": "TIMESTAMP",
    "timespan": "INTERVAL",
    "time": "INTERVAL",
    "guid": "UUID",
    "uuid": "UUID", "uniqueid": "UUID",
    "dynamic": "JSON",
}


class TranslationResult(str):
    """The generated SQL, plus what has to travel alongside it.

    It *is* the SQL string, so anything expecting one keeps working, but it also
    carries ``parameters`` — the values for the ``$slot`` placeholders a
    ``declare query_parameters`` query renders. Those are not optional extras:
    running the SQL without them fails, which is the point. The values were
    never text in the first place, so there is nothing to escape.
    """

    udfs: frozenset[str] = frozenset()
    parameters: Parameters = {}  # noqa: RUF012 - immutable-by-convention default
    #: Declared parameters left without a value or a default. The SQL is still
    #: valid text, but it cannot run until these are supplied.
    unbound: tuple[str, ...] = ()
    #: The query's ``declare query_parameters`` declarations, in order. Needed
    #: by anything that has to explain a generated ``$slot`` back to a human —
    #: the build-time CLI writes them into the SQL's header, because otherwise a
    #: consumer is handed a placeholder with no way to know what belongs in it.
    declarations: tuple[ParameterDeclaration, ...] = ()

    def with_parameters(
        self,
        parameters: Parameters,
        unbound: tuple[str, ...] = (),
        declarations: tuple[ParameterDeclaration, ...] = (),
    ) -> TranslationResult:
        result = TranslationResult(str(self))
        result.udfs = self.udfs
        result.parameters = parameters
        result.unbound = unbound
        result.declarations = declarations
        return result


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


def render_parameter(param: ir.Parameter) -> str:
    """A declared parameter, as a DuckDB placeholder with its declared type.

    The cast is not decoration. Without it DuckDB has to infer a placeholder's
    type from context, and in positions like ``SELECT $p`` there is no context
    to infer from; stating the declared type keeps a parameter behaving exactly
    like the literal it stands in for.
    """
    return f"CAST(${param.slot} AS {TYPE_MAP[param.kind]})"


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
        if lit.value is None:
            return "CAST(NULL AS JSON)"
        return f"CAST({quote_string(str(lit.value))} AS JSON)"
    raise KqlUnsupportedError(f"literal:{lit.kind}")


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------


def render_expr(node: ir.Expr) -> str:
    if isinstance(node, ir.Literal):
        return render_literal(node)

    if isinstance(node, ir.Parameter):
        return render_parameter(node)

    if isinstance(node, ir.ColumnRef):
        return quote_ident(node.name)

    if isinstance(node, ir.RenderedAggregate):
        # Already SQL — see render_aggregate. Parenthesised at construction.
        return node.sql

    if isinstance(node, ir.UnaryOp):
        if node.op == "-":
            return f"(-{render_expr(node.operand)})"
        if node.op in ("not", "!"):
            return f"(NOT {render_expr(node.operand)})"
        raise KqlUnsupportedError(f"unary:{node.op}")

    if isinstance(node, ir.BinaryOp):
        if node.op == "/" and (
            _is_timespan_expr(node.left) or _is_timespan_expr(node.right)
        ):
            # Dividing two timespans yields a NUMBER in KQL (`dow / 1d` is "how
            # many days"). DuckDB has no interval division at all, so this would
            # otherwise fail to bind rather than silently mislead.
            return (
                f"(epoch({render_expr(node.left)}) / epoch({render_expr(node.right)}))"
            )
        if node.op == "/" and (_is_real_expr(node.left) or _is_real_expr(node.right)):
            # `//` is the right default (see BINARY_OPERATORS) but it returns
            # NULL for division by zero, where KQL's *float* division returns
            # ±Infinity. `1.0 / 0` is Infinity in Kusto and null under `//`.
            # Where an operand is visibly a real, say so and use plain `/`.
            # Where it is not — a column — `//` still divides correctly and only
            # a zero divisor differs; that residue is in the divergence catalog.
            return f"({render_expr(node.left)} / {render_expr(node.right)})"
        if (
            node.op in ("==", "!=", "<>")
            and _is_dynamic_expr(node.left)
            and _is_dynamic_expr(node.right)
        ):
            # Kusto refuses this outright — SEM0001, "Cannot compare dynamic
            # values without explicit cast" — because it cannot know which
            # comparison is meant. DuckDB compares the JSON text and answers,
            # so without this the query would run where a cluster would not.
            raise KqlUnsupportedError(
                f"operator:{node.op}",
                hint="Kusto refuses to compare two dynamics; cast one with "
                "tostring()/tolong() to say which comparison is meant",
            )
        spec = BINARY_OPERATORS.get(node.op)
        if spec is None:
            raise KqlUnsupportedError(
                f"operator:{node.op}", hint="no DuckDB mapping in this wave"
            )
        left, right = render_expr(node.left), render_expr(node.right)

        def build(sqls: Sequence[str]) -> str:
            rendered = _render_term_operator(node, sqls) or spec.template.format(*sqls)
            if spec.null_result is None:
                return rendered
            return _apply_null_semantics(
                rendered,
                spec.null_result,
                ((node.left, sqls[0]), (node.right, sqls[1])),
            )

        if _is_string_context(node):
            return _in_string_context(((node.left, left), (node.right, right)), build)
        return build((left, right))

    if isinstance(node, ir.PathAccess):
        return render_path(node)

    if isinstance(node, ir.InList):
        return render_in_list(node)

    if isinstance(node, ir.HasList):
        return render_has_list(node)

    if isinstance(node, ir.FunctionCall):
        if node.name.lower() in ("bin", "floor"):
            # `floor` IS `bin` in KQL, two arguments and all — the emulator
            # refuses `floor(7.9)` with "bin(): function expects 2 argument(s)"
            # and answers -10 for `floor(-7, 5)`. Mapping it to SQL's `floor`
            # answered a query Kusto rejects, and would have answered -7.
            return render_bin(node, name=node.name.lower())
        if node.name.lower() == "tostring" and len(node.args) == 1:
            return render_kql_tostring(node.args[0])
        if node.name.lower() == "reverse" and len(node.args) == 1:
            # KQL's `reverse` reverses the value's **string form** whatever its
            # type — `reverse(12345)` is `'54321'`, `reverse(3h)` is
            # `'00:00:30'`. DuckDB's `reverse` takes VARCHAR only, so a bare
            # mapping produced SQL that would not bind for anything else.
            # (Do not start a comment line with `type:` — mypy reads it as a
            # PEP 484 type comment and reports a syntax error here.)
            #
            # A plain CAST is not enough either — it agrees with KQL for
            # numbers and disagrees for datetimes, where KQL prints seven
            # fractional digits and a `Z`. Reversing the wrong rendering is a
            # wrong answer that still looks like a reversed string, so this goes
            # through the same KQL-spelling helper the hash functions use.
            return f"reverse({render_kql_tostring(node.args[0])})"
        special = _SPECIAL_FORMS.get(node.name.lower())
        if special is not None:
            return special(node)
        if node.name.lower() == "pack_array":
            # json_array() takes mixed types, which to_json([...]) cannot —
            # and it renders an INTERVAL as KQL spells it ("00:00:02").
            return f"json_array({', '.join(render_expr(a) for a in node.args)})"
        if node.name.lower() in ("hash_md5", "hash_sha1", "hash_sha256"):
            fn = {"hash_md5": "md5", "hash_sha1": "sha1", "hash_sha256": "sha256"}[
                node.name.lower()
            ]
            if len(node.args) == 1:
                # Hashing goes through KQL's *string* form, so a datetime must
                # be spelled the way KQL spells it or the digest is wrong —
                # silently, and in security-relevant code.
                return f"{fn}({render_kql_tostring(node.args[0])})"
        if node.name.lower() == "array_concat":
            # DuckDB's list_concat is binary; KQL's array_concat is variadic.
            args = [f"CAST({render_expr(a)} AS JSON[])" for a in node.args]
            if not args:
                return "CAST('[]' AS JSON)"
            folded = args[0]
            for nxt in args[1:]:
                folded = f"list_concat({folded}, {nxt})"
            return f"CAST({folded} AS JSON)"
        if node.name.lower() == "todatetime" and len(node.args) == 1:
            # `todatetime(T)` where T is already a datetime is a no-op in KQL,
            # but the string-parsing template does not bind against a TIMESTAMP
            # — it reached DuckDB as `try_strptime(TIMESTAMP, VARCHAR[])` and
            # came back as a raw BinderException rather than any KQL error.
            if _is_datetime_expr(node.args[0]):
                return render_expr(node.args[0])
        if node.name.lower() in ("totimespan", "timespan") and len(node.args) == 1:
            # `totimespan(4d)` hands us an INTERVAL, not a string — the string
            # parser would fail to bind. Converting an already-converted value
            # is a no-op in KQL too.
            if _is_timespan_expr(node.args[0]):
                return render_expr(node.args[0])
        fn_spec = lookup(node.name)
        if fn_spec is None:
            raise KqlUnsupportedError(
                f"function:{node.name}",
                hint="no DuckDB mapping in this wave; see translate/functions.py",
            )
        args = [_render_arg(node.name, i, a) for i, a in enumerate(node.args)]
        try:
            return fn_spec.render(args)
        except ValueError as e:
            raise KqlUnsupportedError(f"function:{node.name}", hint=str(e)) from None

    raise KqlUnsupportedError(f"expression:{type(node).__name__}")


def render_bin(node: ir.FunctionCall, name: str = "bin") -> str:
    """``bin(value, roundTo)`` — round *down* to a multiple of *roundTo*.

    Two different computations share one KQL name, and which applies is decided
    by the *type* of ``roundTo``, so it is resolved here rather than by a
    template. A timespan means datetime binning.

    KQL bins datetimes from the **Unix epoch**. DuckDB's ``time_bucket`` uses
    2000-01-03 as its default origin, so using it would shift every bucket
    boundary — a wrong answer that still looks like a plausible timestamp (R8).
    Doing the arithmetic on epoch seconds avoids the origin question entirely.
    """
    if len(node.args) != 2:
        raise KqlUnsupportedError(name, hint="expects (value, roundTo)")
    value, round_to = node.args
    v, r = render_expr(value), render_expr(round_to)

    if not (isinstance(round_to, ir.Literal) and round_to.kind == "timespan"):
        return f"(floor({v} / {r}) * {r})"

    # A timespan bin size means the value is temporal — but binning a *timespan*
    # yields a timespan, while binning a datetime yields a datetime. The
    # emulator confirms: bin(14d + 3h, 1d) is 14.00:00:00, not a date in 1970.
    floored = f"floor(epoch({v}) / epoch({r})) * epoch({r})"
    if _is_timespan_expr(value):
        return f"to_seconds(CAST({floored} AS BIGINT))"
    return f"to_timestamp({floored}) AT TIME ZONE 'UTC'"


def _is_timespan_expr(node: ir.Expr) -> bool:
    """Whether an expression is statically known to be a timespan.

    Only literals and arithmetic over them can be decided without a schema;
    a bare column is assumed to be a datetime, which is the overwhelmingly
    common case for ``bin``.
    """
    if isinstance(node, (ir.Literal, ir.Parameter)):
        return node.kind == "timespan"
    if isinstance(node, ir.FunctionCall):
        return node.name.lower() in _TIMESPAN_RETURNING
    if isinstance(node, ir.BinaryOp) and node.op in ("+", "-"):
        return _is_timespan_expr(node.left) and _is_timespan_expr(node.right)
    if isinstance(node, ir.BinaryOp) and node.op == "*":
        # `5 * 1h` is a timespan; a scalar factor does not change that.
        return _is_timespan_expr(node.left) or _is_timespan_expr(node.right)
    if isinstance(node, ir.UnaryOp):
        return _is_timespan_expr(node.operand)
    return False


#: Functions whose result is a timespan, so arithmetic on them is timespan
#: arithmetic. `dayofweek` is the surprising one — it returns a *timespan*
#: (days since Sunday), not an integer.
_TIMESPAN_RETURNING = frozenset({"totimespan", "timespan", "dayofweek", "to_timespan"})


def print_column_name(named: ir.NamedExpr, position: int) -> str:
    """An unnamed ``print`` column is ``print_0``, ``print_1``, ... in KQL.

    Not a generic positional name — output names are user-visible.
    """
    if named.name:
        return named.name
    if isinstance(named.expr, ir.ColumnRef):
        return named.expr.name
    return f"print_{position}"


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
    # KQL numbers unnamed columns from ONE, per operator: `project x+1, x+2`
    # yields Column1, Column2. Zero-based would be off by one on every one.
    return f"Column{position + 1}"


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def render_wildcard(source: ir.WildcardTableRef, schema: Schema | None) -> str:
    """``UT*`` — the matching tables, unioned by name.

    Expanded here rather than at lowering because only this stage knows the
    catalog. Kusto refuses a pattern matching nothing, so `match_wildcard` does.
    """
    from ..schema import match_wildcard

    names = match_wildcard(source, schema)
    parts = [f"SELECT * FROM {_qualified_name(name)}" for name in names]
    return "(" + "\nUNION ALL BY NAME ".join(parts) + ")"


def _qualified_name(name: str) -> str:
    """``Sales.Orders`` -> ``"Sales"."Orders"``; a bare name stays bare."""
    database, _, table = name.rpartition(".")
    if not database:
        return quote_ident(table)
    return f"{quote_ident(database)}.{quote_ident(table)}"


def render_table_ref(source: ir.TableRef) -> str:
    """``"Orders"`` or ``"Sales"."Orders"``.

    Two parts, not three: DuckDB reads ``"db"."name"`` as catalog-and-table and
    finds it wherever the database's search path puts it. Pinning ``"main"`` in
    the middle would be more explicit and would stop resolving the moment
    someone attaches a file whose tables live in another schema.
    """
    if source.database is None:
        return quote_ident(source.name)
    return f"{quote_ident(source.database)}.{quote_ident(source.name)}"


def render_source(source: ir.Source, schema: Schema | None = None) -> str:
    if isinstance(source, ir.TableRef):
        return f"SELECT * FROM {render_table_ref(source)}"

    if isinstance(source, ir.WildcardTableRef):
        return f"SELECT * FROM {render_wildcard(source, schema)}"

    if isinstance(source, ir.PrintSource):
        cols = [
            f"{render_expr(e.expr)} AS {quote_ident(print_column_name(e, i))}"
            for i, e in enumerate(source.expressions)
        ]
        return "SELECT " + ", ".join(cols)

    if isinstance(source, ir.CommandSource):
        # Already SQL — see duckdb_kql.control. Wrapped so the operators after
        # it compose exactly as they would over a table.
        return f"SELECT * FROM ({source.sql})"

    if isinstance(source, ir.RangeSource):
        return render_range(source)

    if isinstance(source, ir.DataTable):
        return render_datatable(source)

    raise KqlUnsupportedError(f"source:{type(source).__name__}")


def render_getschema(prev: str) -> str:
    """``getschema`` — the input's columns, as rows.

    DuckDB's `DESCRIBE` is the only thing that knows a subquery's column types,
    and it works as a subquery itself, so the shape is available without the
    translator having to track types through the pipeline.

    Column names, order and the 0-based ordinal are Kusto's, measured on the
    emulator. `DataType` is the .NET name it reports (`System.SByte` for a bool,
    `System.Data.SqlTypes.SqlDecimal` for a decimal), derived from the same
    table the Kusto client labels its columns with — see duckdb_kql.types.
    """
    from ..types import kusto_type_sql, net_type_sql

    inner = (
        "SELECT column_name AS \"ColumnName\", "
        "CAST(row_number() OVER () - 1 AS INTEGER) AS \"ColumnOrdinal\", "
        f"{kusto_type_sql('column_type')} AS \"ColumnType\" "
        f"FROM (DESCRIBE SELECT * FROM {prev})"
    )
    return (
        'SELECT "ColumnName", "ColumnOrdinal", '
        f'{net_type_sql(chr(34) + "ColumnType" + chr(34))} AS "DataType", '
        f'"ColumnType" FROM ({inner})'
    )


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
            f"CAST(NULL AS {t}) AS {n}" for n, t in zip(names, types, strict=True)
        )
        return f"SELECT {cols} WHERE FALSE"

    rows = []
    for start in range(0, len(dt.values), arity):
        cells = [
            f"CAST({render_expr(v)} AS {t})"
            for v, t in zip(dt.values[start : start + arity], types, strict=True)
        ]
        rows.append("(" + ", ".join(cells) + ")")

    collist = ", ".join(names)
    return f"SELECT * FROM (VALUES {', '.join(rows)}) AS _dt({collist})"


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------


def render_operator(op: ir.Operator, prev: str, cols: list[str] | None = None) -> str:
    """Render one operator as a SELECT over *prev*.

    *cols* is the incoming column order where it is known — from the IR for a
    `datatable`/`print`/`range` source, or from the caller's schema for a table.
    `extend` and `mv-expand` need it, both to keep a replaced column in place.
    """
    if isinstance(op, ir.Where):
        return f"SELECT * FROM {prev} WHERE {render_expr(op.predicate)}"

    if isinstance(op, ir.Project):
        cols = [
            f"{render_expr(e.expr)} AS {quote_ident(output_name(e, i))}"
            for i, e in enumerate(op.expressions)
        ]
        return f"SELECT {', '.join(cols)} FROM {prev}"

    if isinstance(op, ir.Extend):
        # `extend` REPLACES a column whose name already exists, **in its original
        # position**, and appends otherwise. A plain `SELECT *, expr AS c` is
        # silently wrong on a collision — DuckDB emits two columns named `c`
        # without complaining.
        names = [output_name(e, i) for i, e in enumerate(op.expressions)]
        rendered = {
            n: f"{render_expr(e.expr)} AS {quote_ident(n)}"
            for e, n in zip(op.expressions, names, strict=True)
        }
        if cols is not None:
            # Column order is user-visible (TRANSLATION.md §1, §5), so when the
            # incoming columns are known the list is written out explicitly: a
            # replaced name keeps its slot, new ones go on the end.
            select = [rendered.get(c, quote_ident(c)) for c in cols]
            select += [rendered[n] for n in names if n not in cols]
            return f"SELECT {', '.join(select)} FROM {prev}"
        # Without a schema we cannot tell a replacement from an addition, and
        # `EXCLUDE`/`REPLACE` each error in the opposite case. `COLUMNS(x -> x
        # NOT IN (...))` filters dynamically, so it is correct either way — but
        # it appends, so a *replaced* column moves to the end. That is the one
        # residual divergence, and it is why Layer 1 always passes a schema.
        excluded = ", ".join(quote_string(n) for n in names)
        added = ", ".join(rendered[n] for n in names)
        return f"SELECT COLUMNS(x -> x NOT IN ({excluded})), {added} FROM {prev}"

    if isinstance(op, ir.Take):
        # Row order is undefined without a terminal sort (R10).
        return f"SELECT * FROM {prev} LIMIT {op.count}"

    if isinstance(op, ir.GetSchema):
        return render_getschema(prev)

    if isinstance(op, ir.Count):
        return f"SELECT count(*) AS {quote_ident(op.name)} FROM {prev}"

    if isinstance(op, ir.Distinct):
        targets = ", ".join(
            f"{render_expr(e.expr)} AS {quote_ident(name)}"
            for e, name in zip(
                op.expressions, target_names(op.expressions), strict=True
            )
        )
        return f"SELECT DISTINCT {targets} FROM {prev}"

    if isinstance(op, ir.ProjectAway):
        excluded = ", ".join(quote_ident(c) for c in op.columns)
        return f"SELECT * EXCLUDE ({excluded}) FROM {prev}"

    if isinstance(op, ir.ProjectRename):
        # RENAME keeps each column in its original position, which is what KQL
        # does; re-selecting would move renamed columns to the end.
        renames = ", ".join(
            f"{quote_ident(old)} AS {quote_ident(new)}" for old, new in op.renames
        )
        return f"SELECT * RENAME ({renames}) FROM {prev}"

    if isinstance(op, ir.MvExpand):
        return render_mv_expand(op, prev, cols)

    if isinstance(op, ir.Parse):
        return render_parse(op, prev, cols)

    if isinstance(op, ir.Summarize):
        return render_summarize(op, prev)

    if isinstance(op, ir.Sort):
        return f"SELECT * FROM {prev} ORDER BY {render_sort_keys(op.keys)}"

    if isinstance(op, ir.Top):
        # `sort by X | take n` in one CTE. The direction and null placement are
        # `sort`'s, defaults included (R6) — measured, `top 3 by a` is
        # descending and `top 3 by a asc` puts nulls first.
        #
        # The count is clamped at zero because DuckDB *refuses* a negative
        # LIMIT ("LIMIT/OFFSET cannot be negative") where Kusto answers an
        # empty table — measured, `top -1 by a` returns no rows there. An
        # engine error for a query the reference engine accepts is a
        # divergence like any other.
        return (
            f"SELECT * FROM {prev} ORDER BY {render_sort_keys(op.keys)} "
            f"LIMIT {max(op.count, 0)}"
        )

    if isinstance(op, (ir.Join, ir.Lookup, ir.Union)):
        # These need both sides' columns, so they are rendered by to_sql(),
        # which threads the schema. Reaching here means a bug, not a gap.
        name = (
            "union" if isinstance(op, ir.Union)
            else "lookup" if isinstance(op, ir.Lookup)
            else "join"
        )
        raise KqlUnsupportedError(
            name, hint=f"internal: {name} must be rendered by to_sql"
        )

    raise KqlUnsupportedError(f"operator:{type(op).__name__}")


def render_sort_keys(keys: tuple[ir.SortKey, ...]) -> str:
    parts = []
    for key in keys:
        # KQL defaults to DESC — the opposite of SQL (R6) — so always emit the
        # direction explicitly rather than relying on either engine's default.
        direction = "ASC" if key.ascending else "DESC"
        # KQL treats null as the SMALLEST value, so it sorts first ascending and
        # last descending — the opposite of what this emitted until the oracle
        # was asked (R6). `datatable(x:int) [3, int(null), 1] | sort by x asc`
        # returns null, 1, 3 on the emulator; `desc` returns 3, 1, null.
        # DuckDB's own default is NULLS LAST regardless of direction, so this
        # has to be stated either way.
        if key.nulls_first is None:
            nulls = "NULLS FIRST" if key.ascending else "NULLS LAST"
        else:
            nulls = "NULLS FIRST" if key.nulls_first else "NULLS LAST"
        parts.append(f"{render_expr(key.expr)} {direction} {nulls}")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def to_sql(query: ir.Query, schema: Schema | None = None) -> TranslationResult:
    """Render an IR query as DuckDB SQL (a CTE chain).

    *schema* maps table name to column names. It is only consulted for queries
    containing a ``join``, which needs both sides' columns to reproduce KQL's
    column renaming; everything else translates schema-free.
    """
    from ..schema import output_columns

    # A tabular `let` becomes a named CTE, so `TableRef(name)` in the body needs
    # no rewriting — it already refers to the CTE.
    schema = _schema_with_lets(query, schema)
    query = _promote_fuzzy_source(query, schema)
    let_ctes = [
        f"{quote_ident(name)} AS ({to_sql(bound, schema)})"
        for name, bound in query.lets
        if isinstance(bound, ir.Query)
    ]

    stages = [render_source(query.source, schema)]
    # Resolved up front rather than on first join: `extend` needs it too, to
    # keep a replaced column in its original position. A `datatable`/`print`/
    # `range` source carries its columns in the IR, so this succeeds with no
    # schema at all; a bare table without one leaves it None and only `join`
    # then has to fail.
    cols = _known_source_cols(query.source, schema)

    for index, op in enumerate(query.operators):
        prev = f"_s{len(stages) - 1}"
        if isinstance(op, ir.Union):
            from ..schema import union_output_columns

            if cols is None:
                cols = _source_cols(query, schema)
            stages.append(render_union(op, prev, cols, query, schema, index == 0))
            cols = union_output_columns(op, cols, schema)
        elif isinstance(op, (ir.Join, ir.Lookup)):
            # Only these force us to resolve columns, so only these can fail for
            # lack of a schema.
            if cols is None:
                cols = _source_cols(query, schema)
            right_cols = output_columns(op.right, schema)
            right_sql = str(to_sql(op.right, schema))
            if isinstance(op, ir.Lookup):
                from ..schema import lookup_output_columns

                stages.append(render_lookup(op, prev, cols, right_sql, right_cols))
                cols = lookup_output_columns(
                    cols, right_cols, [k.right for k in op.keys]
                )
            else:
                from ..schema import join_output_columns

                stages.append(render_join(op, prev, cols, right_sql, right_cols))
                cols = join_output_columns(cols, right_cols, op.kind)
        else:
            stages.append(render_operator(op, prev, cols))
            if cols is not None:
                from ..schema import _operator_columns

                cols = _operator_columns(op, cols, schema)

    if len(stages) == 1 and not let_ctes:
        return TranslationResult(stages[0])

    ctes = let_ctes + [f"_s{i} AS ({sql})" for i, sql in enumerate(stages)]
    body = f"SELECT * FROM _s{len(stages) - 1}"
    return TranslationResult("WITH " + ",\n     ".join(ctes) + f"\n{body}")


def _promote_fuzzy_source(query: ir.Query, schema: Schema | None) -> ir.Query:
    """Let ``isfuzzy=true`` drop the **first** branch as well as the others.

    A union's first branch is the query's own source, not one of
    `ir.Union.branches`, so `surviving_branches` could never drop it:
    `union isfuzzy=true UT1, NoSuchTable` worked and
    `union isfuzzy=true NoSuchTable, UT1` raised, which is a difference Kusto
    does not make. Reaching a missing first branch also made
    `macro-expand isfuzzy=true` fail whenever the *first* entity was the one
    lacking the table.

    Resolved here rather than at lowering because it needs the schema — whether
    a table exists is not knowable from the query text.
    """
    from ..errors import KqlSchemaError
    from ..schema import output_columns, surviving_branches

    if schema is None:
        return query
    index = next(
        (
            i
            for i, candidate in enumerate(query.operators)
            if isinstance(candidate, ir.Union) and candidate.isfuzzy
        ),
        None,
    )
    if index is None:
        return query
    op = query.operators[index]
    assert isinstance(op, ir.Union)

    head = ir.Query(query.source, list(query.operators[:index]), list(query.lets))
    try:
        output_columns(head, schema)
    except KqlSchemaError:
        pass
    else:
        return query        # the first branch is fine; nothing to promote

    survivors = list(surviving_branches(op, schema))
    if not survivors:
        # Every branch is missing. Kusto has nothing to return either, and the
        # original error names the table, which is more use than an empty table.
        return query

    promoted, *rest = survivors
    out = ir.Query(
        promoted.source,
        list(promoted.operators),
        list(query.lets) + list(promoted.lets),
        list(query.parameters),
    )
    out.operators.append(dataclasses.replace(op, branches=tuple(rest)))
    out.operators.extend(query.operators[index + 1:])
    return out


def _schema_with_lets(query: ir.Query, schema: Schema | None) -> Schema | None:
    """Extend *schema* with the columns each tabular ``let`` produces.

    Without this a join whose side is a `let`-bound table cannot resolve its
    columns, even though they are fully determined by the binding.
    """
    if not query.lets:
        return schema
    from ..schema import output_columns

    extended = dict(schema or {})
    for name, bound in query.lets:
        if isinstance(bound, ir.Query):
            try:
                extended[name] = output_columns(bound, extended)
            except Exception:  # noqa: BLE001 - resolve lazily; join reports it
                pass
    return extended


def _known_source_cols(source: ir.Source, schema: Schema | None) -> list[str] | None:
    """The source's columns, or None when they cannot be known here.

    Unlike :func:`_source_cols` this never raises: not knowing the columns is
    only fatal for `join`, which asks separately and reports it properly.
    """
    from ..errors import KqlSchemaError
    from ..schema import _source_columns

    try:
        return _source_columns(source, schema)
    except KqlSchemaError:
        return None


def _source_cols(query: ir.Query, schema: Schema | None) -> list[str]:
    """Columns produced by the query up to (not including) its first join."""
    from ..schema import _operator_columns, _source_columns

    cols = _source_columns(query.source, schema)
    for op in query.operators:
        if isinstance(op, (ir.Join, ir.Lookup, ir.Union)):
            break
        cols = _operator_columns(op, cols, schema)
    return cols


# ---------------------------------------------------------------------------
# summarize (R12)
# ---------------------------------------------------------------------------


#: Functions that pass their first argument's column name through to the output
#: name of a `summarize` key or a `distinct` target. Measured one by one on the
#: emulator, because it is an **allow-list and not a rule**: `tostring(B)` is
#: named `B` but `tolower(B)` is `Column1`; `startofday(T)` is `T` but
#: `startofweek(T)` is `Column1`; `log2` and `exp2` pass through, `pow` and
#: `exp10` do not. Anything absent falls back to the positional name, which is
#: also what an unrecognised function should do.
_NAME_PRESERVING = frozenset({
    # conversions
    "tostring", "toint", "tolong", "todouble", "toreal", "tobool", "todatetime",
    "totimespan", "toguid", "todecimal", "tohex",
    # bucketing and rounding
    "bin", "bin_at", "floor", "ceiling", "round", "startofday",
    # numeric
    "abs", "sqrt", "log", "log10", "log2", "exp", "exp2",
})


def _argument_name(expr: ir.Expr) -> str | None:
    """The column name an expression contributes to an auto-generated name.

    A bare column gives its own name. A call listed in `_NAME_PRESERVING` gives
    its first argument's name, and nests — `tostring(toint(C))` is `C`, and so
    is `bin(tolong(C), 2)`. A call outside the list contributes nothing *and
    breaks the chain*: `abs(-C)` and `tolower(tostring(B))` are both `Column1`.

    This used to treat **any** single-argument call as name-preserving, which
    named `summarize ... by tolower(x)` after `x` where Kusto names it
    `Column1` — a wrong column name, silently, on one of the most-used
    operators.
    """
    if isinstance(expr, ir.ColumnRef):
        return expr.name
    if isinstance(expr, ir.FunctionCall) and expr.args:
        if expr.name.lower() in _NAME_PRESERVING:
            return _argument_name(expr.args[0])
        return None
    return None


def _wrapped_aggregate(expr: ir.Expr) -> ir.FunctionCall | None:
    """The aggregate a scalar call wraps, following **first arguments only**.

    `round(sum(y), 2)` -> `sum(y)`; `round(round(sum(y),1),2)` -> `sum(y)`;
    `strcat('n=', tostring(count()))` -> ``None``, because the first argument is
    a literal and the name falls back to `Column1`. Every one of those is what
    the emulator returns — the rule is positional, not a search.
    """
    while isinstance(expr, ir.FunctionCall):
        if lookup_aggregate(expr.name) is not None:
            return expr
        if not expr.args:
            return None
        expr = expr.args[0]
    return None


def aggregate_name(named: ir.NamedExpr) -> str:
    """KQL's auto-generated name for one summarize aggregate (R12).

    Every rule here was read off the emulator, because they are not guessable:

        count()             -> count_          (no argument in the name)
        countif(x > 1)      -> countif_        (predicate contributes nothing)
        sum(x)              -> sum_x
        sum(x + z)          -> sum_            (not a bare column)
        make_list(x)        -> list_x          ('make_' is dropped)
        make_set(y)         -> set_y
        percentile(x, 50)   -> percentile_x_50
        take_any(x)         -> x               (keeps the column's own name)

    Output names are user-visible, so a near-miss here is a wrong answer.
    """
    if named.name:
        return named.name

    expr = named.expr
    if not isinstance(expr, ir.FunctionCall):
        # A binary or unary expression over aggregates is `Column1`, whatever it
        # contains: `sum(x) + max(x)`, `count() * 2` and `-sum(x)` all measure
        # as `Column1` on the emulator.
        return "Column1"

    spec = lookup_aggregate(expr.name)
    if spec is None:
        # A scalar function wrapping an aggregate takes the *aggregate's* name,
        # not the function's: `round(sum(y), 2)` is `sum_y` and
        # `tostring(count())` is `count_`. Measured, and not the obvious guess.
        inner = _wrapped_aggregate(expr)
        if inner is not None:
            return aggregate_name(ir.NamedExpr(inner))
        return "Column1"

    if spec.name_is_argument:
        if not expr.args:
            return spec.prefix
        return _argument_name(expr.args[0]) or spec.prefix

    if spec.name_ignores_args or not expr.args:
        return f"{spec.prefix}_"

    arg = _argument_name(expr.args[0]) or ""
    name = f"{spec.prefix}_{arg}"

    # percentile carries the percentile itself: percentile_x_50.
    if spec.name == "percentile" and len(expr.args) > 1:
        p = expr.args[1]
        if isinstance(p, ir.Literal) and isinstance(p.value, (int, float)):
            suffix = int(p.value) if float(p.value).is_integer() else p.value
            name = f"{name}_{suffix}"
    return name


def target_names(targets: Sequence[ir.NamedExpr]) -> list[str]:
    """Output names for a ``summarize by`` key list or a ``distinct`` list.

    One rule for both — measured, they agree on every function probed.

    The positional fallback counts **only the targets that need one**:
    `distinct C, tolower(B)` is `C, Column1`, not `C, Column2`. Numbering by
    absolute position, which is what this did, shifted every fallback name
    sitting after a resolvable one.
    """
    out: list[str] = []
    unnamed = 0
    for target in targets:
        name = target.name or _argument_name(target.expr)
        if name is None:
            unnamed += 1
            name = f"Column{unnamed}"
        out.append(name)
    return out


def group_key_name(named: ir.NamedExpr, position: int) -> str:
    """Name for one ``by`` key, ignoring its neighbours.

    Prefer :func:`target_names`, which gets the positional fallback right; this
    remains for the single-key case, where the two agree.
    """
    return target_names([named])[0] if not named.name else named.name


def _render_aggregate_call(expr: ir.FunctionCall) -> str:
    """One aggregate call, `sum(x)` -> `sum("x")`.

    The nesting check lives here because this is the only place an aggregate is
    rendered — putting it in the caller left the plain `summarize sum(sum(x))`
    path uncovered, and the refusal came back from DuckDB at execution instead.
    """
    spec = lookup_aggregate(expr.name)
    if spec is None:  # pragma: no cover - callers check first
        raise KqlUnsupportedError(f"aggregate:{expr.name}")
    nested = next(filter(None, (_nested_aggregate(a) for a in expr.args)), None)
    if nested is not None:
        raise KqlUnsupportedError(
            f"aggregate:{nested}",
            hint=f"{expr.name}() cannot contain another aggregate; Kusto "
            "refuses this too",
        )
    args = [render_expr(a) for a in expr.args]
    try:
        return spec.render(args)
    except ValueError as e:
        raise KqlUnsupportedError(f"aggregate:{expr.name}", hint=str(e)) from None


def _is_aggregate_call(expr: ir.Expr) -> bool:
    return isinstance(expr, ir.FunctionCall) and lookup_aggregate(expr.name) is not None


def _nested_aggregate(expr: ir.Expr) -> str | None:
    """The name of an aggregate anywhere inside *expr*, if there is one."""
    if isinstance(expr, ir.FunctionCall):
        if lookup_aggregate(expr.name) is not None:
            return expr.name
        return next(filter(None, (_nested_aggregate(a) for a in expr.args)), None)
    if isinstance(expr, ir.BinaryOp):
        return _nested_aggregate(expr.left) or _nested_aggregate(expr.right)
    if isinstance(expr, ir.UnaryOp):
        return _nested_aggregate(expr.operand)
    return None


def _lift_aggregates(expr: ir.Expr, *, depth: int = 0) -> tuple[ir.Expr, int]:
    """Replace every aggregate call in *expr* with its SQL. Returns the count.

    Also enforces the two rules Kusto enforces, both measured on the emulator:

    * **An aggregate may not contain another.** `sum(sum(x))` is refused there
      and here; SQL would reject it too, but with a message about nesting rather
      than about KQL.
    * **A column may not appear outside an aggregate.** `sum(x) + x` and
      `strcat(g, tostring(count()))` are refused by Kusto *even when `g` is a
      grouping key* — and that second one is why this check is not left to
      DuckDB, which would happily accept a grouped column and return a result
      the real engine never would.
    """
    if isinstance(expr, ir.FunctionCall) and lookup_aggregate(expr.name) is not None:
        if depth > 0:
            raise KqlUnsupportedError(
                f"aggregate:{expr.name}",
                hint="an aggregate cannot contain another aggregate; Kusto "
                "refuses this too",
            )
        # Not recursed into: inside an aggregate a column is exactly where it
        # belongs, so the rule below does not apply. Nesting is checked by
        # _render_aggregate_call.
        return ir.RenderedAggregate(f"({_render_aggregate_call(expr)})"), 1

    if isinstance(expr, ir.ColumnRef):
        raise KqlUnsupportedError(
            f"summarize: column {expr.name!r} outside an aggregate",
            hint="every column in a summarize expression must be inside an "
            "aggregate function. Kusto refuses this even for a `by` key, so "
            "translating it would accept a query the real engine rejects",
        )

    if isinstance(expr, ir.FunctionCall):
        lifted, total = [], 0
        for arg in expr.args:
            new, found = _lift_aggregates(arg, depth=depth)
            lifted.append(new)
            total += found
        return dataclasses.replace(expr, args=tuple(lifted)), total

    if isinstance(expr, ir.BinaryOp):
        left, a = _lift_aggregates(expr.left, depth=depth)
        right, b = _lift_aggregates(expr.right, depth=depth)
        return dataclasses.replace(expr, left=left, right=right), a + b

    if isinstance(expr, ir.UnaryOp):
        operand, found = _lift_aggregates(expr.operand, depth=depth)
        return dataclasses.replace(expr, operand=operand), found

    return expr, 0


def render_aggregate(named: ir.NamedExpr) -> str:
    """One `summarize` output expression.

    Usually a bare aggregate call, but KQL allows any scalar expression over
    aggregates — `round(sum(Total), 2)`, `sum(x) / count()`,
    `strcat('n=', tostring(count()))` — and so does SQL, which is what makes
    this a matter of rendering the pieces in place rather than a new feature.
    """
    expr = named.expr

    if isinstance(expr, ir.FunctionCall) and lookup_aggregate(expr.name) is not None:
        return _render_aggregate_call(expr)

    if isinstance(expr, ir.FunctionCall) and lookup(expr.name) is None:
        # Neither a known aggregate nor a known scalar function. In summarize
        # position the overwhelmingly likely reading is an aggregate we have not
        # implemented, and saying so beats a message about its arguments.
        raise KqlUnsupportedError(
            f"aggregate:{expr.name}",
            hint="no DuckDB mapping in this wave; see translate/functions.py",
        )

    if not isinstance(expr, (ir.FunctionCall, ir.BinaryOp, ir.UnaryOp)):
        # `summarize x` is not valid KQL; refuse rather than emit a bare column
        # that DuckDB would reject with a confusing group-by error.
        raise KqlUnsupportedError(
            "summarize", hint="each aggregate must be an aggregate function call"
        )

    lifted, found = _lift_aggregates(expr)
    if not found:
        raise KqlUnsupportedError(
            "summarize", hint="each expression must contain an aggregate function"
        )
    return render_expr(lifted)


def render_summarize(op: ir.Summarize, prev: str) -> str:
    """Render ``summarize`` as GROUP BY.

    Grouping keys are emitted **first**, which is the order KQL returns them in
    regardless of where they appear in the query text (R12).
    """
    if not op.aggregates and not op.by:
        raise KqlUnsupportedError("summarize", hint="nothing to aggregate")

    select: list[str] = []
    group: list[str] = []
    key_names = target_names(op.by)
    for i, key in enumerate(op.by):
        sql = render_expr(key.expr)
        select.append(f"{sql} AS {quote_ident(key_names[i])}")
        # Group by the expression itself rather than by output position: an
        # alias is not visible to GROUP BY in every context, and repeating the
        # expression is what DuckDB will fold anyway.
        group.append(sql)

    # Two aggregates can generate the same name (`summarize make_set(y),
    # make_set(y)`). KQL suffixes the later one -- set_y, set_y1 -- with no
    # separator; DuckDB's own de-duplication would produce set_y_1.
    from ..schema import disambiguate

    taken = list(target_names(op.by))
    for agg in op.aggregates:
        name = disambiguate(aggregate_name(agg), taken)
        taken.append(name)
        select.append(f"{render_aggregate(agg)} AS {quote_ident(name)}")

    sql = f"SELECT {', '.join(select)} FROM {prev}"
    if group:
        sql += f" GROUP BY {', '.join(group)}"
    return sql


# ---------------------------------------------------------------------------
# join (R5)
# ---------------------------------------------------------------------------

#: KQL join kind -> SQL join type. `innerunique` is handled separately.
_SQL_JOIN_TYPE = {
    "innerunique": "INNER",
    "inner": "INNER",
    "leftouter": "LEFT",
    "rightouter": "RIGHT",
    "fullouter": "FULL OUTER",
    "leftsemi": "SEMI",
    "rightsemi": "SEMI",
    "leftanti": "ANTI",
    "rightanti": "ANTI",
}


def render_key_equality(keys: Sequence[ir.JoinKey]) -> str:
    """The ``ON`` predicate for `join` and `lookup` keys.

    ``IS NOT DISTINCT FROM``, not ``=``. KQL matches a **null key to a null
    key**; SQL's ``=`` answers NULL and drops the pair. Measured on the
    emulator across every kind:

        datatable(Row:string, Key:int) ["1", 1, "2", int(null)]
        | join kind=leftouter (datatable(Key:int, Alias:string) [int(null), "dnull"])
          on Key

    returns ``dnull`` on the null row, and `leftanti` correspondingly does *not*
    return it. Emitting ``=`` silently loses every null-keyed match — the exact
    class of quiet wrong answer this project exists to prevent.
    """
    return " AND ".join(
        f"_l.{quote_ident(k.left)} IS NOT DISTINCT FROM _r.{quote_ident(k.right)}"
        for k in keys
    )


def render_lookup(
    op: ir.Lookup, prev: str, left_cols: list[str], right_sql: str, right_cols: list[str]
) -> str:
    """Render ``lookup`` (docs/TRANSLATION.md R14).

    Two measured differences from `join` drive this, and both are the kind that
    would otherwise pass a smoke test and be wrong in the details:

    * the default kind is **leftouter**, where `join` defaults to `innerunique`.
      So `lookup` never de-duplicates the left key set — duplicate left rows all
      survive.
    * the right side's **key columns are dropped**, so the output has no ``Key1``.
    """
    sql_type = {"leftouter": "LEFT", "inner": "INNER"}.get(op.kind)
    if sql_type is None:
        raise KqlUnsupportedError(
            f"lookup kind:{op.kind}", hint="lookup supports only leftouter and inner"
        )

    from ..schema import lookup_output_columns  # avoids a cycle

    dropped = {k.right for k in op.keys}
    kept = [c for c in right_cols if c not in dropped]
    out_names = lookup_output_columns(left_cols, right_cols, [k.right for k in op.keys])

    projection = ", ".join(
        [f"_l.{quote_ident(c)} AS {quote_ident(c)}" for c in left_cols]
        + [
            f"_r.{quote_ident(src)} AS {quote_ident(out)}"
            for src, out in zip(kept, out_names[len(left_cols):], strict=True)
        ]
    )
    return (
        f"SELECT {projection} FROM {prev} AS _l "
        f"{sql_type} JOIN ({right_sql}) AS _r ON {render_key_equality(op.keys)}"
    )


def render_join(
    op: ir.Join, prev: str, left_cols: list[str], right_sql: str, right_cols: list[str]
) -> str:
    """Render ``join`` (docs/TRANSLATION.md R5).

    The default kind, **innerunique**, is the most dangerous default in KQL: it
    de-duplicates the *left* key set before joining, so the SQL that looks
    equivalent — a plain INNER JOIN — silently returns more rows. Measured on
    the emulator, a left side with two 'a' rows joined to two right 'a' rows
    gives 2 rows under innerunique and 4 under inner.

    Semi and anti joins return one side's columns only; every other kind returns
    both, with the right side's colliding names suffixed (``k`` -> ``k1``).
    """
    kind = op.kind
    sql_type = _SQL_JOIN_TYPE.get(kind)
    if sql_type is None:
        raise KqlUnsupportedError(f"join kind:{kind}")

    left = prev
    if kind == "innerunique":
        # Keep one left row per key. DISTINCT ON keeps the first row it meets,
        # which matched the emulator's choice on every probe — but neither
        # engine *promises* which row survives, so a left side with duplicate
        # keys and differing other columns is inherently engine-specific.
        keys = ", ".join(quote_ident(k.left) for k in op.keys)
        left = f"(SELECT DISTINCT ON ({keys}) * FROM {prev})"

    on = render_key_equality(op.keys)

    # Anti/semi are directional: KQL's `rightanti` keeps unmatched RIGHT rows,
    # which is DuckDB's ANTI JOIN with the sides swapped.
    if kind in ("rightsemi", "rightanti"):
        body = (
            f"SELECT _r.* FROM ({right_sql}) AS _r "
            f"{sql_type} JOIN {left} AS _l ON {on}"
        )
        return body
    if kind in ("leftsemi", "leftanti"):
        return (
            f"SELECT _l.* FROM {left} AS _l "
            f"{sql_type} JOIN ({right_sql}) AS _r ON {on}"
        )

    projection = ", ".join(
        [f"_l.{quote_ident(c)} AS {quote_ident(c)}" for c in left_cols]
        + [
            f"_r.{quote_ident(src)} AS {quote_ident(out)}"
            for src, out in zip(right_cols, _renamed(left_cols, right_cols), strict=True)
        ]
    )
    return (
        f"SELECT {projection} FROM {left} AS _l "
        f"{sql_type} JOIN ({right_sql}) AS _r ON {on}"
    )


def render_union(
    op: ir.Union,
    prev: str,
    left_cols: list[str],
    query: ir.Query,
    schema: Schema | None,
    leading: bool,
) -> str:
    """Render ``union`` (docs/TRANSLATION.md R15).

    ``UNION ALL BY NAME`` is the whole trick: it matches branches by column name
    and fills the gaps with null, which is exactly Kusto's *outer* union. A
    plain ``UNION ALL`` matches positionally and would pair unrelated columns
    whenever two branches list the same names in a different order.

    ``ALL``, not ``UNION``: Kusto does not de-duplicate. Measured —
    `union UT1, UT1` returns the row twice.

    The final projection is not redundant. Column order is user-visible (R1),
    ``kind=inner`` has to drop the columns BY NAME just added as null, and
    naming the columns explicitly means the order comes from
    `union_output_columns` — measured against the emulator — rather than from
    DuckDB's own rule for what BY NAME puts where.
    """
    from ..schema import surviving_branches, union_output_columns

    out_cols = union_output_columns(op, left_cols, schema)
    let_names = frozenset(name for name, _ in query.lets)

    qualify = _union_qualifies(query, op, leading)
    arms = _left_arms(op, prev, query, schema, leading, let_names, qualify)
    for index, branch in enumerate(surviving_branches(op, schema), start=1):
        arms += _branch_arms(branch, index, schema, let_names, qualify)

    selects = []
    for sql, label in arms:
        projection = "*"
        if op.with_source:
            projection = f"{quote_string(label)} AS {quote_ident(op.with_source)}, *"
        selects.append(f"SELECT {projection} FROM ({sql})")

    body = "\nUNION ALL BY NAME ".join(selects)
    keep = ", ".join(quote_ident(c) for c in out_cols)
    return f"SELECT {keep} FROM ({body})"


def _left_arms(
    op: ir.Union,
    prev: str,
    query: ir.Query,
    schema: Schema | None,
    leading: bool,
    let_names: frozenset[str],
    qualify: bool,
) -> list[tuple[str, str]]:
    """The arms for the union's left side — the query so far.

    `union A, B` and `A | union B` lower to the same IR, so the left side is
    branch 0 either way and gets branch 0's `withsource` label.
    """
    if leading and isinstance(query.source, ir.WildcardTableRef):
        return _wildcard_arms(query.source, schema)
    label = "union_arg0"
    if leading and isinstance(query.source, ir.TableRef):
        label = _table_label(query.source, let_names, qualify) or label
    return [(f"SELECT * FROM {prev}", label)]


def _branch_arms(
    branch: ir.Query,
    index: int,
    schema: Schema | None,
    let_names: frozenset[str],
    qualify: bool,
) -> list[tuple[str, str]]:
    if not branch.operators and isinstance(branch.source, ir.WildcardTableRef):
        return _wildcard_arms(branch.source, schema)
    label = f"union_arg{index}"
    if not branch.operators and isinstance(branch.source, ir.TableRef):
        label = _table_label(branch.source, let_names, qualify) or label
    return [(str(to_sql(branch, schema)), label)]


def _wildcard_arms(
    source: ir.WildcardTableRef, schema: Schema | None
) -> list[tuple[str, str]]:
    """One arm per matched table, each labelled with its own name.

    Measured: `union withsource=Src UT*` reports `UT1` and `UT2`, not one shared
    label for the pattern — so a wildcard cannot be rendered as a single arm.
    """
    from ..schema import match_wildcard

    return [
        (f"SELECT * FROM {_qualified_name(name)}", name.rpartition(".")[2])
        for name in match_wildcard(source, schema)
    ]


def _table_label(
    source: ir.TableRef, let_names: frozenset[str], qualify: bool
) -> str | None:
    """The `withsource` label for a bare table branch, or None for `union_argN`.

    A `let`-bound name is *not* a table and does not get its name: measured,
    `let A = ...; union withsource=Src UT1, A` reports `union_arg1` for the
    second branch.

    **Known residue — the database qualifier.** Measured, Kusto qualifies every
    label as soon as one branch resolves to a database other than the *current*
    one, and leaves them all bare otherwise:

        union withsource=S database('Other').MT   -> database("Other").MT
        union withsource=S database('Current').MT -> MT
        union withsource=S MT, MT2                -> MT, MT2
        union withsource=S MT, database('Other').MT
                    -> database("Current").MT, database("Other").MT

    Reproducing that needs the name of the *current* database, which arrives
    with the connection and not with the query — `to_sql` has no connection at
    all. So labels stay bare, which is right whenever every branch is in the
    current database and is the common case locally. `qualify` is threaded
    through for when that plumbing exists; it is False today.

    This is also why `macro-expand` refuses `withsource=`: there every branch is
    explicitly a *different* database, so Kusto always qualifies and bare labels
    would be identical for every entity — which defeats the point of asking.
    """
    if source.name in let_names:
        return None
    if qualify and source.database:
        return f'database("{source.database}").{source.name}'
    return source.name


def _union_qualifies(query: ir.Query, op: ir.Union, leading: bool) -> bool:
    """Whether this union's labels carry a `database(...)` qualifier.

    Always False: deciding it needs the current database's name, which is a
    property of the connection and not of the query. See `_table_label`.
    """
    return False


def _renamed(left_cols: list[str], right_cols: list[str]) -> list[str]:
    from ..schema import join_output_columns

    return join_output_columns(left_cols, right_cols, "inner")[len(left_cols):]


def _never_null(node: ir.Expr) -> bool:
    """Whether *node* is statically incapable of evaluating to null."""
    return isinstance(node, ir.Literal) and node.value is not None


def _apply_null_semantics(
    rendered: str, when_null: str, operands: Sequence[tuple[ir.Expr, str]]
) -> str:
    """Give *rendered* KQL's null behaviour rather than SQL's (R4).

    KQL's equality, membership and string-matching operators are **total**: a
    null operand makes the positive form false and the negated form true. SQL
    answers NULL to both, and `where` drops the row — so `| where s !contains
    "x"` silently loses every null row instead of keeping it.

    The trap is that null-on-*both*-sides is the one case KQL leaves NULL
    (`a == b` with both null is null, not false), so a blanket ``coalesce``
    would trade one wrong answer for another on `where a != b`. When an operand
    is a literal that cannot be null that case is unreachable and the cheap form
    is exact — which is nearly every real predicate, so the generated SQL stays
    readable. Otherwise the comparison is guarded.

    All of this is measured on the emulator (`tests/test_null_semantics.py`),
    not read off the documentation.
    """
    if any(_never_null(expr) for expr, _ in operands):
        return f"coalesce({rendered}, {when_null})"
    both_null = " AND ".join(f"{sql} IS NULL" for _, sql in operands)
    return f"CASE WHEN {both_null} THEN NULL ELSE coalesce({rendered}, {when_null}) END"


def _in_result(sql: str, node: ir.InList, value: str) -> str:
    """``in`` / ``!in`` with KQL's null behaviour rather than SQL's (R4).

    Verified on the emulator for all three forms — a literal list, a
    ``dynamic([...])`` array and a subquery behave identically: a null left
    operand makes ``in`` false and ``!in`` **true**, where SQL leaves both NULL
    and `where` drops the row. Unlike ``==``, membership has no symmetric
    both-null case to preserve, so the plain coalesce is exact.
    """
    rendered = f"(NOT {sql})" if node.negated else sql
    if _never_null(node.value):
        return rendered
    return f"coalesce({rendered}, {'TRUE' if node.negated else 'FALSE'})"


def render_in_list(node: ir.InList) -> str:
    """``x in (a, b, ...)`` and its ``!in`` / ``in~`` variants.

    The ``~`` suffix is case-INsensitive (R2), matching `=~` rather than `==`.
    Lowering both sides keeps the comparison symmetric — comparing a lowered
    column against un-lowered literals would silently miss matches.

    A membership test against strings is a **string context**, so a `dynamic`
    left operand is unwrapped first — see :func:`_in_string_context`. Measured:
    `s in ("x")` over ``dynamic(["x"])`` is true in Kusto and crashed here.

    **Not** for the subquery form. The guard renders the whole test twice, and
    DuckDB flattens `IN (subquery)` into a semi-join whose key is computed for
    every row — so the unreached branch's ``json_type()`` ran anyway and
    `State in~ (…)` died on `Malformed JSON … "PENNSYLVANIA"`. Coercing only
    the left operand instead would keep one subquery but break the typed cases
    the guard exists to protect (`ts in (T)` must still compare datetimes), so
    the subquery form keeps today's behaviour and the residue is recorded.
    """
    string_context = node.subquery is None and (
        node.case_insensitive
        or any(_is_string_expr(i) or _is_dynamic(i) for i in node.items)
    )
    if string_context:
        return _in_string_context(
            ((node.value, render_expr(node.value)),),
            lambda sqls: _render_in_list(node, sqls[0]),
        )
    return _render_in_list(node, render_expr(node.value))


def _render_in_list(node: ir.InList, value: str) -> str:
    if node.subquery is not None:
        # KQL tests against the subquery's **first column** and ignores the
        # rest — measured: `x in (T)` where T is `(k, v)` matches on `k`, and a
        # value of the wrong type for `k` is an error (SEM0025) rather than a
        # fallback to `v`. `SELECT *` sent every column and DuckDB refused with
        # "Subquery returns 2 columns - expected 1", which only ever surfaced
        # once `top` made `T | summarize … | top 5 by …` translatable.
        #
        # The alias list picks the first column **without needing the schema**:
        # DuckDB accepts fewer aliases than there are columns and applies them
        # left to right, so `AS _q(_c1)` names column one whatever it is called.
        inner = f"SELECT _c1 FROM ({to_sql(node.subquery)}) AS _q(_c1)"
        if node.case_insensitive:
            # Lower BOTH sides: comparing a lowered value against un-lowered
            # rows would silently miss matches.
            value = f"lower({value})"
            inner = (
                f"SELECT lower(CAST(_c1 AS VARCHAR)) "
                f"FROM ({to_sql(node.subquery)}) AS _q(_c1)"
            )
        sql = f"({value} IN ({inner}))"
        return _in_result(sql, node, value)

    # `x in (dynamic([...]))` tests membership in an ARRAY, not equality with
    # one value — a plain IN would compare the value against the whole array.
    if len(node.items) == 1 and _is_dynamic(node.items[0]):
        arr = f"CAST({render_expr(node.items[0])} AS VARCHAR[])"
        needle = f"CAST({value} AS VARCHAR)"
        if node.case_insensitive:
            arr = f"list_transform({arr}, v -> lower(v))"
            needle = f"lower({needle})"
        sql = f"list_contains({arr}, {needle})"
        return _in_result(sql, node, needle)

    items = [render_expr(i) for i in node.items]
    if node.case_insensitive:
        value = f"lower({value})"
        items = [f"lower({i})" for i in items]
    sql = f"({value} IN ({', '.join(items)}))"
    return _in_result(sql, node, value)


def render_has_list(node: ir.HasList) -> str:
    """``x has_any (a, b, ...)`` / ``x has_all (...)`` (R3).

    These share the `in` family's *grammar* but none of its semantics: each item
    is a `has` needle, matched as a whole **term**, case-insensitively. Measured
    on the emulator — ``"errors" has_any ("error")`` is **false**, exactly as
    ``has`` is, where an `in`-style equality test would be a different question
    entirely.

    So `has_any` is an OR of term matches and `has_all` an AND, sharing one term
    definition with `has` via :func:`term_match_sql`.

    Nulls follow `has`: a null left operand makes both **false** (measured), so
    the result is coalesced rather than left as SQL's NULL.
    """
    if node.subquery is not None:
        # See render_in_list: guarding around a subquery duplicates it and
        # defeats the guard, because DuckDB computes the join key eagerly.
        return _render_has_list(node, render_expr(node.value))
    return _in_string_context(
        ((node.value, render_expr(node.value)),),
        lambda sqls: _render_has_list(node, sqls[0]),
    )


def _render_has_list(node: ir.HasList, value: str) -> str:
    joiner = " AND " if node.require_all else " OR "

    if node.subquery is not None:
        return _render_has_subquery(node, node.subquery, value)

    parts = []
    for item in node.items:
        if _is_dynamic(item):
            unrolled = _unrolled_needles(node, value, item)
            if unrolled is not None:
                parts.append(unrolled)
                continue
            # A list whose needles are only known at run time — a column, a
            # bound parameter, or a literal array this cannot read (see
            # `_literal_needles`). One `list_filter`, and the term pattern is
            # rebuilt per row because the lambda variable is not a constant.
            arr = f"CAST({render_expr(item)} AS VARCHAR[])"
            test = term_match_sql(value, "t")
            matched = f"len(list_filter({arr}, t -> {test}))"
            parts.append(f"({matched} = len({arr}))" if node.require_all
                         else f"({matched} > 0)")
        elif isinstance(item, ir.Literal) and item.kind == "string":
            parts.append(
                term_match_sql(value, quote_string(str(item.value)),
                               needle_text=str(item.value))
            )
        else:
            parts.append(term_match_sql(value, f"CAST({render_expr(item)} AS VARCHAR)"))

    if not parts:
        # `has_any ()` cannot be written — the grammar needs at least one item.
        raise KqlUnsupportedError("has_all" if node.require_all else "has_any")

    sql = f"({joiner.join(parts)})"
    return _has_list_result(sql, node)


def _render_has_subquery(node: ir.HasList, subquery: ir.Query, value: str) -> str:
    """``x has_any (T)`` / ``x has_all (T)`` — a **tabular** right-hand side.

    KQL tests against the subquery's first column. The needles are real runtime
    data here, so the pattern cannot be folded the way `_unrolled_needles` folds
    a literal array, and RE2 recompiles it for every *(row, needle)* pair.

    Two things make that affordable:

    * **`EXISTS`, not an aggregate.** ``bool_or`` over a correlated scalar
      subquery visits every pair; `EXISTS` stops at the first hit and DuckDB
      turns it into a semi-join.
    * **A substring prefilter.** A whole-term match implies a case-insensitive
      substring match, so ``contains`` is a sound cheap gate in front of the
      regex — and it rejects almost every pair without compiling anything.

    Measured together on the corpus fixture: 3415ms -> 45ms, same 44 groups.

    It also **fixes** the degenerate case. ``has_all`` over an empty subquery is
    true on the emulator, matching ``has_all (dynamic([]))``; the old
    ``bool_and`` over zero rows gave NULL and the coalesce turned it into false.
    ``NOT EXISTS`` over nothing is true, which is the measured answer.
    """
    inner = str(to_sql(subquery))
    needle = "CAST(_needle AS VARCHAR)"
    # Sound because `has` implies `contains`, case-insensitively (R3).
    prefilter = f"contains(lower({value}), lower({needle}))"
    matched = f"({prefilter} AND {term_match_sql(value, needle)})"
    source = f"({inner}) AS _q(_needle)"
    if not node.require_all:
        return _has_list_result(f"EXISTS (SELECT 1 FROM {source} WHERE {matched})", node)
    # "every needle matches" is "no needle fails". The left operand has to be
    # tested separately: a null haystack finds no failures, so NOT EXISTS would
    # answer true where KQL answers false (R4).
    return _has_list_result(
        f"({value} IS NOT NULL AND NOT EXISTS "
        f"(SELECT 1 FROM {source} WHERE NOT coalesce({matched}, TRUE)))",
        node,
    )


#: The `has` family, and whether each is case-sensitive and negated. Only these
#: build a term pattern; the rest of the string operators are LIKE-based.
_TERM_OPERATORS = {
    "has": (False, False),
    "!has": (False, True),
    "has_cs": (True, False),
    "!has_cs": (True, True),
}


def _render_term_operator(node: ir.BinaryOp, sqls: Sequence[str]) -> str | None:
    """`has` against a **literal** needle, with the term pattern folded.

    Returns ``None`` for anything else, leaving the operator table's template —
    which builds the pattern from two ``CASE`` expressions — to handle a needle
    that is genuinely unknown until the query runs.

    Worth the special case because the folded form is a constant RE2 compiles
    once: measured on the 5,000-row corpus fixture, `Text has 'x'` went from
    51ms to 15ms. The same fold inside `has_any`'s list is worth far more; see
    :func:`_unrolled_needles`.
    """
    spec = _TERM_OPERATORS.get(node.op)
    if spec is None:
        return None
    if not (isinstance(node.right, ir.Literal) and node.right.kind == "string"):
        return None
    case_sensitive, negated = spec
    matched = term_match_sql(
        sqls[0],
        quote_string(str(node.right.value)),
        case_sensitive=case_sensitive,
        needle_text=str(node.right.value),
    )
    return f"NOT {matched}" if negated else matched


def _literal_needles(item: ir.Expr) -> list[str | None] | None:
    """The needles of ``dynamic([...])`` when they can be read at translation time.

    ``None`` — meaning "leave it to run time" — unless the item is a **literal**
    array whose every element is a string or a JSON null. Numbers and booleans
    are excluded on purpose: DuckDB's ``JSON -> VARCHAR[]`` cast renders them
    (`1`, `true`, `1.5`) and reproducing that formatting here would be a second
    implementation to keep in step, for needles nobody writes. An empty array is
    excluded too, so the ``len(...) = len(...)`` shape keeps deciding the
    degenerate `has_all (dynamic([]))`, which is **true**.
    """
    if not (isinstance(item, ir.Literal) and item.kind == "dynamic"):
        return None
    try:
        value = json.loads(str(item.value))
    except (TypeError, ValueError):
        return None
    if not isinstance(value, list) or not value:
        return None
    if not all(v is None or isinstance(v, str) for v in value):
        return None
    return list(value)


def _unrolled_needles(node: ir.HasList, value: str, item: ir.Expr) -> str | None:
    """``has_any``/``has_all`` over a literal array, one constant test per needle.

    **This is the difference between 28 milliseconds and 28 seconds.** The
    run-time form puts the term test inside ``list_filter(arr, t -> …)``, where
    the regex pattern is concatenated from the lambda variable — so RE2 has no
    constant to compile and rebuilds the pattern for every *(row, needle)* pair.
    On the 5,000-row corpus fixture, `has_all` over four needles meant ~60,000
    regex compilations and 28.6 **seconds**; unrolled it is 28.6 milliseconds,
    for identical results. It was 98% of the acceptance suite's query time.

    A **null** needle matches anything, exactly as `has ""` does. Measured:
    ``'x' has_any (dynamic(['a', null]))`` is true on the emulator and was false
    here, because ``list_filter`` drops a null predicate rather than keeping the
    row — a silent wrong answer that only became visible while unrolling.

        has_any (dynamic([null]))          all rows
        has_any (dynamic(['a', null]))     all rows
        has_all (dynamic(['a', null]))     the rows matching 'a'
        has_all (dynamic([null]))          all rows
    """
    needles = _literal_needles(item)
    if needles is None:
        return None
    if not node.require_all and any(n is None for n in needles):
        return "TRUE"
    present = [n for n in needles if n is not None]
    if not present:
        return "TRUE"  # `has_all` of nothing but nulls
    tests = [
        term_match_sql(value, quote_string(n), needle_text=n) for n in present
    ]
    joiner = " AND " if node.require_all else " OR "
    return f"({joiner.join(tests)})"


def _has_list_result(sql: str, node: ir.HasList) -> str:
    """Give `has_any`/`has_all` KQL's null behaviour rather than SQL's (R4).

    Measured: a null (or empty) left operand makes both false, matching `has`
    rather than leaving NULL for `where` to drop.
    """
    if _never_null(node.value):
        return sql
    return f"coalesce({sql}, FALSE)"


def _is_dynamic_expr(node: ir.Expr) -> bool:
    """Whether an expression statically yields a dynamic value."""
    if isinstance(node, ir.PathAccess):
        return True
    return _is_dynamic(node)


def _is_dynamic(node: ir.Expr) -> bool:
    if isinstance(node, (ir.Literal, ir.Parameter)):
        return node.kind == "dynamic"
    if isinstance(node, ir.FunctionCall):
        return node.name.lower() in ("parse_json", "todynamic", "pack_array")
    return False


#: Binary operators whose operands are **always** strings in KQL — the emulator
#: refuses `long_col contains "1"` (SEM0709) and `dt =~ "x"` (SEM0065). A
#: `dynamic` reaching one of these is coerced to its string form, so these are
#: the operators where an unwrap is unconditionally right.
_STRING_OPERATORS = frozenset(
    {"=~", "!~", "matches regex"}
    | {
        f"{neg}{op}{cs}"
        for op in ("contains", "has", "startswith", "endswith")
        for neg in ("", "!")
        for cs in ("", "_cs")
    }
)

#: Functions whose result is a string, for deciding whether `==` is a string
#: comparison. Deliberately short: over-claiming here would stringify an
#: equality that should stay typed, which is the regression `datetime_col ==
#: "2020-01-01"` would suffer (Kusto coerces the *literal* to datetime there,
#: and so does DuckDB today).
_STRING_RETURNING = frozenset(
    {"tostring", "strcat", "strcat_delim", "strcat_array", "tolower", "toupper",
     "substring", "replace_string", "trim", "trim_start", "trim_end",
     "format_datetime", "format_timespan", "strrep", "reverse"}
)


def _is_string_expr(node: ir.Expr) -> bool:
    if isinstance(node, (ir.Literal, ir.Parameter)):
        return node.kind == "string"
    if isinstance(node, ir.FunctionCall):
        return node.name.lower() in _STRING_RETURNING
    return False


def _is_string_context(node: ir.BinaryOp) -> bool:
    """Whether *node*'s operands are compared **as strings**.

    Two cases. The `contains`/`has`/`startswith` family always is. Equality is
    polymorphic — measured, `datetime_col == "2020-01-01"` coerces the literal
    to a datetime and answers true — so it counts only when the other side is
    visibly a string, which is when a `dynamic` on this side must be unwrapped.
    """
    if node.op in _STRING_OPERATORS:
        return True
    if node.op in ("==", "!=", "<>"):
        return _is_string_expr(node.left) or _is_string_expr(node.right)
    return False


def _may_be_dynamic(node: ir.Expr) -> bool:
    """Whether *node* could turn out to be a `dynamic` at run time.

    A bare column: the schema carries names, not types, so `mv-expand`'s output
    is indistinguishable here from a `string` column. Everything else — a
    literal, a bound parameter, a call with a known mapping — is decided
    statically by :func:`_is_dynamic_expr` and needs no run-time guard.
    """
    return isinstance(node, ir.ColumnRef)


def _in_string_context(
    operands: Sequence[tuple[ir.Expr, str]],
    build: Callable[[Sequence[str]], str],
    index: int = 0,
) -> str:
    """Render ``build`` with each operand seen as KQL sees it in a string context.

    KQL coerces a `dynamic` to its **unwrapped** text before any string
    operator, so `s == "x"` over `dynamic(["x"])` is true and ``k contains s``
    finds `x`, not `"x"`. Rendering the comparison against the raw JSON instead
    is wrong in two directions at once: `s == "x"` crashed (DuckDB casts the
    literal to JSON, and `x` is not JSON) while `s startswith "x"` quietly
    answered **false**, the quote getting in the way.

    A column's type is unknown here, so an unwrap cannot simply be emitted: a
    `datetime` column compared to a string must keep comparing as a datetime.
    The discrimination is therefore left to DuckDB, which does know — one
    ``typeof(...) = 'JSON'`` guard per unresolved operand, with the whole
    operator rendered on each side of it. Only the taken branch evaluates, so
    the JSON cast that used to crash is never reached; and a branch that cannot
    even *bind* keeps its refusal, which is how `long_col contains "1"` stays
    an error rather than becoming a string match Kusto rejects.
    """
    if index == len(operands):
        return build([sql for _, sql in operands])

    expr, sql = operands[index]
    if _is_dynamic_expr(expr):
        # Statically a dynamic: unwrap outright, no branch needed.
        resolved = list(operands)
        resolved[index] = (expr, _unwrap_json_scalar(sql))
        return _in_string_context(resolved, build, index + 1)
    if not _may_be_dynamic(expr):
        return _in_string_context(operands, build, index + 1)

    unwrapped = list(operands)
    unwrapped[index] = (expr, _unwrap_json_scalar(sql))
    return (
        f"CASE WHEN typeof({sql}) = 'JSON' "
        f"THEN {_in_string_context(unwrapped, build, index + 1)} "
        f"ELSE {_in_string_context(operands, build, index + 1)} END"
    )


#: Function name -> the argument positions KQL reads as **strings**, for the
#: functions the emulator *accepts* over a `dynamic`. It refuses several
#: neighbours — `countof`, `extract`, `replace_string`, `trim`, `url_encode`
#: all answer SEM02xx — so this is an allow-list of measured behaviour rather
#: than a guess at which functions "look stringy".
#:
#: Unlike the operators, these need no ``typeof`` branch around the whole call:
#: the argument is a string either way, so routing it through
#: :func:`render_kql_tostring` — which already dispatches on `typeof` — is
#: exactly right, and leaves a `string` column rendering as itself.
_STRING_ARG_POSITIONS: dict[str, tuple[int, ...]] = {
    "strlen": (0,),
    "toupper": (0,),
    "tolower": (0,),
    "substring": (0,),
    "split": (0,),
    "indexof": (0, 1),
    # `isempty`/`isnotempty` ask whether the *string form* is empty, so a
    # dynamic goes through the same unwrap: measured, `isempty(dynamic(''))` is
    # true while `isempty(dynamic([]))` is false — the JSON text `[]` is not
    # empty, and the difference only appears once the quoting is gone.
    "isempty": (0,),
    "isnotempty": (0,),
}


def _render_arg(name: str, index: int, node: ir.Expr) -> str:
    """Render one function argument, as a string where KQL reads one."""
    positions = _STRING_ARG_POSITIONS.get(name.lower())
    if positions and index in positions and (
        _is_dynamic_expr(node) or _may_be_dynamic(node)
    ):
        return render_kql_tostring(node)
    return render_expr(node)


def render_range(source: ir.RangeSource) -> str:
    """``range name from start to stop step step``.

    Both endpoints are **inclusive**, which is what ``generate_series`` gives
    (``range`` in DuckDB excludes the stop, so the near-identical name is a
    trap). A backwards range yields no rows in both engines.

    A **numeric** range casts its bounds to BIGINT. ``generate_series`` is
    overloaded on exact types and has no HUGEINT form, so an expression that
    merely widens made the whole query fail to bind — `array_length(x) - 1` did,
    because DuckDB's json_array_length returns UBIGINT.

    A **temporal** range must not be cast: `generate_series(TIMESTAMP,
    TIMESTAMP, INTERVAL)` is its own overload, and casting a datetime to BIGINT
    does not even convert. So the cast is skipped the moment any bound is
    visibly temporal — a `range` over dates steps by a timespan, which makes
    that easy to see.
    """
    bounds = [source.start, source.stop, source.step]
    temporal = any(
        _is_datetime_expr(e) or _is_timespan_expr(e) for e in bounds
    )
    rendered = ", ".join(
        render_expr(e) if temporal else f"CAST({render_expr(e)} AS BIGINT)"
        for e in bounds
    )
    return (
        f"SELECT UNNEST(generate_series({rendered})) AS {quote_ident(source.name)}"
    )


# ---------------------------------------------------------------------------
# dynamic / JSON (R9)
# ---------------------------------------------------------------------------


def render_path(node: ir.PathAccess) -> str:
    """``d.a``, ``d[0]``, ``d['a']`` — navigation into a dynamic value.

    **A missing property or an out-of-range index is null, never an error**
    (R9), which is what ``json_extract`` already does — so the mapping is a
    good one *provided* the path is built correctly.

    KQL indexes from the end with a negative index (``d[-1]`` is the last
    element). DuckDB spells that ``$[#-1]``, so a bare ``$[-1]`` would silently
    return null instead of the last element.
    """
    base = render_expr(node.base)

    # A fully static path can be one json_extract call.
    static = _static_path(node.steps)
    if static is not None:
        return f"json_extract({base}, {quote_string(static)})"

    # A runtime index has to build its own path fragment.
    sql = base
    for step in node.steps:
        if step.name is not None:
            sql = f"json_extract({sql}, {quote_string('$.' + _json_path_key(step.name))})"
        else:
            assert step.index is not None  # a step is a name or an index
            idx = render_expr(step.index)
            frag = (
                f"'$[' || CASE WHEN {idx} < 0 THEN '#' || CAST({idx} AS VARCHAR) "
                f"ELSE CAST({idx} AS VARCHAR) END || ']'"
            )
            sql = f"json_extract({sql}, {frag})"
    return sql


def _static_path(steps: tuple[ir.PathStep, ...]) -> str | None:
    """The whole path as one JSON-path string, or ``None`` if any step is dynamic.

    Returning ``None`` rather than testing the steps twice keeps the "every step
    is static" condition in one place — the place that relies on it.
    """
    path = "$"
    for step in steps:
        if step.name is not None:
            path += f".{_json_path_key(step.name)}"
            continue
        index = _static_index(step.index)
        if index is None:
            return None
        # KQL indexes from the end with a negative index; DuckDB spells that
        # `$[#-1]`, and a bare `$[-1]` silently returns null instead.
        path += f"[{'#' + str(index) if index < 0 else index}]"
    return path


def _static_index(expr: ir.Expr | None) -> int | None:
    if isinstance(expr, ir.Literal) and isinstance(expr.value, int):
        return expr.value
    if isinstance(expr, ir.UnaryOp) and expr.op == "-":
        inner = _static_index(expr.operand)
        return None if inner is None else -inner
    return None


def _json_path_key(name: str) -> str:
    """Quote a property name if it is not a bare JSON-path identifier."""
    if name.isidentifier():
        return name
    escaped = name.replace('"', '\\"')
    return f'"{escaped}"'


#: The struct one `regexp_extract` produces, holding every captured column.
_PARSE_CAPTURES = "_parse"


def render_parse(op: ir.Parse, prev: str, cols: list[str] | None = None) -> str:
    """``parse Expression with <pattern>`` and ``parse-where``.

    The whole pattern becomes **one** regex with a named group per declared
    column, matched once per row by ``regexp_extract(s, pattern, [names])``,
    which answers a struct. Each column is then a field of that struct with its
    conversion applied.

    Three of DuckDB's behaviours make this an unusually close fit, and all three
    are load-bearing rather than convenient:

    * a **non-match returns the empty string in every field**, which is exactly
      what Kusto gives an unmatched *string* column — measured, `'nomatch'`
      parsed with `"a=" a` yields `''`, not null;
    * RE2's inline ``(?i)``/``(?s)``/``(?m)``/``(?U)`` are Kusto's flags, and
      RE2's bare ``$`` means end-of-*text* (not "before a final newline", the
      way Python and .NET read it), which is what `kind=simple` needs;
    * ``regexp_matches`` over the same pattern gives `parse-where` its
      predicate.

    See :func:`_parse_pattern` for how the pattern is built — the difference
    between `kind=simple` and `kind=regex` lives almost entirely there — and
    :func:`_parse_row_ok` for the all-or-nothing conversion rule, which is the
    part that is not guessable.
    """
    if op.kind not in ("simple", "relaxed", "regex"):
        raise KqlUnsupportedError(
            f"parse kind={op.kind}", hint="expected simple, regex or relaxed"
        )
    if op.kind == "relaxed" and op.drop_unmatched:
        # Kusto refuses this outright — SEM0477, "parse-where: only simple or
        # regex modes are supported" — so accepting it would answer a query a
        # cluster rejects.
        raise KqlUnsupportedError(
            "parse-where kind=relaxed",
            hint="Kusto refuses it too (SEM0477): parse-where supports only "
            "simple and regex",
        )
    _parse_flags(op)

    source = render_expr(op.expression)
    declared = [s for s in op.segments if s.name]
    names = [s.name for s in declared]
    if len(set(names)) != len(names):
        # Kusto refuses a repeated declaration; DuckDB would silently keep one.
        raise KqlUnsupportedError("parse", hint="a column is declared twice")
    _refuse_empty_literal(op)
    _refuse_unanchored_string_column(op)

    pattern = quote_string(_parse_pattern(op))
    if names:
        name_list = ", ".join(quote_string(str(n)) for n in names)
        captures = f"regexp_extract({source}, {pattern}, [{name_list}])"
    else:
        # `regexp_extract` refuses an empty name list, and a pattern that
        # declares nothing is only a filter anyway.
        captures = "NULL"

    ok = _parse_row_ok(op, source, pattern)
    rendered = {
        str(s.name): _parse_column(s, ok) for s in declared
    }
    if cols is not None:
        from ..schema import replacing

        select = [rendered.get(c, quote_ident(c)) for c in cols]
        select += [rendered[n] for n in replacing(cols, list(rendered)) if n not in cols]
    else:
        # Without a schema a declared name may or may not already exist, and
        # `EXCLUDE` errors in the case it does not. `COLUMNS(...)` filters
        # dynamically and is correct either way — but it appends, so a replaced
        # column moves to the end. Same residual divergence as `extend`.
        excluded = ", ".join(quote_string(str(n)) for n in names)
        keep = f"COLUMNS(x -> x NOT IN ({excluded}))" if names else "*"
        select = [keep, *rendered.values()]

    inner = f"SELECT *, {captures} AS {quote_ident(_PARSE_CAPTURES)} FROM {prev}"
    sql = f"SELECT {', '.join(select)} FROM ({inner}) AS _p"
    if op.drop_unmatched and ok is not None:
        sql += f" WHERE {ok}"
    return sql


#: Hyphenated, bare, or braced — all three answered on the emulator, and the
#: braces are an **alternation** rather than an optional `\{?…\}?` pair on
#: purpose: under `flags=U` RE2 reads `?` as lazy, the closing brace would be
#: dropped, and `TRY_CAST('{74be…3642' AS UUID)` is null. Alternation is
#: greediness-neutral, so `U` cannot break the pair apart.
_GUID_SHAPE = (
    r"\s*(?:\{[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}"
    r"-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}\}"
    r"|[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}"
    r"-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12})"
)

#: The two date orders are separate branches because the separator picks the
#: order: `2020-01-01` and `02/17/2016 08:40:01` both answer, and
#: `2020/01/01T00:00:00` — the ISO order with slashes — answers **null**. A
#: single `[-/]` branch would accept it and quietly disagree. Years are four
#: digits with `-` and one to four with `/`, also measured (`20-01-01T00:00:00`
#: is null, `1/2/34` is 2034).
_DATETIME_SHAPE = (
    r"\s*(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{1,4})"
    r"(?:[Tt ]\s*\d{1,2}:\d{1,2}(?::\d{1,2}(?:\.\d*)?)?)?"
    r"(?:[Zz]|[-+]\d{1,2}(?::\d{1,2})?)?"
)

#: `hh:mm[:ss]`, or `d.hh:mm:ss` where the seconds stop being optional.
#: Measured: `00:01` answers, `1.02:03` does not, `1d` and `3` never do — so the
#: suffix forms `totimespan` accepts are not part of the shape.
_TIMESPAN_SHAPE = (
    r"\s*-?(?:\d+\.\d{1,2}:\d{1,2}:\d{1,2}|\d{1,2}:\d{1,2}(?::\d{1,2})?)"
    r"(?:\.\d*)?"
)


#: `name: T` compiles to a **type-shaped** capture, not a wildcard. Measured by
#: putting the column before a `*` — which lets the capture take as much as its
#: shape allows — and reading back what it took:
#:
#:     v: long  over 'v=  12abc'   ->  12     leading space ok, '+ 12' is not
#:     v: real  over 'v=1e3abc'    ->  1.0    the exponent is NOT part of it
#:     v: bool  over 'v=2x'        ->  true   a number counts, 'yes' does not
#:     v: guid  over 'v=74BE…3642x'->  the guid, hyphenated or not
#:
#: This is *why* Kusto refuses `*` after a **string** column and allows it after
#: a typed one (SEM0476): a typed capture knows where to stop on its own, and a
#: string capture only knows "up to the next literal".
_PARSE_TYPE_PATTERNS = {
    "long": r"\s*[-+]?\d+",
    "int": r"\s*[-+]?\d+",
    "int32": r"\s*[-+]?\d+", "int64": r"\s*[-+]?\d+",
    "uint": r"\s*[-+]?\d+", "uint32": r"\s*[-+]?\d+", "uint64": r"\s*[-+]?\d+",
    "ulong": r"\s*[-+]?\d+", "int8": r"\s*[-+]?\d+", "uint8": r"\s*[-+]?\d+",
    "int16": r"\s*[-+]?\d+", "uint16": r"\s*[-+]?\d+",
    # No exponent: `1e3` captured `1`, so `e` terminates the shape.
    "real": r"\s*[-+]?(?:\d+\.?\d*|\.\d+)",
    "double": r"\s*[-+]?(?:\d+\.?\d*|\.\d+)",
    "float": r"\s*[-+]?(?:\d+\.?\d*|\.\d+)",
    "decimal": r"\s*[-+]?(?:\d+\.?\d*|\.\d+)",
    "bool": r"\s*(?:[tT][rR][uU][eE]|[fF][aA][lL][sS][eE]|[-+]?\d+)",
    "boolean": r"\s*(?:[tT][rR][uU][eE]|[fF][aA][lL][sS][eE]|[-+]?\d+)",
    "guid": _GUID_SHAPE,
    "uuid": _GUID_SHAPE,
}


#: `kind=regex` shapes **every** typed column, not only the one before a `*`,
#: and the shapes are not the same ones — see `_PARSE_TYPE_PATTERNS`. Measured
#: by *boundary scan* rather than by guessing: for every way of splitting a
#: value into `<capture><literal>`, ask the emulator whether the pattern still
#: answers, and the splits that do are exactly the prefixes the shape matches.
#:
#: The scan is what makes these exact rather than plausible. Over `27xy` a
#: `long` answers at cuts 1, 2 and 4 and not 3, which is `\s*[-+]?\d+` and
#: nothing else; over `2020-01-01T00:00:00Z` a `datetime` answers at 10, 15, 16,
#: 18, 19 and 20 — so the date alone is enough, minutes may be one digit,
#: seconds are optional, and a bare `:` is not a stopping point.
#:
#: Two differences from the simple-mode table are worth naming, because both
#: are silent if got wrong:
#:
#: * `bool` here is **true/false only** — `q=12` answers null in regex mode and
#:   true in simple;
#: * `real` here has **no leading dot** — `.5` answers null in regex mode and
#:   0.5 in simple.
_PARSE_REGEX_TYPE_PATTERNS = {
    "long": r"\s*[-+]?\d+",
    "int": r"\s*[-+]?\d+",
    "int32": r"\s*[-+]?\d+", "int64": r"\s*[-+]?\d+",
    "uint": r"\s*[-+]?\d+", "uint32": r"\s*[-+]?\d+", "uint64": r"\s*[-+]?\d+",
    "ulong": r"\s*[-+]?\d+", "int8": r"\s*[-+]?\d+", "uint8": r"\s*[-+]?\d+",
    "int16": r"\s*[-+]?\d+", "uint16": r"\s*[-+]?\d+",
    # No bare trailing dot: measured, `1.` before a literal answers null where
    # `1.5` answers 1.5, so the shape stops at `1` and the `.` has to be matched
    # by whatever follows.
    "real": r"\s*[-+]?\d+(?:\.\d+)?",
    "double": r"\s*[-+]?\d+(?:\.\d+)?",
    "float": r"\s*[-+]?\d+(?:\.\d+)?",
    "decimal": r"\s*[-+]?\d+(?:\.\d+)?",
    "bool": r"\s*(?i:true|false)",
    "boolean": r"\s*(?i:true|false)",
    "guid": _GUID_SHAPE,
    "uuid": _GUID_SHAPE,
    "datetime": _DATETIME_SHAPE,
    "date": _DATETIME_SHAPE,
    "timespan": _TIMESPAN_SHAPE,
    "time": _TIMESPAN_SHAPE,
}


def _parse_regex_capture(op: ir.Parse, index: int) -> str:
    """One column's fragment under `kind=regex`: a shape, or a greedy wildcard.

    Greedy and not lazy — measured, `"a" v "c"` over `aXcYc` gives `XcY` in
    regex mode against `X` in simple. `flags=U` inverts it, which is why the
    quantifier is written in its ordinary sense and left to `(?U)`.
    """
    segment = op.segments[index]
    if segment.type in (None, "string", "dynamic"):
        return ".*"
    shape = _PARSE_REGEX_TYPE_PATTERNS.get(str(segment.type))
    if shape is None:
        raise KqlUnsupportedError(
            f"parse kind=regex type:{segment.type}",
            hint="the text this type's capture matches has not been measured; "
            f"known: {sorted(_PARSE_REGEX_TYPE_PATTERNS)}",
        )
    if str(segment.type) in _PARSE_TEMPORAL:
        # The two temporal shapes hold up everywhere the sweep can see them
        # *except* two positions, and in both the emulator stops behaving like a
        # regex engine at all.
        #
        # At the very end of the pattern, with nothing after the column:
        #
        #     'v=2020/01/01T00:00:00'    ->  null
        #     'v=2020/01/01T00:00:00X'   ->  2020-01-01T00:00:00Z
        #
        # A trailing `X` in the *data* cannot make a shape match more, so
        # whatever runs there is not one; three other forms flip the same way
        # (a bare date, `hh:mm` without seconds, and a UTC offset), and a
        # `timespan` in that position answers null for every value tried.
        #
        # Under `flags=U`, where every other shape simply inverts, the temporal
        # ones answer null instead — `02/17/2016 08:40:01` under `U` is null on
        # the emulator and would be `0002-02-17` from a lazily-read shape.
        if _ends_the_pattern(op, index):
            raise KqlUnsupportedError(
                f"parse kind=regex type:{segment.type} at the end of the pattern",
                hint="Kusto's answer here does not follow from the capture — a "
                "trailing character in the data changes it — so put a literal "
                "or a '*' after the column, where the behaviour is measured",
            )
        if op.flags and "U" in op.flags:
            raise KqlUnsupportedError(
                f"parse kind=regex flags=U type:{segment.type}",
                hint=f"`U` inverts every quantifier, but a {segment.type} "
                "capture does not invert with them — it stops answering "
                "altogether, which no lazy reading of a shape reproduces",
            )
    return shape


#: The types whose `kind=regex` capture is only reproducible in the middle of a
#: pattern. See `_parse_regex_capture` for the two positions and why.
_PARSE_TEMPORAL = ("datetime", "date", "timespan", "time")


def _ends_the_pattern(op: ir.Parse, index: int) -> bool:
    """Whether nothing at all follows this column — no literal, no `*`."""
    return index == len(op.segments) - 1 and not op.trailing_star


#: `flags=` letters that map onto an RE2 inline flag with the same meaning. All
#: four were measured to *do* something on the emulator, one flag at a time:
#:
#:     i   "A=5"     "a=" v          ->  '5'  where without it there is no match
#:     s   "a=x\ny"  "a=x." v        ->  'y'  `.` crossed the newline
#:     m   "x\na=1"  "^a=" v         ->  '1'  `^` matched mid-string
#:     U   "y=1,x=2,x=9,"  * "x=" v ","  ->  '9' against '2,x=9'
#:
#: `x` is Kusto's fifth and is refused: RE2 has no ignore-pattern-whitespace
#: mode at all, and DuckDB answers *"invalid perl operator: (?x"*.
_PARSE_FLAGS = "ismU"


def _parse_flags(op: ir.Parse) -> str:
    """The inline flag prefix for the assembled pattern, e.g. ``(?iU)``.

    `U` is the reason this is written inline rather than passed to
    `regexp_extract`'s options argument: DuckDB accepts `i`, `s` and `m` there
    and rejects `U` outright. Inline, RE2 takes all of them.

    And `U` has to be **global**, which is worth stating because it looks like
    it should not be. It swaps the greediness of *everything* in the pattern,
    the `*` skips included — measured on `y=1,x=2,x=9,`:

        parse kind=regex          s with * "x=" v ","   ->  '2,x=9'
        parse kind=regex flags=U  s with * "x=" v ","   ->  '9'

    A greedy skip reached the *last* `x=` under `U`. So the emitter writes
    every quantifier in its ordinary sense and lets one `(?U)` invert the lot,
    rather than trying to emit inverted forms itself.
    """
    if not op.flags:
        return ""
    if op.kind != "regex":
        # SEM0472: "parse: regex flags can be provided only on regex mode".
        raise KqlUnsupportedError(
            f"parse kind={op.kind} flags={op.flags}",
            hint="Kusto refuses flags outside regex mode too (SEM0472)",
        )
    unknown = sorted(set(op.flags) - set(_PARSE_FLAGS))
    if unknown:
        raise KqlUnsupportedError(
            f"parse flags={op.flags}",
            hint=f"unmapped flag(s) {''.join(unknown)!r}; i, s, m and U are "
            "implemented, and RE2 has no equivalent of x",
        )
    # Deduplicated and ordered so the emitted SQL is stable.
    return "(?" + "".join(f for f in _PARSE_FLAGS if f in op.flags) + ")"


def _refuse_empty_literal(op: ir.Parse) -> None:
    """An empty string literal anywhere in the pattern, which Kusto refuses.

    Two columns with nothing between them is the case that matters — written as
    two bare names (`parse s with "t=" t rest`) the grammar catches it first,
    and written with an empty literal (`parse s with "a=" a "" b`) it parses
    cleanly and would translate to two adjacent wildcards whose split is
    undefined: the first takes everything and the second nothing.

    Kusto's own rule is broader and simpler, so it is the one reproduced here —
    measured, every one of these is SEM0476 *"Empty string …"*:

        parse s with "" v
        parse s with "a=" v ""
        parse s with "a=" a "" b
        parse s with * "" v

    A *leading* column, which is the one place an empty literal would be
    natural, is a different production and is refused in `lower` instead.
    """
    for segment in op.segments:
        if segment.literal:
            continue
        raise KqlUnsupportedError(
            "parse: empty string literal in the pattern",
            hint="Kusto refuses it too (SEM0476); with nothing between two "
            "captures there is no defined place for the first to end",
        )


def _refuse_unanchored_string_column(op: ir.Parse) -> None:
    """`*` directly after a **string** column is ambiguous, and Kusto says so.

    Measured: `parse s with "n=" n * "m=" m` answers SEM0476, *"Using '*' after
    string column is ambiguous"*, while the same pattern with `n: long` is fine
    (see `_PARSE_TYPE_PATTERNS` for why). Two lazy wildcards in a row have no
    defined split, so accepting it would be picking an answer rather than
    reproducing one.

    A trailing `*` counts, and so does a string column that simply ends the
    pattern with a `*` after it.
    """
    for index, segment in enumerate(op.segments):
        if segment.name is None or segment.type not in (None, "string"):
            continue
        following = op.segments[index + 1] if index + 1 < len(op.segments) else None
        if (following is not None and following.skip) or (
            following is None and op.trailing_star
        ):
            raise KqlUnsupportedError(
                "parse", hint=f"using '*' after the string column {segment.name!r} "
                "is ambiguous; Kusto refuses it too (SEM0476). Give the column a "
                "type, or anchor it with a literal",
            )


def _parse_capture(op: ir.Parse, index: int) -> str:
    """The regex a declared column compiles to.

    Two policies, because `kind=regex` is not `kind=simple` with a flag on it.

    In **regex** mode every column is a plain regex fragment: a string column is
    `.*` (greedy, unanchored) and a typed column is its **shape**, everywhere in
    the pattern rather than only before a `*`. Measured, `"n=" n: long` over
    `n=27x` answers 27 in regex mode and null in simple.

    In **simple**/**relaxed** mode a string column is `.*?` and relies on the
    next literal — or the end anchor — to stop it, and a typed column carries a
    shape only where a ``*`` follows, the one position with nothing to stop it.
    Everywhere else the shape is not merely unnecessary but *wrong*: measured,
    `"q=" b: bool` over `q=12x` answers null, where a shaped capture would have
    taken `12` and answered true.
    """
    segment = op.segments[index]
    if op.kind == "regex":
        return _parse_regex_capture(op, index)
    if not _followed_by_skip(op, index):
        # Something concrete follows — the next segment's literal, or the end of
        # the input — so a lazy wildcard knows where to stop and no shape is
        # needed even for a typed column. Measured both ways:
        #
        #   relaxed, `"n=" n: long ",m=" m: long` over `n=xx,m=23`  ->  null, 23
        #       the pattern still matched, so the capture was not digits-only;
        #   `"q=" b: bool` over `q=12x`                             ->  null
        #       a *trailing* column takes everything and then fails to convert,
        #       where a shaped one would have taken `12` and answered true.
        #
        # The end anchor that stops the *last* column is on the whole pattern,
        # not here — see `_parse_pattern`, which needs it for a trailing literal
        # as well.
        return ".*?"

    shape = _PARSE_TYPE_PATTERNS.get(str(segment.type))
    if shape is not None:
        if index == len(op.segments) - 1:
            # Before a *trailing* `*` the shape is **optional**, which is only
            # observable in `kind=relaxed`: measured, `"p|" a: string "-q|"
            # b: long *` over `p|1-q|xx` answers `('1', null)` relaxed, where a
            # required shape would have failed the match and blanked `a` too.
            # (Under `simple` the all-or-nothing rule blanks `a` either way, so
            # the two spellings are indistinguishable there.)
            return f"(?:{shape})?"
        return shape
    # A string column here is refused by `_refuse_unanchored_string_column`,
    # which runs first and shares this position test.
    raise KqlUnsupportedError(
        f"parse type:{segment.type} before '*'",
        hint=f"the text a {segment.type} column matches has not been measured, "
        "so it can only be used where a literal or the end of the pattern "
        "follows it",
    )


def _followed_by_skip(op: ir.Parse, index: int) -> bool:
    """Whether a `*` comes directly after this segment's column.

    The only position where a capture has nothing to stop it: a following
    literal anchors it, the end of the pattern anchors it, and a `*` does
    neither. It is exactly where a typed column needs its shape and where a
    string column is ambiguous enough that Kusto refuses it.
    """
    if index + 1 < len(op.segments):
        return op.segments[index + 1].skip
    return op.trailing_star


def _parse_pattern(op: ir.Parse) -> str:
    """The whole pattern as one regex.

    Four pieces, and the mode changes three of them:

    ==============  =========================  ==========================
    piece           `simple` / `relaxed`       `regex`
    ==============  =========================  ==========================
    a literal       escaped — matched as text  the user's own regex
    a `*` skip      ``.*?``                    ``.*?``
    a string column ``.*?`` — lazy             ``.*`` — greedy
    the whole thing anchored at end-of-text    unanchored
    ==============  =========================  ==========================

    The **end anchor** is the one that is easy to miss, because it only shows
    when the pattern ends with a *literal* rather than a column. Measured, and
    the same in `simple` and `relaxed`:

        'aXcYc'       parse s with "a" v "c"      ->  'XcY'   not 'X'
        'abcbd'       parse s with "a" v "b"      ->  ''      no match at all
        'k=1,k=2,end' parse s with "k=" v ","     ->  ''      no match at all
        'aXc' + \\n    parse s with "a" v "c"      ->  ''      \\n is not consumed

    A lazy capture with nothing after it stops at the first `c`; Kusto's stops
    at the last, because the whole pattern has to reach the end of the input.
    The last line is why the anchor is RE2's bare ``$`` and not Python's or
    .NET's: those match *before* a final newline, RE2's means end of text.

    `kind=regex` is not anchored — the same four inputs give `'XcY'`, `'bc'`,
    `'1,k=2'` and `'X'` — and its columns are greedy rather than lazy, which
    `flags=U` then inverts wholesale. See :func:`_parse_flags`.
    """
    out = [_parse_flags(op)]
    for index, segment in enumerate(op.segments):
        if segment.skip:
            # `.*?` and not `.*`: measured, `* "x=" v ","` over `y=1,x=2,x=9,`
            # stops at the *first* `x=`. Under `(?U)` RE2 reads this as greedy,
            # which is exactly what Kusto's `flags=U` does to the skip too.
            out.append(".*?")
        out.append(
            neutralise_groups(segment.literal)
            if op.kind == "regex"
            else re.escape(segment.literal)
        )
        if segment.name:
            out.append(f"(?P<{segment.name}>{_parse_capture(op, index)})")
    if op.trailing_star:
        out.append(".*?")
    if op.kind != "regex":
        out.append("$")
    return "".join(out)


def _parse_row_ok(op: ir.Parse, source: str, pattern: str) -> str | None:
    """Whether the row matched **and** every declared conversion succeeded.

    `kind=simple` is **all-or-nothing**, which a two-column example cannot show
    and three columns can. Measured:

        datatable(s:string)['a=1,b=2,c=zz,d=4']
        | parse s with "a=" a: long ",b=" b ",c=" c: long ",d=" d
            ->  a=null  b=''  c=null  d=''

    `a=1` converts perfectly well and is blanked anyway. An **empty** capture
    into a typed column counts as a failure too — `'a=,b=2'` blanks the row —
    so there is no "empty is not a failure" exception to carve out.

    `parse-where` is then exactly this predicate in a `WHERE`.

    ``kind=relaxed`` is the *absence* of all this: each column converts on its
    own, which is what the raw expressions already do — an unmatched capture is
    `''` from `regexp_extract` and a failed `TRY_CAST` is null. So relaxed needs
    no predicate at all, and returns ``None``.
    """
    if op.kind == "relaxed":
        return None
    tests = [f"regexp_matches({source}, {pattern})"]
    tests += [
        f"{_parse_convert(s)} IS NOT NULL" for s in op.segments if s.name and s.type
    ]
    return "(" + " AND ".join(tests) + ")"


def _parse_column(segment: ir.ParseSegment, ok: str | None) -> str:
    """One output column: the captured text, converted, blanked if the row failed."""
    value = _parse_convert(segment)
    name = quote_ident(str(segment.name))
    if ok is None:  # relaxed — every column stands alone
        return f"{value} AS {name}"
    # A string column that did not match is `''`, which is what `regexp_extract`
    # already returns for an unmatched pattern; a typed one is null.
    blank = "''" if segment.type in (None, "string") else "NULL"
    return f"CASE WHEN {ok} THEN {value} ELSE {blank} END AS {name}"


def _parse_convert(segment: ir.ParseSegment) -> str:
    """The captured text under `name: T`, or the text itself for a bare name."""
    captured = f"{quote_ident(_PARSE_CAPTURES)}.{quote_ident(str(segment.name))}"
    if segment.type is None:
        return captured
    duck = TYPE_MAP.get(segment.type)
    if duck is None:
        raise KqlUnsupportedError(
            f"parse type:{segment.type}", hint=f"known: {sorted(TYPE_MAP)}"
        )
    # Reuse the conversion the `to<T>()` functions use rather than a bare cast.
    # Not tidiness: KQL accepts date formats DuckDB's own cast does not, and the
    # corpus depends on it — `releaseTime: date` over `02/17/2016 08:40:01` is
    # MM/DD/YYYY, which `TRY_CAST(... AS TIMESTAMPTZ)` rejects and
    # `_TODATETIME`'s format list handles. A bare cast made the conversion fail,
    # which under the all-or-nothing rule blanked the entire row.
    if duck == "TIMESTAMP":
        return f"({_TODATETIME.format(captured)})"
    if duck == "INTERVAL":
        return f"({_TOTIMESPAN.format(captured)})"
    if duck == "BOOLEAN":
        # Measured: `true`/`false` case-insensitively, or an **integer** whose
        # nonzero-ness decides — `'2'` and `'-1'` are true, `'0'` is false, and
        # `'1.5'` and `'0.0'` are **null**, not true. The integer test is what
        # keeps those two null, so a bare `TRY_CAST ... AS BIGINT` (which
        # rounds `'1.5'` to 2) will not do.
        #
        # This is exact here and cannot be in `tobool`, which also has to serve
        # a numeric argument — `tobool(1.5)` is true where `tobool('1.5')` is
        # null, and telling those apart needs the column type
        # (docs/column-types-proposal.md). `tobool`'s residue stays recorded.
        # `TRY_CAST(… AS BOOLEAN)` is too generous on the textual side as well:
        # DuckDB accepts `yes`, `no`, `t`, `f`; Kusto accepts only true/false.
        return (
            f"CASE WHEN regexp_matches({captured}, '^\\s*[-+]?\\d+\\s*$') "
            f"THEN TRY_CAST({captured} AS BIGINT) <> 0 "
            f"WHEN regexp_matches({captured}, '(?i)^\\s*(true|false)\\s*$') "
            f"THEN TRY_CAST(trim({captured}) AS BOOLEAN) END"
        )
    if duck in ("BIGINT", "INTEGER"):
        # Measured: `tolong('1.5')` is **null** in Kusto, and `'.5'` and `'1e3'`
        # are too — only integer text converts. DuckDB's `TRY_CAST(… AS BIGINT)`
        # *rounds*, answering 2, 1 and 1000, so the shape test is what keeps
        # them null. (`tolong` itself has the same divergence and cannot be
        # fixed the same way: it must also serve a numeric argument, where
        # `tolong(1.5)` really is 2. See docs/column-types-proposal.md.)
        return (
            f"CASE WHEN regexp_matches({captured}, '^\\s*[-+]?\\d+\\s*$') "
            f"THEN TRY_CAST({captured} AS {duck}) END"
        )
    if duck.startswith("DECIMAL"):
        # `todecimal` has no mapping either, and the reason is the same: DuckDB
        # renders DECIMAL(38,9) as `1.000000000` where Kusto renders `1`.
        raise KqlUnsupportedError(
            "parse type:decimal",
            hint="DuckDB's DECIMAL keeps its scale in the rendered value "
            "(1.000000000, not 1); `todecimal` is unmapped for the same reason",
        )
    # R1 — a conversion that cannot parse is null, never an error.
    return f"TRY_CAST({captured} AS {duck})"


def render_mv_expand(op: ir.MvExpand, prev: str, cols: list[str] | None = None) -> str:
    """``mv-expand [kind=] col [to typeof(T)][, …] [limit N]``.

    Three shapes for one column, all measured on the emulator:

    * an **array** expands to one row per element;
    * an **object** expands to one row per key, each a single-key bag —
      ``{"a":1,"b":2}`` becomes two rows, not one;
    * a **null** yields one row carrying null, while an **empty array** yields
      *no* rows at all.

    Several columns expand in **lockstep**, not as a cross product: the row
    count is the longest list's and the shorter ones pad with null. DuckDB's
    own rule for several ``UNNEST``s in one select list is exactly that,
    including both edges — an empty list beside a one-element list gives one
    row with null, and all-empty gives none — so the zip needs no arithmetic.

    The **column shape** follows `extend`'s rule, which is not what the operator
    looks like it does. Measured on ``datatable(id, a, b)``:

        mv-expand a       -> id, a, b     the expansion replaces `a` in place
        mv-expand x = a   -> id, a, b, x  a NEW column; `a` keeps the whole array
        mv-expand b = a   -> id, a, b     `b` is replaced in place, `a` kept

    So the alias names an *output* column that replaces a same-named input and
    is appended otherwise — the source column only disappears when it happens to
    be the target. Rendering as ``* EXCLUDE (a), UNNEST(...) AS a`` got both
    halves wrong: it moved the expanded column to the end (column order is
    user-visible, R1) and it dropped `a` under an alias. Writing the list out
    needs the incoming columns; without them the ``EXCLUDE`` form is kept and
    the position is the residual divergence, exactly as for `extend`.
    """
    from ..schema import disambiguate, mv_expand_output_columns

    rendered = {t: _mv_expand_unnest(op, t) for t in op.targets}
    names = {t: t.name or t.column for t in op.targets}

    if cols is None:
        replaced = [
            quote_ident(t.column) for t in op.targets if names[t] == t.column
        ]
        star = f"* EXCLUDE ({', '.join(replaced)})" if replaced else "*"
        select = [star, *rendered.values()]
        index_name = op.item_index
    else:
        by_name = {names[t]: sql for t, sql in rendered.items()}
        select = [by_name.get(c, quote_ident(c)) for c in cols]
        select += [sql for name, sql in by_name.items() if name not in cols]
        index_name = (
            disambiguate(op.item_index, mv_expand_output_columns(op, cols)[:-1])
            if op.item_index
            else None
        )

    if index_name:
        # The index list has to be as long as the LONGEST expanded list, since
        # that is how many rows the zip produces. Clamping it to at least one —
        # which an earlier version did — gave an empty array one row carrying
        # null instead of the no rows Kusto answers.
        longest = f"greatest({', '.join(_mv_expand_length(t) for t in op.targets)})"
        length = longest if len(op.targets) > 1 else _mv_expand_length(op.targets[0])
        if op.limit is not None:
            length = f"least({length}, CAST({max(op.limit, 0)} AS BIGINT))"
        select.append(
            f"UNNEST(generate_series(CAST(0 AS BIGINT), {length} - 1)) "
            f"AS {quote_ident(index_name)}"
        )
    return f"SELECT {', '.join(select)} FROM {prev}"


def _mv_expand_elements(op: ir.MvExpand, target: ir.MvExpandTarget) -> str:
    """The JSON list one target expands into, before ``UNNEST``.

    A null or a scalar becomes a one-element list, which is what gives the
    measured "a null yields one row carrying null" while an empty array yields
    none — `json_array(NULL)` is `[null]`, not `[]`.
    """
    col = quote_ident(target.column)
    if op.array_expansion:
        # `kind=array` / `bagexpansion=array`: a bag becomes two-element
        # `[key, value]` arrays rather than single-key bags. Measured to leave
        # a plain array alone.
        bag = (
            f"list_transform(json_keys({col}), "
            f"k -> json_array(k, json_extract({col}, '$.\"' || k || '\"')))"
        )
    else:
        bag = (
            f"list_transform(json_keys({col}), "
            f"k -> json_object(k, json_extract({col}, '$.\"' || k || '\"')))"
        )
    elements = (
        f"CAST(CASE json_type({col}) "
        f"WHEN 'ARRAY' THEN json_extract({col}, '$[*]') "
        f"WHEN 'OBJECT' THEN {bag} "
        f"ELSE json_array({col}) END AS JSON[])"
    )
    if op.limit is not None:
        # `limit N` caps the rows *per input row*, so it truncates each list
        # before the zip rather than the result afterwards. `limit 0` answers
        # no rows, and `list_slice(l, 1, 0)` is `[]`.
        elements = f"list_slice({elements}, 1, {max(op.limit, 0)})"
    return elements


def _mv_expand_unnest(op: ir.MvExpand, target: ir.MvExpandTarget) -> str:
    out = quote_ident(target.name or target.column)
    value = f"UNNEST({_mv_expand_elements(op, target)})"
    if target.to_type is not None:
        value = _mv_expand_convert(value, target.to_type)
    return f"{value} AS {out}"


def _mv_expand_length(target: ir.MvExpandTarget) -> str:
    col = quote_ident(target.column)
    return (
        f"CASE json_type({col}) "
        f"WHEN 'ARRAY' THEN CAST(json_array_length({col}) AS BIGINT) "
        f"WHEN 'OBJECT' THEN CAST(json_array_length(json_keys({col})) AS BIGINT) "
        f"ELSE CAST(1 AS BIGINT) END"
    )


#: `to typeof(T)` -> the JSON types T accepts, and the DuckDB type to cast to.
#: Measured element by element, and it is a **conversion** rather than a
#: declaration: `'2' to typeof(long)` is null, because a JSON string is not a
#: number there, and `2.5 to typeof(bool)` is null while `2` is true.
_MV_EXPAND_CONVERSIONS: dict[str, tuple[tuple[str, ...], str]] = {
    "long": (("UBIGINT", "BIGINT", "DOUBLE"), "BIGINT"),
    "int": (("UBIGINT", "BIGINT", "DOUBLE"), "INTEGER"),
    "real": (("UBIGINT", "BIGINT", "DOUBLE"), "DOUBLE"),
    "double": (("UBIGINT", "BIGINT", "DOUBLE"), "DOUBLE"),
    "bool": (("BOOLEAN", "UBIGINT", "BIGINT"), "BOOLEAN"),
    "boolean": (("BOOLEAN", "UBIGINT", "BIGINT"), "BOOLEAN"),
    "datetime": (("VARCHAR",), "TIMESTAMP"),
    "guid": (("VARCHAR",), "UUID"),
}


def _mv_expand_convert(value: str, to_type: str) -> str:
    """``to typeof(T)`` over one expanded element.

    Not a cast of the JSON text — that would convert far too much. Measured:

        [1, 2.5, -2.5, '2020-01-01T00:00:00Z', true, null] to typeof(long)
            -> 1, 2, -2, null, null, null

    So a number converts (truncating **toward zero**, not flooring: -2.5 is
    -2), and a JSON string does not. `to typeof(bool)` takes a JSON boolean or
    a whole number — `2` is true, `2.5` is null. `to typeof(datetime)` and
    `to typeof(guid)` are the mirror image, taking only a string.

    `timespan` and `decimal` are refused: every input tried, `'1.00:00:00'`
    included, came back null there, so there is no rule to reproduce — and
    answering null for everything would look like a working conversion.
    """
    if to_type == "string":
        # Exactly R17's conversion, and this is where it was measured from:
        # `null to typeof(string)` is `''`, not null.
        return _unwrap_json_scalar(value)
    if to_type == "dynamic":
        return value
    conversion = _MV_EXPAND_CONVERSIONS.get(to_type)
    if conversion is None:
        raise KqlUnsupportedError(
            f"mv-expand to typeof({to_type})",
            hint="every input tried converts to null there, so there is no "
            "measured rule to reproduce",
        )
    accepted, duck_type = conversion
    kinds = ", ".join(f"'{k}'" for k in accepted)
    if duck_type == "BOOLEAN":
        # A JSON boolean is itself; a whole number is "not zero". Rendered as
        # `<> 0` rather than a cast because DuckDB's VARCHAR -> BOOLEAN accepts
        # only '1'/'0'/'true'/'false' and answers null for 2, where Kusto says
        # true. The `= trunc` test is what keeps 2.5 null.
        return (
            f"CASE WHEN json_type({value}) = 'BOOLEAN' THEN CAST({value} AS BOOLEAN) "
            f"WHEN json_type({value}) IN ({kinds}) THEN "
            f"TRY_CAST({value} AS DOUBLE) <> 0 END"
        )
    if duck_type in ("BIGINT", "INTEGER"):
        # Truncation toward zero, which `trunc` does and a cast does not:
        # DuckDB rounds -2.5 to -3 (half away from zero), Kusto answers -2.
        return (
            f"CASE WHEN json_type({value}) IN ({kinds}) THEN "
            f"TRY_CAST(trunc(TRY_CAST({value} AS DOUBLE)) AS {duck_type}) END"
        )
    if duck_type == "TIMESTAMP":
        # R8 — via TIMESTAMPTZ so a trailing `Z` or an offset is resolved
        # rather than silently kept as a local wall time.
        return (
            f"CASE WHEN json_type({value}) IN ({kinds}) THEN "
            f"TRY_CAST({_unwrap_json_scalar(value)} AS TIMESTAMPTZ) "
            f"AT TIME ZONE 'UTC' END"
        )
    return (
        f"CASE WHEN json_type({value}) IN ({kinds}) THEN "
        f"TRY_CAST({_unwrap_json_scalar(value)} AS {duck_type}) END"
    )


def render_kql_tostring(node: ir.Expr) -> str:
    """``tostring(x)`` using **KQL's** spelling, not SQL's.

    Three cases differ from a plain CAST, all measured on the emulator:

    * a **datetime** is ``2020-01-01T00:00:00.0000000Z`` — ISO 8601 with seven
      fractional digits and a ``Z``, not ``2020-01-01 00:00:00``;
    * a **bool** is ``True``/``False`` (.NET capitalisation), not ``true``;
    * a **dynamic string** is the string itself, not its quoted JSON form, and
      a **dynamic null** is the empty string — measured,
      ``isempty(tostring(dynamic(null)))`` is true and ``isnull`` is false.

    This matters beyond formatting: ``hash_md5()`` hashes the string form, so
    the wrong spelling produces a wrong digest with no error at all. And it is
    where `mv-expand` lands: every expanded element is a dynamic, so
    ``strlen(tostring(s))`` answered **3** for ``'x'`` where Kusto says 1.

    The dynamic case is decided at **runtime**, by `typeof`, not statically. A
    bare column carries no type here — which is why the static check missed
    exactly the case that matters, an mv-expanded column — and DuckDB does know
    the type at execution. The guard also keeps a genuine VARCHAR holding the
    text ``"q"`` intact rather than unwrapping it to ``q``.
    """
    if _is_dynamic_expr(node):
        return _unwrap_json_scalar(render_expr(node))

    rendered = render_expr(node)
    if _is_datetime_expr(node):
        # %f is microseconds (6 digits); KQL prints 100ns ticks (7), and the
        # last is always 0 because DuckDB stores microseconds.
        return f"(strftime({rendered}, '%Y-%m-%dT%H:%M:%S.%f') || '0Z')"
    if _is_bool_expr(node):
        return f"CASE WHEN {rendered} THEN 'True' ELSE 'False' END"
    return (
        f"CASE WHEN typeof({rendered}) = 'JSON' "
        f"THEN {_unwrap_json_scalar(rendered)} "
        f"ELSE CAST({rendered} AS VARCHAR) END"
    )


def _unwrap_json_scalar(rendered: str) -> str:
    """A `dynamic` as KQL spells it: the value, not its JSON encoding.

    Measured against the emulator, `strlen` included so it is the value and not
    the display being pinned:

        tostring(dynamic('x'))      -> 'x'        (1, not 3)
        tostring(dynamic(null))     -> ''         (0, and isnull is FALSE)
        tostring(dynamic(1))        -> '1'
        tostring(dynamic([1,2]))    -> '[1,2]'
        tostring(dynamic({'a':1}))  -> '{"a":1}'

    So only a JSON *string* unwraps and a JSON *null* becomes empty; a
    composite keeps its JSON text, which DuckDB already spells the same way.

    DuckDB's ``->> '$'`` happens to be exactly that rule already — it unquotes a
    string and leaves every other form as its JSON text — with one hole: it
    returns SQL NULL for a JSON null, where KQL returns the empty string. So the
    whole conversion is one ``coalesce``. It is worth having as its own function
    anyway: it is emitted twice per guarded operand, and the reason `''` is
    right rather than null is the surprising part.
    """
    return f"coalesce({rendered} ->> '$', '')"


#: Functions whose result is a datetime, for static type reasoning.
_DATETIME_RETURNING = frozenset(
    {"todatetime", "datetime", "now", "ago", "startofday", "startofmonth",
     "startofyear", "startofweek", "endofday", "endofmonth", "endofyear"}
)


#: Functions whose result is a real. Deliberately short: the only thing this
#: decides is whether `/` may keep SQL's float division, and a *wrong* claim
#: here silently turns integer division back into 3.5. Anything not listed
#: falls through to `//`, which is correct for both integers and floats.
_REAL_RETURNING = frozenset({"todouble", "toreal"})


def _is_real_expr(node: ir.Expr) -> bool:
    """Whether an expression is statically known **not** to be an integer."""
    if isinstance(node, (ir.Literal, ir.Parameter)):
        return node.kind in ("real", "decimal")
    if isinstance(node, ir.FunctionCall):
        return node.name.lower() in _REAL_RETURNING
    if isinstance(node, ir.UnaryOp):
        return _is_real_expr(node.operand)
    if isinstance(node, ir.BinaryOp) and node.op in ("+", "-", "*", "/"):
        # Arithmetic with a real operand yields a real, in KQL as in SQL. This
        # is what carries `1.0` through to the division in `1.0 * x / y` — the
        # idiom the docs use to force float division, and the one place a
        # zero divisor has to produce Infinity rather than null.
        return _is_real_expr(node.left) or _is_real_expr(node.right)
    return False


def _is_datetime_expr(node: ir.Expr) -> bool:
    if isinstance(node, (ir.Literal, ir.Parameter)):
        return node.kind == "datetime"
    if isinstance(node, ir.FunctionCall):
        return node.name.lower() in _DATETIME_RETURNING
    return False


def _is_bool_expr(node: ir.Expr) -> bool:
    if isinstance(node, (ir.Literal, ir.Parameter)):
        return node.kind == "bool"
    if isinstance(node, ir.BinaryOp):
        return node.op in (
            "==", "!=", "<>", "<", "<=", ">", ">=", "and", "or", "=~", "!~",
        )
    if isinstance(node, ir.UnaryOp):
        return node.op == "not"
    return False


# ---------------------------------------------------------------------------
# Functions whose shape a template cannot express
# ---------------------------------------------------------------------------


def _render_case(node: ir.FunctionCall) -> str:
    """``case(pred, value, pred, value, ..., else)`` — variadic CASE WHEN."""
    args = [render_expr(a) for a in node.args]
    if len(args) < 3 or len(args) % 2 == 0:
        raise KqlUnsupportedError(
            "case", hint="expects alternating predicate/value pairs plus an else"
        )
    parts = [f"WHEN {args[i]} THEN {args[i + 1]}" for i in range(0, len(args) - 1, 2)]
    return f"CASE {' '.join(parts)} ELSE {args[-1]} END"


def _render_strcat(node: ir.FunctionCall) -> str:
    """``strcat(a, b, …)`` — every argument in **KQL's** string form.

    Not DuckDB's `concat` over the raw values. Measured,
    `strcat('a', 1, true, dynamic([1,2]))` is `a1True[1,2]`: the bool is .NET's
    `True`, a datetime is `2020-01-02T00:00:00.0000000Z`, and a dynamic string
    is unquoted. Concatenating the raw values got the last two wrong — silently,
    since the answer is still a plausible string.

    A **null** argument contributes the empty string, measured: `strcat('a',
    dynamic(null), 'b')` is `ab` with length 2.
    """
    return f"concat({', '.join(_stringified(a) for a in node.args)})"


def _render_strcat_delim(node: ir.FunctionCall) -> str:
    """``strcat_delim(delim, a, b, …)`` — as `strcat`, joined by *delim*.

    The `coalesce` is load-bearing: DuckDB's `concat_ws` **skips** a NULL
    argument, so `strcat_delim('-', 'a', dynamic(null), 'b')` would come back
    as `a-b` where Kusto measures `a--b` — the empty value keeps its slot.
    """
    if not node.args:
        raise KqlUnsupportedError("strcat_delim", hint="expects a delimiter")
    delim, *rest = node.args
    joined = ", ".join(_stringified(a) for a in rest)
    return f"concat_ws({_stringified(delim)}, {joined})"


def _stringified(node: ir.Expr) -> str:
    """One argument as KQL spells it, with null as the empty string."""
    return f"coalesce({render_kql_tostring(node)}, '')"


def _render_substring(node: ir.FunctionCall) -> str:
    """``substring(source, start [, length])`` — 0-based, clamping, R11.

    Every rule measured on the emulator, and none of them is SQL's:

        ('abcdefg', 1, 3)  -> 'bcd'     0-based, not 1-based
        ('abcdefg', 10, 3) -> ''        past the end clamps to empty
        ('abcdefg', 5, 10) -> 'fg'      an over-long length clamps
        ('abcdefg', 1, -1) -> ''        a negative length is empty
        ('abcdefg', -3, 2) -> 'ef'      a negative start counts from the END
        ('abc', -10)       -> ''        ...and reaching past the start is EMPTY

    That last pair is the trap. A negative start is not clamped to zero — going
    back further than the string is long gives nothing, where clamping would
    give the whole string. So the offset is resolved here and only a
    non-negative index is ever handed to DuckDB, whose own negative-start
    handling pairs the length differently again.

    Character-oriented throughout: `substring('héllo', 1, 3)` is 'éll' (R11).
    """
    if len(node.args) not in (2, 3):
        raise KqlUnsupportedError(
            "substring", hint="expects (source, start) or (source, start, length)"
        )
    source = _render_arg("substring", 0, node.args[0])
    start = render_expr(node.args[1])
    # `start` is repeated, so a volatile expression would be evaluated twice.
    # Every KQL function that could be volatile here (`rand`) returns a real,
    # which is not a legal index, so this cannot change an answer.
    offset = f"(CASE WHEN {start} < 0 THEN length({source}) + {start} ELSE {start} END)"
    if len(node.args) == 2:
        taken = f"substring({source}, {offset} + 1)"
    else:
        # A negative length clamps to zero rather than counting backwards.
        taken = f"substring({source}, {offset} + 1, greatest({render_expr(node.args[2])}, 0))"
    return f"(CASE WHEN {offset} < 0 THEN '' ELSE {taken} END)"


def _render_countof(node: ir.FunctionCall) -> str:
    """``countof(text, search [, kind])`` — occurrences, not characters.

    The default kind is ``normal`` (a plain substring); ``regex`` switches to a
    pattern. Overlapping matches are not counted in either mode.
    """
    args = [render_expr(a) for a in node.args]
    kind = "normal"
    if len(args) == 3:
        third = node.args[2]
        if not isinstance(third, ir.Literal):
            raise KqlUnsupportedError("countof", hint="kind must be a literal")
        kind = str(third.value).lower()
    if kind == "regex":
        return f"length(regexp_extract_all({args[0]}, {args[1]}))"
    if kind != "normal":
        raise KqlUnsupportedError(f"countof:{kind}")
    return (
        f"CAST((length({args[0]}) - length(replace({args[0]}, {args[1]}, ''))) "
        f"/ nullif(length({args[1]}), 0) AS BIGINT)"
    )


def _render_zip(node: ir.FunctionCall) -> str:
    """``zip(a, b, ...)`` — element-wise grouping into arrays.

    DuckDB's ``list_zip`` builds STRUCTs, not lists, so it renders as
    ``[{"":1,"":3}]`` rather than ``[[1,3]]``. Indexing by position instead
    gives the arrays KQL returns.

    KQL pads to the **longest** input with nulls (``zip([1,2],[3])`` is
    ``[[1,3],[2,null]]``), which is what out-of-range list indexing yields.
    """
    if len(node.args) < 2:
        raise KqlUnsupportedError("zip", hint="expects at least two arrays")
    # to_json() first: an argument may already be a native DuckDB LIST (from
    # make_list) rather than JSON text, and casting that straight to JSON[]
    # fails to parse.
    lists = [f"CAST(to_json({render_expr(a)}) AS JSON[])" for a in node.args]
    longest = "greatest(" + ", ".join(f"len({x})" for x in lists) + ")"
    row = ", ".join(f"{x}[i]" for x in lists)
    return (
        f"to_json(list_transform("
        f"generate_series(CAST(1 AS BIGINT), CAST({longest} AS BIGINT)), "
        f"i -> [{row}]))"
    )


def _render_make_datetime(node: ir.FunctionCall) -> str:
    """``make_datetime(y, m, d [, h, mi, s])`` — missing parts default to zero."""
    args = [render_expr(a) for a in node.args]
    if len(args) not in (3, 6):
        raise KqlUnsupportedError("make_datetime", hint="expects 3 or 6 arguments")
    args += ["0"] * (6 - len(args))
    return (
        f"make_timestamp(CAST({args[0]} AS BIGINT), CAST({args[1]} AS BIGINT), "
        f"CAST({args[2]} AS BIGINT), CAST({args[3]} AS BIGINT), "
        f"CAST({args[4]} AS BIGINT), "
        # KQL TRUNCATES the sub-second part; make_timestamp rounds, which lands
        # a microsecond out for anything finer than a microsecond.
        f"(floor(CAST({args[5]} AS DOUBLE) * 1000000) / 1000000))"
    )


def _render_make_timespan(node: ir.FunctionCall) -> str:
    """``make_timespan(h, m [, s])`` — hours and minutes, not days."""
    args = [render_expr(a) for a in node.args]
    if len(args) not in (2, 3):
        raise KqlUnsupportedError("make_timespan", hint="expects 2 or 3 arguments")
    total = f"to_hours(CAST({args[0]} AS BIGINT)) + to_minutes(CAST({args[1]} AS BIGINT))"
    if len(args) == 3:
        total += f" + to_seconds(CAST({args[2]} AS BIGINT))"
    return f"({total})"


#: KQL datetime part names -> DuckDB's.
_DATE_PARTS = {
    "year": "year", "quarter": "quarter", "month": "month", "week": "week",
    "week_of_year": "week", "day": "day", "dayofyear": "dayofyear",
    "hour": "hour", "minute": "minute", "second": "second",
    "millisecond": "millisecond", "microsecond": "microsecond",
}


def _date_part(node: ir.Expr, fn: str) -> str:
    if not isinstance(node, ir.Literal) or node.kind != "string":
        raise KqlUnsupportedError(fn, hint="the part must be a string literal")
    part = _DATE_PARTS.get(str(node.value).lower())
    if part is None:
        raise KqlUnsupportedError(f"{fn}:{node.value}")
    return part


def _render_datetime_add(node: ir.FunctionCall) -> str:
    """``datetime_add(part, amount, datetime)``."""
    if len(node.args) != 3:
        raise KqlUnsupportedError("datetime_add", hint="expects (part, amount, date)")
    part = _date_part(node.args[0], "datetime_add")
    return (
        f"({render_expr(node.args[2])} + "
        f"to_{part}s(CAST({render_expr(node.args[1])} AS BIGINT)))"
    )


def _render_datetime_diff(node: ir.FunctionCall) -> str:
    """``datetime_diff(part, first, second)`` — first MINUS second.

    DuckDB's ``date_diff`` takes the arguments the other way round, so a direct
    mapping returns the negated answer.
    """
    if len(node.args) != 3:
        raise KqlUnsupportedError("datetime_diff", hint="expects (part, first, second)")
    part = _date_part(node.args[0], "datetime_diff")
    return (
        f"date_diff('{part}', {render_expr(node.args[2])}, {render_expr(node.args[1])})"
    )


def _render_datetime_part(node: ir.FunctionCall) -> str:
    if len(node.args) != 2:
        raise KqlUnsupportedError("datetime_part", hint="expects (part, date)")
    if (
        isinstance(node.args[0], ir.Literal)
        and str(node.args[0].value).lower() == "nanosecond"
    ):
        # KQL keeps 100ns ticks; DuckDB stores microseconds. The last digit is
        # simply not there to return, so answering would mean quietly rounding
        # a value the caller asked for *because* they wanted that precision.
        raise KqlUnsupportedError(
            "datetime_part:nanosecond",
            hint="DuckDB stores microseconds; the 100ns tick cannot be recovered",
        )
    part = _date_part(node.args[0], "datetime_part")
    return f"CAST(date_part('{part}', {render_expr(node.args[1])}) AS BIGINT)"


def _render_round(node: ir.FunctionCall) -> str:
    """``round(x)`` and ``round(x, precision)``.

    Both cast to DOUBLE first, and that cast is the whole point. DuckDB's
    two-argument ``round`` returns DECIMAL and rounds the *decimal* value, so
    ``round(1.005, 2)`` is ``1.01`` there and ``1.0`` in Kusto — which rounds
    the double ``1.00499999…`` that ``1.005`` actually is. Measured across six
    cases, including a negative precision (``round(12345, -2)`` is ``12300.0``,
    a real, not the integer DuckDB's own ``round`` returns).
    """
    if len(node.args) not in (1, 2):
        raise KqlUnsupportedError(
            "function:round", hint="round() takes (1, 2) argument(s)"
        )
    value = f"CAST({render_expr(node.args[0])} AS DOUBLE)"
    if len(node.args) == 1:
        return f"round({value})"
    # DuckDB overloads `round` on an **INTEGER** precision only, and a KQL
    # integer literal renders as BIGINT (§2), so without this cast the pair
    # matches no overload and the query fails to bind.
    return f"round({value}, CAST({render_expr(node.args[1])} AS INTEGER))"


_SPECIAL_FORMS = {
    "case": _render_case,
    "strcat": _render_strcat,
    "strcat_delim": _render_strcat_delim,
    "substring": _render_substring,
    "round": _render_round,
    "countof": _render_countof,
    "zip": _render_zip,
    "make_datetime": _render_make_datetime,
    "make_timespan": _render_make_timespan,
    "datetime_add": _render_datetime_add,
    "datetime_diff": _render_datetime_diff,
    "datetime_part": _render_datetime_part,
}


#: KQL period name -> (DuckDB date_trunc unit, DuckDB interval unit).
_PERIODS = {
    "day": ("day", "DAY"), "month": ("month", "MONTH"), "year": ("year", "YEAR"),
}


def _render_period(node: ir.FunctionCall) -> str:
    """``startof*`` / ``endof*``, with KQL's optional period offset.

    Two things a template cannot express:

    * the **offset** argument shifts by whole periods, and ignoring it returns
      a plausible datetime for the *wrong* period;
    * KQL's weeks start on **Sunday**, while DuckDB's ``date_trunc('week')``
      starts Monday — a one-day error for every Sunday;
    * ``endof*`` is the last instant *inside* the period, not the start of the
      next one.
    """
    name = node.name.lower()
    if not 1 <= len(node.args) <= 2:
        raise KqlUnsupportedError(name, hint="expects (date [, offset])")
    value = render_expr(node.args[0])
    period = name.removeprefix("startof").removeprefix("endof")

    if period == "week":
        start = f"(date_trunc('day', {value}) - to_days(CAST(dayofweek({value}) AS INTEGER)))"
        step = "INTERVAL 7 DAY"
    else:
        trunc, unit = _PERIODS[period]
        start = f"date_trunc('{trunc}', {value})"
        step = f"INTERVAL 1 {unit}"

    if len(node.args) == 2:
        offset = render_expr(node.args[1])
        start = f"({start} + CAST({offset} AS BIGINT) * {step})"

    if name.startswith("startof"):
        return start
    return f"({start} + {step} - INTERVAL 1 MICROSECOND)"


def _render_extract_all(node: ir.FunctionCall) -> str:
    """``extract_all(regex, text)`` — every match, grouped.

    With ONE capture group KQL returns a flat array of matches; with several it
    returns an array *per match*, each holding that match's groups. DuckDB's
    ``regexp_extract_all`` only ever gives the flat form, so the multi-group
    case needs building up from the group count.
    """
    if len(node.args) != 2:
        raise KqlUnsupportedError("extract_all", hint="expects (regex, text)")
    regex, text = node.args
    pattern = render_expr(regex)
    subject = render_expr(text)

    groups = _capture_group_count(regex)
    if groups <= 1:
        return f"to_json(regexp_extract_all({subject}, {pattern}))"
    names = ", ".join(f"'g{i}'" for i in range(1, groups + 1))
    return (
        f"to_json(list_transform("
        f"regexp_extract_all({subject}, {pattern}), "
        f"m -> [{', '.join(f'regexp_extract(m, {pattern}, {i})' for i in range(1, groups + 1))}]"
        f"))".replace(f"[{names}]", "")
    )


def _capture_group_count(node: ir.Expr) -> int:
    """Count capture groups in a *literal* regex; 1 when it cannot be read."""
    if not isinstance(node, ir.Literal) or node.kind != "string":
        return 1
    pattern, count, i = str(node.value), 0, 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\":
            i += 2
            continue
        if c == "(" and not pattern.startswith("(?", i):
            count += 1
        i += 1
    return max(count, 1)


_SPECIAL_FORMS.update(
    {
        "extract_all": _render_extract_all,
        **{
            n: _render_period
            for n in (
                "startofday", "startofmonth", "startofyear", "startofweek",
                "endofday", "endofmonth", "endofyear", "endofweek",
            )
        },
    }
)
