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

import fnmatch

from . import ir
from .errors import KqlSchemaError

__all__ = [
    "Schema", "output_columns", "join_output_columns", "lookup_output_columns",
    "union_output_columns", "mv_expand_output_columns", "parse_output_columns",
    "replacing", "match_wildcard",
    "surviving_branches",
    "disambiguate",
]

#: Table name -> ordered column names.
Schema = dict[str, list[str]]


def _table_columns(name: str, schema: Schema | None) -> list[str]:
    if schema is None:
        raise KqlSchemaError(
            name,
            hint="join needs the table's columns to reproduce KQL's column "
            "renaming; use duckdb_kql.kql(con, ...) or pass schema=",
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


def match_wildcard(source: ir.WildcardTableRef, schema: Schema | None) -> list[str]:
    """Table names matching ``UT*``, in name order.

    Kusto refuses a pattern that matches nothing (SEM0100), so this does too —
    an empty union would otherwise return an empty result and look like data.

    **Name order is a deliberate divergence.** Measured: Kusto expands a
    wildcard in its own *creation* order — tables made as Zed, Alpha, Mid give
    columns ``z1, a1, m1``, not ``a1, m1, z1``. A DuckDB catalog does not record
    that order, and inventing an ordering that happens to agree on some
    databases would be worse than one that is always explainable. The rows are
    the same either way; only the outer union's column *order* differs, and only
    when the matched tables have different columns. See docs/TRANSLATION.md R15.
    """
    if schema is None:
        raise KqlSchemaError(
            source.pattern,
            hint="union with a wildcard needs the table list; use duckdb_kql.kql() "
            "or pass schema=",
        )
    prefix = f"{source.database}." if source.database else ""
    names = sorted(
        name
        for name in schema
        if name.startswith(prefix)
        and "." not in name[len(prefix):]
        and fnmatch.fnmatchcase(name[len(prefix):], source.pattern)
    )
    if not names:
        where = f" in database {source.database!r}" if source.database else ""
        raise KqlSchemaError(
            source.pattern,
            hint=f"union wildcard matched no table{where}; known: {sorted(schema)}",
        )
    return names


def _source_columns(source: ir.Source, schema: Schema | None) -> list[str]:
    if isinstance(source, ir.WildcardTableRef):
        matched = match_wildcard(source, schema)
        columns: list[str] = []
        for name in matched:
            for column in _table_columns(name, schema):
                if column not in columns:
                    columns.append(column)
        return columns
    if isinstance(source, ir.TableRef):
        # `Sales.Orders` when qualified, `Orders` when not — the same string
        # `engine.schema` keys attached databases by.
        return _table_columns(source.qualified, schema)
    if isinstance(source, ir.CommandSource):
        return list(source.columns)
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
    from .translate import aggregate_name, output_name, target_names

    if isinstance(op, (ir.Where, ir.Take, ir.Sort, ir.Top)):
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
        return mv_expand_output_columns(op, cols)
    if isinstance(op, ir.Parse):
        return parse_output_columns(op, cols)
    if isinstance(op, ir.GetSchema):
        return ["ColumnName", "ColumnOrdinal", "DataType", "ColumnType"]
    if isinstance(op, ir.Count):
        return [op.name]
    if isinstance(op, ir.Distinct):
        return target_names(op.expressions)
    if isinstance(op, ir.Summarize):
        out = target_names(op.by)
        for agg in op.aggregates:
            out.append(disambiguate(aggregate_name(agg), out))
        return out
    if isinstance(op, ir.Join):
        left, right = cols, output_columns(op.right, schema)
        return join_output_columns(left, right, op.kind)
    if isinstance(op, ir.Union):
        return union_output_columns(op, cols, schema)
    if isinstance(op, ir.Lookup):
        right = output_columns(op.right, schema)
        return lookup_output_columns(cols, right, [k.right for k in op.keys])
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


def union_output_columns(
    op: ir.Union, left: list[str], schema: Schema | None
) -> list[str]:
    """Column names a ``union`` produces (R15).

    ``outer`` (the default) is the **union** of every branch's columns in order
    of first appearance; ``inner`` is the intersection, in the same order.
    Measured: `union A, B` gives ``x, y, z`` and `union B, A` gives ``x, z, y``,
    so the order follows the branches rather than being sorted.

    ``withsource=Name`` prepends one column, and it survives ``kind=inner`` —
    the intersection is taken over the *data* columns only.
    """
    per_branch = [list(left)]
    for branch in surviving_branches(op, schema):
        per_branch.append(output_columns(branch, schema))

    if op.kind == "inner":
        shared = set(per_branch[0]).intersection(*(set(b) for b in per_branch[1:]))
        out = [c for c in per_branch[0] if c in shared]
    else:
        out = []
        for branch_columns in per_branch:
            for name in branch_columns:
                if name not in out:
                    out.append(name)

    return ([op.with_source] if op.with_source else []) + out


def surviving_branches(op: ir.Union, schema: Schema | None) -> tuple[ir.Query, ...]:
    """The branches that actually resolve, honouring ``isfuzzy=true``.

    `isfuzzy` tolerates a **missing table** and nothing else: measured,
    `union isfuzzy=true UT1, NoSuchTable` returns UT1's rows instead of failing.

    It deliberately does not fire when no schema was supplied. "I cannot resolve
    this branch because I was given no catalog" is not "this table does not
    exist", and treating it as such would silently drop every branch and return
    a plausible-looking short answer.
    """
    if not op.isfuzzy or schema is None:
        return op.branches
    kept = []
    for branch in op.branches:
        try:
            output_columns(branch, schema)
        except KqlSchemaError:
            continue
        kept.append(branch)
    return tuple(kept)


def lookup_output_columns(
    left: list[str], right: list[str], right_keys: list[str]
) -> list[str]:
    """Column names a ``lookup`` produces (R14).

    The difference from :func:`join_output_columns` that makes `lookup` worth a
    separate operator: the right side's **key columns are dropped** instead of
    being carried through with a ``1`` suffix. Everything left over is appended
    and disambiguated exactly as a join would.

    Measured on the emulator. With left ``(Row, Key, V)`` and right
    ``(Key, V, Alias)`` joined on ``Key``, a `join` gives
    ``Row, Key, V, Key1, V1, Alias`` but a `lookup` gives
    ``Row, Key, V, V1, Alias`` — `Key1` is absent, and `V1` is still there, so
    this is specifically about the *keys*, not about collisions in general.

    Keys are matched by name on the right side, which is what
    ``on $left.K1 == $right.K2`` drops: ``K2`` goes, ``K1`` stays.
    """
    dropped = set(right_keys)
    taken = list(left)
    out = list(left)
    for name in right:
        if name in dropped:
            continue
        out.append(disambiguate(name, taken))
        taken.append(out[-1])
    return out


def replacing(cols: list[str], declared: list[str]) -> list[str]:
    """Add *declared* to *cols* the way `extend` does: in place, else appended.

    KQL's recurring rule for an operator that introduces columns — a name that
    already exists is **overwritten where it stands**, keeping its position, and
    only a genuinely new name goes on the end. Column order is user-visible
    (R1), so getting this wrong reorders a result.

    Three operators want it — `extend`, `mv-expand` (R18) and `parse` — and it
    was written twice before being named.
    """
    out = list(cols)
    out.extend(name for name in declared if name not in out)
    return out


def parse_output_columns(op: ir.Parse, cols: list[str]) -> list[str]:
    """``parse``'s output columns. Measured to follow `extend`'s rule:

        datatable(s:string, a:string) | parse s with "a=" a   ->  s, a

    with `a` overwritten in place rather than a second `a` appended.
    """
    return replacing(cols, [s.name for s in op.segments if s.name])


def mv_expand_output_columns(op: ir.MvExpand, cols: list[str]) -> list[str]:
    """``mv-expand``'s output columns — `extend`'s rule, not a rename.

    Measured on ``datatable(id, a, b)``: `mv-expand a` answers `id, a, b`,
    `mv-expand x = a` answers `id, a, b, x` (with `a` still holding the whole
    array), and `mv-expand b = a` answers `id, a, b`. So the alias names an
    *output* column that replaces a same-named input in place and is appended
    otherwise; the source column survives unless it is that target.

    ``with_itemindex`` always lands last, and collides like a join key rather
    than like DuckDB's own de-duplication: measured, `with_itemindex=b` over a
    table that already has `b` answers **b1**, where two columns both called
    `b` would have left DuckDB naming the second `b_1`.
    """
    out = replacing(cols, [t.name or t.column for t in op.targets])
    if op.item_index:
        out.append(disambiguate(op.item_index, out))
    return out


def disambiguate(name: str, taken: list[str]) -> str:
    if name not in taken:
        return name
    n = 1
    while f"{name}{n}" in taken:
        n += 1
    return f"{name}{n}"
