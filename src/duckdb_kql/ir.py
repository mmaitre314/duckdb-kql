"""Intermediate representation — stage 2 of the pipeline.

A small, stable KQL AST sitting between ANTLR's concrete syntax tree and SQL
generation (``docs/implementation-plan.md`` §2). Everything downstream depends on
*this*, never on ANTLR, so the parser can be patched, regenerated, or replaced
without touching the translator.

Only Wave 1 is modelled. Anything not represented here raises
``KqlUnsupportedError`` during lowering, which is the intended behaviour —
partial coverage must fail loudly (``docs/TRANSLATION.md`` principle 5).
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "Node", "Expr", "Operator", "Query",
    # expressions
    "Literal", "ColumnRef", "BinaryOp", "UnaryOp", "FunctionCall", "NamedExpr",
    # sources
    "TableRef", "DataTable", "PrintSource",
    # operators
    "Summarize", "Join", "JoinKey",
    "Where", "Project", "Extend", "Take", "Sort", "SortKey", "Count", "Distinct",
]


class Node:
    """Base for every IR node."""


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------


class Expr(Node):
    """A scalar expression."""


@dataclass(frozen=True)
class Literal(Expr):
    """A literal value.

    ``kind`` is the *KQL* type name (``long``, ``real``, ``string``, ``bool``,
    ``datetime``, ``timespan``, ``dynamic``, ``guid``, ``null``). Keeping the KQL
    type rather than a Python type is what lets the emitter choose the right
    DuckDB cast — see ``docs/TRANSLATION.md`` §3.
    """

    value: object
    kind: str

    def __post_init__(self) -> None:
        if self.kind not in {
            "long", "int", "real", "decimal", "string", "bool",
            "datetime", "timespan", "dynamic", "guid", "null",
        }:
            raise ValueError(f"unknown literal kind: {self.kind!r}")


@dataclass(frozen=True)
class ColumnRef(Expr):
    """A reference to a column by name. Case-sensitive (R7)."""

    name: str


@dataclass(frozen=True)
class BinaryOp(Expr):
    """A binary operation.

    ``op`` is the *KQL* spelling (``==``, ``=~``, ``has``, ``contains_cs``, …),
    never the SQL one. Preserving the KQL spelling is essential: ``==`` and
    ``=~`` differ only in case sensitivity, and ``has`` is term-based while
    ``contains`` is substring (R2, R3). Collapsing them here would lose exactly
    the distinction the translator must honour.
    """

    op: str
    left: Expr
    right: Expr


@dataclass(frozen=True)
class UnaryOp(Expr):
    op: str
    operand: Expr


@dataclass(frozen=True)
class FunctionCall(Expr):
    name: str
    args: tuple[Expr, ...] = ()


@dataclass(frozen=True)
class NamedExpr(Node):
    """``name = expression``, as used by ``project``, ``extend``, ``summarize``.

    ``name`` is None when the expression is unnamed, in which case the emitter
    derives the output name (R12).
    """

    expr: Expr
    name: str | None = None


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


class Source(Node):
    """Whatever a pipeline starts from."""


@dataclass(frozen=True)
class TableRef(Source):
    name: str


@dataclass(frozen=True)
class DataTable(Source):
    """``datatable(col:type, ...) [values...]`` — inlines its own input.

    The bulk of the acceptance corpus is built on this
    (``docs/frequency-scan-results.md``), so it is Wave 1 despite looking exotic.
    """

    columns: tuple[tuple[str, str], ...]  # (name, kql_type)
    values: tuple[Expr, ...]

    @property
    def arity(self) -> int:
        return len(self.columns)


@dataclass(frozen=True)
class PrintSource(Source):
    """``print x = 1 + 1`` — a single-row source."""

    expressions: tuple[NamedExpr, ...]


# ---------------------------------------------------------------------------
# Tabular operators
# ---------------------------------------------------------------------------


class Operator(Node):
    """One stage of a pipeline; consumes a relation and produces one."""


@dataclass(frozen=True)
class Where(Operator):
    predicate: Expr


@dataclass(frozen=True)
class Project(Operator):
    """``project`` — selects and reorders; may compute. Column order matters."""

    expressions: tuple[NamedExpr, ...]


@dataclass(frozen=True)
class Extend(Operator):
    """``extend`` — appends columns, and *replaces* one that already exists."""

    expressions: tuple[NamedExpr, ...]


@dataclass(frozen=True)
class Take(Operator):
    """``take`` / ``limit`` — row order is not guaranteed (R10)."""

    count: int


@dataclass(frozen=True)
class SortKey(Node):
    expr: Expr
    #: KQL sorts **descending** by default — the opposite of SQL (R6).
    ascending: bool = False
    nulls_first: bool | None = None


@dataclass(frozen=True)
class Sort(Operator):
    keys: tuple[SortKey, ...]


@dataclass(frozen=True)
class Count(Operator):
    """``count`` — a single row named ``Count``."""

    name: str = "Count"


@dataclass(frozen=True)
class Distinct(Operator):
    columns: tuple[str, ...]


@dataclass(frozen=True)
class JoinKey(Node):
    """One equality in a join's ``on`` clause.

    ``on k`` is shorthand for ``$left.k == $right.k``, so both sides default to
    the same name.
    """

    left: str
    right: str


@dataclass(frozen=True)
class Join(Operator):
    """``join kind=... (rightQuery) on keys``.

    The default kind is **innerunique**, which de-duplicates the *left* key set
    before joining. It is NOT a SQL inner join (docs/TRANSLATION.md R5).
    """

    right: Query
    keys: tuple[JoinKey, ...]
    kind: str = "innerunique"


@dataclass(frozen=True)
class Summarize(Operator):
    """``summarize agg, ... by key, ...``.

    Grouping keys come **first** in KQL's output, before the aggregates,
    regardless of where they appear in the query text (R12).
    """

    aggregates: tuple[NamedExpr, ...]
    by: tuple[NamedExpr, ...] = ()


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


@dataclass
class Query(Node):
    """A complete query: a source followed by zero or more operators."""

    source: Source
    operators: list[Operator] = field(default_factory=list)
    #: ``let`` bindings, in declaration order.
    lets: list[tuple[str, Query | Expr]] = field(default_factory=list)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        ops = " | ".join(type(o).__name__ for o in self.operators)
        return f"<Query {type(self.source).__name__}{' | ' + ops if ops else ''}>"
