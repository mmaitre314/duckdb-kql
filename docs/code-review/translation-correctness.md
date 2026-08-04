# Area: Translation correctness

**Scope:** `src/duckdb_kql/parser.py`, `lower.py`, `ir.py`, `translate/`
(`__init__.py`, `functions.py`), `schema.py`, `engine.py`.
**Out of scope:** the generated parser (`_antlr/`), query-parameter *safety*
(that's the security area), the Kusto client.

**Read first:** [`../TRANSLATION.md`](../TRANSLATION.md) in full, especially §4
(R1–R12). Most bugs in this area are a missed R-rule. Then the
[charter](README.md).

This is the area where the charter's whole reason for existing bites: the
translator's job is to be *semantically* identical to Kusto, and the failure
mode is code that is syntactically plausible and semantically wrong. Assume
every mapping is wrong until you've found the trap it must survive.

## The R-rule sweep — do this for every construct the diff touches

For each operator or function in the diff, walk R1–R12 and ask "does this one
apply, and does the emission honour it?" The high-frequency killers:

- [ ] **R1 — `TRY_CAST`, never `CAST`.** Every KQL conversion (`toint`,
  `todatetime`, `toguid`, …) returns *null* on bad input; DuckDB's `CAST`
  *throws*. A bare `CAST` anywhere the input isn't translator-constructed is an
  **S1**. Confirm bad input yields null, not an error.
- [ ] **R2 — string comparison case.** `==` is case-**sensitive** (`=`), `=~` is
  case-**insensitive** (`lower()=lower()`). Getting these backwards is silent.
- [ ] **R3 — `has` vs `contains`.** The single highest-risk family. `has` is
  **whole-term**, not substring: `Text has "err"` must be **false** for
  `"error"`. `has` rendered as `LIKE '%err%'` is a textbook **S1**. `contains`
  is substring + case-insensitive → `ILIKE` with `%`/`_` escaped. Check the
  whole matrix (`_cs` variants, `startswith`/`hasprefix`, and every negated
  form) — the trap is family-wide.
- [ ] **R4 — nulls.** `count(expr)` counts non-null → `count(expr)`; bare
  `count()` counts rows → `count(*)`. Negated string operators (`!has`, `!=`)
  do **not** always match a naive `NOT(...)` on null — must be pinned to the
  oracle. `isempty` ≠ `isnull`.
- [ ] **R5 — `join` defaults to `innerunique`.** A bare `| join` de-duplicates
  the *left* key set before an inner join. Emitting `INNER JOIN` silently
  returns extra rows — **S1**. Every `kind=` must map exactly (see the table).
- [ ] **R6 — `sort`/`order`/`top` default to `desc`.** Opposite of SQL. Every
  ordering must emit an explicit `ASC`/`DESC`, and null ordering must be
  explicit, not DuckDB's default.
- [ ] **R7 — identifiers are case-sensitive, always double-quoted.** `Foo` and
  `foo` are distinct KQL columns; DuckDB folds them. A bare identifier is
  wrong even when it looks safe. A collision that survives folding must raise
  `KqlSchemaError`, not be resolved arbitrarily.
- [ ] **R8 — datetime is UTC; `now()`/`ago()` evaluate once per query.** Never
  `TIMESTAMPTZ`, never a local zone. Repeated `now()` references must agree
  (single evaluation). `bin()` is a floor-from-epoch and applies to numbers too
  — the origin and week/month cases differ from `date_trunc`/`time_bucket`.
- [ ] **R9 — `dynamic` missing property is null, never an error.** Out-of-range
  index too. `json_extract` (JSON value) vs `json_extract_string` (text) —
  wrong one gives spuriously quoted strings. `parse_json` on garbage → null.
- [ ] **R10 — nondeterministic operators asserted as sets.** `take`/`sample`/
  `top` ties are not ordered; a test asserting a specific row order without a
  terminal `sort` is itself the bug.
- [ ] **R11 — characters not bytes; approximations are approximate.** `strlen`
  → `length` not `octet_length`; `substring` 0-based and clamps out-of-range.
  `dcount`/`percentile` are approximate — never asserted exact.
- [ ] **R12 — `summarize` output names.** `count()`→`count_`, `avg(X)`→`avg_X`;
  group keys first in source order. Names are user-visible; a wrong one is a
  contract break.

## Architecture-level checks (the pipeline itself)

- [ ] **One operator → exactly one CTE**, named `_s0`, `_s1`, … in source
  order, never reused or reordered (TRANSLATION.md §1). Fusing operators for
  cosmetic brevity is a defect — DuckDB collapses them anyway and 1:1
  traceability is the point.
- [ ] **Output column list is tracked and order-preserving.** `project`,
  `project-away`, `extend`, `summarize` reshape it; column *order* is
  user-visible. `project-away`/`extend`-replaces must expand to an explicit
  column list, never `SELECT *`.
- [ ] **`union` unifies to the column superset**, null-filling missing columns
  per branch — not SQL's arity-matched `UNION ALL`. `kind=inner` restricts to
  common columns. `union` is non-deduplicating → `UNION ALL`.
- [ ] **`let` bindings** become CTEs (tabular) or inlined scalars, emitted
  before `_s0`.
- [ ] **Literals** follow §3 exactly: bare integer is `long`→`BIGINT` (don't let
  DuckDB infer `INTEGER` and overflow at 2³¹); typed nulls are
  `CAST(NULL AS <type>)`, never bare `NULL` (loses type in a `UNION`).

## IR and lowering (`ir.py`, `lower.py`)

- [ ] Is the IR node total over what the grammar accepts, or can a valid parse
  reach a node the lowerer doesn't handle and produce `None`/garbage instead of
  a clean `KqlUnsupportedError` with a span?
- [ ] Does lowering preserve **source spans** so errors can point at the
  offending text? A refusal without a span is a worse refusal.
- [ ] Is anything inferred from the *shape* of the tree that should come from
  the schema (column existence, join keys)? Unknown table/column → `KqlSchemaError`.

## The registry (`translate/functions.py`)

- [ ] Every row at the **lowest sufficient `kind`**: `native` → `template` →
  `udf` → `unsupported`. A `template` where `native` is correct is needless; a
  `udf` where a template works violates §7.
- [ ] Every row **cites its R-rules and at least one test**. A mapping touching
  a §4 rule with no trap test is unverified — treat as S3 minimum.
- [ ] `{0}`-style templates: are operands that could be complex expressions
  parenthesized so precedence can't bite? Is a pattern argument escaped?
- [ ] An `unsupported` row is a *legitimate, correct* outcome — don't push back
  on a refusal that has a reason. Pushing an author to map something
  unverifiable is pushing them toward an S1.

## engine.py (the Layer-1 seam)

- [ ] Does the session set `TimeZone='UTC'`? An offset-less datetime string is
  cast against the *session* zone — a missing `SET TimeZone='UTC'` silently
  shifts every datetime (R8). This is easy to lose in a refactor.
- [ ] Are parameters passed to DuckDB as **bindings**, never formatted into SQL
  (the security area owns the depth here, but a translation-side regression that
  starts interpolating is an S1/S2 seam).

## What to enumerate as "checked clean"

The R-rules that *didn't* apply to this diff and why; the edge inputs you
traced (null, empty, single row, out-of-range, overflow, non-ASCII); and any
construct you confirmed correctly refuses rather than guesses.
