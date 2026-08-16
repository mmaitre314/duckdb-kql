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
| `decimal` | `DECIMAL(38,·)` | precision policy TBD (§9) |
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

- `contains` → `ILIKE '%' || … || '%'` (escape `%`/`_` in the pattern).
- `has` → **not** a substring match. `Text has "err"` is **false** for
  `"error"`, because `has` matches whole terms delimited by non-alphanumeric
  characters. Emit a tokenization-aware form (regex on term boundaries), never a
  bare `LIKE '%x%'`.
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
*Trap: `trap-r7-identifiers`*

KQL identifiers are case-sensitive; DuckDB's are case-insensitive by default.
`Foo` and `foo` are distinct KQL columns.

> **Always emit double-quoted identifiers.** Never emit a bare identifier, even
> when it looks safe. Detect collisions that survive DuckDB's folding and raise
> `KqlSchemaError` rather than resolving them arbitrarily.

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
  input without erroring (KQL clamps; SQL's `substring` is 1-based).
- `dcount`/`dcountif` are **approximate** (HLL) → `approx_count_distinct`; never
  assert exact equality in tests.
- `percentile`/`percentiles` use a specific estimation algorithm — pin the
  algorithm choice (`quantile_cont` vs `quantile_disc` vs `approx_quantile`)
  against the oracle and record the tolerance.

---

### R12 — `summarize` output column naming
*Trap: `trap-r12-summarize-naming`*

Auto-generated names are user-visible and must match exactly: `count()` →
`count_`, `avg(X)` → `avg_X`, etc. Group-by keys come first, in source order, then
aggregates. An explicit `Name =` overrides. Null group keys form their own group.

An aggregate may be wrapped in a scalar expression — `round(sum(y), 2)`,
`sum(x) / count()` — and then the name comes from the **aggregate**, not the
wrapper: `round(sum(y), 2)` is `sum_y`. The rule is positional, following first
arguments only, so `strcat('n=', tostring(count()))` is `Column1`. A column
outside an aggregate is refused, as Kusto refuses it — *including* a `by` key,
which plain SQL would accept.

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
| `union [kind=]` | column-unifying `UNION ALL` (see below) |
| `datatable(...)` / `print` / `range` | `VALUES` / `SELECT` / `range()` — self-contained, ideal for corpus tests |

**`union` column unification:** KQL unions produce the **superset** of columns,
filling missing ones with null — unlike SQL `UNION ALL`, which requires matching
arity. Compute the unified column set, then emit explicit
`SELECT col1, col2, NULL AS col3 …` per branch. `kind=inner` restricts to common
columns. `union` is **not** deduplicating → `UNION ALL`.

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

- `decimal` precision/scale policy (§2).
- Exact `percentile` algorithm + tolerance (R11).
- `has` tokenization: regex boundaries vs UDF — decide after the R3 trap tests
  run against the emulator.
- `mv-expand` null/empty-array row preservation (R9).
- ~~Null-ordering defaults for `sort` (R6).~~ **Settled 2026-08-05:** KQL treats
  null as the **smallest** value — `sort by x asc` returns null first, `desc`
  returns it last. The emitter had this inverted while its comments asserted the
  opposite as fact. Pinned by `tests/test_column_order_and_null_sort.py`.
- Whether the emitter builds SQL strings directly or via `sqlglot`
  (`implementation-options.md` Option 2) — deferred; keep the emitter behind a
  narrow interface either way.

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
