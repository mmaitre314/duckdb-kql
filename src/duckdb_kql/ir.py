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
from typing import TYPE_CHECKING

#: What a KQL literal can hold once lexed. ``dynamic``, ``datetime``,
#: ``timespan`` and ``guid`` all arrive as their source text; ``null`` is None.
LiteralValue = int | float | str | bool | None

if TYPE_CHECKING:
    # Type-checker only: `params` imports nothing from here, and keeping the
    # runtime import out avoids making the IR depend on the binding layer.
    from .params import ParameterDeclaration

__all__ = [
    "Node", "Expr", "Operator", "Query",
    # expressions
    "Literal", "ColumnRef", "BinaryOp", "UnaryOp", "FunctionCall", "NamedExpr",
    "InList", "PathAccess", "PathStep", "Parameter",
    # sources
    "TableRef", "DataTable", "PrintSource", "RangeSource",
    # operators
    "Summarize", "Join", "JoinKey", "Lookup", "MvExpand", "ProjectAway", "ProjectRename",
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

    ``value`` is the *lexical* value, not a converted one: a datetime literal
    holds the text KQL wrote, because the conversion depends on the same
    format-guessing ``todatetime()`` does (R8). ``kind`` is what says how to
    read it.
    """

    value: LiteralValue
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
class Parameter(Expr):
    """A value supplied by the caller, never spliced into the query text.

    ``declare query_parameters(user:string)`` binds a *value*, not a fragment of
    KQL. Rendering it as a literal would put caller-controlled text into the
    generated SQL and make the escaping rules the only thing standing between a
    caller and injection. Instead it renders as a DuckDB prepared-statement
    placeholder (``$slot``) and the value travels out of band, so no amount of
    quoting in the value can change the shape of the statement.

    ``slot`` is generated, never the caller's name: the SQL text then contains no
    caller-controlled bytes at all.
    """

    name: str
    kind: str
    slot: str


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
    """A table, optionally in another database.

    ``database("Sales").Orders`` is Kusto's cross-database reference and it
    lowers to here rather than to a function call: `database()` is not a scalar
    function, it is the first half of a qualified name. DuckDB spells the same
    thing ``"Sales"."Orders"`` once the file is attached, which is what makes
    this a rename rather than a feature.
    """

    name: str
    #: ``None`` means the connection's current database, which is what an
    #: unqualified name means in both languages.
    database: str | None = None

    @property
    def qualified(self) -> str:
        """``Orders`` or ``Sales.Orders`` — for messages, not for SQL."""
        return self.name if self.database is None else f"{self.database}.{self.name}"


@dataclass(frozen=True)
class CommandSource(Source):
    """A control command's result, standing in as a table.

    Kusto composes the two dialects: `.show tables | limit 3` runs the command
    and pipes its result through ordinary query operators. The command half has
    no query syntax — it is a closed set of literals, not a grammar — so it
    arrives here already translated, and the pipeline after the first `|` is
    lowered by the normal path with this as its source.

    ``columns`` is what the command produces, needed by the operators that
    resolve names before the query runs (`join` renaming, `extend` ordering).
    """

    sql: str
    columns: tuple[str, ...]
    #: The command as written, for error messages that name what the user typed.
    command: str


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
class GetSchema(Operator):
    """``getschema`` — replace the rows with a description of the columns.

    Unusual among operators in that its output does not depend on the input's
    *values* at all, only on its shape. That is what makes it useful as a test:
    it turns "what type is this column" into a row you can assert on.
    """


@dataclass(frozen=True)
class Count(Operator):
    """``count`` — a single row named ``Count``."""

    name: str = "Count"


@dataclass(frozen=True)
class Distinct(Operator):
    columns: tuple[str, ...]


@dataclass(frozen=True)
class RangeSource(Source):
    """``range name from start to stop step step`` — a generated column.

    Both endpoints are **inclusive**, and the result is empty when the range
    runs backwards.
    """

    name: str
    start: Expr
    stop: Expr
    step: Expr


@dataclass(frozen=True)
class RenderedAggregate(Expr):
    """An aggregate call already translated to SQL, standing in its own place.

    `summarize Revenue = round(sum(Total), 2)` is a **scalar expression over an
    aggregate**, and SQL writes it the same way. So the translation is: render
    the aggregates, put each back where it stood, and render the surrounding
    expression by the ordinary path — which is what keeps the null semantics,
    the operator table and the type sniffing consistent between an expression
    that happens to contain an aggregate and one that does not.

    This node is what "put each back" means. It is produced only by the
    summarize translator and never by lowering, so it cannot become a general
    escape hatch for hand-written SQL.
    """

    sql: str


@dataclass(frozen=True)
class InList(Expr):
    """``x in (a, b, ...)`` and its negated / case-insensitive variants."""

    value: Expr
    items: tuple[Expr, ...]
    negated: bool = False
    case_insensitive: bool = False
    #: A tabular right-hand side: ``x in (SomeTable | project col)``. When set,
    #: ``items`` is empty and membership is tested against the query's first
    #: column.
    subquery: Query | None = None


@dataclass(frozen=True)
class PathStep(Node):
    """One step of a ``dynamic`` access: ``.name``, ``['name']`` or ``[expr]``."""

    #: Set for a property step; None for an index step.
    name: str | None = None
    #: Set for an index step; None for a property step.
    index: Expr | None = None


@dataclass(frozen=True)
class PathAccess(Expr):
    """``d.a.b`` / ``d[0]`` / ``d['a']`` — navigation into a dynamic value.

    A missing property or an out-of-range index yields **null**, never an
    error (docs/TRANSLATION.md R9).
    """

    base: Expr
    steps: tuple[PathStep, ...]


@dataclass(frozen=True)
class MvExpand(Operator):
    """``mv-expand col`` — one output row per array element."""

    column: str
    name: str | None = None
    #: `with_itemindex=Name` adds a 0-based position column.
    item_index: str | None = None


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
class Lookup(Operator):
    """``lookup kind=... (rightQuery) on keys`` — enrich rows from a dimension table.

    Deliberately **not** a flavour of :class:`Join`, because two of its defining
    behaviours differ (docs/TRANSLATION.md R14):

    * the default kind is **leftouter**, not `join`'s `innerunique`;
    * the right side's **key columns are dropped** from the output rather than
      carried through with a ``1`` suffix.

    Only `leftouter` and `inner` exist — Kusto rejects every other kind.
    """

    right: Query
    keys: tuple[JoinKey, ...]
    kind: str = "leftouter"


@dataclass(frozen=True)
class ProjectAway(Operator):
    """``project-away c1, c2`` — drop columns, keep the rest in order."""

    columns: tuple[str, ...]


@dataclass(frozen=True)
class ProjectRename(Operator):
    """``project-rename new = old`` — rename in place, keeping position."""

    renames: tuple[tuple[str, str], ...]


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
    #: ``declare query_parameters`` declarations, in declaration order. Values
    #: are supplied at execution time, not here.
    parameters: list[ParameterDeclaration] = field(default_factory=list)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        ops = " | ".join(type(o).__name__ for o in self.operators)
        return f"<Query {type(self.source).__name__}{' | ' + ops if ops else ''}>"
