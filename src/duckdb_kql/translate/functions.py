"""KQL → DuckDB function and operator registry.

Kept as **data**, not code (``docs/ai-cost-strategy.md`` §6.2): one row per KQL
construct, each citing the R-rules it must honour. This table *is* the coverage
surface — growing support means adding rows and tests, not writing new logic.

Placeholders are ``{0}``, ``{1}``, … for arguments.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "FunctionSpec", "SCALAR_FUNCTIONS", "lookup", "BINARY_OPERATORS", "BinarySpec",
    "AggregateSpec", "AGGREGATE_FUNCTIONS", "lookup_aggregate",
]


@dataclass(frozen=True)
class FunctionSpec:
    """One KQL function's DuckDB mapping."""

    name: str
    #: ``native`` (a DuckDB builtin), ``template`` (a SQL expansion), or
    #: ``udf`` (a registered Python UDF — last resort).
    kind: str
    template: str
    #: Accepted argument counts; empty means variadic.
    arities: tuple[int, ...] = ()
    #: R-rules from docs/TRANSLATION.md §4 that this mapping must honour.
    rules: tuple[str, ...] = ()
    note: str = ""

    @property
    def variadic(self) -> bool:
        return not self.arities

    def render(self, args: list[str]) -> str:
        if self.arities and len(args) not in self.arities:
            raise ValueError(
                f"{self.name}() takes {self.arities} argument(s), got {len(args)}"
            )
        if self.variadic:
            # Variadic templates carry a single `{}` that receives the whole
            # comma-joined argument list, e.g. strcat -> concat(a, b, c).
            return self.template.format(", ".join(args))
        return self.template.format(*args)


def _f(name, kind, template, arities=(), rules=(), note=""):
    return FunctionSpec(name, kind, template, arities, rules, note)


# KQL accepts a wider set of date formats than DuckDB's TIMESTAMP cast, and it
# resolves UTC offsets instead of discarding them. Both gaps were measured
# against the emulator (see docs/test-plan.md §6), not guessed:
#
#   '12-02-2022'                -> 2022-12-02  (MM-DD-YYYY; '13-01-2022' is null,
#                                               which is what proves the order)
#   '2022-12-02T13:45:56+02:00' -> 11:45:56    (converted to UTC, NOT truncated)
#
# The offset case is the dangerous one: a plain TIMESTAMP cast parses it happily
# and silently keeps the local wall time, so the query returns a wrong hour with
# no error. Casting via TIMESTAMPTZ resolves it.
#
# ORDER MATTERS. The TIMESTAMPTZ cast must come first -- it is the only branch
# that honours an offset -- and try_strptime only sees what it rejects.
#
# This branch reads the session TimeZone for offset-less input, so the emitted
# SQL is correct only under `SET TimeZone='UTC'` (R8). duckdb_kql.sql() sets it;
# to_sql() documents it for callers running the SQL themselves.
_DATETIME_FORMATS = (
    "%m-%d-%Y", "%m/%d/%Y", "%m.%d.%Y",
    "%m-%d-%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S",
    "%-d %b %Y", "%b %-d, %Y",
    "%a, %d %b %Y %H:%M:%S GMT",
    "%Y%m%d",
)
_FORMAT_LIST = "[" + ", ".join(f"'{f}'" for f in _DATETIME_FORMATS) + "]"
_TODATETIME = (
    "COALESCE(TRY_CAST({0} AS TIMESTAMPTZ) AT TIME ZONE 'UTC', "
    f"try_strptime({{0}}, {_FORMAT_LIST}))"
)


#: Wave 1 scalar and aggregate functions.
SCALAR_FUNCTIONS: dict[str, FunctionSpec] = {
    s.name: s
    for s in [
        # --- strings (R11: character-oriented, not byte-oriented) ----------
        _f("strlen", "native", "length({0})", (1,), ("R11",)),
        _f("toupper", "native", "upper({0})", (1,)),
        _f("tolower", "native", "lower({0})", (1,)),
        _f("strcat", "template", "concat({})", (), (), "variadic"),
        _f("trim_start", "native", "regexp_replace({1}, '^' || {0}, '')", (2,)),
        _f("strrep", "native", "repeat({0}, {1})", (2,)),
        _f("reverse", "native", "reverse({0})", (1,)),
        # KQL substring is 0-based and clamps out-of-range; SQL's is 1-based (R11).
        _f("substring", "template", "substring({0}, {1} + 1)", (2,), ("R11",)),
        _f("split", "native", "str_split({0}, {1})", (2,)),
        _f("replace_string", "native", "replace({0}, {1}, {2})", (3,)),
        _f("indexof", "template", "(position({1} IN {0}) - 1)", (2,), ("R11",)),
        # --- null handling (R4) --------------------------------------------
        _f("isnull", "native", "({0} IS NULL)", (1,), ("R4",)),
        _f("isnotnull", "native", "({0} IS NOT NULL)", (1,), ("R4",)),
        _f("notnull", "native", "({0} IS NOT NULL)", (1,), ("R4",)),
        # isempty is null OR empty string — NOT the same as isnull. The CAST is
        # load-bearing: these accept any type, and comparing a DOUBLE column to
        # '' makes DuckDB raise a conversion error instead of answering false.
        _f("isempty", "template", "({0} IS NULL OR CAST({0} AS VARCHAR) = '')", (1,), ("R4",)),
        _f("isnotempty", "template",
           "({0} IS NOT NULL AND CAST({0} AS VARCHAR) <> '')", (1,), ("R4",)),
        _f("coalesce", "template", "coalesce({})", (), ("R4",), "variadic"),
        _f("isnan", "native", "isnan({0})", (1,)),
        _f("isfinite", "native", "isfinite({0})", (1,)),
        _f("isinf", "native", "isinf({0})", (1,)),
        # --- conversions: null on failure, never an error (R1) --------------
        _f("toint", "template", "TRY_CAST({0} AS INTEGER)", (1,), ("R1",)),
        _f("tolong", "template", "TRY_CAST({0} AS BIGINT)", (1,), ("R1",)),
        _f("todouble", "template", "TRY_CAST({0} AS DOUBLE)", (1,), ("R1",)),
        _f("toreal", "template", "TRY_CAST({0} AS DOUBLE)", (1,), ("R1",)),
        _f("tostring", "template", "CAST({0} AS VARCHAR)", (1,), ("R1",)),
        _f("tobool", "template", "TRY_CAST({0} AS BOOLEAN)", (1,), ("R1",)),
        _f("toboolean", "template", "TRY_CAST({0} AS BOOLEAN)", (1,), ("R1",)),
        _f("todatetime", "template", _TODATETIME, (1,), ("R1", "R8"),
           "wider format surface than TRY_CAST; resolves UTC offsets"),
        _f("totimespan", "template", "TRY_CAST({0} AS INTERVAL)", (1,), ("R1", "R8")),
        _f("toguid", "template", "TRY_CAST({0} AS UUID)", (1,), ("R1",)),
        # --- math -----------------------------------------------------------
        _f("abs", "native", "abs({0})", (1,)),
        _f("ceiling", "native", "ceil({0})", (1,)),
        _f("floor", "template", "floor({0})", (1,)),
        _f("exp", "native", "exp({0})", (1,)),
        _f("log", "native", "ln({0})", (1,)),
        _f("log10", "native", "log10({0})", (1,)),
        _f("log2", "native", "log2({0})", (1,)),
        _f("sqrt", "native", "sqrt({0})", (1,)),
        _f("pow", "native", "power({0}, {1})", (2,)),
        _f("sign", "native", "sign({0})", (1,)),
        _f("round", "native", "round({0})", (1,)),
        _f("gamma", "native", "gamma({0})", (1,)),
        # --- datetime (R8: UTC, origin-sensitive binning) --------------------
        _f("now", "template", "now()", (0,), ("R8",)),
        _f("ago", "template", "(now() - {0})", (1,), ("R8",)),
        _f("startofday", "native", "date_trunc('day', {0})", (1,), ("R8",)),
        _f("startofmonth", "native", "date_trunc('month', {0})", (1,), ("R8",)),
        _f("startofyear", "native", "date_trunc('year', {0})", (1,), ("R8",)),
        _f("getyear", "native", "year({0})", (1,), ("R8",)),
        _f("getmonth", "native", "month({0})", (1,), ("R8",)),
        _f("dayofmonth", "native", "day({0})", (1,), ("R8",)),
        _f("dayofyear", "native", "dayofyear({0})", (1,), ("R8",)),
        _f("dayofweek", "template", "to_days(CAST(dayofweek({0}) AS INTEGER))", (1,), ("R8",)),
        # --- conditional -----------------------------------------------------
        _f("iff", "template", "CASE WHEN {0} THEN {1} ELSE {2} END", (3,)),
        _f("iif", "template", "CASE WHEN {0} THEN {1} ELSE {2} END", (3,)),
        # --- aggregates ------------------------------------------------------
        # count() counts rows; count(x) ignores nulls (R4).
        _f("count", "template", "count(*)", (0,), ("R4",)),
        _f("countif", "template", "count(*) FILTER (WHERE {0})", (1,), ("R4",)),
        _f("sum", "native", "sum({0})", (1,), ("R4",)),
        _f("avg", "native", "avg({0})", (1,), ("R4",)),
        _f("min", "native", "min({0})", (1,), ("R4",)),
        _f("max", "native", "max({0})", (1,), ("R4",)),
        _f("stdev", "native", "stddev_samp({0})", (1,), ("R4",)),
        _f("variance", "native", "var_samp({0})", (1,), ("R4",)),
        # dcount is APPROXIMATE in KQL (R11) — matching it with an approximate
        # DuckDB aggregate is deliberate, not a shortcut.
        _f("dcount", "template", "approx_count_distinct({0})", (1, 2), ("R11",)),
        _f("make_list", "native", "list({0})", (1, 2), ("R4",)),
        _f("make_set", "native", "list(DISTINCT {0})", (1, 2), ("R4",)),
    ]
}


@dataclass(frozen=True)
class BinarySpec:
    """A KQL binary operator's DuckDB rendering.

    ``template`` receives ``{0}`` = left, ``{1}`` = right.
    """

    op: str
    template: str
    rules: tuple[str, ...] = ()
    note: str = ""


def _escape_like(operand: str) -> str:
    """Escape LIKE metacharacters in a runtime value."""
    return f"replace(replace({operand}, '%', '\\%'), '_', '\\_')"


# Term boundary for `has` (R3): KQL matches whole *terms*, so a substring LIKE
# is wrong -- `t has "error"` must be FALSE for "errors". DuckDB's regex engine
# spells the word boundary `\b`; `(?i)` makes the default form case-insensitive.
_HAS = r"regexp_matches({0}, '(?i)\b' || regexp_escape({1}) || '\b')"
_HAS_CS = r"regexp_matches({0}, '\b' || regexp_escape({1}) || '\b')"

# `contains` IS plain substring (R3) -- the mirror image of `has`. The needle is
# a runtime value, so its LIKE metacharacters must be escaped or `a contains "%"`
# would match everything.
_CONTAINS = "({0} ILIKE '%' || " + _escape_like("{1}") + " || '%')"
_CONTAINS_CS = "({0} LIKE '%' || " + _escape_like("{1}") + " || '%')"

#: Wave 1 binary operators, keyed by their KQL spelling.
BINARY_OPERATORS: dict[str, BinarySpec] = {
    b.op: b
    for b in [
        # arithmetic / comparison
        BinarySpec("+", "({0} + {1})"),
        BinarySpec("-", "({0} - {1})"),
        BinarySpec("*", "({0} * {1})"),
        BinarySpec("/", "({0} / {1})"),
        BinarySpec("%", "({0} % {1})"),
        BinarySpec("<", "({0} < {1})"),
        BinarySpec("<=", "({0} <= {1})"),
        BinarySpec(">", "({0} > {1})"),
        BinarySpec(">=", "({0} >= {1})"),
        BinarySpec("and", "({0} AND {1})"),
        BinarySpec("or", "({0} OR {1})"),
        # R2 — equality case sensitivity
        BinarySpec("==", "({0} = {1})", ("R2",), "case-SENSITIVE"),
        BinarySpec("!=", "({0} <> {1})", ("R2", "R4")),
        BinarySpec("<>", "({0} <> {1})", ("R2", "R4")),
        BinarySpec("=~", "(lower({0}) = lower({1}))", ("R2",), "case-INsensitive"),
        BinarySpec("!~", "(lower({0}) <> lower({1}))", ("R2",)),
        # R3 — contains is SUBSTRING, case-insensitive by default
        BinarySpec("contains", _CONTAINS, ("R3",)),
        BinarySpec("!contains", f"NOT {_CONTAINS}", ("R3",)),
        BinarySpec("contains_cs", _CONTAINS_CS, ("R3",)),
        BinarySpec("!contains_cs", f"NOT {_CONTAINS_CS}", ("R3",)),
        # R3 — has is TERM-based, not substring
        BinarySpec("has", _HAS, ("R3",), "whole-term match"),
        BinarySpec("!has", f"NOT {_HAS}", ("R3",)),
        BinarySpec("has_cs", _HAS_CS, ("R3",)),
        BinarySpec("!has_cs", f"NOT {_HAS_CS}", ("R3",)),
        # R3 — prefix / suffix, case-insensitive by default
        BinarySpec("startswith", "({0} ILIKE " + _escape_like("{1}") + " || '%')", ("R3",)),
        BinarySpec("!startswith", "NOT ({0} ILIKE " + _escape_like("{1}") + " || '%')", ("R3",)),
        BinarySpec("startswith_cs", "starts_with({0}, {1})", ("R3",)),
        BinarySpec("endswith", "({0} ILIKE '%' || " + _escape_like("{1}") + ")", ("R3",)),
        BinarySpec("!endswith", "NOT ({0} ILIKE '%' || " + _escape_like("{1}") + ")", ("R3",)),
        BinarySpec("endswith_cs", "ends_with({0}, {1})", ("R3",)),
    ]
}


def lookup(name: str) -> FunctionSpec | None:
    """Find a scalar/aggregate mapping by KQL name (case-insensitive)."""
    return SCALAR_FUNCTIONS.get(name.lower())


# ---------------------------------------------------------------------------
# Aggregates (summarize)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AggregateSpec:
    """One KQL aggregate's DuckDB mapping and its output-name rule (R12).

    ``name_prefix`` drives KQL's auto-naming: ``sum(x)`` becomes ``sum_x``,
    ``make_list(x)`` becomes ``list_x`` (the ``make_`` is dropped), and an
    argument that is not a bare column contributes nothing — ``sum(x+z)`` is
    ``sum_``. All of it measured against the emulator, not inferred.
    """

    name: str
    template: str
    arities: tuple[int, ...] = ()
    #: Prefix for the generated column name; defaults to ``name``.
    name_prefix: str | None = None
    #: Ignore the argument when building the name (`countif(x>1)` -> `countif_`).
    name_ignores_args: bool = False
    #: Emit the argument's own name (`take_any(x)` -> `x`).
    name_is_argument: bool = False
    rules: tuple[str, ...] = ()
    note: str = ""

    @property
    def prefix(self) -> str:
        return self.name_prefix if self.name_prefix is not None else self.name

    def render(self, args: list[str]) -> str:
        if self.arities and len(args) not in self.arities:
            raise ValueError(
                f"{self.name}() takes {self.arities} argument(s), got {len(args)}"
            )
        return self.template.format(*args)


def _a(name, template, arities=(), **kw):
    return AggregateSpec(name, template, arities, **kw)


# KQL returns a neutral value where SQL returns NULL for an aggregate with no
# non-null input, and this shows up constantly: any group whose values are all
# null, and any empty input. Every one of these was measured on the emulator:
#
#            KQL (empty or all-null)   DuckDB
#   sum                0                NULL
#   sumif              0                NULL
#   avg                NaN              NULL
#   stdev / variance   0                NULL
#   make_list/set      []               NULL, or a list OF nulls
#   min / max          null             NULL      (agree)
#   count / dcount     0                0         (agree)
#
# Left unmapped, each is a wrong answer that arrives without an error.
# A bare `[]` is type-polymorphic in COALESCE — DuckDB unifies it with the
# aggregate's element type. Naming a concrete type here (VARCHAR[]) made every
# non-string make_list fail to bind.
_EMPTY_LIST = "[]"

AGGREGATE_FUNCTIONS: dict[str, AggregateSpec] = {
    s.name: s
    for s in [
        _a("count", "count(*)", (0,), name_ignores_args=True, rules=("R4", "R12")),
        _a("countif", "count(*) FILTER (WHERE {0})", (1,),
           name_ignores_args=True, rules=("R4", "R12")),
        # KQL sums to 0, not null, when nothing survives.
        _a("sum", "coalesce(sum({0}), 0)", (1,), rules=("R4",)),
        _a("sumif", "coalesce(sum({0}) FILTER (WHERE {1}), 0)", (2,), rules=("R4",)),
        # ...but averages to NaN. Not a typo — the emulator says so.
        _a("avg", "coalesce(avg({0}), CAST('NaN' AS DOUBLE))", (1,), rules=("R4",)),
        _a("avgif", "coalesce(avg({0}) FILTER (WHERE {1}), CAST('NaN' AS DOUBLE))",
           (2,), rules=("R4",)),
        _a("min", "min({0})", (1,), rules=("R4",)),
        _a("max", "max({0})", (1,), rules=("R4",)),
        _a("stdev", "coalesce(stddev_samp({0}), 0.0)", (1,), rules=("R4",)),
        _a("stdevif", "coalesce(stddev_samp({0}) FILTER (WHERE {1}), 0.0)", (2,)),
        _a("variance", "coalesce(var_samp({0}), 0.0)", (1,), rules=("R4",)),
        _a("varianceif", "coalesce(var_samp({0}) FILTER (WHERE {1}), 0.0)", (2,)),
        # dcount is APPROXIMATE in KQL (R11), so an approximate DuckDB aggregate
        # looks like the honest match — but measured against the emulator it is
        # not. At the cardinalities in the corpus KQL's HLL returns the exact
        # value while approx_count_distinct is ~13% low (37 vs 32), which is far
        # outside any sane tolerance and, worse, reorders `top N by dcount`.
        # Exact counting matches the oracle AND is reproducible; the residual
        # risk is the reverse divergence at cardinalities high enough for KQL's
        # estimate to drift, which the drift lane would surface.
        _a("dcount", "count(DISTINCT {0})", (1, 2), rules=("R11",),
           note="exact: KQL's estimate is exact at corpus cardinalities"),
        _a("dcountif", "count(DISTINCT {0}) FILTER (WHERE {1})", (2, 3),
           rules=("R11",)),
        # make_list/make_set SKIP nulls; plain list() keeps them, so an
        # all-null group would come back as [null, null] instead of [].
        _a("make_list", f"coalesce(list({{0}}) FILTER (WHERE {{0}} IS NOT NULL), {_EMPTY_LIST})",
           (1, 2), name_prefix="list", rules=("R4",)),
        _a("make_set",
           f"coalesce(list(DISTINCT {{0}}) FILTER (WHERE {{0}} IS NOT NULL), {_EMPTY_LIST})",
           (1, 2), name_prefix="set", rules=("R4",)),
        _a("any", "any_value({0})", (1,)),
        _a("take_any", "any_value({0})", (1,), name_is_argument=True),
        # quantile_DISC, not quantile_cont: KQL uses nearest-rank, not linear
        # interpolation. Measured per-state on the fixture, disc matched all 52
        # groups exactly while cont was off by up to 39% — a gap the 5%
        # approximate-function tolerance would have hidden on smaller inputs.
        # At large N KQL switches to an estimate, and there the tolerance
        # legitimately covers the remaining ~0.07%.
        _a("percentile", "quantile_disc({0}, {1} / 100.0)", (2,), rules=("R11",)),
    ]
}


def lookup_aggregate(name: str) -> AggregateSpec | None:
    return AGGREGATE_FUNCTIONS.get(name.lower())
