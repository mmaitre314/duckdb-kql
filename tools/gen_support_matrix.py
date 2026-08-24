#!/usr/bin/env python3
"""Generate ``docs/kql-support.md`` — what is supported, and what to watch for.

A hand-maintained support table is a table that lies. This one is derived from
the same registries the translator dispatches on
(``duckdb_kql.translate.functions``), so a mapping cannot exist without being
documented and cannot be documented without existing.

The *tabular operator* rows go further: every claim is **probed** at generation
time by translating a real query, so "supported" means it translated today, not
that someone believed it did. ``tests/test_support_matrix.py`` regenerates the
file and fails on any difference, which is what keeps the committed copy honest.

Usage::

    python tools/gen_support_matrix.py            # write docs/kql-support.md
    python tools/gen_support_matrix.py --check    # exit 1 if it would change
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# E402 x4 below: these follow the `sys.path` shim above, which is the whole
# point of it — the tool runs from a checkout without the package installed.
import duckdb_kql  # noqa: E402
from duckdb_kql.errors import KqlError  # noqa: E402
from duckdb_kql.translate import _SPECIAL_FORMS  # noqa: E402
from duckdb_kql.translate.functions import (  # noqa: E402
    AGGREGATE_FUNCTIONS,
    BINARY_OPERATORS,
    SCALAR_FUNCTIONS,
)

OUTPUT = ROOT / "docs" / "kql-support.md"

# ---------------------------------------------------------------------------
# The semantic invariants, in one line each
# ---------------------------------------------------------------------------

#: R-rule -> the gotcha it names, phrased for someone reading a table rather
#: than the spec. Full text: docs/TRANSLATION.md §4.
RULES = {
    "R1": "Unparseable input yields **null**, never an error.",
    "R2": "`==` is case-**sensitive**; `=~` is the insensitive form.",
    "R3": "`has` matches whole **terms**, `contains` matches **substrings**; both default to case-insensitive.",
    "R4": "Null-aware: `isempty` (null **or** empty string) is not the same as `isnull`, and arithmetic propagates null.",
    "R5": "A bare `join` is **`innerunique`** — it de-duplicates the left key set first.",
    "R6": "Sort order defaults to **descending**, the opposite of SQL.",
    "R7": "Identifiers are case-**sensitive**; two columns differing only in case are a schema error.",
    "R8": "Datetimes are UTC. Needs `SET TimeZone='UTC'` — see the note at the top.",
    "R9": "A missing property or out-of-range index is **null**, never an error.",
    "R10": "Which rows come back is **not deterministic** without a terminal `sort`.",
    "R11": "Character-oriented, not byte-oriented; `substring` indices are 0-based and clamp.",
    "R12": "Output column names follow KQL's scheme (`count_`, `avg_X`), not SQL's.",
    "R13": "`/` on two integers is **integer** division, truncating toward zero.",
    "R14": "`lookup` defaults to **`leftouter`** and drops the right key columns; join/lookup keys match **null to null**.",
    "R15": "`union` matches branches by column **name**, not position, and does not de-duplicate.",
    "R16": "`macro-expand` runs its body once per entity and unions the results.",
    "R17": "A `dynamic` in a string context is its **unwrapped** text — `dynamic('x')` is `x`, not `\"x\"`, and `dynamic(null)` is the empty string.",
    "R18": "`mv-expand` zips several columns rather than crossing them, replaces a column in place, and `to typeof(T)` **converts** rather than declares.",
    "R19": "`parse` is **all-or-nothing**: one failed conversion blanks the whole row. A non-match keeps the row with `''`, not null. `kind=simple` anchors at end-of-text and captures lazily; `kind=regex` does neither.",
    "R20": "A value's string form is .NET's: a bool is `True`/`False`, a datetime is `2020-01-02T03:04:05.6000000Z`. `tostring` is **total** — a null of any type is the empty string, not null.",
    "R21": "An assignment sees the operator's **input** columns only — `extend a = x+1, b = a+1` is refused; split it into two clauses. Replacing a column under its own name is fine and reads the *input* value.",
}

#: R11 covers two unrelated hazards. Aggregates get the one that applies to them.
RULES_AGGREGATE = dict(
    RULES,
    R11="**Approximate**, not exact — do not assert equality.",
    R4="Nulls are ignored. `count(X)` counts non-null values; bare `count()` counts rows.",
)

#: Per-entry gotchas for rows whose registry note is absent or too terse to be
#: useful on its own. Written here rather than in the registry because they are
#: reader-facing prose, not translator behaviour.
SPECIFIC_GOTCHAS: dict[str, str] = {
    # -- the has/contains family: the highest-risk corner of the language ----
    "contains": "**Substring**, case-insensitive. `has` is the whole-term form and gives different answers.",
    "contains_cs": "Substring, case-**sensitive**.",
    "!contains": "Negated substring match. Null handling is pinned against the emulator rather than derived from `NOT (…)`.",
    "!contains_cs": "Negated case-sensitive substring match.",
    "has": 'Whole **term**, case-insensitive. `Text has "err"` is **false** for `"error"`. A term is a run of Unicode letters and digits; every other character delimits one, **underscore included** — so `"a_b" has "a"` is **true**. (Regex `\\b` would say false; measured on the emulator.)',
    "has_cs": "Whole term, case-**sensitive**.",
    "!has": "Negated whole-term match — not `NOT contains`. Null handling pinned against the emulator.",
    "!has_cs": "Negated case-sensitive whole-term match.",
    "startswith": "Prefix, case-**insensitive** by default. `startswith_cs` is the sensitive form.",
    "startswith_cs": "Prefix, case-sensitive.",
    "!startswith": "Negated prefix match. Null handling pinned against the emulator.",
    "!startswith_cs": "Negated case-sensitive prefix match.",
    "endswith": "Suffix, case-**insensitive** by default.",
    "endswith_cs": "Suffix, case-sensitive.",
    "!endswith": "Negated suffix match. Null handling pinned against the emulator.",
    "!endswith_cs": "Negated case-sensitive suffix match.",
    "matches regex": "Regex match, case-sensitive. Both engines use RE2-family syntax, so lookarounds are unavailable on either side.",
    "==": "Case-**sensitive** on strings — SQL collation defaults do not apply. `=~` is the insensitive form.",
    "!=": "Case-sensitive, and null-propagating: a null on either side yields null, not `true`.",
    "<>": "Synonym for `!=`.",
    "=~": "Case-**insensitive** equality.",
    "!~": "Case-insensitive inequality.",
    "%": "KQL's modulo is **mathematical** — always non-negative. DuckDB's takes the dividend's sign, so `-10 % 4` is `2` in KQL and `-2` in plain SQL.",
    "/": "Two integers divide as **integers**, truncating toward zero: `7 / 2` is `3` and `-7 / 2` is `-3`, where SQL's `/` answers `3.5`. One real operand makes the whole expression real. Division by zero is **null** for integers and **±Infinity** for reals. Dividing two timespans yields a number, not a timespan; DuckDB has no interval division at all. **Caveat:** the mapping is DuckDB's `//`, which picks integer or float division from the operand types but returns null for *either* zero divisor. Where an operand is visibly a real — a literal, `todouble`, or arithmetic involving one — plain `/` is emitted and Infinity comes back; a zero divisor under a bare real *column* yields null instead. Every non-zero divisor is correct.",
    "+": "A datetime plus a timespan is a datetime. Adding two datetimes is an error in both languages.",
    "-": "Subtracting two datetimes yields a **timespan**.",
    # -- aggregates ---------------------------------------------------------
    "any": "Picks an **arbitrary** row from each group. Which one is not defined — do not depend on it.",
    "take_any": "Picks an arbitrary row from each group; the choice is not deterministic and may differ from Kusto's.",
    "dcount": "Exact `count(DISTINCT …)`. KQL's is an HLL **estimate**, so the two can differ at high cardinality — ours is the exact number, which is not the same as agreeing.",
    "dcountif": "Exact, where KQL's is an HLL estimate. See `dcount`.",
    "percentile": "Uses `quantile_disc`, which returns a value actually present in the data rather than interpolating between two. Pinned against the emulator; `quantile_cont` would differ.",
    "stdev": "Sample standard deviation (`n-1`). A one-row group yields **0**, matching KQL, where SQL's `stddev_samp` yields null. `stdevp` (population) is a separate, unsupported function.",
    "stdevif": "Sample standard deviation over the rows matching the predicate.",
    "variance": "Sample variance (`n-1`). A one-row group yields **0**, matching KQL, where SQL yields null.",
    "varianceif": "Sample variance over the rows matching the predicate.",
    "make_set": "Element **order is not defined** — compare as a set. Nulls are dropped rather than collected.",
    "make_list": "Preserves input order only if the input is ordered; without a preceding `sort` that order is not defined.",
    "count": "Bare `count()` counts rows; `count(X)` counts non-null values. Auto-named `count_` (R12).",
    "countif": "Counts rows where the predicate is **true** — a null predicate does not count.",
    # -- scalars whose real hazard the generic rule text does not name -------
    "extract": "Argument order is **(regex, group, text)** — the reverse of DuckDB's `regexp_extract`. Returns null, not an error, when nothing matches.",
    "indexof": "**0-based**, and returns `-1` when not found, where SQL's `position` is 1-based and returns `0`.",
    "strlen": "Counts **characters**, not bytes — a multi-byte string is shorter than its `octet_length`.",
    "substring": "**0-based**, and clamps out-of-range or negative input instead of erroring. SQL's `substring` is 1-based.",
    "array_length": "Counts an array's elements as a `long`. A **non-array is null**, not 0 — `array_length(dynamic({'a':1}))` and `array_length(dynamic(null))` are both null, where DuckDB's json_array_length answers 0. The result is cast to BIGINT because it is otherwise unsigned, which widens `array_length(x) - 1` past what `range` can bind.",
    "split": "Returns a dynamic array. An empty separator and an out-of-range index both yield null rather than an error.",
    "isempty": "True for null **or** the empty string — not the same as `isnull`.",
    "isnotempty": "The negation of `isempty`, so a null is *not* non-empty.",
    "isnull": "True only for null. An empty string is not null; use `isempty` for that.",
    "isnotnull": "False for null, true for the empty string.",
    "gettype": "Reports the **KQL** type name, not DuckDB's.",
    "todatetime": "Accepts a wider set of formats than a plain `TIMESTAMP` cast, and **resolves UTC offsets** rather than keeping the local wall time. Unparseable input yields null (R1).",
    "toguid": "Returns null on a malformed GUID rather than raising (R1).",
    "coalesce": "Variadic. Returns the first non-null argument. Every argument must be the **same type** — stricter than SQL, and stricter than it looks: `coalesce(5, 1.5)` is refused, as Kusto refuses it (SEM0525). `dynamic` pairs with anything.",
    "strcat": "Variadic. A null argument contributes the **empty string**, as in KQL — `strcat('a', int(null), 'b')` is `ab` — because `tostring` is total (R20).",
    "strcat_delim": "Variadic after the delimiter. A null keeps its slot: `strcat_delim('-', 'a', int(null), 'b')` is `a--b`, not `a-b` (R20).",
    "replace": "Azure Monitor's spelling of `replace_string`. Kusto proper spells the regex form `replace_regex`.",
    "base64_decode_tostring": "Bytes that are **not valid UTF-8** diverge from Kusto — see [Known divergences](#known-divergences).",
    "base64_decodestring": "Azure Monitor's spelling of `base64_decode_tostring`, and shares its UTF-8 divergence.",
    "base64_encodestring": "Azure Monitor's spelling of `base64_encode_tostring`.",
    "rand": "Nondeterministic, so results cannot be compared against a frozen expectation (R10).",
    "now": "Evaluated **once per query**, not once per row, so repeated references agree.",
    "ago": "`ago(x)` is `now() - x`, with `now()` evaluated once per query.",
    "dayofweek": "Returns a **timespan** (days since Sunday), not an integer.",
    "range": "The scalar `range(start, stop, step)` builds a dynamic array — distinct from the `range` *operator*.",
    "log": "**Natural** logarithm, mapped to SQL's `ln`. SQL's own `log()` is base-10 in most dialects, so the naive mapping is off by a factor of `ln(10)`.",
    "round": "Both arities. The value is cast to DOUBLE first: DuckDB's two-argument `round` returns DECIMAL and rounds the decimal value, so `round(1.005, 2)` is `1.01` there and `1.0` in Kusto, which rounds the double `1.005` actually is.",
    "trim_start": "The first argument is a **regular expression**, not a set of characters to strip. `trim_start(' ', s)` strips one leading space, not all whitespace.",
    "replace_regex": "Replaces **all** matches. Capture references use `\\\\1`; KQL and DuckDB agree, but `$1` does not work in either.",
    "replace_string": "Plain substring replacement — no regex. `replace_regex` is the pattern form.",
    "reverse": "Reverses the value's KQL **string** form by character, whatever its type — `reverse(3h)` is `00:00:30` (R20). Applying it to a dynamic array is not supported.",
    "iff": "Both branches must be the **same type**, as in KQL — even `long` and `real` do not mix. Checked for literal branches, which refuse cleanly; a mismatch between two *columns* still reaches DuckDB, since column types are not carried. `iif` is the same function.",
    "iif": "Alias for `iff`.",
    "binary_shift_left": "The shift count is taken as given; KQL masks it to 6 bits, so a count above 63 differs.",
    "binary_shift_right": "The shift count is taken as given; KQL masks it to 6 bits, so a count above 63 differs.",
}


# ---------------------------------------------------------------------------
# Tabular operators, sources and statements — probed, not asserted
# ---------------------------------------------------------------------------

#: ``(name, probe, note)``. The probe is translated at generation time; whether
#: it succeeds decides the row's status, so this table cannot claim support the
#: translator does not have.
OPERATORS: list[tuple[str, str, str]] = [
    ("where", "T | where a == 1", "String comparison is case-sensitive with `==`, insensitive with `=~` (R2)."),
    ("project", "T | project a", "Sets output column order."),
    ("project-away", "T | project-away b", "Expands to an explicit column list, so a later `*` still behaves."),
    ("project-rename", "T | project-rename c = a", "Keeps the renamed column's position."),
    ("extend", "T | extend c = 1", "Redefining an existing column **replaces** it rather than adding a second one."),
    ("summarize", "T | summarize n = round(sum(a), 2) by b", "Any scalar expression over aggregates, not just a bare call — `round(sum(x), 2)`, `sum(x) / count()`, `strcat('n=', tostring(count()))`. Auto-generated names follow KQL: the name comes from the *aggregate*, so `round(sum(y), 2)` is `sum_y`, and an expression whose first argument is not an aggregate is `Column1`. Group keys come first in source order, and a null key forms its own group (R12). A column outside an aggregate is refused, as Kusto refuses it — even a `by` key."),
    ("join", "T | join kind=inner (T) on a", "Needs the input schema to reproduce KQL's column renaming — `duckdb_kql.kql()` supplies it; `to_sql()` needs `schema=`."),
    ("mv-expand", "T | mv-expand a", "Several columns expand in **lockstep**, not as a cross product: the row count is the longest list's and the shorter ones pad with null. `to typeof(T)` **converts** rather than declares — a JSON string is not a number, so `'2' to typeof(long)` is null; `timespan` and `decimal` are refused because every input tried converts to null there. `limit N` caps rows per input row, and `kind=array`/`bagexpansion=array` turns a bag into `[key, value]` pairs. The expanded column keeps its **position**, and `mv-expand x = a` adds `x` while leaving `a` holding the whole array — both need the incoming column list. An alias or `with_itemindex=` therefore **raises** `KqlSchemaError` without one, rather than silently keeping a stale column of the same name; pass a schema or use `duckdb_kql.kql()`, which derives it from the connection."),
    ("distinct", "T | distinct a", "Takes **expressions**, not just column names — `distinct B2 = tostring(B)` — despite a documented syntax of a column list. Output names follow R12's allow-list, shared with `summarize`'s `by` keys: `tostring(B)` is named `B`, `tolower(B)` is `Column1`. `distinct *` is refused; it needs the input schema to expand. **Residue:** arithmetic gets a number one higher than expected (`distinct -C` is `Column2`), and `strlen(B)` is named `strlen_B` — two data points too few to derive a rule from, so both fall back to the positional name."),
    ("count", "T | count", "Output column is named `Count`."),
    ("sort", "T | sort by a", "Defaults to **descending**, the opposite of SQL. Null placement is emitted explicitly rather than left to DuckDB's default (R6)."),
    ("order", "T | order by a", "Synonym for `sort`."),
    ("take", "T | take 1", "Which rows come back is **not defined** without a preceding `sort` (R10)."),
    ("limit", "T | limit 1", "Synonym for `take`."),
    ("render", "T | render table", "Parsed and **ignored**: there is no chart to draw. The rows are unchanged, so a query ending in `render` still returns its data."),
    ("top", "T | top 1 by a", "`sort by X | take n` in one step, and it inherits `sort`'s defaults rather than SQL's: **descending** unless `asc` is written, and null sorts first ascending / last descending (R6). Exactly one sort key — `top 2 by a, b` is a syntax error in Kusto too. A negative count returns no rows, as it does in Kusto; DuckDB refuses a negative LIMIT, so the count is clamped. Which rows come back on a tie is undefined in both engines (R10)."),
    ("macro-expand", "macro-expand entity_group [database('d')] as s (s.T)", "Runs the body **once per entity** and unions the results (R16), so `count` inside the parentheses returns one row per entity and outside returns one. Column unification, `isfuzzy` and row order are R15's. Entities come from an inline or `let`-bound group, or — for a *named* group, which is cluster-side state — from `entity_groups={\"G\": [\"database('d')\"]}`; an unmapped name is refused rather than expanded to nothing. A `cluster(...)` entity resolves through `clusters=`. Refused as Kusto refuses them: duplicate entities, nesting, a bare scope reference. **`withsource=` is refused**: Kusto qualifies every label here and reproducing that needs the current database's name, which belongs to the connection rather than the query."),
    ("union", "T | union T", "Branches are matched by column **name**, not position, and are never de-duplicated (R15). The default `kind=outer` keeps the union of the branches' columns with nulls for the gaps; `kind=inner` keeps the intersection. Column order is first appearance, left to right. `withsource=` names a branch by its table name, but a subquery, a `let`-bound name and a piped left side are all `union_argN`, counting the left side as 0. `isfuzzy=true` drops a branch whose table is missing; a wildcard matching no table is an error, not an empty result. **Residue:** two branches giving one name two different types are split into two columns by Kusto and merged by DuckDB — undetectable without column types. A wildcard also expands in *name* order here and in *creation* order in Kusto, so the columns of `union UT*` can be ordered differently; the rows are the same."),
    ("parse", "T | parse a with 'x' v", "All three kinds — `simple` (the default), `relaxed` and `regex` — plus `flags=i/s/m/U`. **All-or-nothing**: under `simple`, one failed conversion blanks the *whole row*, columns that converted included. A non-match keeps the row with `\'\'` for a string column and null for a typed one. `simple` and `relaxed` anchor the pattern at end-of-text and capture lazily; `kind=regex` does neither, treats each literal as the user\'s regex, and shapes a typed column everywhere rather than only before a `*` — the shapes differ between the two modes, so `\'12\'` as a bool is true in `simple` and null in `regex`. A string column before a `*` is refused as ambiguous (SEM0476, as Kusto refuses it). Conversions are KQL\'s, not DuckDB\'s — `\'1.5\'` as a long is null, not 2. Not supported: `: decimal`, `flags=x`, a `datetime`/`timespan` column before a `*` in `simple`, and a temporal column at the end of a `kind=regex` pattern or under `flags=U`. See R19."),
    ("parse-where", "T | parse-where a with 'x' v", "The same pattern as `parse`, dropping the rows it would have blanked — i.e. matched **and** every conversion succeeded. `parse-where kind=relaxed` is refused, as Kusto refuses it (SEM0477)."),
    ("lookup", "T | lookup (T) on a", "Defaults to **`leftouter`**, not `join`'s `innerunique`, and only `leftouter` and `inner` exist — every other kind is refused, as Kusto refuses it. The right side's **key columns are dropped**, so there is no `Key1`; non-key collisions still get the `1` suffix (R14). Needs the input schema, like `join`. As with any outer join, an unmatched `string` column is null here but `''` in Kusto, so a downstream `!= \"\"` differs — `isempty()` is the portable test."),
    ("getschema", "T | getschema", "Reports `ColumnName`, `ColumnOrdinal` (0-based), `DataType` and `ColumnType`, verified against the emulator including the non-obvious .NET names (`bool` is `System.SByte`, `decimal` is `System.Data.SqlTypes.SqlDecimal`). Types are DuckDB's, named as Kusto names them, so a DuckDB type with no Kusto counterpart reports as `dynamic` (composites) or `string` (everything else) rather than inventing a name."),
    ("project-keep", "T | project-keep a", "Wildcard column selection against the input schema. The plumbing `project-away` uses would cover it."),
    ("project-reorder", "T | project-reorder b, a", "The same schema plumbing as `project-keep`, plus the trailing-column rules."),
    ("make-series", "T | make-series n = count() on a from 1 to 2 step 1", "Produces array-valued columns over a generated axis, with gap filling. A different result *shape*, not just a different aggregate."),
    ("mv-apply", "T | mv-apply a on (where a > 0)", "Runs a sub-pipeline per expanded element, so it needs correlated lateral evaluation. `mv-expand` covers the common flattening case."),
    ("evaluate", "T | evaluate bag_unpack(a)", "A plugin dispatch point. Some plugins (`bag_unpack`, `narrow`) are pure reshaping and could be done; others execute code or call the network and never will be."),
    ("sample", "T | sample 1", "DuckDB's `USING SAMPLE` has different distribution guarantees. The result is nondeterministic either way (R10), so a mismatch here would be invisible in tests — which is precisely why it is not guessed at."),
    ("sample-distinct", "T | sample-distinct 1 of a", "See `sample`."),
    ("serialize", "T | serialize rn = row_number()", "Pins row order so window functions (`row_number`, `prev`, `next`) can reference it. Those are unsupported too, so this would pin an order nothing consumes."),
    ("as", "T | as X", "Names an intermediate result for later reference, which needs multi-statement scope."),
    ("consume", "T | consume", "Discards its input without returning rows. It exists to make a cluster execute a query while sending nothing back, which is meaningless without the cluster it is measuring."),
    ("facet", "T | facet by a", "Returns **multiple** result tables. Layer 1 returns one relation, so this needs a response shape that does not exist yet."),
    ("fork", "T | fork (where a == 1)", "Multiple result tables, like `facet`."),
    ("invoke", "T | invoke f()", "Calls a tabular user-defined function, so it needs `let` function support first."),
    ("partition", "T | partition by a { T }", "Runs a sub-pipeline per key group, so it needs correlated sub-pipeline evaluation. Only the `{ … }` body form parses; the vendored grammar rejects the parenthesised one."),
    ("reduce", "T | reduce by a", "Fuzzy string clustering with a specific similarity algorithm. An approximation would produce plausible, differently-grouped output."),
    ("scan", "T | scan declare(x: long) with (step s: true => x = 1;)", "A row-by-row state machine over ordered rows. No SQL equivalent short of a recursive CTE built per query shape."),
    ("top-nested", "T | top-nested 1 of a by max(b)", "Hierarchical top-N with per-level aggregates — a distinct result shape, not a composition of supported operators."),
    ("top-hitters", "T | top-hitters 1 of a", "Approximate top-N by frequency. The approximation is the point, and matching its error profile is not something to guess at."),
    ("search", 'search "x"', "Searches every column of every table in scope. Needs schema-wide expansion plus KQL's term matching (R3) applied across all string columns."),
    ("find", 'find "x"', "Cross-table search with a source-column projection. The same schema-wide expansion as `search`, plus per-source column packing."),
]

SOURCES: list[tuple[str, str, str]] = [
    ("table reference", "T", "A KQL table name is a DuckDB table, view, or registered relation. Case-**sensitive**."),
    ("keyword and escaped names", "T | project ['my col'], id", "Most KQL keywords are legal names (`id`, `count`, `by`, `range`), and `['...']` names anything a bare identifier cannot — including a table, which is the only way to reference one called `count`. Escaped names are unescaped once: `['my col']` is the column `my col`."),
    ("`database()`", 'database("Sales").Orders', 'A cross-database reference, spelled `"Sales"."Orders"` in DuckDB. Attach the file first — `duckdb-kql serve --init` does it for a whole server. Joins may cross databases.'),
    ("`cluster()`", 'cluster("c").database("d").T', 'Needs a **mapping** saying which local database stands in for each cluster: `clusters={("c", "d"): "local"}`, or `--cluster-map` for the server. Without one it is refused, because reading it as local would answer a question about somewhere else with data from here. Cluster spellings are normalized (scheme, trailing slash, host case) since Kusto resolves the argument to `https://host/`; a short name is **not** expanded to a domain, because the engine does not expand it either. `cluster(...)` without `.database(...)` is refused, as Kusto refuses it (SEM0048).'),
    ("`print`", "print x = 1", "Unnamed columns are `print_0`, `print_1`, … as in KQL."),
    ("`datatable`", "datatable (a: long) [1, 2]", "Values are read row-major and must divide evenly by the column count, as in KQL."),
    ("`range`", "range i from 1 to 3 step 1", "The end value is **inclusive**, unlike DuckDB's `range()`. Works over datetimes with a timespan step."),
    ("`externaldata`", 'externaldata (a: long) ["https://example/x"]', "Fetches a URL at query time — out of scope for an offline transpiler. Read the file with DuckDB (`read_csv`, `read_parquet`) and expose it as a view instead."),
]

STATEMENTS: list[tuple[str, str, str]] = [
    ("`let` (scalar)", "let x = 1; print y = x", "Substituted into the query before translation."),
    ("`let` (tabular)", "let A = T; A | count", "Becomes a named CTE. `x in (A)` resolves the name to that CTE — including inside another `let`, a join's right side, or a union branch. **Residue:** when a *column* in scope has the same name as the tabular `let`, this binds the `let` where Kusto binds the column and then rejects the query (SEM0040); telling them apart needs the input's column names, which lowering does not have."),
    ("`let` (function)", "let f = (x: long) { x + 1 }; print f(1)", "Needs inlining with argument substitution and scope handling. A partial version would translate some calls and silently mis-scope others."),
    ("`declare query_parameters`", "declare query_parameters(p: long = 1); print x = p", "Bound as a **value**, never spliced into the SQL. See [Getting started](getting-started.md#query-parameters-and-user-input)."),
    ("`set`", "set query_now = datetime(2020-01-01); print 1", "Request options as a statement. Silently ignoring one — `query_now`, `truncationmaxrecords` — would change what the caller believes happened, so it raises instead. Same reasoning as [the client's option table](kusto-client.md#request-options)."),
    ("`alias database`", "alias database D = cluster('c').database('d'); print 1", "Names a remote database. There is no cluster, and resolving it locally would answer a question about somewhere else with data from here."),
    ("`declare pattern`", "declare pattern P = (a: string)[b: string] { ('x').['y'] = { print 1 } }; print 1", "A query-time macro expansion mechanism. Rare, and expanding it wrongly would be invisible."),
    ("`restrict`", "restrict access to (T); T | count", "Narrows the visible entity set for the rest of the query. Ignoring it would **widen** access — the failure direction that matters."),
    ("multiple query statements", "print 1; print 2", "Only one query statement per call. Send them separately."),
]

#: Schema the probes translate against.
#: `d.T` is here for the `macro-expand` probe: an entity group's members are
#: always `database(...)`-qualified, so its body names a table in another
#: database and the probe cannot reach `T`.
PROBE_SCHEMA = {"T": ["a", "b"], "d.T": ["a", "b"]}


def probe(kql: str) -> tuple[bool, str]:
    """Translate *kql*; return ``(supported, reason_if_not)``."""
    try:
        duckdb_kql.to_sql(kql, schema=PROBE_SCHEMA)
    except KqlError as exc:
        return False, type(exc).__name__
    except Exception as exc:  # noqa: BLE001 - a crash is still "not supported"
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


# ---------------------------------------------------------------------------
# Function families
# ---------------------------------------------------------------------------

#: Prefixes and exact names that place a scalar function in a family. Checked in
#: order; the first match wins. Anything unmatched lands in "Other", which is a
#: visible bucket rather than a silent one.
#: Order matters — the first match wins, and several families share prefixes.
#: ``Conversion`` claims everything starting with ``to``, so anything that
#: happens to start that way but belongs elsewhere (`tolower`, `todatetime`)
#: must be claimed by an earlier family.
FAMILIES: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    ("Conditional", (), ("case", "iff", "iif", "coalesce", "max_of", "min_of")),
    ("Hash", ("hash_",), ()),
    ("IP address", ("ipv4_", "ipv6_", "parse_ipv"), ()),
    (
        "Null and type checks",
        ("isnull", "isnotnull", "isempty", "isnotempty", "isnan", "isinf",
         "isfinite", "isascii", "isutf8", "gettype"),
        (),
    ),
    (
        "Dynamic and array",
        ("array_", "bag_", "pack", "set_", "jaccard", "zip", "treepath",
         "mv_", "json", "parse_json", "parse_url", "parse_csv", "parse_xml"),
        ("todynamic", "arraylength", "range"),
    ),
    (
        "Datetime and timespan",
        ("datetime_", "startof", "endof", "dayof", "weekof", "monthof",
         "format_datetime", "format_timespan", "make_datetime", "make_timespan",
         "unixtime_", "hourofday", "getyear", "getmonth", "week_of_year"),
        ("now", "ago", "bin", "todatetime", "totimespan", "timespan",
         "datetime_utc_to_local"),
    ),
    (
        "String",
        ("str", "sub", "split", "trim", "replace", "reverse", "indexof",
         "countof", "extract", "parse_", "url", "base64", "translate",
         "unicode", "punycode"),
        ("toupper", "tolower", "tostring", "startswith", "endswith",
         "has_any_index", "format_bytes", "repeat"),
    ),
    (
        "Mathematical",
        ("log", "exp", "sqrt", "pow", "abs", "sign", "round", "floor", "ceiling",
         "gamma", "beta", "erf", "cos", "sin", "tan", "acos", "asin", "atan",
         "degrees", "radians", "rand", "gcd", "lcm", "hypot", "binary_",
         "bitset_", "cot", "pi", "not"),
        (),
    ),
    ("Conversion", ("to",), ()),
]


def family_of(name: str) -> str:
    for label, prefixes, exact in FAMILIES:
        if name in exact or any(name.startswith(p) for p in prefixes):
            return label
    return "Other"


# ---------------------------------------------------------------------------
# Functions handled outside the registries
# ---------------------------------------------------------------------------

#: Supported but not registry rows — the shapes a `{0}`-style template cannot
#: express. Listing them here is what stops the doc from under-reporting.
SPECIAL_NOTES: dict[str, str] = {
    "bin": "Floors to a multiple of the bin size **from the epoch origin**, and works on numbers as well as datetimes. Weeks and months are pinned against the emulator rather than mapped to `date_trunc`.",
    "case": "Variadic `case(pred, val, …, else)`.",
    "countof": "Both kinds, and they differ on **overlap**: the default `normal` (substring) kind counts overlapping occurrences — `countof('aaaa', 'aa')` is **3** — while `regex` does not, answering 2. Counted over start positions, since no DuckDB function overlaps and RE2 has no lookahead. An empty needle is 0 and a null haystack is 0, not null. **Residue:** a non-literal search term is accepted here and rejected by a cluster.",
    "tobool": "Text is `true`/`false` (case-insensitive, trimmed) **or an integer** — `'2'` is true and `'1.5'` is null. A *number* is its nonzero-ness, so `tobool(1.5)` is true and `tobool('1.5')` is not: the same value spelled two ways converts differently, and the two are told apart at run time. DuckDB's own boolean cast is wrong in both directions — it accepts `yes`/`no`/`y`/`n`/`t`/`f` and rejects `'2'` (R1).",
    "toboolean": "Alias for `tobool`.",
    "datetime_add": "Takes a **period**: year, quarter, month, week, day, hour, minute, second, millisecond, microsecond. Not `week_of_year` or `dayofyear`, which are ordinals rather than durations — a different list from `datetime_part`'s, and Kusto refuses the wrong one (SEM0235).",
    "datetime_diff": "Returns a whole number of periods, truncated toward zero. Same period list as `datetime_add`, which is **not** `datetime_part`'s.",
    "datetime_part": "Reads a **field**: year, quarter, month, week_of_year, day, dayofyear, hour, minute, second, millisecond, microsecond. Note `week_of_year` and `dayofyear` are here and bare `week` is not — the mirror image of `datetime_add`'s list. `nanosecond` is **refused**: KQL keeps 100 ns ticks and DuckDB stores microseconds, so the digit asked for does not exist.",
    "extract_all": "Capture-group count changes the result shape, so it is resolved at translation time from the pattern.",
    "make_datetime": "**Truncates** the sub-second part where DuckDB's `make_timestamp` rounds.",
    "make_timespan": "",
    "endofday": "The last instant *inside* the period, not the start of the next one.",
    "endofmonth": "The last instant *inside* the period, not the start of the next one.",
    "endofweek": "The last instant *inside* the period. KQL weeks start on **Sunday**; DuckDB's `date_trunc('week')` starts Monday.",
    "endofyear": "The last instant *inside* the period, not the start of the next one.",
    "startofday": "Takes an optional offset in whole periods; ignoring it would return a plausible datetime for the *wrong* period.",
    "startofmonth": "Takes an optional offset in whole periods.",
    "startofweek": "KQL weeks start on **Sunday**; DuckDB's `date_trunc('week')` starts Monday. Takes an optional offset.",
    "startofyear": "Takes an optional offset in whole periods.",
    "zip": "Built by positional indexing: DuckDB's `list_zip` produces structs (`[{\"\":1}]`), not the arrays KQL returns.",
    "tostring": "Uses .NET's spelling, which differs from DuckDB's for **bools** (`True`, not `true`), datetimes and dynamics, and it is **total** — a null of any type is the empty string, not null (R20). Getting it wrong changes every hash computed over it.",
    "pack_array": "Renders as `json_array`, which takes mixed types — `to_json([...])` cannot.",
    "array_concat": "Variadic in KQL; folded over DuckDB's binary `list_concat`.",
    "hash_md5": "Hashes KQL's **string** form of the value, so a datetime or a bool must be spelled the way KQL spells it or the digest silently differs (R20). The empty string hashes to the **empty string**, not to `d41d8cd9…`.",
    "hash_sha1": "Hashes KQL's string form of the value — see `hash_md5`.",
    "hash_sha256": "Hashes KQL's string form of the value — see `hash_md5`.",
    "totimespan": "Handles the `[d.]hh:mm:ss` form, whose leading day part DuckDB's `INTERVAL` cast silently returns null for.",
    "timespan": "Synonym for `totimespan`.",
}

#: Things that are refused on purpose, with the reason. A refusal is a *feature*
#: here: each of these has a plausible-looking wrong mapping available.
REFUSALS: list[tuple[str, str]] = [
    (
        "`hash()`, `hash_xxhash64()`",
        "xxhash64, which DuckDB does not have. DuckDB's own `hash()` is a "
        "*different* function, so mapping to it would return confident, "
        "wrong-looking-correct digests — in security code, where that matters most.",
    ),
    (
        "`datetime_part('nanosecond', …)`",
        "KQL keeps 100 ns ticks; DuckDB stores microseconds. The digit being "
        "asked for is not there to return, and anyone asking for nanoseconds "
        "wants that precision.",
    ),
    (
        "`mv-expand … to typeof(timespan)` / `to typeof(decimal)`",
        "Every input tried converts to null on the emulator, `'1.00:00:00'` "
        "included, so there is no rule to reproduce. Answering null for "
        "everything would look like a working conversion.",
    ),
    (
        "`let` user-defined functions",
        "Needs inlining with argument substitution and scope handling. A "
        "partial version would translate some calls and silently mis-scope others.",
    ),
    (
        "`parse_xml()`",
        "DuckDB has no XML parser at all, so this would need a Python UDF running per row. Worth doing if the demand appears; not worth a half-implementation that handles attributes but not namespaces.",
    ),
    (
        "`geo_*` functions",
        "S2/H3 cell arithmetic and geodesic distance. DuckDB's spatial "
        "extension covers some of it with different edge-case behaviour, which "
        "is the kind of near-miss this project refuses to ship.",
    ),
    (
        "`series_*` functions",
        "Time-series decomposition, anomaly detection and forecasting. Real "
        "algorithms with real parameters; an approximation would be indistinguishable "
        "from a result.",
    ),
    (
        "`hll`, `tdigest` and their `_merge` / `dcount_hll` / `percentile_tdigest` forms",
        "These serialise a specific sketch **format**. Producing a differently "
        "shaped blob under the same name would break anything that stores or "
        "merges them.",
    ),
    (
        "Plugins (`evaluate` with `python`, `sql_request`, `cosmosdb_sql_request`, …)",
        "They execute code or call out over the network. Out of scope for an "
        "offline transpiler by construction.",
    ),
]

#: A known divergence we record rather than fix.
DIVERGENCES: list[tuple[str, str]] = [
    (
        "`base64_decode_tostring()` of bytes that are not valid UTF-8",
        "KQL returns an empty string; DuckDB's `BLOB`→`VARCHAR` cast returns the "
        "bytes with `\\x` escapes. DuckDB has no UTF-8 validity predicate to "
        "switch on, and sniffing for `\\x` in the output would misfire on a "
        "legitimate backslash. Valid UTF-8 — the case that matters — is correct.",
    ),
]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def cell(text: str) -> str:
    """Escape a table cell so a template's `|` does not break the row."""
    return text.replace("|", "\\|").replace("\n", " ").strip() or "—"


def gotchas(name: str, rules: tuple[str, ...], note: str, aggregate: bool = False) -> str:
    """The cell for one entry: the specific gotcha, or the rule's if there is none.

    A registry ``note`` is written for *that* row, so it always wins. Falling
    back to the rule summary only when there is no note is what keeps `%` from
    being told that its indices are 0-based: `%` cites R11 because KQL's modulo
    is mathematical, which the generic string phrasing does not describe.
    """
    specific = SPECIFIC_GOTCHAS.get(name)
    if specific is not None:
        return cell(specific)
    if note:
        # Registry notes are terse fragments written for a maintainer reading
        # code; make them read as sentences here.
        text = note.strip().rstrip(".")
        return cell(text[:1].upper() + text[1:] + ".")
    table = RULES_AGGREGATE if aggregate else RULES
    return cell(" ".join(table[r] for r in rules if r in table))


def section(title: str, rows: list[tuple[str, ...]], headers: tuple[str, ...]) -> list[str]:
    out = [f"### {title}", "", "| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows]
    out.append("")
    return out


def operator_rows(entries: list[tuple[str, str, str]]) -> tuple[list, list]:
    supported, unsupported = [], []
    for name, kql, note in entries:
        ok, reason = probe(kql)
        label = name if name.startswith("`") or " " in name else f"`{name}`"
        if ok:
            supported.append((label, note or "—"))
        else:
            unsupported.append((label, note or "—"))
    return supported, unsupported


def build() -> str:
    lines: list[str] = []
    a = lines.append

    a("# KQL support matrix")
    a("")
    a("<!-- Generated by tools/gen_support_matrix.py. Do not edit by hand. -->")
    a("")
    a("Every KQL construct this version handles, every one it does not, and what to")
    a("watch for in each. Generated from the translator's own registries, and the")
    a("operator rows are **probed** — a construct is listed as supported because it")
    a("translated when this file was built, not because someone said so.")
    a("")
    a("A construct that is not supported raises `KqlUnsupportedError`. It never")
    a("returns an approximate answer.")
    a("")
    a("> **Two things apply everywhere.** KQL datetimes are UTC, so the generated SQL")
    a("> needs `SET TimeZone='UTC'` — `duckdb_kql.connect()` and `duckdb_kql.kql()` do")
    a("> it for you (R8). And KQL identifiers are case-sensitive while DuckDB folds")
    a("> case, so identifiers are always emitted quoted and a collision is a")
    a("> `KqlSchemaError` rather than an arbitrary winner (R7).")
    a("")
    a("`R1`–`R12` below are the semantic invariants in")
    a("[`TRANSLATION.md` §4](TRANSLATION.md#4-semantic-invariants--the-golden-rules).")
    a("")

    # -- Contents -----------------------------------------------------------
    a("## Contents")
    a("")
    a("- [Tabular operators](#tabular-operators)")
    a("- [Sources](#sources)")
    a("- [Statements](#statements)")
    a("- [Binary and string operators](#binary-and-string-operators)")
    a("- [Aggregate functions](#aggregate-functions)")
    a("- [Scalar functions](#scalar-functions)")
    a("- [Data types](#data-types)")
    a("- [Deliberate refusals](#deliberate-refusals)")
    a("- [Known divergences](#known-divergences)")
    a("")

    # -- Operators ----------------------------------------------------------
    ops_yes, ops_no = operator_rows(OPERATORS)
    a("## Tabular operators")
    a("")
    a(f"{len(ops_yes)} of {len(OPERATORS)} supported.")
    a("")
    lines += section("Supported", ops_yes, ("Operator", "Limitations and gotchas"))
    lines += section("Not supported", ops_no, ("Operator", "Notes"))

    # -- Sources ------------------------------------------------------------
    src_yes, src_no = operator_rows(SOURCES)
    a("## Sources")
    a("")
    lines += section("Supported", src_yes, ("Source", "Limitations and gotchas"))
    if src_no:
        lines += section("Not supported", src_no, ("Source", "Notes"))

    # -- Statements ---------------------------------------------------------
    st_yes, st_no = operator_rows(STATEMENTS)
    a("## Statements")
    a("")
    lines += section("Supported", st_yes, ("Statement", "Limitations and gotchas"))
    lines += section("Not supported", st_no, ("Statement", "Notes"))

    # -- Binary operators ---------------------------------------------------
    a("## Binary and string operators")
    a("")
    a(f"{len(BINARY_OPERATORS)} supported.")
    a("")
    rows = [
        (f"`{spec.op}`", gotchas(spec.op, spec.rules, spec.note))
        for _, spec in sorted(BINARY_OPERATORS.items())
    ]
    lines += section("Supported", rows, ("Operator", "Limitations and gotchas"))
    a("The `in` family (`in`, `!in`, `in~`, `!in~`) is supported, including the")
    a("subquery form `x in (T | project col)`.")
    a("")
    a("`has_any` and `has_all` are supported. They share the `in` family's syntax")
    a("but not its meaning: each item is a whole-**term** match like `has`, so")
    a('`\"errors\" has_any (\"error\")` is **false** (R3). The right-hand side may be a')
    a("value list, a `dynamic` array, or a subquery. Kusto has no `!has_any`,")
    a("`!has_all` or `has_any_cs`, and neither does this — they are refused.")
    a("")
    a("Two degenerate needle sets, both measured: a **null** needle matches")
    a("anything, exactly as `has \"\"` does — so `has_any (dynamic([null]))` is")
    a("true for every row, while under `has_all` the null simply drops out of")
    a("the conjunction. And `has_all` over an **empty** list is **true** (the")
    a("empty conjunction) where `has_any` over one is false.")
    a("")
    a("Not supported: `between` / `!between`, and the term-prefix forms")
    a("`hasprefix` / `hassuffix`.")
    a("")

    # -- Aggregates ---------------------------------------------------------
    a("## Aggregate functions")
    a("")
    a(f"{len(AGGREGATE_FUNCTIONS)} supported.")
    a("")
    rows = [
        (f"`{name}`", gotchas(name, spec.rules, spec.note, aggregate=True))
        for name, spec in sorted(AGGREGATE_FUNCTIONS.items())
    ]
    lines += section("Supported", rows, ("Function", "Limitations and gotchas"))
    a("Not supported: `arg_max`, `arg_min`, `binary_all_*`, `buildschema`,")
    a("`hll` / `hll_merge` / `dcount_hll`, `tdigest` / `percentile_tdigest`,")
    a("`make_bag`, `make_list_if` / `make_set_if`, `count_distinctif`,")
    a("`percentiles_array`, `series_*` aggregates.")
    a("")

    # -- Scalars ------------------------------------------------------------
    scalar_names = (
        (set(SCALAR_FUNCTIONS) - set(AGGREGATE_FUNCTIONS))
        | set(_SPECIAL_FORMS)
        | (set(SPECIAL_NOTES) - set(BINARY_OPERATORS) - set(AGGREGATE_FUNCTIONS))
    )
    total_scalar = len(scalar_names)
    a("## Scalar functions")
    a("")
    a(f"{total_scalar} supported, grouped by family.")
    a("")

    by_family: dict[str, list[tuple[str, str]]] = {}
    for name, spec in SCALAR_FUNCTIONS.items():
        if name in AGGREGATE_FUNCTIONS:
            # A handful of names are in both registries. They belong under
            # "Aggregate functions"; repeating them here would double-count the
            # surface and put `sum` under a heading nobody would look in.
            continue
        note = SPECIAL_NOTES.get(name, spec.note)
        by_family.setdefault(family_of(name), []).append(
            (f"`{name}`", gotchas(name, spec.rules, note))
        )
    for name in list(_SPECIAL_FORMS) + list(SPECIAL_NOTES):
        if name in SCALAR_FUNCTIONS:
            continue
        entry = (f"`{name}`", cell(SPECIAL_NOTES.get(name, "")))
        bucket = by_family.setdefault(family_of(name), [])
        if entry[0] not in {e[0] for e in bucket}:
            bucket.append(entry)

    for label in [f[0] for f in FAMILIES] + ["Other"]:
        rows = by_family.get(label)
        if not rows:
            continue
        lines += section(label, sorted(set(rows)), ("Function", "Limitations and gotchas"))

    # -- Types --------------------------------------------------------------
    a("## Data types")
    a("")
    a("| KQL | DuckDB | Limitations and gotchas |")
    a("|---|---|---|")
    a("| `bool` | `BOOLEAN` | — |")
    a("| `int` | `INTEGER` | 32-bit, as in KQL. |")
    a("| `long` | `BIGINT` | KQL's default integer. Integer literals are cast explicitly so DuckDB does not infer `INTEGER` and overflow at 2^31. |")
    a("| `real` | `DOUBLE` | — |")
    a("| `decimal` | `DECIMAL(38,9)` | KQL's `decimal` is a 128-bit type; the scale here is fixed. |")
    a("| `string` | `VARCHAR` | Character-oriented (R11). |")
    a("| `datetime` | `TIMESTAMP` | Always UTC, never `TIMESTAMPTZ` (R8). Microsecond precision; KQL keeps 100 ns ticks, so the seventh digit is not available. |")
    a("| `timespan` | `INTERVAL` | The `[d.]hh:mm:ss` literal form needs special handling — DuckDB's cast returns null for the leading day part. |")
    a("| `guid` | `UUID` | — |")
    a("| `dynamic` | `JSON` | Missing properties are null, never an error (R9). |")
    a("")

    # -- Refusals -----------------------------------------------------------
    a("## Deliberate refusals")
    a("")
    a("These are not gaps waiting to be filled by whoever gets there first. Each one")
    a("has an obvious-looking mapping that returns a *different answer* than Kusto,")
    a("and shipping that would be worse than raising.")
    a("")
    a("| Construct | Why it raises instead |")
    a("|---|---|")
    for what, why in REFUSALS:
        a(f"| {what} | {cell(why)} |")
    a("")

    # -- Divergences --------------------------------------------------------
    a("## Known divergences")
    a("")
    a("Cases that translate and run but do **not** match Kusto. Each is enforced as a")
    a("known failure in `tests/test_behavior.py::KNOWN_DIVERGENCES`, so one that")
    a("starts passing fails the build and has to leave the list.")
    a("")
    a("| Case | What differs |")
    a("|---|---|")
    for what, why in DIVERGENCES:
        a(f"| {what} | {cell(why)} |")
    a("")
    a("---")
    a("")
    a("Coverage against a published external subset:")
    a("[Azure Monitor profile](azure-monitor-profile.md). The normative mapping spec,")
    a("including the full text of R1–R21: [`TRANSLATION.md`](TRANSLATION.md).")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the committed file differs from what would be generated",
    )
    args = parser.parse_args()

    generated = build()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != generated:
            print(
                f"{OUTPUT.relative_to(ROOT)} is stale — run "
                "`python tools/gen_support_matrix.py`",
                file=sys.stderr,
            )
            return 1
        print(f"{OUTPUT.relative_to(ROOT)} is up to date")
        return 0

    OUTPUT.write_text(generated, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({len(generated.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
