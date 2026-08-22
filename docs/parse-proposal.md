# Proposal — the `parse` family

> **Status: proposal.** Nothing here is implemented. Every behavioural claim was
> measured on the pinned Kusto Emulator and the measurement is quoted beside it;
> every structural claim is cited to `microsoft/Kusto-Query-Language` at commit
> `12608cc`, read under the attribution terms
> [`column-types-proposal.md`](column-types-proposal.md) §2.0 settles.

## 0. Why this one

`docs/column-types-proposal.md` §0.2 lists what actually moves the coverage
number. `parse` / `parse-where` is **11 corpus cases**, and unlike the bigger
items it is one self-contained operator with a small grammar and — as it turns
out — an almost exact DuckDB counterpart. The catch is §5: 6 of those 11 need
`kind=regex`, which is the one part carrying a real faithfulness risk.
`parse-kv` is a separate operator that comes nearly free once the
column-declaration plumbing exists.

It also happens to be the operator people reach for first when they point KQL at
unstructured log text, which is the shape of data DuckDB is often already
holding.

## 1. The surface

### 1.1 Grammar

From the vendored grammar, which is unusually tidy here:

```antlr
parseOperator:
    PARSE (KindClause)? Expression WITH Pattern=parseOperatorPattern;
parseOperatorKindClause:
    KIND '=' (SIMPLE | REGEX | RELAXED) (FLAGS '=' IDENTIFIER)?;
parseOperatorPattern:
    (LeadingColumn)? (Segments+=parseOperatorPatternSegment)* (TrailingStar='*')?;
parseOperatorPatternSegment:
    ('*')? Text=stringLiteralExpression (Column=parseOperatorNameAndOptionalType)?;
parseOperatorNameAndOptionalType:
    Name (':' Type=scalarType)?;
parseWhereOperator:  PARSEWHERE (KindClause)? Expression WITH Pattern;
parseKvOperator:     PARSEKV Expression Keys=rowSchema (WithClause)?;
```

A pattern is therefore a sequence of **segments**, each one *(optional skip,
string literal, optional column)*, with an optional leading column and an
optional trailing `*`. That is a better shape than the flat list the C# binder
walks, and it means the lowering has almost no shape-checking to do.

### 1.2 Well-formedness, from the binder

`Binder/Binder_NodeBinder.cs:3440` (`ParseVisitCommon`) rejects three
arrangements, and the grammar above already makes two of them unrepresentable:

| Rule | Diagnostic |
|---|---|
| a column must follow a string literal | `GetParsePatternNameDoesNotFollowStringLiteral` |
| a `*` must be followed by a string literal | `GetParsePatternStringLiteralMustFollowStar` |
| a `*` must not follow a **string-typed** column | `GetParsePatternUsingStarAfterStringColumnIsAmbiguous` |

Only the third needs checking by hand, and it is genuinely ambiguous rather than
pedantic: a lazy `(.*?)` followed by `.*?` has no defined split.

### 1.3 Output columns

Also from the binder: incoming columns first, then one column per declaration —
a bare name is **`string`**, `name: T` is `T`. Measured, a name that collides
with an existing column **replaces it in place** rather than appending:

```
datatable(s:string, a:string)['a=1', 'zz'] | parse s with "a=" a
    ->  s, a          with a = '1'
```

which is `extend`'s rule again (R18 §1.5 — the binder's `ProjectionStyle`), and
the third operator in this codebase to want it. Worth extracting a shared helper
rather than writing it a third time.

## 2. Measured semantics

All on the pinned emulator. These are the parts no amount of grammar-reading
would give.

### 2.1 A non-match keeps the row, with empty strings

```
datatable(s:string)['a=1, b=xy', 'nomatch'] | parse s with "a=" a ", b=" b
    ['a=1, b=xy', '1', 'xy']
    ['nomatch',   '',  '']        <-- kept, and '' — NOT null
```

A **string** column that does not match is the **empty string**; a **typed**
column is **null**. `parse-where` drops the row instead.

### 2.2 Columns are lazy in `simple` and greedy in `regex`

The two modes differ in more than whether the literals are escaped.

```
datatable(s:string)['aXcYc']
| parse             s with "a" v "c" *      ->  v = 'X'      lazy
| parse kind=regex  s with "a" v "c" *      ->  v = 'XcY'    greedy
| parse kind=regex flags=U s with "a" v "c" * ->  v = 'X'    lazy again
```

So `simple` compiles a column to `(.*?)` and `regex` to `(.*)`, and `flags=U`
swaps it. A `*` is a **non-greedy skip** in both, capturing nothing — measured,
`* "x=" v ","` over `y=1,x=2,x=9` gives `2`, i.e. it stopped at the *first*
`x=`.

Literals: `simple` escapes them (`parse s with "a.b" v` does **not** match
`axbZ`), `regex` does not (it does).

A trailing column with no following literal runs to the end of the input.

### 2.3 `simple` is all-or-nothing; `relaxed` is per-column

The trap, and the one worth writing a test for before writing any code. A
two-column example is ambiguous between "stop at the failure" and "blank the
whole row"; three columns disambiguate:

```
datatable(s:string)['a=1,b=2,c=zz,d=4']
| parse s with "a=" a: long ",b=" b ",c=" c: long ",d=" d
    ->  a=null   b=''   c=null   d=''
```

`a=1` converts perfectly well and is still blanked. **If any declared column
fails to convert, the entire row becomes not-matched.** Under `kind=relaxed`
the same input gives `a=1, b='2', c=null, d='4'` — each column independent.

`parse-where` is then exactly "the pattern matched **and** every conversion
succeeded". And `parse-where kind=relaxed` is **refused** — SEM0477,
*"parse-where: only simple or regex modes are supported"* — which we should
refuse too rather than quietly accepting.

### 2.4 Flags

`i`, `s`, `m`, `U`, `x` are accepted and combinable (`flags=ims` applies `i`).
Measured: `i` case-insensitive, `s` dot-matches-newline, `U` swaps greediness.
`m` produced no observable difference in the cases tried — **unverified on both
sides**, so it should be refused rather than mapped on faith.

## 3. The DuckDB mapping

DuckDB turns out to have almost exactly the right primitive:

```sql
regexp_extract(s, pattern, ['v', 'w'] , 'i')   -- -> STRUCT(v VARCHAR, w VARCHAR)
```

Verified behaviours, all of them load-bearing:

| | |
|---|---|
| named groups → one struct, one pass | `regexp_extract('ab12cd34','ab(?P<v>.*?)cd(?P<w>.*)',['v','w'])` → `{'v':'12','w':'34'}` |
| **no match → `''` in every field** | which is Kusto's string-column rule, for free |
| inline flags | `(?i)`, `(?s)`, `(?U)` all work in RE2 and match `i`, `s`, `U` |
| a fourth argument | `regexp_extract(s, pat, names, 'i')` — flags without touching the pattern |
| the match/no-match test | `regexp_matches(s, pat)`, for `parse-where` |

So the shape is: build **one** regex, extract **one** struct, then project each
column out of it with its conversion applied.

```sql
-- parse s with "a=" a: long ", b=" b
SELECT *, ..., regexp_extract("s", 'a\=(?P<a>.*?), b\=(?P<b>.*)', ['a','b']) AS _p
-- then, per column:
--   a  ->  TRY_CAST(_p.a AS BIGINT)          typed: null when it fails
--   b  ->  _p.b                              string: '' when it fails
```

### 3.1 The one real gap: the name list is positional

```sql
regexp_extract('ab12cd34', 'a(b)(?P<v>.*?)cd(?P<w>.*)', ['v','w'])
    ->  {'v': 'b', 'w': '12'}          -- WRONG: shifted by the user's group
```

DuckDB's name list labels groups **by position**, not by name — `['w','v']`
against the same pattern just relabels, and `['q','z']` happily returns
`{'q':'12','z':'34'}`. Kusto is immune (`"a(b)" v "cd" w` gives `12, 34`),
because it matches by name.

This only bites in `kind=regex`, where a literal may contain the user's own
groups. The fix is to rewrite every **capturing** `(` in a regex-mode literal to
`(?:`, which needs a small scanner that respects `\(`, `[...]` character classes
and existing `(?`. That scanner is the single fiddliest piece of the whole
operator and deserves its own tests.

`simple` mode escapes its literals wholesale, so it cannot hit this at all.

### 3.2 `parse-where` costs a second regex evaluation

`regexp_matches(s, pat) AND <every conversion succeeded>`, alongside the
`regexp_extract`. RE2 will compile the pattern once, but it is evaluated twice
per row. Given `docs/TRANSLATION.md` R3's history — a term-match pattern rebuilt
per row cost 28 seconds — this is worth measuring rather than assuming, and the
corpus budget test (`SLOWEST_QUERY_BUDGET`) will notice if it is bad.

### 3.3 Conversions

`name: T` maps to the existing `to<T>()` mapping, i.e. `TRY_CAST` (R1) — the
same rule that already makes `tolong('xx')` null rather than an error, so
nothing new. `date` is accepted as an alias for `datetime` (corpus case
`parse-operator-01` uses it).

For `simple`'s all-or-nothing rule (§2.3), the row is blanked when **any** typed
column's `TRY_CAST` is null — including when the capture was legitimately
empty. Measured, because the obvious "an empty capture is not a failure"
exception does **not** hold:

```
datatable(s:string)['a=,b=2'] | parse s with "a=" a: long ",b=" b
    ->  a=null  b=''          the whole row blanked; `b` never sees '2'
    ...same, with `a` untyped ->  a=''  b='2'      no blanking at all
    ...under parse-where      ->  no rows
```

So it is one boolean per row, computed once and reused by every column:

```sql
_ok = regexp_matches(s, pat)
      AND TRY_CAST(_p.a AS BIGINT) IS NOT NULL
      AND TRY_CAST(_p.c AS BIGINT) IS NOT NULL
```

then each column is `CASE WHEN _ok THEN <value> ELSE <not-matched> END`, and
`parse-where` is `WHERE _ok`. `relaxed` skips `_ok` entirely. A pattern with no
typed columns reduces `_ok` to the `regexp_matches` alone.

## 4. Risk: RE2 is not .NET

The one place this could be quietly wrong. `kind=regex` splices the user's
regex fragments into a pattern that DuckDB runs on **RE2**, while Kusto runs
.NET's backtracking engine. They agree on the common cases and differ on
others; RE2 also refuses constructs .NET accepts — backreferences and
lookaround, most notably.

One measured case already looks like an engine difference rather than a rule:

```
datatable(s:string)['ab12cd34'] | parse kind=regex s with "[a-z]+" v "[a-z]+" w
    Kusto:  v = '12c',  w = '34'
```

which is what a *greedy* `v` gives, consistent with §2.2 — but it is the kind of
answer that depends on how the engine resolves competing quantifiers, and RE2
resolving it differently would be a silent wrong answer.

The honest response is to **not treat `kind=regex` as done when it translates**.
Either ship `simple` first and gate `regex` behind its own corpus verification,
or ship both and put the regex cases through a differential sweep the way R17's
term matching was. A lookaround or backreference in a literal must be refused
outright, since RE2 will reject the pattern anyway — better a `KqlUnsupportedError`
naming the construct than a raw DuckDB binder error.

## 5. Phases

The 11 corpus cases split by mode as follows — counted, because the split is
not what the phase order would suggest:

```
parse       kind=simple    3     (parse-operator-01, -02, parse-where-operator-01,
                                  which despite its id uses plain `parse`)
parse       kind=regex     4     (-03, -04, -05, -06;  -05 and -06 use flags)
parse       kind=relaxed   1     (-07)
parse-where kind=simple    1     (parse-where-operator-02)
parse-where kind=regex     2     (parse-where-operator-03, -04;  -04 uses flags)
```

| Phase | Scope | Corpus |
|---|---|---|
| 1 | `kind=simple` (the default): segments, `*`, typed columns, the not-matched rule, the all-or-nothing rule, replace-in-place output columns | 3 of 11 |
| 2 | `parse-where` (`_ok` in a `WHERE`), and refusing `parse-where kind=relaxed` | +1 |
| 3 | `kind=relaxed` | +1 |
| 4 | `kind=regex` + `flags=i/s/U`, the capturing-group scanner, the differential sweep of §4 | **+6** |
| 5 | `parse-kv` | 0 today |

**The awkward part of this plan is that the value is in the risky phase.**
Phase 1 is the one that has to be right and is worth 3 cases; phase 4 carries
all of §4's RE2-versus-.NET exposure and is worth 6. Phases 2 and 3 are a
boolean and its absence.

That is an argument for the split rather than against it — phases 1–3 are
shippable on their own and leave `kind=regex` raising `KqlUnsupportedError`
exactly as it does today, which is the honest state while its faithfulness is
unproven. But it does mean nobody should treat "parse is done" as reached at
phase 3, and it means the differential sweep in phase 4 is the deliverable
rather than an afterthought.

`parse-kv` earns no corpus cases and is listed last for that reason, but it is
small — measured working on the emulator as
`parse-kv s as (a:long, b:long, c:string) with (pair_delimiter=" ", kv_delimiter="=")`
— and it is the form people actually have in structured logs.

## 6. Out of scope, and open questions

* **`flags=m` and `flags=x`.** Accepted by the parser, no measured effect.
  Refuse until there is a case that shows what they do.
* **`parse-kv`'s `regex`, `quote`, `escape`, `greedy` properties**
  (`QueryOperatorParameters.ParseKvWithProperties`). Phase 5 should implement
  `pair_delimiter` and `kv_delimiter` only, and refuse the rest.
* **Does `parse` require a string input?** The binder only calls
  `CheckIsScalar`. What a cluster does with `parse someLong with …` is
  unmeasured.
* **Is the second regex evaluation in `parse-where` (§3.2) actually free?**
  Measure before assuming; DuckDB may or may not common-subexpression it.
* **Should the replace-in-place column rule be extracted now?** `extend`,
  `mv-expand` and now `parse` all want it. Doing it as part of phase 1 is more
  refactoring than the phase needs; doing it later means writing it a third
  time first.
