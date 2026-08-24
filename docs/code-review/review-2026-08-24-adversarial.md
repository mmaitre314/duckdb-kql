# duckdb-kql — adversarial review, whole repo

**Date:** 2026-08-24 · **Reviewed at:** `d7be6ff` · **Method:** five
`adversarial-reviewer` agents, one per slice of the mapping surface, each
working `docs/TRANSLATION.md` §4 against its files and **verifying every claim
by executing generated SQL against DuckDB** — not by reading alone.

`main` and the review branch were the same commit, so this is a review of the
codebase as it stands, not of a diff. The suite is green; everything below is
what a green suite does not catch.

> **This file is a worklist, not a snapshot.** It exists for a later agent to
> pick items off. Each finding carries a reproduction, a root cause, the file to
> change, and a fix direction. Items are **not** struck through as they land —
> check the code before trusting any entry, and delete an entry once its fix
> ships with a trap test.

## Slices reviewed

| Pass | Files | Rules |
|---|---|---|
| 1 | `comparison.py`, `translate/regexfrag.py`, `has`/`in`/term operators | R2, R3, R4, R17 |
| 2 | `translate/functions.py`, `_SPECIAL_FORMS` + special-form renderers | R1, R9, R11, R13, R17 |
| 3 | `join` / `lookup` / `union` / `mv-expand` / `macro-expand` | R5, R14, R15, R16, R18 |
| 4 | `summarize` / `sort` / `take`+`top` / `distinct` / `project` / `parse` | R4, R6, R10, R12, R19 |
| 5 | `quote_ident`, `lower.py`, `ir.py`, `params.py`, `tests/` integrity | R7, R10, R11, §0 |

## Verdict

**5 S1, 5 S2, 5 S3, 2 S4/Nit.** *(S1-3 fixed 2026-08-24 and its entry removed; S1-2 reclassified to S2-6 — see the note there.)* Unlike the 2026-08-04 pass, this one found a
**systemic weakness**, not a scatter of independent divergences: four of the
five S1s are the same underlying problem wearing different clothes (see
[The pattern](#the-pattern-that-matters-more-than-any-single-finding)). The
error taxonomy also leaks in five places — each individually minor, collectively
a pattern of "reaches DuckDB and lets the engine complain."

What held up held up well, and is listed at the end so nobody re-reviews it.

---

## S1 — wrong answer

### S1-1 · R7's collision detection was never built

`translate/__init__.py:113-120` (`quote_ident`), `schema.py:119-163`
(`_operator_columns`), `schema.py:323-329` (`disambiguate`).

R7 has **two** clauses. The first — always emit double-quoted identifiers — is
honoured everywhere. The second is not implemented anywhere in the repo:

> **Detect collisions that survive DuckDB's folding and raise `KqlSchemaError`
> rather than resolving them arbitrarily.**

The reviewer established the mechanism by testing DuckDB itself rather than
assuming its behaviour:

- a quoted `"FOO"` **silently resolves** to a `"foo"` column when no exact match
  exists — `SELECT "FOO" FROM (SELECT 1 AS "foo")` returns `1`, no error;
- a SELECT list producing two case-variant names **silently renames** the second
  (`"Foo"` → `"Foo_1"`).

Meanwhile duckdb-kql's own column bookkeeping compares names with exact-match
Python equality (`c not in cols`) — correct for KQL, where `Foo` and `foo` are
genuinely distinct, but not a faithful model of what DuckDB does with the SQL we
hand it. Where the two models disagree, data is silently wrong.

```python
duckdb_kql.to_sql("datatable(foo:long) [1,2,3] | extend Foo = foo + 100 | project foo, Foo")
# executes to [(1, 1), (2, 2), (3, 3)]   —  Foo should be 101, 102, 103
duckdb_kql.to_sql("datatable(Foo:long, foo:long) [1,2] | project Foo, foo")
# executes to [(1, 1)]                   —  should be [(1, 2)]
```

No exception, plausible-looking output, wrong numbers. Any log schema carrying
both `Type` and `type` is affected, as is any `extend`/`project` that happens to
produce a case-variant of an existing name.

**Fix the class:** wherever a stage's output column *list* is computed
(`_operator_columns`, and `ir.DataTable`'s column declarations in
`lower.py:492-504`), add a case-insensitive collision check that raises
`KqlSchemaError`. Case-sensitive Python bookkeeping cannot be made correct here
— the check has to exist.

**Test gap:** the only case-sensitivity test in the suite,
`test_join.py::test_a_table_name_is_matched_exactly_not_case_folded`, tests a
*table name* lookup against a caller-supplied Python dict. It never reaches
DuckDB, and no test anywhere exercises a *column*-level collision.

### S1-2 · Intra-clause references resolve to the stale column when the name shadows an input

`translate/__init__.py:509-514` (`Project`), `516-540` (`Extend`), `552-559`
(`Distinct`), `1095-1129` (`render_summarize`'s `by` keys).

Each of these renders its named expressions into **one flat SQL SELECT list**.
DuckDB's lateral-column-alias fallback only fires when no upstream column of
that name exists; when the computed name collides with a real input column, a
later reference in the *same clause* binds to the **upstream** column, not to
the alias just computed.

```python
# Sales(Total=100)
duckdb_kql.kql(con, "Sales | extend Total = Total * 2, Ratio = Total / 100")
# Total=200, Ratio=1      —  Ratio computed from the PRE-extend Total (100/100)
#                            Kusto evaluates left-to-right: expected Ratio=2
```

Emitted SQL makes the mechanism plain:

```sql
SELECT COLUMNS(x -> x NOT IN ('Total','Ratio')),
       ("Total" * 2) AS "Total", ("Total" / 100) AS "Ratio"
FROM _s0
```

Reproduced in all four operators, on both the `COLUMNS(...)` fallback and the
explicit-column-list path:

| Query | Got | Expected |
|---|---|---|
| `T5(a=100,x=10) \| project a = x + 1, b = a + 1` | `a=11, b=101` | `b=12` |
| `Dt(a=100,x=10) \| distinct a = x + 1, b = a + 1` | `(11, 101)` | `(11, 12)` |
| `K(k=100,x=10) \| summarize count() by k = x, m = k + 1` | `m=101` | `m=11` |

Control case pinning the mechanism — with no colliding name, DuckDB's lateral
alias activates and the answer is right: `T(x=10) | project a = x + 1, b = a + 1`
→ `a=11, b=12`. ✔

This is the most common real-world shape there is: transforming a column under
its own name.

> ### ⚠ Settled against the emulator 2026-08-24 — **this finding is inverted**
>
> The ruling this asked for was taken, and it reverses the entry. Kusto's rule
> is that an assignment sees **only the operator's input columns**, never a name
> assigned earlier in the same clause. So:
>
> | Query | Kusto | Ours | |
> |---|---|---|---|
> | `T(Total=100) \| extend Total = Total*2, Ratio = Total/100` | `(200, 1)` | `(200, 1)` | ✔ agree |
> | `T(a=100,x=10) \| project a = x+1, b = a+1` | `(11, 101)` | `(11, 101)` | ✔ agree |
> | `T(a=100,x=10) \| distinct a = x+1, b = a+1` | `(11, 101)` | `(11, 101)` | ✔ agree |
> | `T(k=100,x=10) \| summarize count() by k = x, m = k+1` | `(10, 101)` | `(10, 101)` | ✔ agree |
> | `T(x=10) \| extend a = x+1, b = a+1` | **SEM0100, refused** | `(11, 12)` | ✘ **diverges** |
> | `T(x=10) \| project a = x+1, b = a+1` | **SEM0100, refused** | `(11, 12)` | ✘ **diverges** |
>
> Every expected value in the table above — `Ratio=2`, `b=12`, `(11,12)`,
> `m=11` — is wrong; the entry's "control case pinning the mechanism" is in fact
> the **only** case that diverges, and it diverges the other way. Kusto refuses
> it (*"Failed to resolve scalar expression named 'a'"*) because `a` is not an
> input column; DuckDB's lateral alias answers it.
>
> **So there is no wrong answer here — this is not S1.** The real defect is
> **S2-class and inverted**: we *accept* a query a cluster refuses, the same
> class as `floor(7.9)`. Reclassified below.
>
> The mechanism the entry describes is real and correctly explained; only the
> ground truth it was measured against was assumed rather than taken.

**Reclassified: S2-6 — an intra-clause forward reference is accepted, and Kusto
refuses it.** `datatable(x:long)[10] | extend a = x + 1, b = a + 1` answers 12
here and is SEM0100 on a cluster. Not a wrong answer — a query that will not run
in production. Needs an R-rule (assignments see the operator's *input* columns
only) and a refusal when a name is referenced in the same clause that binds it.
The colliding case needs no change and must keep working; a fix that "repairs"
it by making assignments sequential would break agreement with Kusto.

---

## S2 — safety / contract

Five sites where a query that should raise the project's own error instead
reaches DuckDB and leaks a raw engine exception. None returns a wrong answer, so
none is S1 — but each violates §8's taxonomy and principle 5's "refusing is
always better", and each exposes generated SQL to the caller. Code that catches
`KqlUnsupportedError` will be surprised by all five.

### S2-1 · Generated `_sN` CTE names are not reserved against user `let` names

`translate/__init__.py:640-707` (`to_sql`), `:653` (`let_ctes`), `:668/705`
(`_s{i}` naming).

```
let _s0 = datatable(x:long) [1,2,3];
_s0 | where x > 1
```
emits `WITH "_s0" AS (...), _s0 AS (...), _s1 AS (...)` →
`duckdb.ParserException: Duplicate CTE name "_s0"`. Legal KQL; the error names
nothing the user could act on. **Fix:** check `let` names against `_s\d+` and
either raise `KqlSchemaError` or pick a non-colliding internal prefix.

### S2-2 · Tabular `let` redeclaration crashes instead of shadowing

`lower.py:1761-1811` (`_lower_lets`).

`scalars` is a `dict`, so scalar `let` shadowing works by overwrite. `tabulars`
is a plain `list`, so two same-named tabular `let`s both become CTEs:

```
let T = datatable(x:long)[1,2];
let T = datatable(x:long)[3,4];
T | count
```
→ `Parser Error: Duplicate CTE name "T"`.

> ### ⚠ Corrected against the emulator 2026-08-24 — **do not implement the fix as written**
>
> Kusto does **not** shadow. It refuses, both forms:
>
> ```
> let T = datatable(x:long)[1,2]; let T = datatable(x:long)[3,4]; T | count
>   -> SEM0079: Let with the same name was already used in current context: 'T'
> let v = 1; let v = 2; print x = v
>   -> SEM0079: ... 'v'
> ```
>
> So the scalar path is the one that is wrong: it silently shadows and answers
> `2` where a cluster refuses the query. "Implement shadowing the way the scalar
> path already does" would spread that defect to the tabular path rather than
> fix it — and the crash it replaced is at least loud.
>
> **Fix:** raise on *any* `let` redeclaration, scalar or tabular, naming SEM0079.
> Two divergences close at once: the tabular crash and the silent scalar shadow.

`tests/test_let.py` has no let-vs-let redeclaration case.

### S2-3 · An aggregate in a `summarize ... by` key reaches the binder

`translate/__init__.py:1104-1113`.

The aggregate *list* is guarded (`_lift_aggregates`/`_render_aggregate_call`,
`:963-1055`, raising cleanly for `sum(sum(x))`). The `by` keys go through a bare
`render_expr` with no check, so `summarize count() by bin(count(), 1)`
translates happily and dies at execution with
`Binder Error: GROUP BY clause cannot contain aggregates!`. Same for
`by count()`, `by min(C)`, `by sum(C)+1`. **Fix:** extend the existing aggregate
detection to the `by`-key side.

### S2-4 · `datetime_add("dayofyear", ...)` leaks a `CatalogException`

`translate/__init__.py:3082` (`_DATE_PARTS`), `:3090-3107` (`_date_part`).

One part-table is shared verbatim by `datetime_part` (extraction),
`datetime_add` and `datetime_diff` (arithmetic periods) — three functions with
different valid domains. `dayofyear` is extraction-only and has no
`to_dayofyears()` in DuckDB:

```
print x = datetime_add("dayofyear", 1, datetime(2024-01-01))
-> duckdb.CatalogException: Scalar Function with name to_dayofyears does not exist!
```

`week_of_year`/`quarter` also "succeed" for `datetime_add`/`datetime_diff` with
periods Microsoft's documented list does not include — the quieter half of the
same root cause. **Fix:** split the table per function domain.

### S2-5 · `case`/`iff`/`coalesce` with mismatched branch types leak `ConversionException`

`_render_case` (`:2923`), the `iff`/`iif` and `coalesce` registry rows.

```
print x = case(1>0, "positive", 5)  -> duckdb.ConversionException: Could not convert string 'positive' to INT64
print x = iff(1>0, "a", 5)          -> same
print x = coalesce("a", 5)          -> same
```

Systemic rather than a slip — the translator has no column types anywhere, as
R14/R17's residue sections acknowledge. Recorded so it is a known, ranked gap
rather than a surprise.

---

## S3 — correctness-adjacent

### S3-1 · `countof` counts non-overlapping; the generated docs assert overlapping

`translate/__init__.py:3006` (`_render_countof`), vs
`tools/gen_support_matrix.py:325` → `docs/kql-support.md:321`.

The substring mode computes
`(length(s) - length(replace(s, needle, ''))) / length(needle)`, and `replace()`
is non-overlapping left-to-right, so `countof("aaaa", "aa")` → `2`. The
committed doc says, in as many words, *"The substring kind counts **overlapping**
occurrences, which `regexp_count` does not."* The function's own docstring says
the opposite of the doc. Three-way contradiction between code, comment and
published doc. Kusto's canonical `countof` example is overlap-counting, so the
code is the likely-wrong party — **settle against the emulator**, then fix
whichever two of the three are wrong.

### S3-2 · `tobool("yes")` returns `true`; Kusto returns null *(unverified)*

`translate/functions.py:208-209` — the row is `TRY_CAST({0} AS BOOLEAN)`.
DuckDB's boolean cast accepts `yes/no/y/n/t/f` on top of `true/false/1/0`
(verified: `TRY_CAST('yes' AS BOOLEAN)` → `TRUE`). Kusto's `tobool()` recognises
only `"true"`/`"false"` (case-insensitive) and numerics, so this should be null.
A silently *wrong value*, which is the failure R1 exists to prevent — but the
Kusto side is unverified here and no trap or `source_url` covers this row's
string vocabulary. **Escalates to S1 if the oracle confirms.**

### S3-3 · `substring`'s null `length` silently becomes `''`

`translate/__init__.py:2968` (`_render_substring`). The negative-length clamp is
`greatest(<length>, 0)`, and DuckDB's `greatest(NULL, 0)` is `0`, not NULL
(verified). So the same function treats its two null arguments differently:

```
print x = substring("abcdefg", 1, int(null))  -> ''      (length silently -> 0)
print x = substring("abcdefg", long(null))    -> NULL    (start propagates)
```

> ### ⚠ Settled against the emulator 2026-08-24 — **the halves are the other way round**
>
> R4's null-propagation default made `''` look like the suspicious one. It is
> the correct one:
>
> | Call | Kusto | Ours | |
> |---|---|---|---|
> | `substring("abcdefg", 1, int(null))` | `''` | `''` | ✔ agree |
> | `substring("abcdefg", long(null))` | `'abcdefg'` | `NULL` | ✘ **diverges** |
> | `substring("abcdefg", int(null), 3)` | `'abc'` | `NULL` | ✘ **diverges** |
>
> A null **length** clamps to 0 and yields `''`; a null **start** is treated as
> 0 and the substring runs from the beginning. Neither propagates. The entry
> marked the diverging half ✔ and the agreeing half suspicious.
>
> **Fix:** `coalesce(<start>, 0)`, same shape as the existing length clamp. The
> internal inconsistency the entry spotted is real — it is just that both
> arguments should stop propagating, not that both should.

### S3-4 · `hasprefix`/`hassuffix` are documented and in the corpus, but unmapped

R3 documents them; the lexer supports all four spellings plus negations; the
corpus (`tests/cases/docs/docs-corpus.json`) contains cases. But
`BINARY_OPERATORS` has **no rows** for any of them, and `lower.py:43`'s
`_BINARY_TEXT_OPS` omits the `_cs` variants entirely.
`Logs | where Text hasprefix "er"` → `KqlUnsupportedError`. Fails loudly, so not
dangerous — but it is a live gap between what §4 documents as behaviour and what
the translator accepts.

### S3-5 · The trap-test IDs the spec relies on do not exist

`docs/TRANSLATION.md` §4 cites specific IDs (`trap-r7-identifiers`,
`trap-r2-case-sensitivity`, `trap-r5-join-kinds`, …) as the artifact that "must
pin" each rule, and its own preamble says **"a rule is not 'known' until its
test exists and passes."** None of those literal ID strings appears anywhere in
the test tree. Coverage mostly exists under conventional filenames
(`test_join.py`, `test_null_semantics.py`, …) with no traceable link back to the
promised ID.

This is not bookkeeping pedantry: **it is how S1-1 stayed invisible.** There is
no artifact anyone can point at and say "this is `trap-r7-identifiers`, and it
covers only table-name lookup, not column collision." Either introduce the IDs
as test markers/ids, or amend §4 to cite the real test names.

---

## S4 / Nit

- **S4 — dead code.** `translate/__init__.py:3354-3377` (`_render_extract_all`):
  `names = ", ".join(f"'g{i}'" ...)` is computed and used only in
  `.replace(f"[{names}]", "")`, but that literal substring never appears in the
  string being built. Permanent no-op; output is correct regardless.
- **Nit — undisclosed precision loss.** `datetime` values lose the 7th
  (100 ns tick) digit: `tostring(datetime(2024-01-01T12:34:56.1234567Z))` →
  `'...1234560Z'`. A consequence of the `datetime → TIMESTAMP` mapping (§2), not
  a registry bug, but §2/R8 do not disclose it.

---

## The pattern that matters more than any single finding

**S1-1, S1-2, S1-4 and both `let` crashes (S2-1, S2-2) are one problem.**

The translator models column and relation names with **exact-match Python string
comparison**, and that is not a faithful model of what DuckDB does with the SQL
we emit. DuckDB case-folds quoted references when no exact match exists,
silently renames duplicate output names to `_1`, binds a SELECT-list reference
to an upstream column in preference to a same-clause alias, and rejects
duplicate CTE names outright. Every one of those divergences is currently
handled — or not handled — at an individual call site. None is defended
centrally.

Recommended shape of the fix: **one name-resolution/collision layer** that every
stage's output-column computation passes through, which knows DuckDB's folding
and shadowing rules and raises `KqlSchemaError` when a KQL-legal name set cannot
be represented faithfully. Fix the cluster as one piece of work; four separate
patches will leave the fifth site to be discovered later.

**Secondary pattern — incomplete allow-lists where the right pattern already
exists in-tree.** `_escape_like` (S1-3) omitted the escape character while the
`has` family escapes correctly via `regexp_escape()`; `_is_bool_expr` (S1-5)
omitted the whole `has`/`contains` family while `==` was handled. Both fixed.
Worth recording how, because the review's own prescription — "derive it from
the registry" — was only half right. It fits S1-3, where the question is about
a *literal* the emitter can see. It does not fit S1-5: the operand is often a
bare column, and no registry can type one. There the allow-list had to be
replaced by a run-time `typeof` guard, not completed. The general lesson is
narrower than "derive from the registry": **a static predicate over the IR is
only sound where the IR carries the answer**, and a `ColumnRef` never does.

**Third pattern — tests that cannot reach the branch they claim to cover.**
Twice, the *only* test of a rule was structurally blind to the bug: every
`mv-expand` collision test uses a `datatable` source, so `cols` is never `None`
(S1-4); R7's only test never reaches DuckDB (S1-1). When writing the trap tests
for these fixes, assert the *source type* matters — cover the bare-table,
no-schema path explicitly.

This one bit the fixer as well as the author: the first attempt to reproduce
S1-4 used `duckdb_kql.kql(con, ...)`, which derives a schema from the
connection, and concluded the finding did not reproduce. It reproduces exactly
as written — through `to_sql()` with no schema. **The path a repro takes is
part of the repro.**

## Suggested order of work

1. **Settle the two open semantics questions against the emulator** — S1-2's
   intra-clause evaluation order, S3-1's `countof` overlap, S3-2's `tobool`
   vocabulary. Cheap, and two of them gate fixes below.
2. **The name-collision cluster as one change** — S1-1, S1-2, S1-4, S2-1, S2-2.
   Largest blast radius; do it while the analysis above is fresh.
   **S1-4 is done** and did *not* need the shared layer: the two forms that can
   corrupt (an alias, a `with_itemindex=` name) now force column resolution and
   raise `KqlSchemaError`, the way `join`/`lookup` already do. Refusing settles
   it because the collision is unknowable without a schema, not merely
   undetected — there is nothing for a resolution layer to resolve. The
   remaining four still want one.
3. ~~**The two allow-lists** — S1-3, S1-5.~~ **Done.** Both shipped with trap
   tests (`test_has_list.py`, `test_tostring.py`) and their entries deleted.
   S1-5's fix went further than the entry asked: deriving boolean-ness from the
   registry, as suggested, would still have missed a bool **column**, which
   carries no static type at all — so `render_kql_tostring` now dispatches on
   DuckDB's run-time `typeof` and the allow-list is gone rather than widened.
   That also drained `reverse-function-01`, the datetime-column divergence,
   which was the same hole wearing a different type.
4. **Error-taxonomy sweep** — S2-3, S2-4, S2-5 together, plus the
   `__init__.py:200` docstring correction from S1-4.
5. **S3-4, S3-5, S4** as maintenance.

Every fix lands with a trap test, per §4's own standard — and per S3-5, name it
so the spec can cite it.

## What held up — verified by execution, do not re-review

- **R5/R14 joins:** all nine `kind=` values with duplicate left keys;
  `innerunique`'s `DISTINCT ON` dedup (2 rows vs `inner`'s 4 on a 2×2); correct
  directionality of `rightsemi`/`rightanti`; self-join; `let`-bound right side.
  `lookup`'s `leftouter` default, its key-column-drop rule including the
  asymmetric `$left.K1 == $right.K2` case, and null-key-matches-null-key via
  `IS NOT DISTINCT FROM` (not `=`).
- **R15 union:** by-name (not positional) matching, `UNION ALL` non-dedup,
  `kind=inner`, first-appearance column ordering, `withsource=`, wildcard arms,
  `isfuzzy=true` dropping a missing table.
- **R16 macro-expand:** inline/`let`/named groups, empty-group refusal, and the
  `isfuzzy` first-vs-later-entity ordering asymmetry — already deliberately
  handled by `_promote_fuzzy_source` (`:710-765`). Both orderings verified
  identical. Not a bug; noted so it is not "found" again.
- **R18 mv-expand semantics** (as distinct from S1-4's collision bug): the full
  measured zip/pad table — unequal lengths, empty-vs-nonempty, empty-vs-empty,
  null-vs-two-element. `mv-apply` and `to typeof(timespan)` refuse cleanly.
- **R6 sort:** `DESC NULLS LAST` / `ASC NULLS FIRST` executed against
  `[3,null,1]`; `top` inherits it; `top -1` clamps to `LIMIT 0`.
- **R4 aggregates:** empty table with no `by` → one row `(0,0,nan)`; with `by` →
  zero rows. Null totality of `!contains`/`==`/`!=` against a null column.
- **R10:** `sample`/`sample-distinct` refuse cleanly; `top` ties are carved out
  as data-dependent in `test_behavior.py`'s `NONDETERMINISTIC_BY_DATA` rather
  than pinned to one row set.
- **R12 naming:** `count_`, `distinct C, tolower(B)` → `C, Column1`, the
  documented `Column1` residue, and `C`/`C1` disambiguation.
- **R19 parse:** all-or-nothing blanking against the spec's three-column example;
  anchored lazy captures (`'aXcYc'`→`'XcY'`, `'abcbd'`→`''`); typed-shape-before-`*`
  for `long`/`real`/`bool`; overflow and unparseable typed captures → null per R1;
  `parse-where kind=relaxed`, empty-literal patterns, duplicate capture names and
  unanchored-string-before-`*` all refuse loudly.
- **R1 conversions:** `toint`/`tolong`/`todouble`/`toreal`/`todatetime`/
  `totimespan`/`toguid` all `TRY_CAST`-based, null on `"abc"`, `""`, typed null
  and out-of-range.
- **R13 division:** `7/2`, `-7/2` truncation, `7/0` → null, `7.0/0` → Infinity,
  `%` always non-negative.
- **R2/R3:** `==`/`=~` sensitivity, `_cs` variants, `has` whole-term matching
  (`"error" has "err"` false) including the full documented boundary table,
  `has_any`/`has_all` degenerate-set rules.
- **R9/R11:** `array_length`/`array_index_of`/`array_slice`/`zip` and dynamic
  property access all null on missing/out-of-range/non-array; `substring`
  clamping including `café` and emoji; `format_datetime`/`format_timespan`/
  `trim`/`bag_keys` refuse rather than emit wrong SQL.
- **Injection:** `quote_ident` doubles embedded `"` (verified through `['a"b']`
  end to end); `quote_string` doubles `'` and backslash is inert in DuckDB's
  default string literals; `dynamic()` JSON round-trips embedded apostrophes
  correctly. `params.py` values never touch SQL text — they cross via DuckDB's
  binding API, and `slot` is deliberately not derived from the caller-supplied
  parameter name. No injection surface found.
- **`lower.py` silent drops:** all 14 bare `pass`/`continue` sites swept — every
  one is a literal-parse fallthrough or a deliberate, documented no-op
  (`hint.*`, `materialize()`). No construct is silently ignored instead of
  raising.
- **Test integrity:** no weakened, skipped or deleted assertions found. Every
  `skipif`/`importorskip` is an optional-dependency or repo-root guard.
  `test_corpus.py`'s `BASELINE_PARSED` is a genuine monotonic floor;
  `test_support_matrix.py` regenerates and byte-diffs the docs table, which is a
  strong anti-drift check rather than a pinned-bug snapshot. `git log -p --
  tests/` shows no relaxed assertion; the "retire the suppression ledger" commit
  is a `ruff` `noqa` cleanup.
- **`comparison.py` and `regexfrag.py`:** read in full, no defects found.
  `regexfrag`'s unbalanced-paren handling and lookaround/backreference refusals
  are internally consistent with the documented Kusto-vs-RE2 divergences —
  reviewed by reading, not verified against the emulator.
