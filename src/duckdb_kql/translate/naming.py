"""Kusto's names for unnamed columns (R12) — one rule for every operator that makes one.

`project`, `extend`, `summarize` and `distinct` each name an expression the
caller did not, and the names are user-visible: a later operator reads the
column by them. They used to come from three places that disagreed, and two of
them disagreed with Kusto — measured on the emulator, 2026-10-25:

* **An expression takes a name from its column.** `extend abs(x)` is named `x`
  and so *replaces* `x`, where this added `Column1` beside it; a following
  `where x > 0` then read the un-extended values, with no error. The functions
  that pass a name through are an allow-list, measured one by one
  (:data:`NAME_PRESERVING`); `strlen(s)` is `strlen_s` and `gettype(x)`
  `type_x`; a path is its steps joined, `d.b[0]` -> `d_b_0`.
* **A positional name counts only the targets that need one** — `project a,
  a+1` is `a, Column1`, where this said `Column2`.
* **A repeated name gets `1`, `2`, … with no separator** — `x, x1`, where
  DuckDB's own de-duplication made `x_1`.

:class:`Namer` holds the assignment rule, which is the same for every operator
and is not the obvious one; :func:`expression_name` holds the base names.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .. import ir
from ..errors import KqlSchemaError, KqlUnsupportedError
from .functions import AggregateSpec, lookup_aggregate

#: Functions that name their result after their **first argument**. Measured one
#: by one in `project`, `extend`, `summarize … by` and `distinct`, which agree
#: on every entry: an allow-list, not a rule — `tostring(B)` is `B` but
#: `tolower(B)` is `Column1`, `startofday(T)` is `T` but `startofweek(T)` is
#: `Column1`, `sin` passes the name through and `cot` does not. Absent means
#: positional, which is also right for a function nobody has measured.
NAME_PRESERVING = frozenset({
    # conversions
    "tostring", "toint", "tolong", "todouble", "toreal", "tobool", "toboolean",
    "todatetime", "totimespan", "timespan", "toguid", "todecimal", "tohex",
    "todynamic", "parse_json", "split",
    # bucketing and rounding
    "bin", "bin_at", "floor", "ceiling", "round", "startofday",
    # numeric — not `pow`, `exp10`, `sign`, `cot`, `degrees` or `radians`
    "abs", "sqrt", "log", "log10", "log2", "exp", "exp2", "gamma",
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
    "binary_and", "binary_or", "binary_xor", "binary_not",
    "binary_shift_left", "binary_shift_right",
    # datetime — not `trim`, `trim_start` or `trim_end` on the string side
    "format_datetime",
})

#: Functions whose name is a prefix and their argument's: `strlen(s)` is
#: `strlen_s`, `gettype(x)` is `type_x`, and with no name to add, `strlen_`.
_PREFIXED = {"strlen": "strlen", "gettype": "type"}

#: `array_sort_asc(d)` is `d_sorted`; anything but a bare column is
#: `array0_sorted`, measured for a path, `split` and `pack_array` alike.
_SORTING = frozenset({"array_sort_asc", "array_sort_desc"})


def expression_name(expr: ir.Expr) -> str | None:
    """The name Kusto gives *expr* unnamed, or None for a positional `ColumnN`.

    Nests: `tostring(toint(C))` is `C`, `strlen(tostring(d.a))` is `strlen_d_a`
    and `abs(min(x))` in a `summarize` is `min_x`. A call outside the lists
    contributes nothing *and breaks the chain* — `abs(-C)` and
    `tolower(tostring(B))` are both positional — and so does any operator.
    """
    if isinstance(expr, ir.ColumnRef):
        return expr.name
    if isinstance(expr, ir.ToScalar):
        # Measured: `extend toscalar(…)`, `by toscalar(…)`, `extend
        # tostring(toscalar(…))` and `max(toscalar(…))` are named `__scalar_`
        # and a fresh GUID, different on every run. `toscalar(…) + 1` never
        # gets here — an operator is positional before its operands are seen.
        raise KqlUnsupportedError(
            "toscalar",
            hint="name this column (`x = toscalar(…)`): Kusto names it `__scalar_` "
            "and a random GUID, so no name given here could match",
        )
    if isinstance(expr, ir.PathAccess):
        return _path_name(expr)
    if not isinstance(expr, ir.FunctionCall):
        return None
    name = expr.name.lower()
    spec = lookup_aggregate(name)
    if spec is not None:
        return _aggregate_name(spec, expr)
    if not expr.args:
        return None
    first = expr.args[0]
    if name in NAME_PRESERVING:
        return expression_name(first)
    if name in _PREFIXED:
        return f"{_PREFIXED[name]}_{expression_name(first) or ''}"
    if name in _SORTING:
        return f"{first.name}_sorted" if isinstance(first, ir.ColumnRef) else "array0_sorted"
    return None


def _path_name(expr: ir.PathAccess) -> str:
    """`d.a` and `d['a']` are `d_a`, `d.b[-1]` is `d_b__1`, `pack_array(1)[0]` is `0`."""
    base = expression_name(expr.base)
    parts = [base] if base else []
    for step in expr.steps:
        if step.name is not None:
            parts.append(step.name)
        elif isinstance(step.index, ir.Literal) and step.index.kind in ("int", "long"):
            parts.append(str(step.index.value).replace("-", "_"))
        else:
            raise KqlUnsupportedError(
                "dynamic index",
                hint="name this column: Kusto names `d[i]` after a random GUID, "
                "so no name given here could match",
            )
    return "_".join(parts)


def _aggregate_name(spec: AggregateSpec, expr: ir.FunctionCall) -> str:
    """`count()` -> `count_`, `sum(x)` -> `sum_x`, `sum(x + z)` -> `sum_`,
    `make_list(x)` -> `list_x`, `percentile(x, 50)` -> `percentile_x_50`,
    `take_any(x)` -> `x` — each read off the emulator."""
    if spec.name_is_argument:
        return (expression_name(expr.args[0]) if expr.args else None) or spec.prefix
    if spec.name_ignores_args or not expr.args:
        return f"{spec.prefix}_"
    name = f"{spec.prefix}_{expression_name(expr.args[0]) or ''}"
    if spec.name == "percentile" and len(expr.args) > 1:
        p = expr.args[1]
        if isinstance(p, ir.Literal) and isinstance(p.value, (int, float)):
            suffix = int(p.value) if float(p.value).is_integer() else p.value
            name = f"{name}_{suffix}"
    return name


def declared(targets: Sequence[ir.NamedExpr], operator: str) -> list[str]:
    """The names a list spells out — `name = …` and bare columns — refusing a repeat.

    Kusto refuses `project x, x` and `project y = a, y = b` (SEM0034), though it
    renames freely when the clash is with a name it generated: `project abs(x),
    x` is `x, x1`.
    """
    out: list[str] = []
    for target in targets:
        name = target.name or (target.expr.name if isinstance(target.expr, ir.ColumnRef) else None)
        if name is None:
            continue
        if name in out:
            raise KqlSchemaError(
                name, hint=f"`{operator}` names two columns {name!r}; Kusto refuses it (SEM0034)"
            )
        out.append(name)
    return out


class Namer:
    """One operator's output names, assigned left to right as Kusto assigns them.

    Each column takes its base name — the one it spells, the one
    :func:`expression_name` gives it, or the next `ColumnN` — **unless an
    earlier column already has it**, and then the first of `name1`, `name2`, …
    that is neither taken nor spelled out anywhere in the list. Measured, and
    not the obvious rule, because a spelled-out name is not first come first
    served:

    * `project abs(x), sqrt(x), x1 = 100` is `x, x2, x1` — `x1` is reserved;
    * `project i + 1, Column1 = 100` is `Column1, Column11` — the *explicit*
      name is the one that moves, because the generated one got there first.

    *skip* is for `extend` and `summarize`, whose `ColumnN` never reuses an
    input column's name — `extend Column1 = 5 | extend i + 1` adds `Column2` —
    where `project`'s does: `project i + 1, Column1` is `Column1, Column11`.
    """

    def __init__(self, reserved: Iterable[str] = (), skip: Iterable[str] = ()) -> None:
        self._taken: set[str] = set()
        self._reserved = set(reserved)
        self._skip = set(skip)
        self._positional = 0

    def name(self, target: ir.NamedExpr) -> str:
        """*target*'s name, taken."""
        return self.take(target.name or expression_name(target.expr) or self.positional())

    def positional(self) -> str:
        self._positional += 1
        while f"Column{self._positional}" in self._skip:
            self._positional += 1
        return f"Column{self._positional}"

    def take(self, base: str) -> str:
        name, n = base, 0
        while name in self._taken or (n and name in self._reserved):
            n += 1
            name = f"{base}{n}"
        self._taken.add(name)
        return name


def project_names(targets: Sequence[ir.NamedExpr]) -> list[str]:
    """`project`'s output names."""
    namer = Namer(declared(targets, "project"))
    return [namer.name(t) for t in targets]


def extend_names(targets: Sequence[ir.NamedExpr], cols: Sequence[str] | None) -> list[str]:
    """`extend`'s names for what it adds or replaces.

    No renaming here, unlike `project`: a generated name equal to an input
    column **replaces** it (`extend abs(x)` writes over `x`), and two equal
    names in one `extend` are refused, as Kusto refuses `extend abs(x),
    sqrt(x)` (SEM0034). Without the input columns a `ColumnN` cannot step past
    one already there; `duckdb_kql.kql()` always supplies them.
    """
    namer = Namer(skip=cols or ())
    names: list[str] = []
    for target in targets:
        name = target.name or expression_name(target.expr) or namer.positional()
        if name in names:
            raise KqlSchemaError(
                name, hint=f"`extend` names two columns {name!r}; Kusto refuses it (SEM0034)"
            )
        names.append(name)
    return names


def distinct_names(targets: Sequence[ir.NamedExpr]) -> list[str]:
    """`distinct`'s output names: `summarize … by`'s, with one exception.

    A `gettype` that would name the column itself is positional there —
    measured, `distinct gettype(s)` and `distinct tostring(gettype(x))` are
    `Column1` where `by gettype(x)` is `type_x` — though `distinct
    strlen(gettype(x))` is still `strlen_type_x`.

    **Residue:** `distinct` numbers some positional names one higher, and by no
    rule the measurements give — `distinct i + 1` is `Column2`, `distinct
    tolower(s), i + 1` is `Column1, Column3`. Here they count up from one.
    """
    namer = Namer(declared(targets, "distinct"))
    out: list[str] = []
    for target in targets:
        if target.name is None and _names_by_gettype(target.expr):
            out.append(namer.take(namer.positional()))
        else:
            out.append(namer.name(target))
    return out


def _names_by_gettype(expr: ir.Expr) -> bool:
    while isinstance(expr, ir.FunctionCall) and expr.args:
        name = expr.name.lower()
        if name == "gettype":
            return True
        if name not in NAME_PRESERVING:
            return False
        expr = expr.args[0]
    return False
