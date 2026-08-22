"""KQL → DuckDB function and operator registry.

Kept as **data**, not code (``docs/ai-cost-strategy.md`` §6.2): one row per KQL
construct, each citing the R-rules it must honour. This table *is* the coverage
surface — growing support means adding rows and tests, not writing new logic.

Placeholders are ``{0}``, ``{1}``, … for arguments.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "FunctionSpec", "SCALAR_FUNCTIONS", "lookup", "BINARY_OPERATORS", "BinarySpec",
    "AggregateSpec", "AGGREGATE_FUNCTIONS", "lookup_aggregate", "term_match_sql",
    "is_term_char", "can_fold_term_boundary",
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


def _f(
    name: str,
    kind: str,
    template: str,
    arities: tuple[int, ...] = (),
    rules: tuple[str, ...] = (),
    note: str = "",
) -> FunctionSpec:
    """Shorthand for one registry row — the table below is 100+ calls to it."""
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
# SQL is correct only under `SET TimeZone='UTC'` (R8). duckdb_kql.kql() sets it;
# to_sql() documents it for callers running the SQL themselves.
_DATETIME_FORMATS = (
    "%m-%d-%Y", "%m/%d/%Y", "%m.%d.%Y",
    "%m-%d-%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S",
    "%-d %b %Y", "%b %-d, %Y",
    "%a, %d %b %Y %H:%M:%S GMT",
    "%Y%m%d",
)
_FORMAT_LIST = "[" + ", ".join(f"'{f}'" for f in _DATETIME_FORMATS) + "]"
# The CAST to VARCHAR on the strptime branch is a binding fix, not a
# conversion. `todatetime(T)` where T is already a datetime is a no-op in KQL,
# but `try_strptime(TIMESTAMP, VARCHAR[])` has no overload, so the whole
# expression failed to *bind* — and surfaced as a raw DuckDB BinderException
# rather than any KQL error. A column carries no type at translation time, so
# this cannot be decided statically; making the branch bind for both inputs can.
# On a TIMESTAMP the first branch already succeeds, so the second is never the
# answer; on a VARCHAR the cast is a no-op.
_TODATETIME = (
    "COALESCE(TRY_CAST({0} AS TIMESTAMPTZ) AT TIME ZONE 'UTC', "
    f"try_strptime(CAST({{0}} AS VARCHAR), {_FORMAT_LIST}))"
)

# A KQL timespan string is `[-][d.]hh:mm:ss[.fffffff]`. DuckDB's INTERVAL cast
# handles `hh:mm:ss` but returns NULL for the leading day part, so `4.00:00:00`
# silently became null. Split the days off and add them back.
_TOTIMESPAN = (
    "CASE WHEN regexp_matches({0}, '^-?[0-9]+\\.[0-9]{{1,2}}:') THEN "
    "(CASE WHEN starts_with({0}, '-') THEN -1 ELSE 1 END) * "
    "(to_days(CAST(regexp_extract(ltrim({0}, '-'), '^([0-9]+)\\.', 1) AS INTEGER)) "
    "+ CAST(regexp_replace(ltrim({0}, '-'), '^[0-9]+\\.', '') AS INTERVAL)) "
    "ELSE TRY_CAST({0} AS INTERVAL) END"
)


#: Wave 1 scalar and aggregate functions.
SCALAR_FUNCTIONS: dict[str, FunctionSpec] = {
    s.name: s
    for s in [
        # --- strings (R11: character-oriented, not byte-oriented) ----------
        _f("strlen", "native", "length({0})", (1,), ("R11", "R17")),
        _f("toupper", "native", "upper({0})", (1,), ("R17",)),
        _f("tolower", "native", "lower({0})", (1,), ("R17",)),
        _f("strcat", "template", "concat({})", (), ("R17",), "variadic"),
        _f("trim_start", "native", "regexp_replace({1}, '^' || {0}, '')", (2,)),
        _f("strrep", "native", "repeat({0}, {1})", (2,)),
        _f("reverse", "native", "reverse({0})", (1,)),
        # KQL substring is 0-based, clamps out of range, and counts a NEGATIVE
        # start from the end — all measured (R11):
        #   ('abcdefg', 1, 3)  -> 'bcd'      ('abcdefg', 10, 3) -> ''
        #   ('abcdefg', -3, 2) -> 'ef'       ('abc',     -10)   -> ''
        #   ('abcdefg', 1, -1) -> ''         ('abcdefg', 5, 10) -> 'fg'
        # A start that reaches back past the start of the string is EMPTY, not
        # clamped to 0 — `substring('abc', -10)` is '' and not 'abc'. Handing
        # the negative index to DuckDB directly is also wrong: it counts from
        # the end but pairs the length differently, so the offset is resolved
        # here and only a non-negative index ever reaches `substring`.
        # Rendered by `_render_substring`; this row carries the arities.
        _f("substring", "template", "", (2, 3), ("R11", "R17")),
        _f("split", "native", "str_split({0}, {1})", (2,), ("R17",)),
        _f("replace_string", "native", "replace({0}, {1}, {2})", (3,)),
        _f("indexof", "template", "(position({1} IN {0}) - 1)", (2,), ("R11", "R17")),
        _f("strcat_delim", "template", "concat_ws({})", (), ("R17",), "variadic"),
        _f("replace_regex", "template", "regexp_replace({0}, {1}, {2}, 'g')", (3,)),
        _f("replace", "native", "replace({0}, {1}, {2})", (3,),
           note="Azure Monitor spells replace_string as replace"),
        # KQL's extract takes (regex, captureGroup, text); DuckDB's takes
        # (text, regex, group) -- the argument order is a silent trap.
        _f("extract", "template",
           "regexp_extract({2}, {0}, CAST({1} AS INTEGER))", (3, 4), ("R11",)),
        _f("extract_all", "template", "", (2, 3), ("R11",), "special"),
        _f("countof", "template", "", (2, 3), ("R11",), "variadic:countof"),
        _f("base64_encode_tostring", "template", "to_base64(CAST({0} AS BLOB))", (1,)),
        _f("base64_encodestring", "template", "to_base64(CAST({0} AS BLOB))", (1,),
           note="Azure Monitor's spelling of base64_encode_tostring"),
        # KQL returns an EMPTY STRING when the decoded bytes are not valid
        # UTF-8; a plain cast yields mojibake instead.
        _f("base64_decode_tostring", "template",
           "coalesce(TRY_CAST(TRY_CAST(from_base64({0}) AS VARCHAR) AS VARCHAR), '')",
           (1,)),
        _f("base64_decodestring", "template",
           "coalesce(TRY_CAST(TRY_CAST(from_base64({0}) AS VARCHAR) AS VARCHAR), '')",
           (1,),
           note="Azure Monitor's spelling of base64_decode_tostring"),
        # --- null handling (R4) --------------------------------------------
        _f("isnull", "native", "({0} IS NULL)", (1,), ("R4",)),
        _f("isnotnull", "native", "({0} IS NOT NULL)", (1,), ("R4",)),
        _f("notnull", "native", "({0} IS NOT NULL)", (1,), ("R4",)),
        # isempty is null OR empty string — NOT the same as isnull. The CAST is
        # load-bearing: these accept any type, and comparing a DOUBLE column to
        # '' makes DuckDB raise a conversion error instead of answering false.
        _f("isempty", "template",
           "({0} IS NULL OR CAST({0} AS VARCHAR) = '')", (1,), ("R4", "R17")),
        _f("isnotempty", "template",
           "({0} IS NOT NULL AND CAST({0} AS VARCHAR) <> '')", (1,), ("R4", "R17")),
        _f("coalesce", "template", "coalesce({})", (), ("R4",), "variadic"),
        _f("isnan", "native", "isnan({0})", (1,)),
        _f("isfinite", "native", "isfinite({0})", (1,)),
        _f("isinf", "native", "isinf({0})", (1,)),
        # --- conversions: null on failure, never an error (R1) --------------
        # KQL **truncates toward zero**; a SQL cast rounds. `tolong(1.7)` is 1
        # in Kusto and was 2 here — a silent wrong answer for every
        # non-integral value, and the reason `tolong(avg(x))` disagreed with
        # the engine by one. Measured across 17 probes.
        #
        # The fallback cast is not belt-and-braces: routing through DOUBLE
        # loses precision above 2^53, so `tolong(9223372036854775807)` comes
        # back NULL from the first branch and the plain cast answers it.
        _f("toint", "template",
           "coalesce(TRY_CAST(trunc(TRY_CAST({0} AS DOUBLE)) AS INTEGER), "
           "TRY_CAST({0} AS INTEGER))", (1,), ("R1",)),
        _f("tolong", "template",
           "coalesce(TRY_CAST(trunc(TRY_CAST({0} AS DOUBLE)) AS BIGINT), "
           "TRY_CAST({0} AS BIGINT))", (1,), ("R1",)),
        _f("todouble", "template", "TRY_CAST({0} AS DOUBLE)", (1,), ("R1",)),
        _f("toreal", "template", "TRY_CAST({0} AS DOUBLE)", (1,), ("R1",)),
        _f("tostring", "template", "CAST({0} AS VARCHAR)", (1,), ("R1", "R17")),
        _f("tobool", "template", "TRY_CAST({0} AS BOOLEAN)", (1,), ("R1",)),
        _f("toboolean", "template", "TRY_CAST({0} AS BOOLEAN)", (1,), ("R1",)),
        _f("todatetime", "template", _TODATETIME, (1,), ("R1", "R8"),
           "wider format surface than TRY_CAST; resolves UTC offsets"),
        _f("totimespan", "template", _TOTIMESPAN, (1,), ("R1", "R8"),
           "KQL timespans carry a d. prefix that DuckDB's INTERVAL cast rejects"),
        _f("timespan", "template", _TOTIMESPAN, (1,), ("R1", "R8"), "alias of totimespan"),
        _f("toguid", "template", "TRY_CAST({0} AS UUID)", (1,), ("R1",)),
        # --- dynamic / JSON (R9: missing -> null, never an error) -----------
        _f("parse_json", "template", "TRY_CAST({0} AS JSON)", (1,), ("R9",)),
        _f("todynamic", "template", "TRY_CAST({0} AS JSON)", (1,), ("R9",)),
        _f("gettype", "template", "lower(json_type({0}))", (1,), ("R9",)),
        # Two divergences in one row, both measured. DuckDB's json_array_length
        # returns **UBIGINT**, so `array_length(x) - 1` widened to HUGEINT and
        # `generate_series` had no overload for it — a `range` over an array's
        # length failed to bind. KQL's array_length is a `long`, so the CAST is
        # fidelity, not defensiveness. And a non-array is **null** in KQL, where
        # json_array_length answers 0: `array_length(dynamic({'a':1}))` and
        # `array_length(dynamic(null))` are both null on the emulator, and 0 is
        # the kind of wrong answer a loop bound silently swallows.
        _f("array_length", "template",
           "(CASE WHEN json_type({0}) = 'ARRAY' "
           "THEN CAST(json_array_length({0}) AS BIGINT) END)", (1,), ("R9",)),
        # list_concat is binary in DuckDB, so a variadic KQL call has to fold.
        _f("array_concat", "template", "", (), ("R9",), "variadic:fold-list_concat"),
        # array_index_of returns -1 when absent, NOT null: `== -1` is how KQL
        # queries test for absence, so a null would silently change the answer.
        _f("array_index_of", "template",
           "coalesce(list_position(CAST({0} AS JSON[]), CAST(to_json({1}) AS JSON)) - 1, -1)",
           (2,), ("R9",)),
        # KQL's array_slice takes start and END index, both INCLUSIVE, and
        # counts from the end for negatives. SQL slicing is 1-based.
        _f("array_slice", "template",
           "to_json(list_slice(CAST({0} AS JSON[]), "
           "CASE WHEN {1} < 0 THEN {1} ELSE {1} + 1 END, "
           "CASE WHEN {2} < 0 THEN {2} ELSE {2} + 1 END))", (3,), ("R9",)),
        _f("array_sort_asc", "template",
           "to_json(list_sort(CAST({0} AS JSON[])))", (1,), ("R9",)),
        _f("array_sort_desc", "template",
           "to_json(list_reverse_sort(CAST({0} AS JSON[])))", (1,), ("R9",)),
        _f("array_sum", "template",
           "CAST(list_sum(CAST({0} AS DOUBLE[])) AS DOUBLE)", (1,), ("R9",)),
        _f("array_reverse", "template",
           "to_json(list_reverse(CAST({0} AS JSON[])))", (1,), ("R9",)),
        _f("pack_array", "template", "to_json([{}])", (), ("R9",), "variadic"),
        _f("pack", "template", "json_object({})", (), ("R9",), "variadic"),
        _f("bag_pack", "template", "json_object({})", (), ("R9",), "variadic"),
        _f("zip", "template", "", (), ("R9",), "variadic:zip"),
        _f("set_has_element", "template",
           "list_contains(CAST({0} AS JSON[]), CAST(to_json({1}) AS JSON))", (2,), ("R9",)),
        # --- hashing --------------------------------------------------------
        # md5/sha1/sha256 match KQL's output byte for byte (verified against the
        # emulator). `hash()` and `hash_xxhash64()` are xxhash64, which DuckDB
        # does not provide -- and DuckDB's own hash() is a DIFFERENT function,
        # so mapping to it would return plausible-looking wrong digests.
        # The CAST matters: KQL hashes any type by its string form
        # (hash_md5(123) is md5("123")), while DuckDB's md5 only takes VARCHAR
        # and would fail to bind.
        _f("hash_md5", "native", "md5(CAST({0} AS VARCHAR))", (1,)),
        _f("hash_sha1", "native", "sha1(CAST({0} AS VARCHAR))", (1,)),
        _f("hash_sha256", "native", "sha256(CAST({0} AS VARCHAR))", (1,)),
        # --- math -----------------------------------------------------------
        _f("abs", "native", "abs({0})", (1,)),
        _f("ceiling", "native", "ceil({0})", (1,)),
        # KQL's `floor` IS `bin` — the emulator refuses `floor(7.9)` with
        # "SEM0219: bin(): function expects 2 argument(s)", and `floor(-7, 5)`
        # is -10, the bin answer, not -7. Rendered by `render_bin`; this row
        # exists so the arity is checked and the name is known.
        _f("floor", "template", "", (2,), note="alias of bin"),
        _f("exp", "native", "exp({0})", (1,)),
        _f("log", "native", "ln({0})", (1,)),
        _f("log10", "native", "log10({0})", (1,)),
        _f("log2", "native", "log2({0})", (1,)),
        _f("sqrt", "native", "sqrt({0})", (1,)),
        _f("pow", "native", "power({0}, {1})", (2,)),
        _f("sign", "native", "sign({0})", (1,)),
        # Rendered by translate._render_round: two arities, and both need a
        # DOUBLE cast to match Kusto. Registered so `round` is a known
        # scalar function and reports its arities.
        _f("round", "template", "round(CAST({0} AS DOUBLE))", (1, 2)),
        _f("gamma", "native", "gamma({0})", (1,)),
        _f("exp2", "template", "pow(CAST(2 AS DOUBLE), {0})", (1,)),
        _f("exp10", "template", "pow(CAST(10 AS DOUBLE), {0})", (1,)),
        # --- bitwise ---------------------------------------------------------
        _f("binary_and", "template", "({0} & {1})", (2,)),
        _f("binary_or", "template", "({0} | {1})", (2,)),
        _f("binary_xor", "template", "xor({0}, {1})", (2,)),
        _f("binary_not", "template", "(~{0})", (1,)),
        _f("binary_shift_left", "template", "({0} << {1})", (2,)),
        _f("binary_shift_right", "template", "({0} >> {1})", (2,)),
        # --- conditional -----------------------------------------------------
        _f("max_of", "template", "greatest({})", (), (), "variadic"),
        _f("min_of", "template", "least({})", (), (), "variadic"),
        # --- datetime (R8: UTC, origin-sensitive binning) --------------------
        # DuckDB's now() is TIMESTAMPTZ; rendering it needs the ICU/pytz module,
        # which is not always present, and KQL's now() is a naive UTC timestamp
        # regardless. Found by the Azure Monitor profile probes.
        _f("now", "template", "(now() AT TIME ZONE 'UTC')", (0, 1), ("R8",)),
        _f("ago", "template", "((now() AT TIME ZONE 'UTC') - {0})", (1,), ("R8",)),
        _f("getyear", "native", "year({0})", (1,), ("R8",)),
        _f("getmonth", "native", "month({0})", (1,), ("R8",)),
        _f("dayofmonth", "native", "day({0})", (1,), ("R8",)),
        _f("dayofyear", "native", "dayofyear({0})", (1,), ("R8",)),
        _f("dayofweek", "template", "to_days(CAST(dayofweek({0}) AS INTEGER))", (1,), ("R8",)),
        _f("monthofyear", "native", "month({0})", (1,), ("R8",)),
        # start/end-of-period take an OPTIONAL offset in periods; ignoring it
        # returns the wrong period silently, so they are special forms.
        _f("startofday", "template", "", (1, 2), ("R8",), "special"),
        _f("startofmonth", "template", "", (1, 2), ("R8",), "special"),
        _f("startofyear", "template", "", (1, 2), ("R8",), "special"),
        _f("startofweek", "template", "", (1, 2), ("R8",), "special"),
        _f("endofday", "template", "", (1, 2), ("R8",), "special"),
        _f("endofmonth", "template", "", (1, 2), ("R8",), "special"),
        _f("endofyear", "template", "", (1, 2), ("R8",), "special"),
        _f("endofweek", "template", "", (1, 2), ("R8",), "special"),
        _f("hourofday", "native", "hour({0})", (1,), ("R8",)),
        _f("weekofyear", "native", "weekofyear({0})", (1,), ("R8",)),
        _f("week_of_year", "native", "weekofyear({0})", (1,), ("R8",)),
        # KQL weeks start on SUNDAY; DuckDB's date_trunc('week') starts Monday,
        # so using it would shift the boundary by a day for every Sunday.
        _f("startofweek", "template",
           "(date_trunc('day', {0}) - to_days(CAST(dayofweek({0}) AS INTEGER)))",
           (1, 2), ("R8",)),
        # KQL's end-of-* is the last representable instant *inside* the period,
        # not the start of the next one.
        _f("endofday", "template",
           "(date_trunc('day', {0}) + INTERVAL 1 DAY - INTERVAL 1 MICROSECOND)",
           (1, 2), ("R8",)),
        _f("endofmonth", "template",
           "(date_trunc('month', {0}) + INTERVAL 1 MONTH - INTERVAL 1 MICROSECOND)",
           (1, 2), ("R8",)),
        _f("endofyear", "template",
           "(date_trunc('year', {0}) + INTERVAL 1 YEAR - INTERVAL 1 MICROSECOND)",
           (1, 2), ("R8",)),
        _f("endofweek", "template",
           "(date_trunc('day', {0}) - to_days(CAST(dayofweek({0}) AS INTEGER)) "
           "+ INTERVAL 7 DAY - INTERVAL 1 MICROSECOND)", (1, 2), ("R8",)),
        _f("make_datetime", "template", "", (3, 6), ("R8",), "variadic:make_datetime"),
        _f("make_timespan", "template", "", (2, 3), ("R8",), "variadic:make_timespan"),
        # --- conditional -----------------------------------------------------
        _f("iff", "template", "CASE WHEN {0} THEN {1} ELSE {2} END", (3,)),
        _f("iif", "template", "CASE WHEN {0} THEN {1} ELSE {2} END", (3,)),
        # `not()` is a FUNCTION in KQL, not the `!` prefix operator, and it does
        # NOT get R4's totality treatment: measured on the emulator,
        # `not(bool(null))` is **null**, not true. SQL's `NOT NULL` is null too,
        # so the plain mapping is exact. The cast is what makes `not(1)` false
        # rather than a binder error — KQL accepts a non-bool argument.
        _f("not", "template", "(NOT CAST({0} AS BOOLEAN))", (1,), ("R4",)),
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
        # No scalar `dcount` row: it is only valid inside `summarize`, and the
        # aggregate registry maps it to EXACT count(DISTINCT) after measuring
        # approx_count_distinct ~13% low against the oracle. A scalar row saying
        # the opposite is a landmine for whatever reaches for it next.
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
    #: What KQL yields when exactly one operand is null, where SQL yields NULL.
    #: ``None`` means SQL's NULL is already what KQL produces. Measured on the
    #: emulator, not inferred — see ``_apply_null_semantics`` and R4.
    null_result: str | None = None


#: DuckDB's LIKE has **no default escape character**, so escaping `%`/`_` with a
#: backslash does nothing on its own -- the pattern then requires a literal
#: backslash and matches nothing. Every LIKE built from a runtime value must
#: therefore carry this clause. Without it `s contains "user_id"` silently
#: returned zero rows: the `_` stayed a wildcard, the added `\` did not.
#: Found by a random differential sweep against the emulator.
_LIKE_ESCAPE = " ESCAPE '\\'"


def _escape_like(operand: str) -> str:
    """Escape LIKE metacharacters in a runtime value.

    Only half the job -- the resulting comparison must also end with
    :data:`_LIKE_ESCAPE`, or the escaping is inert.
    """
    return f"replace(replace({operand}, '%', '\\%'), '_', '\\_')"


# Term boundary for `has` (R3): KQL matches whole *terms*, so a substring LIKE
# is wrong -- `t has "error"` must be FALSE for "errors".
#
# NOT regex `\b`, which is what this used to emit. `\b` treats `_` as a word
# character, so `"a_b" has "a"` came back FALSE here and TRUE on the emulator.
# A Kusto term is a run of Unicode letters and digits; every other character
# delimits one, underscore included. Measured across ~30 punctuation characters:
# all of them delimit, while `a1`, `aa` and `ea` (accented) do not.
#
# Spelled `\pL\pN` rather than `\p{L}\p{N}`: these strings are also used as
# `str.format` templates, where a brace is a placeholder. RE2 accepts the
# single-letter form and both were checked to behave identically.
_TERM_START = r"(?:^|[^\pL\pN])"
_TERM_END = r"(?:$|[^\pL\pN])"


def is_term_char(char: str) -> bool:
    """Whether *char* is a Kusto **term** character — RE2's ``[\\pL\\pN]``.

    ``\\pL`` is Unicode's Letter categories and ``\\pN`` its Number ones, which
    is exactly ``str.isalpha()`` and ``str.isnumeric()``. Not ``isalnum()``,
    which also admits Nd-adjacent oddities, and not ``\\w``, which admits the
    underscore — the whole reason `has` cannot be written with ``\\b``.

    Only meaningful where :func:`can_fold_term_boundary` is true. See it for
    why this cannot simply be trusted.
    """
    return bool(char) and (char.isalpha() or char.isnumeric())


def can_fold_term_boundary(needle: str) -> bool:
    """Whether *needle*'s boundaries may be decided here rather than by DuckDB.

    Only when both edge characters are **ASCII** — and that restriction is not
    caution, it is a caught bug. ``str.isalpha()`` reads Python's bundled
    Unicode tables, whose version travels with the *interpreter*: 3.10 ships
    Unicode 13.0 and 3.11 ships 14.0, while DuckDB's RE2 tables are its own.
    U+0870 (Arabic Extended-B, added in 14.0) is a letter to DuckDB and to
    Python 3.11, and **not** a letter to Python 3.10 — so folding there would
    have made `has` match differently on one Python than another. CI caught it
    on the 3.10 leg; it passed locally on 3.11.

    ASCII is the subset every Unicode version has always agreed on, and it is
    what real needles are made of. A needle with a non-ASCII edge keeps the
    emitted ``CASE`` form, which asks the database and is therefore always
    right — it is only the extra ~3x that is given up, not the ~300x from
    unrolling the list in the first place.
    """
    return needle[:1].isascii() and needle[-1:].isascii()


def term_match_sql(
    haystack: str,
    needle: str,
    *,
    case_sensitive: bool = False,
    needle_text: str | None = None,
) -> str:
    """SQL testing whether *needle* occurs in *haystack* as a whole term.

    Shared by `has`/`has_cs` and the `has_any`/`has_all` list forms, so the term
    definition lives in exactly one place.

    The boundary is applied at an edge **only when the needle's own character at
    that edge is a term character** — found by a random differential sweep, not
    by reading the docs. Measured on the emulator:

    ======================  =========  ==========================================
    query                   Kusto      why
    ======================  =========  ==========================================
    ``'xa b' has "a "``     false      needle starts with `a`, so `x` blocks it
    ``'a b'  has "a "``     true       same needle, now at a real boundary
    ``'x ab y' has " a"``   false      needle ends with `a`, so `b` blocks it
    ``'b .b-' has " "``     **true**   needle is all delimiters: plain substring
    ``anything has ""``     true       ditto, degenerately
    ======================  =========  ==========================================

    Wrapping an all-delimiter needle in boundaries makes ``has " "`` false where
    Kusto says true, so the two arms are load-bearing rather than defensive.

    **Pass `needle_text` whenever the needle is a literal.** The pattern is then
    a constant and RE2 compiles it once; otherwise it is concatenated from two
    ``CASE`` expressions and DuckDB rebuilds — and recompiles — it **per row**.
    (Subject to :func:`can_fold_term_boundary`, which withholds the fold where
    Python's Unicode tables cannot be trusted to match the database's.)
    An earlier version left this to the optimizer on the theory that "for the
    usual string literal DuckDB folds them away". It does not: measured on the
    5,000-row corpus fixture, folding here took one `has_any` from 52ms to 15ms,
    and the same missing fold inside a `list_filter` lambda (where the needle
    genuinely is per-row) cost **28 seconds**. See `_render_has_list`.
    """
    flag = "" if case_sensitive else "(?i)"
    if needle_text is not None and can_fold_term_boundary(needle_text):
        lead = _TERM_START if is_term_char(needle_text[:1]) else ""
        trail = _TERM_END if is_term_char(needle_text[-1:]) else ""
        pattern = f"'{flag}{lead}' || regexp_escape({needle})"
        if trail:
            pattern += f" || '{trail}'"
        return f"regexp_matches({haystack}, {pattern})"
    lead = f"CASE WHEN regexp_matches({needle}, '^[\\pL\\pN]') THEN '{_TERM_START}' ELSE '' END"
    trail = f"CASE WHEN regexp_matches({needle}, '[\\pL\\pN]$') THEN '{_TERM_END}' ELSE '' END"
    return (
        f"regexp_matches({haystack}, '{flag}' || {lead} "
        f"|| regexp_escape({needle}) || {trail})"
    )


_HAS = term_match_sql("{0}", "{1}")
_HAS_CS = term_match_sql("{0}", "{1}", case_sensitive=True)

# `contains` IS plain substring (R3) -- the mirror image of `has`. The needle is
# a runtime value, so its LIKE metacharacters must be escaped or `a contains "%"`
# would match everything.
_CONTAINS = "({0} ILIKE '%' || " + _escape_like("{1}") + " || '%'" + _LIKE_ESCAPE + ")"
_CONTAINS_CS = "({0} LIKE '%' || " + _escape_like("{1}") + " || '%'" + _LIKE_ESCAPE + ")"
_STARTSWITH = "({0} ILIKE " + _escape_like("{1}") + " || '%'" + _LIKE_ESCAPE + ")"
_ENDSWITH = "({0} ILIKE '%' || " + _escape_like("{1}") + _LIKE_ESCAPE + ")"

#: Wave 1 binary operators, keyed by their KQL spelling.
BINARY_OPERATORS: dict[str, BinarySpec] = {
    b.op: b
    for b in [
        # arithmetic / comparison
        BinarySpec("+", "({0} + {1})"),
        BinarySpec("-", "({0} - {1})"),
        BinarySpec("*", "({0} * {1})"),
        # `//`, not `/`. KQL divides two integers as **integers**, truncating
        # toward zero: `7 / 2` is 3 and `-7 / 2` is -3. SQL's `/` promotes to
        # double and answers 3.5 — a silently wrong number in the most ordinary
        # arithmetic there is.
        #
        # DuckDB's `//` is not "floor division" despite the spelling: it
        # truncates toward zero on integers *and behaves as ordinary division on
        # floats* (`7.5 // 2` is 3.75). That is exactly KQL's rule, and it is
        # decided from the operand types by DuckDB — which is the type
        # information the translator does not have. Measured across 17 forms:
        # `//` matches 14, `/` matched 5. See translate.render_expr for the one
        # case that needs `/` back.
        BinarySpec("/", "({0} // {1})"),
        # KQL's % is a MATHEMATICAL modulo: the result takes the sign of
        # nothing -- it is always non-negative. SQL's % takes the dividend's
        # sign, so `-10 % 4` is 2 in KQL and -2 in DuckDB. Measured, not assumed.
        BinarySpec("%", "((({0} % {1}) + abs({1})) % abs({1}))", ("R11",),
                   "always non-negative, unlike SQL"),
        BinarySpec("<", "({0} < {1})"),
        BinarySpec("<=", "({0} <= {1})"),
        BinarySpec(">", "({0} > {1})"),
        BinarySpec(">=", "({0} >= {1})"),
        BinarySpec("and", "({0} AND {1})"),
        BinarySpec("or", "({0} OR {1})"),
        # R4 — the equality and matching families are TOTAL in KQL where SQL's
        # are three-valued. Against a null operand KQL answers false for the
        # positive form and TRUE for the negated one; SQL answers NULL both
        # times, and `where` drops the row. That makes `| where s !contains "x"`
        # silently lose every null row. Measured on the emulator, per operator.
        # Ordering comparisons above (`<`, `>`, `<=`, `>=`) are NOT total —
        # they stay null in KQL too, which is why they carry no null_result.
        BinarySpec("==", "({0} = {1})", ("R2", "R4", "R17"), "case-SENSITIVE",
                   null_result="FALSE"),
        BinarySpec("!=", "({0} <> {1})", ("R2", "R4", "R17"), null_result="TRUE"),
        BinarySpec("<>", "({0} <> {1})", ("R2", "R4", "R17"), null_result="TRUE"),
        BinarySpec("=~", "(lower({0}) = lower({1}))", ("R2", "R4", "R17"),
                   "case-INsensitive", null_result="FALSE"),
        BinarySpec("!~", "(lower({0}) <> lower({1}))", ("R2", "R4", "R17"),
                   null_result="TRUE"),
        # R3 — contains is SUBSTRING, case-insensitive by default
        BinarySpec("contains", _CONTAINS, ("R3", "R4", "R17"), null_result="FALSE"),
        BinarySpec("!contains", f"NOT {_CONTAINS}", ("R3", "R4", "R17"), null_result="TRUE"),
        BinarySpec("contains_cs", _CONTAINS_CS, ("R3", "R4", "R17"), null_result="FALSE"),
        BinarySpec("!contains_cs", f"NOT {_CONTAINS_CS}", ("R3", "R4", "R17"),
                   null_result="TRUE"),
        # R3 — has is TERM-based, not substring
        BinarySpec("has", _HAS, ("R3", "R4", "R17"), "whole-term match", null_result="FALSE"),
        BinarySpec("!has", f"NOT {_HAS}", ("R3", "R4", "R17"), null_result="TRUE"),
        BinarySpec("has_cs", _HAS_CS, ("R3", "R4", "R17"), null_result="FALSE"),
        BinarySpec("!has_cs", f"NOT {_HAS_CS}", ("R3", "R4", "R17"), null_result="TRUE"),
        # R3 — prefix / suffix, case-insensitive by default
        BinarySpec("startswith", _STARTSWITH, ("R3", "R4", "R17"), null_result="FALSE"),
        BinarySpec("!startswith", f"NOT {_STARTSWITH}", ("R3", "R4", "R17"),
                   null_result="TRUE"),
        BinarySpec("startswith_cs", "starts_with({0}, {1})", ("R3", "R4", "R17"),
                   null_result="FALSE"),
        BinarySpec("endswith", _ENDSWITH, ("R3", "R4", "R17"), null_result="FALSE"),
        BinarySpec("!endswith", f"NOT {_ENDSWITH}", ("R3", "R4", "R17"), null_result="TRUE"),
        BinarySpec("endswith_cs", "ends_with({0}, {1})", ("R3", "R4", "R17"),
                   null_result="FALSE"),
        BinarySpec("!endswith_cs", "NOT ends_with({0}, {1})", ("R3", "R4", "R17"),
                   null_result="TRUE"),
        BinarySpec("!startswith_cs", "NOT starts_with({0}, {1})", ("R3", "R4", "R17"),
                   null_result="TRUE"),
        # `matches regex` is a FULL-string match in Azure Monitor transformations
        # but a partial match in Log Analytics; KQL proper is partial, which is
        # what regexp_matches does.
        BinarySpec("matches regex", "regexp_matches({0}, {1})", ("R3", "R4", "R17"),
                   null_result="FALSE"),
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


def _a(
    name: str,
    template: str,
    arities: tuple[int, ...] = (),
    **kw: Any,
) -> AggregateSpec:
    """Shorthand for one aggregate row. ``**kw`` carries the R12 naming flags."""
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
        # make_set UNIONS dynamic arrays rather than collecting them: a column
        # of ["A1","A2"] and ["A2","C1"] gives {A1, A2, C1}, not two arrays.
        # Measured on the emulator. Non-array values are collected as usual, so
        # the runtime json_type check covers both without a schema.
        _a("make_set",
           "to_json(coalesce(list_distinct(flatten(list("
           "CASE WHEN json_type(TRY_CAST({0} AS JSON)) = 'ARRAY' "
           "THEN CAST({0} AS JSON[]) ELSE [to_json({0})] END"
           ") FILTER (WHERE {0} IS NOT NULL))), []))",
           (1, 2), name_prefix="set", rules=("R4", "R9")),
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
