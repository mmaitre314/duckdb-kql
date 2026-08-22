# Proposal — completing `mv-expand`, and the `dynamic` work it depends on

> **Status: done, except D3.** All six phases are implemented; the outcome
> lives in `TRANSLATION.md` as **R17** (a `dynamic` in a string context) and
> **R18** (`mv-expand`'s three rules), with `tests/test_dynamic_strings.py` and
> the `mv-expand` block of `tests/test_dynamic.py` holding the measurements.
> This file is kept as the record of how the shape of the work was found; the
> two R-rules are the spec. Every behaviour in §1 and §2 was measured on the
> pinned Kusto Emulator, and the measurement is quoted next to the claim it
> supports.
>
> **Where the plan was wrong, and it was wrong three times:**
>
> 1. **D2 was under-counted.** Written as "comparing a dynamic to a string
>    crashes", the crash turned out to be the *smaller* half: `startswith`,
>    `endswith`, `matches regex`, `=~` and `in` all answered **false** where
>    Kusto says true, and `contains`/`has` looked correct only because `"x"`
>    happens to contain `x` — `s contains '"'` was true here and false there.
>    Six string *functions* were wrong the same way.
> 2. **B2's model was wrong.** "The alias creates a new column" is `extend`'s
>    rule wearing a disguise: `mv-expand b = a` overwrites `b` *in place*.
> 3. **§2.3's `to typeof` reading was wrong.** It is not a declaration whose
>    only effect is on `getschema`, and not a cast of the text either: a JSON
>    string does not become a long, and `2.5` does not become a bool.
>
> **What was found on the way:** `with_itemindex` over an empty array produced
> one row carrying null where Kusto produces none — a shipped bug this proposal
> never mentioned, caught only because the multi-column row count made the
> index length load-bearing.

## 0. Short answer to "does `dynamic` need resolving first?"

**Yes — three things, and two of them are silently wrong today.** `mv-expand`
is the main *producer* of `dynamic` scalars: before it, a dynamic is usually a
whole array being passed around; after it, every row holds one element and the
next operator does arithmetic, comparison or string work on it. So the gaps
below are mostly invisible until `mv-expand` is used properly, and then they
are on the very next line of the query.

| # | Gap | How it fails today |
|---|---|---|
| D1 | `tostring()` of a dynamic keeps JSON quoting | **Silently wrong.** `strlen(tostring(s))` is `3` where Kusto says `1` |
| D2 | comparing a dynamic to a string | **Crash.** `ConversionException: Malformed JSON at byte 0` |
| D3 | `summarize by` / `sort by` a dynamic | Kusto **refuses** (SEM0001, SEM0480); we accept and answer |

D1 is the one that matters most, because it is the failure mode this project
exists to prevent — a query that runs and returns the wrong answer. The others
are loud, in opposite directions.

These are worth fixing **before** extending `mv-expand`, not after: the new
surface (multi-column expansion, `to typeof`) is only useful if what comes out
of it behaves, and the tests for the operator would otherwise have to avoid
touching the expanded value.

## 1. The `dynamic` gaps in detail

### D1 — `tostring()` of a dynamic keeps JSON quoting

A KQL `dynamic` is DuckDB `JSON`, and a JSON scalar's text form quotes strings.
Kusto's does not. Measured, with `strlen` proving it is the value and not the
display:

| expression | Kusto | `strlen` | ours |
|---|---|---|---|
| `tostring(dynamic('x'))` | `x` | 1 | `"x"` (3) |
| `tostring(dynamic(1))` | `1` | 1 | `1` ✓ |
| `tostring(dynamic(1.5))` | `1.5` | 3 | `1.5` ✓ |
| `tostring(dynamic(true))` | `true` | 4 | `true` ✓ |
| `tostring(dynamic(null))` | `` (empty) | 0 | null |
| `tostring(dynamic([1,2]))` | `[1,2]` | 5 | `[1,2]` ✓ |
| `tostring(dynamic({'a':1}))` | `{"a":1}` | 7 | `{"a":1}` ✓ |

So only two rows are wrong, and they are the two that matter: a **string**
element (the common case after `mv-expand` over a list of names) and **null**.

The rule to implement: a JSON *string* unwraps to its text, a JSON *null*
becomes the empty string, everything else is its JSON text. In DuckDB:

```sql
CASE
  WHEN {0} IS NULL OR json_type({0}) = 'NULL' THEN ''
  WHEN json_type({0}) = 'VARCHAR' THEN {0} ->> '$'
  ELSE CAST({0} AS VARCHAR)
END
```

Verified: `json_type` returns `VARCHAR` for a JSON string and `->> '$'` unwraps
it. This belongs in `render_kql_tostring`, which every string-context caller
already goes through — `strcat`, the hash functions, `reverse`.

> **Note the blast radius.** `strcat(s, '!')` returns `"x"!` today for the same
> reason, and so does anything else that renders a dynamic as text. Fixing
> `render_kql_tostring` fixes the family, which is why it is one item and not
> five.

### D2 — comparing a dynamic to a string crashes

```
datatable(s:dynamic)[dynamic(['x'])] | mv-expand s | where s == 'x'
    Kusto: one row
    ours:  ConversionException: Malformed JSON at byte 0 of input
```

DuckDB casts the *string* to JSON to match the column, and `x` is not valid
JSON. It works for a number (`s == 1`) only because `1` happens to be.

The fix is the same unwrapping, applied to the dynamic operand when the other
side is a string — the pattern `render_expr`'s `ir.BinaryOp` branch already
uses for timespan division and real division. Measured targets:

| expression | Kusto |
|---|---|
| `s == 'x'` | `true` |
| `s != 'x'` | `false` |
| `s == dynamic('x')` | **refused** — dynamic cannot be compared to dynamic |

That last row is worth honouring: comparing two dynamics is a semantic error
there, so it should be one here rather than something that happens to work.

### D3 — `summarize by` and `sort by` a dynamic

Kusto refuses both, and we answer:

```
| summarize n = count() by s   SEM0001: Summarize group key 's' is of a 'dynamic' type
| sort by s asc                SEM0480: order operator: key can't be of dynamic type
```

Refusing needs to know the column's *type*, which the translator does not
carry — the schema is names only. So this is either **not fixable at this
layer** or needs the type plumbing D-next describes. It is the mild direction
(we accept what a cluster rejects), so it can be recorded rather than fixed;
the entry belongs in the divergence catalog either way.

### What is *not* broken

Worth stating, because it bounds the work. The `dynamic` **column** is fine:
`KustoClient` decodes it correctly (`raw_rows` carries `x`, not `"x"`), and
`getschema` reports `dynamic`. Only *scalar functions and operators over it*
are wrong. The Layer 1 relation showing `'"x"'` is JSON materialisation, not a
translation bug.

## 2. `mv-expand` — what exists, what is wrong, what is missing

### 2.1 Already implemented

Single-column expansion, and the three input shapes, all already measured and
correct: an **array** expands to one row per element; an **object** expands to
one row per key, each a single-key bag; a **null** yields one row carrying
null while an **empty array** yields **no rows**. `with_itemindex=` works.

### 2.2 Two bugs in what exists

Both are column-shape bugs, and column order is user-visible (R1).

**B1 — the expanded column moves to the end.**

```
datatable(id:long, a:dynamic, b:dynamic)[...] | mv-expand a
    Kusto: id, a, b
    ours:  id, b, a
```

Caused by rendering as `* EXCLUDE (a), UNNEST(...) AS a`, which appends. The
fix is the one `extend` already uses: with the incoming columns known, write
the list out explicitly so the expanded column keeps its position.

**B2 — `mv-expand x = a` should *add* `x`, not replace `a`.**

```
... | mv-expand x = a
    Kusto: id, a, b, x   — and `a` still holds the whole array
    ours:  id, b, x      — `a` is gone
```

Measured: the alias creates a **new** column and leaves the source intact.
`schema._operator_columns` renames in place, which is the wrong model.

> **Correction found while implementing.** "Creates a new column" is not the
> rule; it is `extend`'s rule. `mv-expand b = a` over `id, a, b` answers
> `id, a, b` with **`b` overwritten in place** by the expansion of `a`, and `a`
> still holding the array. So the alias names an *output* column that replaces
> a same-named input and is appended otherwise — which also explains the
> no-alias case, where the target happens to be the source. The item index
> collides like a join key on top of that: `with_itemindex=b` gives `b1`, not
> DuckDB's own `b_1`.

### 2.3 Missing surface

| Form | Kusto behaviour (measured) | Notes |
|---|---|---|
| `mv-expand a, b` | expands in **lockstep**, padding the shorter with null: `[1,2,3]` × `['x']` gives `(1,'x'), (2,null), (3,null)` | An **empty** array padded against a non-empty one contributes a null row — unlike the single-column case, where empty yields nothing |
| `mv-expand a to typeof(long)` | ~~same rows; the output column's declared type becomes `long` instead of `dynamic`, visible only in `getschema`~~ | A non-convertible element becomes **null**, not an error |
| `mv-expand a limit N` | at most N rows per input row | |
| `mv-expand kind=array a` / `bagexpansion=array` | on an **object**, expands to two-element `[key, value]` arrays instead of single-key bags | Default is `bag` |
| `mv-expand` of a non-dynamic column | **refused** (SEM0447) | We should refuse too, but it needs the column type |

`mv-apply` is a different operator — it runs a sub-pipeline per element — and
is out of scope here.

## 3. Proposed order of work

| Phase | Scope | Why here |
|---|---|---|
| ~~1~~ | **D1** — `render_kql_tostring` unwraps JSON scalars | **Done.** Fixes a silent wrong answer, and fixes `strcat`/hash/`reverse` with it. |
| ~~2~~ | **D2** — a dynamic in any string context | **Done**, and wider than planned — see R17. |
| ~~3~~ | **B1, B2** — column position and alias semantics | **Done.** The alias turned out to follow `extend`'s replace-in-place rule, so `mv-expand b = a` overwrites `b` where it stands. |
| ~~4~~ | `to typeof(T)`, `limit N` | **Done**, and it needed no type plumbing at all: the conversion is decided from the element's *JSON* type at run time, which DuckDB knows. `timespan` and `decimal` are refused. |
| ~~5~~ | multi-column zipped expansion | **Done**, and it was the *smallest* piece, not the largest: DuckDB's rule for several `UNNEST`s in one select list is already KQL's, both padding edges included. |
| ~~6~~ | `kind=` / `bagexpansion=` | **Done.** One branch of the expansion expression. |

Phases 1–3 are worth doing as one change: they are all corrections to shipped
behaviour, and 3's tests would trip over 1 and 2 otherwise.

> **In hindsight the estimates were inverted.** Phase 5 was called "the largest
> piece" and took one extra `UNNEST` per target; phase 4 was expected to need
> the type plumbing D3 wants and needed none, because `to typeof` reads the
> *element's* JSON type rather than the *column's* declared one. The expensive
> phase was 2, which the plan sized as a single crash.

**D3 and the SEM0447 refusal both need the same missing thing** — the *type* of
a column at translation time. The schema currently carries names only. That is
a larger piece of plumbing than anything above, it would also close R14's
null-string residue and `reverse()`'s datetime divergence, and it should be its
own proposal rather than being smuggled in here.

## 4. Open questions — and how they resolved

1. **Should `tostring(dynamic(null))` be `''` or null?** Measured as `''`
   (`strlen` 0), which is surprising — most KQL conversions return null. Worth
   re-confirming against a real cluster before freezing, since it is the one
   row of the D1 table that does not follow the obvious rule.

   *Still open in that sense.* Implemented as `''`, measured three ways
   (`strlen` 0, `isnull` false, wire value `""`) and now the load-bearing half
   of the R17 conversion — `coalesce(x ->> '$', '')`. `to typeof(string)` over
   a null agrees, which is a second, independent measurement of the same rule.
   Only a real cluster can close it.

2. **Does the D1 unwrap belong to `tostring` only, or to every string context?**
   Proposed: to `render_kql_tostring`, because every string-context caller
   already routes through it. That makes `strcat(s,'!')` correct as a
   side effect — verify no caller *wants* the JSON text.

   *Resolved: every string context, and the sweep found more of them than the
   question assumed.* No caller wants the JSON text. The one place the unwrap
   is deliberately **not** applied is `in`/`has_any` against a subquery, for a
   reason that has nothing to do with wanting JSON — see R17.

3. **`to typeof(T)` with no type plumbing.** The rows are unaffected; only
   `getschema` differs. Options are to implement it as a cast (changing the
   DuckDB column type, which `getschema` then reports correctly) or to accept
   and ignore it. A cast is probably right and probably cheap — measure whether
   `to typeof(string)` over `[1,2]` gives `strlen` 1 (it does, so a cast to
   VARCHAR agrees).

   *Resolved, and the premise was false.* The rows **are** affected: `'2' to
   typeof(long)` is null, not 2, so accepting and ignoring would have been a
   silent wrong answer and a plain cast would have been a different one. It
   needs no type plumbing either — the conversion reads the *element's* JSON
   type, which DuckDB knows at run time. See R18 for the measured table.

## 5. What is still open

* **D3** — `summarize by` / `sort by` a dynamic, which Kusto refuses and we
  answer. Unchanged, and still blocked on the column-type plumbing §3
  describes.
* **`mv-expand` of a non-dynamic column** (SEM0447) — same blocker.
* **Arithmetic over a dynamic.** Found while measuring D2 and not covered
  anywhere in this document: `s + 1` over `dynamic([1,2])` answers 2, 3 in
  Kusto and fails to bind here. Loud rather than silent, so it is recorded in
  R17's residue table rather than fixed. The hard part is not detection but the
  result *type* — a `CASE` over `json_type` unifies BIGINT and DOUBLE into
  DOUBLE, so `dynamic(1) + 1` would answer `2.0` where Kusto answers `2`.
* **`mv-apply`**, which runs a sub-pipeline per element. A different operator,
  and out of scope throughout.
