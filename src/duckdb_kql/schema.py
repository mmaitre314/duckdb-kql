"""Output-column tracking (pipeline stage 2.5).

Most of the translation is schema-free by design: a KQL pipeline maps to a chain
of CTEs without anyone needing to know what columns exist. ``join`` breaks that.
KQL renames the right side's colliding columns — ``k`` becomes ``k1``, and ``k1``
becomes ``k2`` if ``k1`` is taken — and reproducing those names requires knowing
both sides' columns before the query runs.

So this module computes each operator's output columns statically, given the
base tables' columns. It is the only part of translation that needs a schema,
and only queries containing a ``join`` require one at all.
"""

from __future__ import annotations

from . import ir
from .errors import KqlSchemaError

__all__ = [
    "Schema", "output_columns", "join_output_columns", "disambiguate",
]

#: Table name -> ordered column names.
Schema = dict[str, list[str]]


def _table_columns(name: str, schema: Schema | None) -> list[str]:
    if schema is None:
        raise KqlSchemaError(
            name,
            hint="join needs the table's columns to reproduce KQL's column "
            "renaming; use duckdb_kql.sql(con, ...) or pass schema=",
        )
    # Exact match only. KQL identifiers are case-sensitive (R7), so folding the
    # case here would let `foo` silently bind to a table named `Foo` — resolving
    # arbitrarily where the rule says to raise.
    if name in schema:
        return list(schema[name])
    raise KqlSchemaError(name, hint=f"unknown table; known: {sorted(schema)}")


def output_columns(query: ir.Query, schema: Schema | None = None) -> list[str]:
    """The column names *query* produces, in order."""
    cols = _source_columns(query.source, schema)
    for op in query.operators:
        cols = _operator_columns(op, cols, schema)
    return cols


def _source_columns(source: ir.Source, schema: Schema | None) -> list[str]:
    if isinstance(source, ir.TableRef):
        return _table_columns(source.name, schema)
    if isinstance(source, ir.DataTable):
        return [name for name, _ in source.columns]
    if isinstance(source, ir.RangeSource):
        return [source.name]
    if isinstance(source, ir.PrintSource):
        from .translate import print_column_name

        return [print_column_name(e, i) for i, e in enumerate(source.expressions)]
    raise KqlSchemaError(type(source).__name__, hint="cannot determine columns")


def _operator_columns(
    op: ir.Operator, cols: list[str], schema: Schema | None
) -> list[str]:
    from .translate import aggregate_name, group_key_name, output_name

    if isinstance(op, (ir.Where, ir.Take, ir.Sort)):
        return cols
    if isinstance(op, ir.Project):
        return [output_name(e, i) for i, e in enumerate(op.expressions)]
    if isinstance(op, ir.Extend):
        # `extend` replaces a column of the same name IN PLACE, keeping its
        # position, and appends only genuinely-new names. Returning `kept +
        # added` moved a replaced column to the end, which a `join` downstream
        # then inherited as the wrong column order. Confirmed on the emulator:
        # `datatable(a:int, b:int, c:int) [1,2,3] | extend a = 99` returns
        # a, b, c — not b, c, a.
        added = [output_name(e, i) for i, e in enumerate(op.expressions)]
        return cols + [c for c in added if c not in cols]
    if isinstance(op, ir.ProjectAway):
        return [c for c in cols if c not in op.columns]
    if isinstance(op, ir.ProjectRename):
        mapping = dict(op.renames)
        return [mapping.get(c, c) for c in cols]
    if isinstance(op, ir.MvExpand):
        return [op.name or c if c == op.column else c for c in cols] + (
            [op.item_index] if op.item_index else []
        )
    if isinstance(op, ir.Count):
        return [op.name]
    if isinstance(op, ir.Distinct):
        return list(op.columns)
    if isinstance(op, ir.Summarize):
        out = [group_key_name(k, i) for i, k in enumerate(op.by)]
        for agg in op.aggregates:
            out.append(disambiguate(aggregate_name(agg), out))
        return out
    if isinstance(op, ir.Join):
        left, right = cols, output_columns(op.right, schema)
        return join_output_columns(left, right, op.kind)
    raise KqlSchemaError(type(op).__name__, hint="cannot determine columns")


#: Kinds that return only one side's columns.
_LEFT_ONLY = {"leftsemi", "leftanti"}
_RIGHT_ONLY = {"rightsemi", "rightanti"}


def join_output_columns(left: list[str], right: list[str], kind: str) -> list[str]:
    """Column names a join produces (R5).

    Semi and anti joins return **one side only**. Every other kind returns both,
    with the right side's colliding names suffixed: ``k`` -> ``k1``, and ``k1``
    -> ``k2`` when ``k1`` is already taken. Measured on the emulator; the
    suffix has no separator, so ``k_1`` would be wrong.
    """
    if kind in _LEFT_ONLY:
        return list(left)
    if kind in _RIGHT_ONLY:
        return list(right)

    taken = list(left)
    out = list(left)
    for name in right:
        out.append(disambiguate(name, taken))
        taken.append(out[-1])
    return out


def disambiguate(name: str, taken: list[str]) -> str:
    if name not in taken:
        return name
    n = 1
    while f"{name}{n}" in taken:
        n += 1
    return f"{name}{n}"
