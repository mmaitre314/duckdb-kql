# TRANSLATION.md — Normative KQL → DuckDB SQL Mapping Guide

> **Status: normative.** This is the specification the translator must follow —
> the analogue of Bun's `PORTING.md`, written *before* the implementation (see
> [`lessons-from-bun-rewrite.md`](./lessons-from-bun-rewrite.md) L2).
>
> Every rule here is binding. A deviation requires a comment citing *why*, and a
> test. If you find yourself writing a paragraph to justify a workaround, the
> mapping is wrong — fix the mapping.
>
> **Verification status.** The semantic rules in §4 are drawn from Microsoft's
> public KQL documentation, which is our normative specification. Each one is
> marked with the ID of the trap test that must pin it against the Kusto Emulator.
> **A rule is not "known" until its test exists and passes.** Until then it is a
> documented expectation, not a verified fact.

## 0. Governing principles

1. **Docs are the spec; the emulator verifies.** Microsoft's public docs (CC-BY)
   define correct behavior. The Kusto Emulator confirms it. Never infer semantics
   from another translator's source.
2. **SQL first, UDF last.** Prefer a native DuckDB expression, then a SQL
   template, then — only if neither is correct or practical — a Python UDF (§7).
3. **Fix the rule, not the query.** Never special-case one query to make a test
   pass. Fix the mapping entry or the invariant so the whole class is repaired.
4. **Never weaken, skip, or delete a test.** If the oracle disagrees with us, we
   are wrong. Changing an expectation requires a written argument that the
   *oracle* was wrong.
5. **Fail loudly, never silently wrong.** An unsupported construct must raise
   `KqlUnsupportedError` with the construct name and source span. A wrong answer
   is far worse than a clear refusal.
6. **Every mapping carries a test.** No entry lands in the registry without at
   least one corpus case and, for anything in §4, a trap test.

## 1. Pipeline architecture

KQL is a linear pipeline of tabular operators. Render it as a **chain of CTEs**,
one per operator, in source order. DuckDB optimizes straight through them, and
the 1:1 correspondence makes generated SQL debuggable.

```
Logs | where Level == "Error" | project Component | take 10
```
```sql
WITH _s0 AS (SELECT * FROM "Logs"),
     _s1 AS (SELECT * FROM _s0 WHERE "Level" = 'Error'),
     _s2 AS (SELECT "Component" FROM _s1),
     _s3 AS (SELECT * FROM _s2 LIMIT 10)
SELECT * FROM _s3
```

**Conventions**
- CTE names: `_s0`, `_s1`, … in pipeline order. Never reuse or reorder.
- One operator → exactly one CTE. Do **not** fuse operators, even when trivially
  fusable; readability and 1:1 traceability beat cosmetic SQL brevity. DuckDB's
  optimizer collapses them anyway.
- The translator tracks an ordered **output column list** per stage; operators
  that reshape columns (`project`, `project-away`, `extend`, `summarize`) update
  it. Column order is user-visible and must be preserved.
- `let` bindings become CTEs (tabular) or inlined scalars (scalar), emitted before
  `_s0`.
- All identifiers are emitted **double-quoted** (§4 R7). All string literals are
  emitted single-quoted with `''` escaping.

## 2. Type mapping

| KQL type | DuckDB type | Notes |
|---|---|---|
| `bool` | `BOOLEAN` | |
| `int` | `INTEGER` | 32-bit |
| `long` | `BIGINT` | 64-bit; KQL's default integer type |
| `real` | `DOUBLE` | |
| `decimal` | `DECIMAL(38,9)` | the scale is rendered, so `1` reads as `1.000000000` — which is why `todecimal` and `parse … : decimal` are refused rather than mapped |
| `string` | `VARCHAR` | UTF-8; `strlen` counts **characters** (§4 R11) |
| `datetime` | `TIMESTAMP` | **always UTC** (§4 R8) |
| `timespan` | `INTERVAL` | see §3 literals |
| `guid` | `UUID` | |
| `dynamic` | `JSON` | §4 R9; lists/structs only where provably equivalent |

**Integer default:** a bare integer literal in KQL is `long` → emit `BIGINT`.
Do not let DuckDB infer `INTEGER` and silently overflow at 2^31.

## 3. Literal mapping

| KQL | DuckDB |
|---|---|
| `"text"` / `'text'` | `'text'` (escape `'` → `''`) |
| `true` / `false` | `TRUE` / `FALSE` |
| `123` | `123::BIGINT` |
| `1.5` | `1.5::DOUBLE` |
| `datetime(2024-01-01)` | `TIMESTAMP '2024-01-01'` |
| `datetime(null)` | `CAST(NULL AS TIMESTAMP)` |
| `1d`, `90m`, `100ms`, `1s`, `1h`, `1tick` | `INTERVAL '1 day'`, `INTERVAL '90 minutes'`, `INTERVAL '100 milliseconds'`, … ; `1tick` = 100 ns |
| `dynamic({...})` / `dynamic([...])` | `'…'::JSON` |
| `dynamic(null)` | `CAST(NULL AS JSON)` |
| `guid(...)` | `UUID '…'` |

Typed nulls (`int(null)`, `long(null)`, …) map to `CAST(NULL AS <type>)` — never a
bare `NULL`, which would lose type information in a `UNION`.

## 4. Semantic invariants — the golden rules

**This section is the heart of the guide.** Each rule is a place where KQL and SQL
look identical and behave differently — the *"syntactically identical, semantically
different"* failure mode that produced most of Bun's regressions. Every rule needs
a trap test (`tests/traps/`) whose expectation comes from the emulator.

---

### R1 — Conversions return **null**, never an error → `TRY_CAST`
*Trap: `trap-r1-conversions`*

KQL's `toint()`, `tolong()`, `todouble()`, `todatetime()`, `toguid()`,
`totimespan()` return **null** on unparseable input. DuckDB's `CAST` **throws**.

> **Always emit `TRY_CAST`, never `CAST`,** for any KQL conversion function.

`CAST` is permitted only where the translator itself constructs a provably-valid
value (e.g. typed null literals, §3).

---

### R2 — String comparison case-sensitivity
*Trap: `trap-r2-case-sensitivity`*

| KQL | Case | DuckDB |
|---|---|---|
| `==` | **sensitive** | `=` |
| `!=` | **sensitive** | `<>`, wrapped so a null operand is **true** (R4) |
| `=~` | **insensitive** | `lower(a) = lower(b)` |
| `!~` | **insensitive** | `lower(a) <> lower(b)` |

> `==` on strings is **case-sensitive**; `=~` is the insensitive form. Do not
> assume SQL collation defaults.

---

### R3 — `has` is **term-based**; `contains` is **substring**; both default **case-insensitive**
*Trap: `trap-r3-has-vs-contains`*

The highest-risk family in the language.

| KQL | Meaning | Case (default) |
|---|---|---|
| `contains` | **substring** | insensitive |
| `contains_cs` | substring | sensitive |
| `has` | **whole term** (tokenized) | insensitive |
| `has_cs` | whole term | sensitive |
| `startswith` / `endswith` | prefix / suffix | insensitive |
| `startswith_cs` / `endswith_cs` | prefix / suffix | sensitive |
| `hasprefix` / `hassuffix` | **term** prefix / suffix | insensitive |
| `has_any (a, b, …)` | whole term, **any** item matches | insensitive |
| `has_all (a, b, …)` | whole term, **every** item matches | insensitive |

- `contains` → `ILIKE '%' || … || '%'` (escape `%`/`_` in the pattern).
- `has` → **not** a substring match. `Text has "err"` is **false** for
  `"error"`, because `has` matches whole terms delimited by non-alphanumeric
  characters. Emit a tokenization-aware form (regex on term boundaries), never a
  bare `LIKE '%x%'`.

**What a term is, exactly.** A run of Unicode letters and digits; *every* other
character delimits one — **underscore included**. This is not regex `\b`, which
counts `_` as a word character:

> `"a_b" has "a"` is **true** in Kusto. Emitting `\ba\b` makes it **false**.

Measured on the emulator across ~30 punctuation characters (all delimit) against
`a1`, `aa` and `éa` (none do — accented letters are term characters). The
mapping is therefore `(?:^|[^\pL\pN])needle(?:$|[^\pL\pN])`, spelled with the
brace-free `\pL` form because these patterns double as `str.format` templates.

**`has_any` / `has_all` are the list forms of `has`, not of `in`.** The grammar
groups them with `in` — they share `listEqualityExpression` — but they are term
matches, not equality tests: `"errors" has_any ("error")` is **false**, exactly
as `has` is. `has_any` is an OR of term matches, `has_all` an AND, over one
shared term definition. The right-hand side may be a value list, a `dynamic`
array, or a tabular subquery; all three were confirmed accepted.

There is **no** `!has_any`, `!has_all` or `has_any_cs` — Kusto rejects all
three, so they are refused here too.

**Two degenerate needle sets, both measured, neither guessable:**

| form | Kusto |
|---|---|
| `has_any (dynamic([null]))`, `has_any (dynamic(['a', null]))` | **every row** — a null needle matches anything, exactly as `has ""` does |
| `has_all (dynamic(['a', null]))` | the rows matching `'a'` — the null drops out of the conjunction |
| `has_all (dynamic([]))`, `has_all (T)` for an empty `T` | **every row** — the empty conjunction is true |
| `has_any (dynamic([]))` | no rows |

Both were wrong here until the term pattern was folded: `list_filter` drops a
null predicate rather than keeping the row, and `bool_and` over zero rows is
NULL, which the R4 coalesce turned into false.

**The term pattern must be a constant wherever the needle is.** A literal needle
lets the boundary be decided at translation time (`is_term_char`, checked
exhaustively against RE2's `[\pL\pN]`), so RE2 compiles the pattern once; built
from `CASE` expressions instead it is rebuilt and recompiled **per row**, and
inside a `list_filter` lambda per *(row, needle)*. Measured on a 5,000-row
table: `has_all` over four literal needles went from **28 seconds** to 29ms.
A literal `dynamic([...])` array is therefore unrolled into one constant test
per needle rather than filtered at run time.

For a **tabular** right-hand side the needles are real runtime data and cannot
be folded, so that form uses `EXISTS` (which short-circuits and decorrelates
into a semi-join) behind a `contains` prefilter — sound because a term match
implies a case-insensitive substring match. Measured: 3415ms → 45ms.

**`not()` is a function, not the `!` prefix.** It is also the one member of this
neighbourhood that does *not* get R4's totality treatment: `not(bool(null))` is
**null**, not true, so SQL's `NOT` maps across directly. `not(1)` is `false`, so
the argument is cast rather than required to be boolean.
- Every operator above has a negated form (`!has`, `!contains`, …), and each is
  **true** on a null operand rather than null (R4) — a bare `NOT (…)` silently
  drops those rows.

> Getting `has` wrong yields plausible-but-wrong results on real log queries. It
> must be implemented as a family, with the whole matrix tested at once.

---

### R4 — Null semantics
*Trap: `trap-r4-nulls`*

- **Aggregates ignore nulls:** `count(Expr)` counts **non-null** values
  (→ `count(expr)`), while bare `count()` counts **all rows** (→ `count(*)`).
  `sum`/`avg`/`min`/`max` ignore nulls, matching SQL.
- **The equality, membership and matching families are TOTAL**, where SQL's are
  three-valued. Measured on the emulator (`tests/test_null_semantics.py`):

  | family | non-null | one operand null | both null |
  |---|---|---|---|
  | `==` `=~` `in` `contains` `has` `startswith` `endswith` `matches regex` | as expected | **false** | null |
  | `!=` `!~` `!in` `!contains` `!has` `!startswith` `!endswith` | as expected | **true** | null |
  | `<` `<=` `>` `>=` | as expected | null | null |

  A naive `NOT (…)` therefore **drops** every null row from
  `| where s !contains "x"` — a smaller, plausible answer with nothing to
  indicate it. The emitter wraps these in `coalesce(…, TRUE/FALSE)`. The both-null
  row of the table is why it cannot do so unconditionally: `a == b` with both
  sides null is null, so a blanket coalesce trades one wrong answer for another.
  When either operand is a literal that case is unreachable and the cheap form
  is exact; otherwise the comparison is guarded.
- **Ordering comparisons are not total** and must stay three-valued — the fix
  above must not be extended to them.
- **Known divergence:** KQL has no null `string` — an absent string *is* the
  empty string, so `isnull(s)` is always false and `s == ""` is true for it. A
  DuckDB `VARCHAR` really can be NULL, so `s == ""` against a null column is
  true in Kusto and false here. Not reconciled: doing so means coalescing every
  string operand to `''`, which changes `isnull`/`isempty` as well.
- `isnull()`, `isnotnull()`, `isempty()` (null **or** empty string),
  `isnotempty()`, `coalesce()` — note `isempty` ≠ `isnull`.
- Arithmetic propagates null.

---

### R5 — `join` defaults to `innerunique`, **not** SQL inner join ⚠️
*Trap: `trap-r5-join-kinds`*

This is the single most dangerous default in KQL.

> A bare `| join (T) on Key` is **`innerunique`**: it **de-duplicates the left
> side's join keys** before joining. Emitting `INNER JOIN` is **wrong** and
> silently returns extra rows.

| KQL `kind=` | DuckDB |
|---|---|
| *(omitted)* / `innerunique` | de-duplicate left key set first, then inner join |
| `inner` | `INNER JOIN` |
| `leftouter` | `LEFT JOIN` |
| `rightouter` | `RIGHT JOIN` |
| `fullouter` | `FULL JOIN` |
| `leftsemi` | `SEMI JOIN` / `WHERE EXISTS` |
| `rightsemi` | mirror of `leftsemi` |
| `leftanti` (`anti`) | `ANTI JOIN` / `WHERE NOT EXISTS` |
| `rightanti` | mirror of `leftanti` |

Implement **all kinds in one wave**, never incrementally.

---

### R6 — `sort` defaults to **descending**
*Trap: `trap-r6-sort-defaults`*

> `sort by X` and `order by X` default to **`desc`** — the opposite of SQL's
> `ASC` default. Always emit an explicit `ASC`/`DESC`.

Null ordering (`nulls first`/`last`) must be pinned by the oracle and emitted
explicitly rather than relying on DuckDB's default.

---

### R7 — Identifiers are **case-sensitive**
*Traps: `trap-r7-identifiers`, `tests/test_case_collisions.py`*

KQL identifiers are case-sensitive; DuckDB's are case-insensitive by default.
`Foo` and `foo` are distinct KQL columns.

> **Always emit double-quoted identifiers.** Never emit a bare identifier, even
> when it looks safe. Detect collisions that survive DuckDB's folding and raise
> `KqlSchemaError` rather than resolving them arbitrarily.

**Quoting is not enough**, which is the half that is easy to skip. Measured
against DuckDB itself, not assumed:

* a quoted `"FOO"` **falls back** to a `foo` column when no exact match exists
  — `SELECT "FOO" FROM (SELECT 1 AS "foo")` returns 1, with no error;
* a SELECT list producing both spellings **renames the second** to `Foo_1`.

Together those turn a legal KQL query into wrong numbers under a plausible
schema:

| query | Kusto | quoted-only rendering |
|---|---|---|
| `datatable(foo:long)[1,2,3] \| extend Foo = foo+100 \| project foo, Foo` | `1, 101` | `foo=1, Foo_1=1` |
| `datatable(Foo:long, foo:long)[1,2] \| project Foo, foo` | `1, 2` | `1, 1` |
| `datatable(Foo:long, foo:long)[1,2] \| project foo` | `2` | `1` |
| `datatable(a:string, A:string)['x','y'] \| summarize count() by a, A` | `x, y` | `x, x` |

So the collision check is not defensive polish; it is the rule. It runs
wherever a column list is *computed* — `_source_columns` and
`_operator_columns` in `schema.py` — so the error names the stage that
introduced the pair. **These queries are legal KQL and we refuse them**,
deliberately: no rendering can keep two names that are one name to the engine
underneath, and a caller who never meant to collide is far commoner than one
who did. `project-rename` is the way out.

Not checked when no schema is available, because then there is no column list
to check — one more reason `duckdb_kql.kql()` is the better entry point.

---

### R8 — datetime is UTC; binning has an origin
*Trap: `trap-r8-datetime`*

- `datetime` is **always UTC**; never emit `TIMESTAMPTZ` or apply a local zone.
- `now()` and `ago(ts)` must be **evaluated once per query**, not per row, so
  repeated references agree. `ago(x)` → `(now() - <x>)` with a single evaluation.
- `bin(value, roundTo)` is a **floor to a multiple of `roundTo` from the epoch
  origin** — `time_bucket` / `date_trunc` are close but the origin and
  week/month cases differ. Pin every unit against the oracle before use.
- `bin()` also applies to **numeric** values (`bin(x, 10)` → floor to multiple of
  10), not just datetimes.

---

### R9 — `dynamic`: missing property is **null**, never an error
*Trap: `trap-r9-dynamic`*

- Accessing a missing property or out-of-range index returns **null**, never an
  error. Map to `json_extract`, which must not be allowed to raise.
- Distinguish `json_extract` (JSON value) from `json_extract_string` (text) —
  choosing wrong yields spuriously quoted strings.
- `parse_json`/`todynamic` on invalid input → null (R1), not an error.
- `mv-expand` expansion of an empty or null array: row-preserving behavior must be
  pinned by the oracle (it differs from a naive `UNNEST`, which drops rows).
- What comes *out* of `mv-expand` is a dynamic scalar, and the next operator is
  usually a string one — see R17, which is where that goes wrong.

---

### R10 — Nondeterministic operators must be asserted as sets
*Trap: `trap-r10-nondeterminism`*

`take`/`limit`, `sample`, and `top … by` tie-breaking are **not deterministic** in
KQL. The translation is still `LIMIT`, but tests must compare **unordered**, and
we must never claim a specific row order absent a terminal `sort`.

---

### R11 — Strings are character-oriented; approximations are approximate
*Traps: `trap-r11-strings`, `trap-r11-approx`*

- `strlen` counts **characters**, not bytes → `length()` (not `octet_length`).
  `substring` uses **0-based** indices and must tolerate negative/out-of-range
  input without erroring (KQL clamps; SQL's `substring` is 1-based). A **null**
  index propagates on both start and length, so `greatest(length, 0)` is the
  wrong clamp — DuckDB's `greatest(NULL, 0)` is 0, which turned a null length
  into `''`. The *source* does not propagate: a null string is `''` (R20).

  Measuring this needs care, because Kusto answers it two ways. Its constant
  folder reads a null index as 0 — `print x = substring('abcdefg', long(null))`
  is `'abcdefg'` — while the row engine propagates. The row engine is what runs
  over data, so it is what the mapping reproduces; a `print`-only measurement
  gets the opposite answer and looks just as authoritative.
- `countof`'s default `normal` kind counts **overlapping** occurrences —
  `countof('aaaa', 'aa')` is 3 — while its `regex` kind does not (2). No DuckDB
  function overlaps and RE2 has no lookahead, so the substring kind counts start
  positions instead.
- `dcount`/`dcountif` are **approximate** (HLL) in KQL, and the honest-looking
  mapping to `approx_count_distinct` is nevertheless the wrong one. Measured
  against the emulator, at corpus cardinalities KQL's estimate returns the
  *exact* value while `approx_count_distinct` is ~13% low (37 against 32) —
  outside any sane tolerance, and enough to reorder `top N by dcount`. So the
  mapping is exact `count(DISTINCT …)`: it matches the oracle and is
  reproducible. The residual risk runs the other way — at cardinalities high
  enough for KQL's own estimate to drift — and is what the drift lane is for.
- `percentile`/`percentiles` use **nearest-rank**, not linear interpolation →
  `quantile_disc`. Measured per state on the fixture, `disc` matched all 52
  groups exactly where `quantile_cont` was off by up to 39%. At large N KQL
  switches to an estimate and the 5% approximate-function tolerance covers the
  remaining ~0.07%.

---

### R12 — `summarize` and `distinct` output column naming
*Traps: `trap-r12-summarize-naming`, `tests/test_distinct.py`*

Auto-generated names are user-visible and must match exactly: `count()` →
`count_`, `avg(X)` → `avg_X`, etc. Group-by keys come first, in source order, then
aggregates. An explicit `Name =` overrides. Null group keys form their own group.

An aggregate may be wrapped in a scalar expression — `round(sum(y), 2)`,
`sum(x) / count()` — and then the name comes from the **aggregate**, not the
wrapper: `round(sum(y), 2)` is `sum_y`. The rule is positional, following first
arguments only, so `strcat('n=', tostring(count()))` is `Column1`. A column
outside an aggregate is refused, as Kusto refuses it — *including* a `by` key,
which plain SQL would accept.

**A `by` key names itself after its inner column only for an allow-listed set of
functions**, and `distinct` — which also takes expressions, despite a documented
syntax of a column list — follows the same rule. This is a list, not a
principle, and every entry was measured:

| | |
|---|---|
| passes the name through | `tostring` `toint` `tolong` `todouble` `toreal` `tobool` `todatetime` `totimespan` `toguid` `todecimal` `tohex` `bin` `bin_at` `floor` `ceiling` `round` `startofday` `abs` `sqrt` `log` `log10` `log2` `exp` `exp2` |
| falls back to `ColumnN` | everything else — including `tolower`, `toupper`, `isempty`, `strcat`, `sign`, `pow`, `exp10`, `startofweek`, `dayofweek` |

So `tostring(B)` is `B` while `tolower(B)` is `Column1`, and `startofday(T)` is
`T` while `startofweek(T)` is `Column1`. The pass-through nests —
`tostring(toint(C))` is `C` — but a call outside the list **breaks the chain**:
`abs(-C)` and `tolower(tostring(B))` are both `Column1`.

The positional fallback counts **only the targets that need one**:
`distinct C, tolower(B)` is `C, Column1`, not `C, Column2`.

**Known residue.** Arithmetic gets a number one higher than expected —
`distinct -C` and `distinct C + 0` are both `Column2` on a table of any width,
with no second column to explain it — and `strlen(B)` is named `strlen_B`, the
aggregate convention appearing on a scalar. Neither is reproduced; both are rare
enough that guessing a rule from two data points would be worse than the
positional name. See the support matrix.

### R13 — `/` on two integers is **integer** division

*Trap: `tests/test_division.py`*

`7 / 2` is `3`, not `3.5`, and it **truncates toward zero**: `-7 / 2` is `-3`,
not `-4`. One real operand makes the whole expression real. Division by zero is
`null` for integers and `±Infinity` for reals.

Rendered with DuckDB's `//`, which despite the spelling is not floor division —
it truncates toward zero on integers and behaves as ordinary division on floats
(`7.5 // 2` is `3.75`). DuckDB decides which from the operand types, which is
exactly the type information the translator does not have. SQL's plain `/`
promotes to double and answers `3.5`: a silently wrong number in the most
ordinary arithmetic in the language.

The one place `/` is still emitted is when an operand is *visibly* a real — a
literal, `todouble`/`toreal`, or arithmetic involving one — because `//` returns
null for a zero divisor where KQL's float division returns `±Infinity`. Under a
bare real column that residue remains; see the support matrix.

---

### R14 — `lookup` is not `join kind=leftouter`, and join keys match **null to null**

*Trap: `tests/test_lookup.py`*

Two independent rules, both measured on the emulator, both of the "looks right,
returns the wrong rows" kind.

**(a) `lookup` has its own defaults and its own column rule.**

> `| lookup (D) on Key` defaults to **`leftouter`** — *not* `join`'s
> `innerunique` — and **drops the right side's key columns** from the output.

Only `leftouter` and `inner` exist; the emulator rejects `innerunique`,
`fullouter`, `leftsemi`, `leftanti` and the rest outright, so accepting them
would let a query pass locally and fail on a real cluster. Because the default
is `leftouter`, `lookup` never de-duplicates the left key set the way a bare
`join` does.

The column rule is specifically about *keys*, not collisions in general. With
left `(Row, Key, V)` and right `(Key, V, Alias)` on `Key`:

| | output |
|---|---|
| `join kind=leftouter` | `Row, Key, V, Key1, V1, Alias` |
| `lookup` | `Row, Key, V, V1, Alias` |

`Key1` is gone; `V1` remains. With `on $left.K1 == $right.K2` it is `K2` that
disappears and `K1` that stays — the key is identified by its name on the right.

**(b) A null key matches a null key.** This one applies to `join` too:

> KQL's join/lookup key equality is **not** SQL's `=`. `Key == null` on the left
> matches `Key == null` on the right.

Measured across every kind: `leftouter`, `inner` and `innerunique` all return the
matched row, and `leftanti` correspondingly does *not* return it. SQL's `=`
answers NULL and drops the pair, so both operators emit
**`IS NOT DISTINCT FROM`**. Emitting `=` silently loses every null-keyed match.

**Known residue.** KQL has no null string — an outer join's unmatched `string`
column is `''` there and NULL in DuckDB. `isempty()` reads both correctly, but a
downstream `| where Alias != ""` keeps the unmatched row here and drops it in
Kusto. Fixing it needs column *types*, which the translator does not carry — the
schema is names only. Applies equally to `join kind=leftouter`; see the support
matrix.

---

### R15 — `union` matches columns by **name**, not by position

*Trap: `tests/test_union.py`*

> A KQL `union` pairs each branch's columns **by name** and pads what is missing
> with null. SQL's `UNION ALL` pairs them **by position**.

Rendered with DuckDB's **`UNION ALL BY NAME`**, which is the same rule. Plain
`UNION ALL` would line up two branches that list the same names in a different
order and answer with a string in a float column; plain `UNION` would also
de-duplicate, and KQL does not — measured, `union UT1, UT1` returns each row
twice.

The rest was read off the emulator, because none of it is guessable:

| | behaviour |
|---|---|
| default kind | **`outer`** — the *union* of the branches' columns |
| `kind=inner` | the *intersection* of the column names |
| column order | **first appearance, left to right** — `union A, B` gives `x, y, z`; `union B, A` gives `x, z, y` |
| `withsource=Col` | one leading column naming each row's branch |
| `isfuzzy=true` | a branch whose **table does not exist** is dropped instead of failing |
| `union UT*` | every table matching the pattern; matching *nothing* is an error (SEM0100), not an empty result |

Column order is user-visible (R1), so the emitter names the output columns
explicitly rather than inheriting whatever `BY NAME` produces.

**`withsource` labels are positional unless the branch is a bare table.** A bare
table reports its own name with any database qualifier stripped
(`database('D').UT2` → `UT2`); a wildcard reports each *matched* table's name,
so it cannot be rendered as one arm. Everything else — a subquery branch, a
piped left side, and, measured, a **`let`-bound name** — is `union_argN`,
counting the left side as branch 0.

`union A, B` and `A | union B` return identical results, so both lower to one
`ir.Union` whose left side is branch 0. Two code paths here would be two things
that must never disagree.

`isfuzzy` deliberately does not fire when no schema was supplied: "I have no
catalog" is not "this table does not exist", and conflating them would drop
every branch and return a short answer that looks like data.

**Known residue**, both about columns rather than rows:

* Two branches giving the **same column name a different type** diverge: Kusto
  splits them (`c_string`, `c_real`), DuckDB casts both into one column.
  Detecting it needs column *types*, which the schema does not carry — the same
  limit as R14's null-string residue.
* A **wildcard expands in name order** here and in **creation order** in Kusto.
  Measured: tables made as `Zed`, `Alpha`, `Mid` give `union *` the columns
  `z1, a1, m1` there, `a1, m1, z1` here. A DuckDB catalog does not record
  creation order, so this is deterministic rather than faithful; the rows are
  identical either way, and only the column order of the outer union moves.

See the support matrix.

---

### R16 — `macro-expand` is `union` with the source rewritten per entity

*Trap: `tests/test_macro_expand.py`*

> `macro-expand G as s (body)` evaluates **body once per entity** in the group
> and unions the results. It is not a second way to combine branches.

Measured against two databases, and every part of it is R15's:

| | behaviour |
|---|---|
| `count` inside the parentheses | one row **per entity** |
| `count` after the closing paren | one row, over the union |
| differing schemas | outer union of column names in first-appearance order |
| `isfuzzy=true` | drops an entity whose **table** is missing |
| `hint.*` | accepted; cannot change the result |
| row order | undefined, as R10 already says |

So it lowers to an `ir.Union` and inherits all of that, rather than growing a
parallel implementation that would have to be kept in step.

**`scope.T` is not a table reference to the lowerer.** Outside a macro-expand it
is dynamic property access on a column called `scope`, and nothing in the syntax
tells them apart. The body is therefore lowered **once per entity with the scope
bound**, not lowered once and rewritten — by the time it is IR the two readings
are indistinguishable. The same applies inside the body: `let t = s.T` binds a
*table*, which only the bound scope reveals.

**Where the entities come from.** Inline (`entity_group [...]`) and `let`-bound
groups carry them in the query text. A **named** group is cluster-side state
created by `.create entity_group`, so it is supplied by the caller through
`entity_groups=`, on exactly the reasoning `cluster()` uses (R3 of
`duckdb_kql.clusters`): expanding an unknown group to nothing would answer a
question about several databases with none. Entries are KQL entity references as
text, so a `cluster(...)` entity resolves through the existing `clusters=` map
and the two features compose.

Refused, each because Kusto refuses it: a duplicate entity (SEM0614), a nested
`macro-expand` (SEM0611), a bare scope used as a table (SEM0608), and an empty
group.

**`withsource=` is refused**, and this is the one place the desugar does not
carry over. Measured, Kusto qualifies *every* label as soon as one branch is in
a database other than the current one:

```
union withsource=S database('Other').T   ->  database("Other").T
union withsource=S database('Current').T ->  T
union withsource=S T, T2                 ->  T, T2
union withsource=S T, database('Other').T
                    ->  database("Current").T, database("Other").T
```

Reproducing that needs the name of the *current* database, which arrives with
the connection and not with the query — `to_sql` has no connection at all. Under
`macro-expand` every branch is a different database by construction, so Kusto
always qualifies and a bare table name would label every entity identically,
which is the one thing `withsource` is asked not to do. Plain `union` keeps bare
labels, which are right whenever every branch is in the current database.

---

### R17 — A `dynamic` in a **string context** is its unwrapped text
*Trap: `trap-r17-dynamic-string`*

A KQL `dynamic` is DuckDB `JSON`, and the two disagree about a value's text
form: JSON quotes a string, KQL does not. Kusto coerces a dynamic to its
unquoted text before every string operator, so the disagreement surfaces as a
**wrong answer** rather than an error — and it surfaces on the line after
`mv-expand`, which is the main producer of dynamic scalars.

Measured, with `strlen` proving it is the value and not the display:

| expression | Kusto | naive rendering |
|---|---|---|
| `tostring(dynamic('x'))` | `x` (1) | `"x"` (3) |
| `tostring(dynamic(null))` | `` (0, **not** null) | null |
| `tostring(dynamic([1,2]))` | `[1,2]` | `[1,2]` ✓ |
| `s startswith 'x'` over `dynamic(['x'])` | true | **false** |
| `s contains '"'` over `dynamic(['x'])` | false | **true** |
| `s == 'x'` over `dynamic(['x'])` | true | *crash* |

**The conversion** is `coalesce(x ->> '$', '')`. DuckDB's `->> '$'` is already
KQL's rule for every form — it unquotes a string and leaves everything else as
its JSON text — with one hole: it returns SQL null for a JSON null, where KQL
returns the empty string. `tostring(dynamic(null))` being `''` rather than null
is the single conversion in KQL that does not propagate null.

**Where it applies.** Three places, and the mechanism differs because the
translator has column *names* but not column *types*:

1. **`tostring` and its callers** (`strcat`, `strcat_delim`, the hash
   functions, `reverse`) go through one helper, so the family is fixed at once.
2. **String-only functions** — `strlen`, `toupper`, `tolower`, `substring`,
   `split`, `indexof`, `isempty`, `isnotempty` — coerce the argument through
   that same helper. An allow-list, not a rule: `countof`, `extract`,
   `replace_string`, `trim` and `url_encode` all **refuse** a dynamic on a
   cluster (SEM02xx), so a "looks stringy" heuristic would invent coercions.
3. **Operators** get a run-time branch instead of a coercion, because equality
   is polymorphic. Measured, `datetime_col == '2020-01-01'` coerces the
   *literal* to a datetime and answers true, so stringifying the column would
   answer false. The whole comparison is therefore rendered on both sides of a
   `typeof(x) = 'JSON'` guard; only the taken branch evaluates, and a branch
   that cannot even *bind* keeps its refusal — which is how `long_col contains
   '1'` stays an error, as it is on a cluster (SEM0709).

The `contains`/`has`/`startswith`/`endswith`/`matches regex`/`=~`/`!~` family
is always a string context. `==`/`!=` count only when the other operand is
visibly a string.

**Comparing two dynamics is refused** — Kusto answers SEM0001, "Cannot compare
dynamic values without explicit cast", and DuckDB would happily compare the
JSON text. Only decidable when both sides are statically dynamic.

**Residue**, all recorded in `tests/test_dynamic_strings.py` and all in the
loud direction unless marked:

| case | Kusto | here |
|---|---|---|
| `s == 1`, `s == true` over a string dynamic | false | conversion error |
| `s < 'y'` | refuses (SEM0064) | conversion error |
| `k == s` (string column vs dynamic column) | true | conversion error |
| `s + 1` (arithmetic over a dynamic) | 2 | binder error |
| `s in (subquery)` | coerces | compares JSON text (**mild**) |
| `s == s` (two dynamic *columns*) | refuses | answers (**mild**) |
| `countof`/`extract`/`replace_string` over a dynamic | refuse | answer (**mild**) |
| `summarize by` / `sort by` a dynamic | refuse | answer (**mild**) |

Every one of them needs the column's *type* at translation time, which the
schema does not carry. That plumbing is its own piece of work, specified in
[`column-types-proposal.md`](column-types-proposal.md) — it would also drain
R14's null-string residue and `reverse()`'s datetime divergence, and it would
turn the `typeof(...) = 'JSON'` guard above from the default into the fallback.
Worth knowing while reading this rule: Microsoft's own type system declares
`string` as **wider than** `dynamic`, so all of R17 is one widening conversion
in the official model (`Symbols/ScalarTypes.cs`).

---

### R18 — `mv-expand` zips, replaces in place, and **converts**
*Trap: `trap-r18-mv-expand`*

Three separate rules, none of them what the operator looks like it does.

**The column shape is `extend`'s.** Measured on `datatable(id, a, b)`:

```
mv-expand a       ->  id, a, b     the expansion replaces `a` where it stands
mv-expand x = a   ->  id, a, b, x  a NEW column; `a` keeps the whole array
mv-expand b = a   ->  id, a, b     `b` is overwritten; `a` kept
```

So the alias names an *output* column that replaces a same-named input and is
appended otherwise — the source disappears only when it happens to be the
target. `* EXCLUDE (a), UNNEST(…) AS a` got both halves wrong: it moved the
expanded column to the end (column order is user-visible, R1) and dropped `a`
under an alias. `with_itemindex=` always lands last and collides like a join
key — measured, `with_itemindex=b` over a table that already has `b` answers
**b1**, not DuckDB's own `b_1`.

Writing the list out needs the incoming columns, and **an alias or a
`with_itemindex=` name without a schema is refused rather than approximated.**
The two cases are not alike. A same-name expansion falls back to `* EXCLUDE
(a), UNNEST(…) AS a`, whose only residue is position — `extend`'s residue, and
visible. An alias has no safe fallback: whether it collides with an existing
column is exactly what is unknown, so the star keeps the old column and appends
a second of the same name, DuckDB resolves a later reference to the **stale**
one, and `T(id, b, a) | mv-expand b = a | project b` answers `orig` twice where
a cluster answers 10 and 20. Right row count, no error, wrong values — so these
forms join `join`/`lookup` in raising `KqlSchemaError`. `duckdb_kql.kql()`
derives the schema from the connection and never sees it.

**Several columns zip, they do not cross-product.** The row count is the
longest list's and the shorter ones pad with null. DuckDB's rule for several
`UNNEST`s in one select list is exactly that, both edges included, so the zip
needs no arithmetic:

| a | b | rows |
|---|---|---|
| `[10,20,30]` | `['p']` | `(10,'p') (20,null) (30,null)` |
| `[]` | `['p']` | `(null,'p')` — an **empty** array padded against a non-empty one contributes a null row, unlike the single-column case where it yields none |
| `[]` | `[]` | none |
| `null` | `['p','q']` | `(null,'p') (null,'q')` — a null is *one* element, not zero |
| `{'k':1}` | `['p','q']` | `({"k":1},'p') (null,'q')` — an object counts one per key |

The index list must therefore be as long as the **longest** expansion, with no
floor of one: clamping it to at least one gave an empty array a single row
carrying null where Kusto answers none.

**`to typeof(T)` converts; it does not declare.** Measured element by element
over `[1, 2.5, -2.5, '2', true, null]`:

| T | result |
|---|---|
| `long` / `int` | `1, 2, -2, null, null, null` — a number, truncated **toward zero** (a cast would make -2.5 into -3) |
| `real` / `double` | `1.0, 2.5, -2.5, null, null, null` |
| `bool` | `true, null, null, null, true, null` — a JSON boolean or a *whole* number; `2` is true and `2.5` is null |
| `string` | `'1', '2.5', '-2.5', '2', 'true', ''` — exactly R17's conversion |
| `datetime` / `guid` | the mirror image: only a JSON **string** converts |
| `timespan` / `decimal` | **refused** — every input tried, `'1.00:00:00'` included, is null there, so there is no rule to reproduce, and answering null for everything would look like a working conversion |

A JSON *string* is not a number here: `'2' to typeof(long)` is null, which is
what makes this a conversion rather than a declaration and why a cast of the
JSON text would be wrong.

**`limit N`** caps the rows produced *per input row*, so it truncates each list
before the zip rather than the result afterwards; `limit 0` answers no rows.
**`kind=array`** / **`bagexpansion=array`** turns a bag into two-element
`[key, value]` arrays instead of single-key bags, and leaves a plain array
alone. `bag` is the default.

**Not implemented:** `mv-expand` of a non-dynamic column, which Kusto refuses
(SEM0447) and needs the column's type; and `mv-apply`, which runs a
sub-pipeline per element and is a different operator.

---

### R19 — `parse` is all-or-nothing, and its captures are type-shaped
*Trap: `trap-r19-parse`*

`parse Expression with <pattern>` compiles to **one** regex with a named group
per declared column, matched once per row by `regexp_extract(s, pattern,
[names])`. DuckDB fits it unusually well: a non-match returns `''` in every
field, which *is* Kusto's rule for a string column.

Four rules, none of them guessable, all measured:

**A non-match keeps the row.** `''` for a string column, null for a typed one —
not null for strings, and not dropped. `parse-where` drops it instead.

**`kind=simple` is all-or-nothing.** If *any* declared column fails to convert,
the entire row is blanked — including columns that converted fine:

```
datatable(s:string)['a=1,b=2,c=zz,d=4']
| parse s with "a=" a: long ",b=" b ",c=" c: long ",d=" d
    ->  a=null  b=''  c=null  d=''      `a=1` is perfectly good and is blanked
```

A two-column example cannot tell this from "stop at the first failure"; three
can. An **empty** capture into a typed column counts as a failure too, so there
is no "empty is not a failure" exception. `kind=relaxed` converts each column
independently. `parse-where` is then exactly *matched **and** every conversion
succeeded* — and `parse-where kind=relaxed` is refused, as Kusto refuses it
(SEM0477).

**`kind=simple` reaches the end of the input; `kind=regex` does not.** The
pattern is anchored at end-of-text in `simple` and `relaxed`, which is only
visible when it ends with a *literal* rather than a column:

```
'aXcYc'        parse s with "a" v "c"    ->  'XcY'   a lazy capture, forced long
'abcbd'        parse s with "a" v "b"    ->  ''      no match at all
'aXc' + \n     parse s with "a" v "c"    ->  ''      the newline is not consumed
```

The anchor is RE2's bare `$`, which means end of *text* — Python's and .NET's
would match before that final newline and answer `'X'`. `kind=regex` has no
anchor, and the same three inputs give `'XcY'`, `'bc'` and `'X'`.

**A capture is lazy, except before a `*` — and in `kind=regex` it is greedy.**
`"a" v "c"` over `aXcYc` gives `X` in simple mode, the first `c` and not the
last, and `*` skips non-greedily too. The exception in simple mode is the one
position with nothing to stop the capture: a column immediately followed by a
`*`. There a **typed** column matches its *type's shape* (`\s*[-+]?\d+` for an
integer, and so on), which is why Kusto allows `*` after a typed column and
refuses it after a **string** one (SEM0476) — we refuse it too. A *trailing*
`*` is a no-op and makes the shape optional; a `*` in the middle does not.

**`kind=regex` is a different capture policy, not a flag on the same one.**
Literals become the user's regex, string columns are greedy, and typed columns
are shaped *everywhere* rather than only before a `*` — `"n=" n: long` over
`n=27x` is 27 in regex mode and null in simple. The shapes themselves differ
too: `bool` is `true`/`false` only here (`12` is null, where simple mode reads
it as true) and `real` takes no leading dot (`.5` is null, where simple mode
reads 0.5). `flags=i`, `s`, `m` and `U` become one inline `(?…)` prefix, which
is how Kusto composes them as well — it reports a bad flag as `(?I)`. `U` has
to be global: it inverts the `*` skips along with everything else.

Two things a user's regex fragment cannot be passed through as written.
`regexp_extract` maps its name list onto groups **by position**, so any
capturing group in a fragment would shift every column after it; each one is
rewritten to `(?:…)` by `translate.regexfrag`. And an **unbalanced**
parenthesis is a literal to Kusto (`parse kind=regex s with ")" v` over `a)b`
is `b`) where RE2 rejects the whole pattern, so it is escaped. Lookaround and
backreferences are refused: RE2 has neither, and Kusto's own analysis refuses
lookaround too (SEM0476).

**The conversions are KQL's, not DuckDB's.** Three places where `TRY_CAST` is
too generous, each one a silent wrong answer if taken:

| text | Kusto | `TRY_CAST` |
|---|---|---|
| `'1.5'` as `long` | null | **2** — it rounds |
| `'yes'` as `bool` | null | **true** — DuckDB accepts yes/no/t/f |
| `'02/17/2016 08:40:01'` as `datetime` | the datetime | **null** — the cast has no MM/DD/YYYY |

So an integer column tests the text is integer-shaped first, a bool accepts
only `true`/`false` or a whole number, and a datetime reuses `todatetime`'s
format list.

`parse` knows its argument is *text*; `tolong`/`tobool` must also serve a
**numeric** one, where the answer genuinely differs — `tolong(1.5)` is 2 while
`tolong('1.5')` is null, and `tobool(1.5)` is true while `tobool('1.5')` is
null. That looked like it had to wait for column types, and for `tobool` it did
not: the answer is not needed until *execution*, and R20's run-time `typeof`
dispatch supplies it there. `tobool` now shares `parse`'s exact text rule and
is measured clean. **`tolong`/`toint` are the same fix and are not yet done**:
`tolong('1.5')` still answers 1 here and null on a cluster.

`todynamic` has a recorded residue of its own, found by the `parse` sweep and
not yet fixed: KQL wraps text that is not JSON as a dynamic **string**, so
`todynamic('abc')` is `'abc'` on a cluster and null here, and a `: dynamic`
column in `parse` inherits that. `totimespan` had the mirror-image bug and *is*
fixed — DuckDB's `INTERVAL` cast silently ignores trailing text, so
`totimespan('00:01:00 junk')` answered one minute where a cluster answers null,
and a shape test now guards it.

**Not implemented:** `: decimal` (DuckDB renders the scale — `1.000000000`, not
`1`), `flags=x` (RE2 has no ignore-pattern-whitespace mode), a
`datetime`/`timespan` column before a `*` in simple mode (no measured shape), a
temporal column at the **end** of a `kind=regex` pattern or under `flags=U`
(see below), and `parse-kv`. Each refuses rather than guessing. See
[`parse-proposal.md`](parse-proposal.md).

**The temporal shapes have an edge.** `datetime` and `timespan` are the only
declared types whose `kind=regex` capture is not a plain regex on the
emulator's side. Two positions give it away, and both are refused rather than
approximated:

* at the very end of the pattern, a character in the **data** after the value
  changes the answer — `'v=2020/01/01T00:00:00'` is null and
  `'v=2020/01/01T00:00:00X'` is the datetime, which no shape can do;
* under `flags=U`, where every other shape simply inverts, a temporal capture
  stops answering at all.

The refusal is **positional**, so one case slips past it: a fragment that can
itself match the empty string — `"|"`, `"(x)?"` — leaves a temporal column
effectively at the end of the pattern without being last in the source. There a
cluster answers null and we answer a value; a fragment that must consume a
character agrees. That is the only place a disagreement runs in this direction.

Everywhere else the shapes hold across the differential sweep, and the residue
runs the other way: this translator answers null where a cluster answers a
value — a blank row rather than a wrong one. Two such are known and recorded
rather than fixed: a trailing `datetime` column in `simple`/`relaxed` over text
with trailing junk (Kusto parses a prefix; we take the whole capture and fail),
and `: dynamic`, which is the `todynamic` gap noted above rather than a `parse`
one.

### R20 — A value's **string form** is .NET's, and `tostring` is total
*Trap: `tests/test_tostring.py`*

R17 is about one type reaching a string context. This is the rule underneath
it: every type has a KQL string form, and three of them are not SQL's.

| expression | Kusto | `CAST(… AS VARCHAR)` |
|---|---|---|
| `tostring(true)` | `True` | **`true`** |
| `tostring(datetime(2020-01-02 03:04:05.6))` | `2020-01-02T03:04:05.6000000Z` | **`2020-01-02 03:04:05.6`** |
| `tostring(dynamic('x'))` | `x` | **`"x"`** (R17) |
| `tostring(int(null))` | `''`, `strlen` 0, `isnull` **false** | **null** |

The last row is the one that is easy to miss. `tostring` is **total**: it
returns the empty string for a null of *every* type — bool, int, long, real,
datetime, timespan, guid, dynamic all measured — which is why `strcat` needs no
null handling and why `strcat_delim('-', 'a', int(null), 'b')` is `a--b` and
not `a-b`. The hash family inherits it, and adds a rule of its own: Kusto
hashes the empty string to the **empty string**, not to `d41d8cd9…`.

**The dispatch is DuckDB's run-time `typeof`, not static inspection of the
IR.** This is the load-bearing part, and it was learned the hard way. A static
predicate can only answer for expressions whose type the IR carries, and the
commonest operand — a bare `ColumnRef` — carries none. So a static version is
not merely incomplete at the edges: it is wrong for `tostring(bool_column)` and
`tostring(datetime_column)`, which is most real usage. Every branch of the
`CASE` must also *bind* for every operand type, because DuckDB binds all of
them: hence the CAST inside the `strftime`, and a bool branch that compares the
VARCHAR form rather than testing the operand as a condition.

**Where it applies:** `tostring` and everything that renders through it —
`strcat`, `strcat_delim`, `hash_md5`/`hash_sha1`/`hash_sha256`, `reverse`, and
R17's string-only function list. A wrong string form is not cosmetic there: the
hash functions digest it, so the query returns a plausible digest that no
cluster would ever produce.

**Residue.** Sub-microsecond input truncates rather than rounds — KQL prints
100ns ticks and DuckDB stores microseconds, so the seventh digit is always `0`.
And the two static shortcuts in `render_kql_tostring` (a statically known
`dynamic` or `datetime` skips the dispatch) are a *size* optimisation only:
`datetime('…')` renders as a multi-line `try_strptime` list, and the dispatch
would substitute it five times into one expression.

### R21 — An assignment sees the operator's **input** columns, and nothing else
*Trap: `tests/test_clause_scope.py`*

A clause's assignments are evaluated against what flows *into* the operator,
not left-to-right against each other. So a name the same clause binds is not in
scope for it, and Kusto answers SEM0100 — *"Failed to resolve scalar expression
named 'a'"*:

```
datatable(x:long)[10] | extend a = x + 1, b = a + 1        refused
datatable(x:long)[10] | extend a = x + 1 | extend b = a + 1   12
```

The trap is that **the two halves of this rule pull opposite ways**, and SQL
gets one of them right by accident. DuckDB has a lateral-column-alias fallback
that fires only when no upstream column of that name exists — so the first line
above quietly answered 12, while the shape that looks identical:

```
datatable(x:long)[10] | extend x = x + 1, b = x + 1    ->   x=11, b=11
```

is *correct*, and correct precisely because `b` reads the **pre-extend** `x`.
Anyone reading the first case as "assignments should be sequential" would fix
it by making them sequential and silently break the second, which is the
commonest shape in real queries: transforming a column under its own name.

So the check is `introduced − input`, not `introduced`. Only an explicit
`name = expr` introduces a binding; an unnamed entry copies a column through
(`project ['my col']`) or takes R12's auto-generated name, and neither can
shadow anything.

Applies to `project`, `extend`, `distinct`, and `summarize` — whose keys and
aggregates are **one** scope, measured: `summarize s = sum(a) by a = x` is
refused even though `a` is introduced after the reference in the query text.
The rule is therefore order-insensitive.

Needs the input column list, so it does not fire without a schema — the same
limit R7's collision check has.

---

## 5. Tabular operator conventions

| KQL | DuckDB rendering |
|---|---|
| `where P` | `SELECT * FROM prev WHERE <P>` |
| `project a, b=expr` | `SELECT "a", <expr> AS "b" FROM prev` — sets column order |
| `project-away c` | expand to the explicit remaining column list (never `SELECT *`) |
| `project-keep` / `project-reorder` | explicit column list |
| `project-rename new=old` | `SELECT "old" AS "new", …` preserving position |
| `extend c=expr` | `SELECT prev.*, <expr> AS "c"` — **but** if `c` already exists it **replaces** it, so emit the explicit column list |
| `take n` / `limit n` | `LIMIT n` (R10) |
| `top n by X [asc\|desc]` | `ORDER BY <X> <dir> LIMIT n` (R6 default, R10 ties) |
| `sort by X [asc\|desc]` | `ORDER BY <X> <dir>` — always explicit (R6) |
| `distinct a, b` | `SELECT DISTINCT "a","b"` |
| `count` | `SELECT count(*) AS "Count"` |
| `summarize …  by …` | `GROUP BY` (R12 naming, R4 null handling) |
| `join` | R5 — kind-dependent |
| `union [kind=]` | `UNION ALL BY NAME` with an explicit output column list (R15) |
| `mv-expand [kind=] [x =] c [to typeof(T)][, …] [limit N]` | One `UNNEST` per target, written into an explicit column list — see R18 |
| `parse [kind=] E with <pattern>` / `parse-where` | One `regexp_extract` with a named group per column — see R19 |
| `datatable(...)` / `print` / `range` | `VALUES` / `SELECT` / `range()` — self-contained, ideal for corpus tests |

**`union` column unification:** KQL unions produce the **superset** of columns,
filling missing ones with null — unlike SQL `UNION ALL`, which requires matching
arity *and* pairs columns positionally. DuckDB's `UNION ALL BY NAME` does both
halves of that, so branches are emitted as-is and the final projection names the
unified column set in KQL's order. `kind=inner` restricts to common columns.
`union` is **not** deduplicating → `UNION ALL`. Full rules in R15.

## 6. Scalar function registry

Function mappings live in a **reviewable data file** (TSV/YAML), not in code — the
`LIFETIMES.tsv` analogue. It generates both the translator's dispatch and the
coverage matrix.

Columns: `kql_name`, `arity`, `kind` (`native` | `template` | `udf` |
`unsupported`), `duckdb`, `rules` (applicable R-IDs), `wave`, `source_url`,
`test_ids`, `notes`.

```tsv
kql_name  arity  kind      duckdb                                    rules    wave  test_ids
strlen    1      native    length({0})                               R11      1     case-strlen-01
toupper   1      native    upper({0})                                         1     case-toupper-01
toint     1      template  TRY_CAST({0} AS INTEGER)                  R1       1     trap-r1-conversions
ago       1      template  (now() - {0})                             R8       1     trap-r8-datetime
iff       3      template  CASE WHEN {0} THEN {1} ELSE {2} END                1     case-iff-01
contains  2      template  ({0} ILIKE '%' || escape({1}) || '%')      R3       1     trap-r3-has-vs-contains
```

Rules: one row per KQL function; `unsupported` rows are legitimate and drive
`KqlUnsupportedError`; every row cites its doc URL and at least one test.

## 7. UDF policy

A Python UDF is permitted **only** when no correct native or template form exists.
Order of preference: **native → template → UDF → `unsupported`**.

- Register on the connection via `con.create_function(...)`, namespaced `kql_*`,
  **idempotently** (track per connection; never double-register).
- The translator records which UDFs a query needs; only those are registered — a
  query needing none touches none.
- Prefer **Arrow/vectorized** signatures; per-row Python is a last resort.
- Every UDF's docstring must state: why no SQL form exists, its performance
  characteristics, and its R-rule references.
- Candidates: term-boundary matching for `has` (if regex proves insufficient),
  some `parse`/regex extraction, IPv4/CIDR operations, exotic `dynamic`
  manipulation.

## 8. Errors

| Error | Raised when |
|---|---|
| `KqlSyntaxError` | parse failure — include source span |
| `KqlUnsupportedError` | recognized construct we don't translate — include name + span |
| `KqlSchemaError` | unknown table/column, or an identifier collision (R7) |

Never emit SQL that "probably works." Refusing is always better than a silent
wrong answer (principle 5).

## 9. Open items

One item is genuinely open. The rest are kept, struck through and dated, because
a settled question that vanishes gets re-opened by the next reader — and §10
sends every contributor through §4 before they map anything.

- Whether the emitter builds SQL strings directly or via `sqlglot`
  (`implementation-options.md` Option 2) — deferred; keep the emitter behind a
  narrow interface either way.
- ~~Null-ordering defaults for `sort` (R6).~~ **Settled 2026-08-05:** KQL treats
  null as the **smallest** value — `sort by x asc` returns null first, `desc`
  returns it last. The emitter had this inverted while its comments asserted the
  opposite as fact. Pinned by `tests/test_column_order_and_null_sort.py`.
- ~~`decimal` precision/scale policy (§2).~~ **Settled:** `DECIMAL(38,9)`
  (`translate/__init__.py`, `TYPE_MAP`). The scale is *why* `todecimal` and
  `parse … : decimal` are refused rather than mapped — DuckDB renders the scale,
  so `1` comes back as `1.000000000`. §2's table says so now instead of pointing
  back here.
- ~~Exact `percentile` algorithm + tolerance (R11).~~ **Settled:**
  `quantile_disc`, because KQL uses nearest-rank rather than linear
  interpolation. Measured per state on the fixture: `disc` matched all 52 groups
  exactly, `cont` was off by up to 39% — a gap the 5% approximate-function
  tolerance would have hidden on smaller inputs (`functions.py`, `percentile`).
- ~~`has` tokenization: regex boundaries vs UDF.~~ **Settled:** regex boundaries,
  in one place — `term_match_sql` (`functions.py`), shared by the binary and list
  forms. No UDF exists, and R3 records the boundary rule that a bare `\b` gets
  wrong (`_` is a term separator in Kusto and a word character in regex).
- ~~`mv-expand` null/empty-array row preservation (R9).~~ **Settled:** measured
  in full and written up as **R18**, including the two edges that do not follow
  from each other — a null is *one* element rather than zero, and an empty array
  contributes no row alone but a null row when zipped against a non-empty one.

## 10. Adding a mapping — checklist

1. Read the **Microsoft doc page** for the construct; note the URL.
2. Check it against §4 — does any R-rule apply? If it's a new divergence, **add a
   rule and a trap test** before implementing.
3. Add the registry row (§6) at the lowest sufficient `kind`.
4. Add corpus cases; generate expectations from the **emulator**, not by hand.
5. **Adversarial pass:** in a fresh context with only the diff, try to break it —
   case sensitivity? nulls? empty input? type coercion? ties? overflow?
6. Implement the whole **family** at once (§5 of the lessons doc), not one
   function at a time.
7. Flip the corpus cases from `xfail` and confirm the coverage matrix moved.
