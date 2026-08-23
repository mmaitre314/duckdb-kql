# Proposal — the `parse` family

> **Status: phases 1–4 implemented** (`kind=simple`, `parse-where`,
> `kind=relaxed`, `kind=regex` with `flags=i/s/m/U`) — all 11 corpus cases, with
> the rules written up as **R19** in `TRANSLATION.md` and `tests/test_parse.py`
> and `tests/test_regexfrag.py` holding the measurements. Phase 5 (`parse-kv`)
> is outstanding and refuses rather than guesses, as do four narrow places
> inside `kind=regex` (§4.5). Every behavioural
> claim was measured on the pinned Kusto Emulator and the measurement is quoted
> beside it; every structural claim is cited to
> `microsoft/Kusto-Query-Language` at commit `12608cc`, read under the
> attribution terms [`column-types-proposal.md`](column-types-proposal.md) §2.0
> settles.
>
> **Six things this document got wrong**, all found by implementing it and all
> now corrected in place:
>
> 1. **A capture is not always `.*?`.** Immediately before a `*` — the one
>    position with nothing to stop it — a **typed** column matches its *type's
>    shape* (`\s*[-+]?\d+` for an integer, and so on). That is *why* Kusto
>    allows `*` after a typed column and refuses it after a string one; §3 had
>    the refusal recorded without the reason, and the reason turns out to be the
>    implementation. A **trailing** `*` is a no-op and makes the shape optional.
> 2. **The conversions are not `TRY_CAST`.** Three ways DuckDB is too generous,
>    each a silent wrong answer if taken: `'1.5'` as a long is **2** (it rounds)
>    where Kusto says null; `'yes'` as a bool is **true** where Kusto says null;
>    and `'02/17/2016 08:40:01'` as a datetime is **null** where Kusto parses it
>    — the corpus depends on that last one, and a bare cast blanked whole rows.
> 3. **`decimal` cannot be supported** while `todecimal` is unmapped: DuckDB
>    renders `DECIMAL(38,9)` as `1.000000000`, not `1`.
> 4. **`: string` written explicitly** is the same column as a bare name and
>    earns the same `*`-after-it refusal. Missing that produced 57 sweep
>    failures in one run.
> 5. **`kind=simple` anchors the pattern at end-of-text**, and phases 1–3
>    shipped without it. `"a" v "c"` over `aXcYc` is `'XcY'` and not `'X'`,
>    because the pattern has to reach the end; over `abcbd`, `"a" v "b"` does
>    not match at all. §2.2 described the capture as lazy and stopped there,
>    which is right about the quantifier and wrong about the answer. `relaxed`
>    is anchored too; `kind=regex` is not.
> 6. **The regex-mode shapes are not the simple-mode shapes.** §4.2 assumed one
>    table read differently. Measured, `bool` in regex mode is `true`/`false`
>    only where simple mode also takes an integer, and `real` takes no leading
>    dot where simple mode does. Two tables.
>
> **Three bugs found in shipped code on the way**, all by the phase-4 sweep:
>
> * **`totimespan` ignored trailing text.** DuckDB's `INTERVAL` cast does, so
>   `totimespan('00:01:00 junk')` answered one minute where a cluster answers
>   null. **Fixed** with a shape test.
> * **Adjacent string literals were not concatenated.** KQL joins them the way
>   C does — `print x = 'a' 'b'` is `ab` — and the lowering read the combined
>   token text instead, answering `a''b`. It also mis-read a verbatim `@'a''b'`,
>   where the doubled quote is an escape. **Fixed** by decoding each literal
>   token separately, which is the only place the boundary survives.
> * **`todynamic` drops non-JSON text.** KQL wraps it as a dynamic *string*, so
>   `todynamic('abc')` is `'abc'` on a cluster and null here, and `: dynamic`
>   in `parse` inherits it. **Recorded, not fixed** — it belongs to `todynamic`
>   and deserves its own measured change.
>
> **And one recorded earlier and still unfixed.** `tolong('1.5')` answers **1**
> here and **null** on a cluster; `toint`, `toreal`'s neighbours and
> `tobool('2')` are the same family. `parse` can be exact because what it
> converts is always *text*; `tolong` cannot, because it must also serve
> `tolong(1.5)` — which really is 2 — and telling those apart needs
> [column types](column-types-proposal.md).

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

## 4. Phase 4 — `kind=regex`, and what it is actually exposed to

Re-measured after phases 1–3 shipped. **The headline risk turned out to be much
smaller than this section originally claimed**, and three smaller ones turned
out to be real.

### 4.1 The RE2-versus-.NET fear was mostly unfounded

`kind=regex` splices the user's regex into a pattern DuckDB runs on **RE2**
while Kusto runs .NET's backtracking engine. That sounded like an open-ended
silent-wrong-answer surface. Two measurements bound it:

**Kusto's own validator refuses lookaround** — every form, with SEM0476
*"Invalid regex pattern"*:

```
parse kind=regex s with "(?=a)" v      REJECTED      (?!a) and (?<=a) likewise
```

That is precisely RE2's principal gap, so the dialect Kusto accepts here is
already close to the dialect DuckDB can run. Everything else tried is common to
both: named groups, `(?:…)`, counted repetition, POSIX classes, `\p{L}`,
alternation, inline flags.

**And the hardest pattern in the corpus agrees, field for field.**
`parse-operator-03` — user `(.*?)` groups, `\s*\d+`, an optional `(previous)?`,
and greedy captures throughout — hand-translated to `regexp_extract` returns
exactly what the emulator returns for all five columns. That is the case most
likely to expose a backtracking difference, and it does not.

The residual risk is therefore ordinary rather than structural, and the
differential sweep in §5 is how it stays that way.

### 4.2 Regex mode is a second capture policy, not a flag

Three rules differ from `kind=simple`, and only the first was known:

| | `simple` / `relaxed` | `regex` |
|---|---|---|
| literals | escaped | the user's regex |
| string column | `(.*?)` lazy | `(.*)` **greedy** |
| typed column | shaped **only** before a `*` | shaped **always** |
| the shapes themselves | one table | **a different table** |
| the whole pattern | anchored at end-of-text | unanchored |

The last two rows were added while implementing, and both were wrong here
before. The shapes differ in two measured places — `bool` in regex mode is
`true`/`false` only where simple mode also accepts an integer, and `real` takes
no leading dot where simple mode does:

```
parse            s with "q=" b: bool * "m=" m   over 'q=12x m=1'  ->  true
parse kind=regex s with "q=" b: bool * "m=" m   over 'q=12x m=1'  ->  null
```

The anchor is the more consequential one, because it is a rule about
`kind=simple` that phases 1–3 shipped without — see erratum 5 at the top.

The third row is what this section originally called new. Measured,
`"n=" n: long` *trailing*:

```
parse            s with "n=" n: long   over 'n=27 junk'  ->  null
parse kind=regex s with "n=" n: long   over 'n=27 junk'  ->  27
```

and `flags=U` inverts the shapes too, not just the wildcards:

```
parse kind=regex flags=U s with "n=" n: long  over 'n=27 junk'  ->  2
```

So `_parse_capture` grows a mode branch rather than a parameter. A `*` is
non-greedy in **both** modes — that one does not change.

### 4.3 The group-neutralising scanner is the real work

DuckDB's group-name list is **positional** (§3.1), so every capturing group in
a user literal shifts the column mapping. Two of the six regex corpus patterns
contain `(.*?)`, so this is needed on day one, not eventually.

The scanner rewrites each capturing `(` to `(?:`, and has to respect: an
escaped `\(`, a `[...]` character class (including `[]]` and `[^]]`), an
existing `(?:` or `(?i)`, and `(?<name>` — which Kusto accepts and which must
also become non-capturing, since a user group named the same as a column would
otherwise collide. A prototype handling all five cases translates
`parse-operator-03` correctly; it wants its own unit tests independent of
`parse`.

One thing this section predicted correctly and one it missed. The five cases
listed are all real. The sixth, found by the sweep, is that an **unbalanced**
parenthesis is a *literal* to Kusto and a fatal error to RE2:

```
parse kind=regex s with ")"    v   over 'a)b'   ->  'b'
parse kind=regex s with "(a"   v   over '(ab'   ->  'b'
parse kind=regex s with "(a))" v   over 'a)b'   ->  'b'
```

Finding which parentheses are stray is why the scanner is two passes: a stack
over the ones that survive the first pass, and anything left unpaired escaped.
A counter would mis-pair `)(`.

The result lives in `translate/regexfrag.py` with its own tests, and the
property those tests assert is mechanical rather than by eye — after the
rewrite the fragment must contain **zero** capturing groups, checked against
DuckDB itself, and must still match the same text.

### 4.4 Two constructs to refuse

**Backreferences.** Kusto accepts `(a)\1` (it parses, and answers no match);
RE2 rejects the pattern outright — `Binder Error: invalid escape sequence: \1`.
Left alone that surfaces as a raw DuckDB error, so the scanner detects `\1`–`\9`
outside a character class, and `(?P=name)`, and raises `KqlUnsupportedError`.

**`flags=x`.** Kusto accepts it and it does something measurable — `"a ="`
matches `a=` only under `x`. RE2 has no ignore-pattern-whitespace mode at all
(`invalid perl operator: (?x`), so it is refused rather than dropped, which
would silently change what the pattern matches.

### 4.5 The temporal shapes have an edge, and it is refused

`datetime` and `timespan` are the only declared types whose `kind=regex`
capture does not behave like a regex on the emulator's side. The shape itself
was recovered by **boundary scan** — for every way of splitting a value into
`<capture><literal>`, ask whether the pattern still answers; the splits that do
are exactly the prefixes the shape matches — and over `2020-01-01T00:00:00Z` it
answers at cuts 10, 15, 16, 18, 19 and 20, which reads as a date, an optional
`hh:mm[:ss[.f]]` and an optional zone.

That shape agrees with the emulator everywhere except two positions, and in
both the emulator stops behaving like a matcher:

```
parse kind=regex s with "v=" c: datetime      over 'v=2020/01/01T00:00:00'   ->  null
parse kind=regex s with "v=" c: datetime      over 'v=2020/01/01T00:00:00X'  ->  the datetime
```

A trailing `X` in the **data** cannot make a shape match *more*. Three other
forms flip the same way at that position, and a `timespan` there answers null
for every value tried. Under `flags=U`, where every other shape simply
inverts, a temporal capture stops answering at all. Both positions raise
`KqlUnsupportedError`; every other position is swept clean.

### 4.6 What the sweep actually caught

The sweep was written before the code, generating what the phase 1–3 sweep did
not: patterns whose last element is a **literal**, `kind=regex`, and every
`flags=` combination — including `U` crossed with a typed column, which no
corpus case does. It is two programs, one random over patterns and one
exhaustive over 11 declared types × 39 awkward values × 4 positions × 6 flag
settings.

It found the missing end-of-text anchor (erratum 5), the two shape-table
differences (erratum 6), the unbalanced-parenthesis rule (§4.3), the two
temporal edges (§4.5), and three bugs in already-shipped code (top of this
document). None of those would have been found by the corpus, which all six
regex cases now pass.

What residue is left is almost **one-directional**, which is the property
worth stating: nearly every disagreement still findable is this translator
answering null where a cluster answers a value — a blank row, not a wrong one.

The exception is worth naming because it is the same §4.5 edge reached
sideways. That refusal is positional, and a fragment that can match the empty
string leaves a temporal column effectively terminal without being last:

```
parse kind=regex s with "p: " a: string ".q: " b: date "x"   ->  both answer null
parse kind=regex s with "p: " a: string ".q: " b: date "|"   ->  Kusto null, we answer
parse kind=regex s with "p: " a: string ".q: " b: date "(x)?"  ->  the same
```

Catching it properly means asking whether an arbitrary fragment is nullable,
which is a real analysis rather than a position test. Recorded rather than
approximated.

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
| ~~1~~ | **Done.** `kind=simple` (the default): segments, `*`, typed columns, the not-matched rule, the all-or-nothing rule, replace-in-place output columns | 3 of 11 |
| ~~2~~ | **Done.** `parse-where` (`_ok` in a `WHERE`), and refusing `parse-where kind=relaxed` | +1 |
| ~~3~~ | **Done.** `kind=relaxed` — which turned out to relax the *pattern* too, not only the atomicity: an anchored typed column captures lazily there | +1 |
| ~~4~~ | **Done.** `kind=regex` + `flags=i/s/m/U`: the second capture policy (§4.2), the capturing-group scanner (§4.3), the refusals (§4.4, §4.5), and the differential sweep — which found three bugs outside `kind=regex` on the way (§4.6) | **+6** |
| 5 | `parse-kv` | 0 today |

**The awkward part of this plan was that the value sat in the riskiest phase.**
Phase 1 is the one that had to be right and is worth 3 cases; phase 4 was worth
6. Phases 2 and 3 are a boolean and its absence.

That worked out, but not the way the plan expected. The RE2-versus-.NET
question — the reason phase 4 was scheduled last — was the *easy* part (§4.1).
What cost the time was that `kind=regex` is a genuinely different capture
policy whose details are only visible by measurement, and that measuring it
exposed a rule about `kind=simple` that phases 1–3 had shipped wrong.

The general lesson is in §4.6: **the sweep was the deliverable, and it had to
be written to generate what the previous sweep did not.** The end-of-text
anchor survived three commits and a full corpus pass because every pattern the
phase 1–3 sweep generated happened to end on a column. A sweep only covers the
shapes it emits.

`parse-kv` earns no corpus cases and is listed last for that reason, but it is
small — measured working on the emulator as
`parse-kv s as (a:long, b:long, c:string) with (pair_delimiter=" ", kv_delimiter="=")`
— and it is the form people actually have in structured logs.

## 6. Out of scope, and open questions

* ~~**`flags=m` and `flags=x`.**~~ Settled in phase 4. `m` *does* have a
  measured effect — `'^a='` over `"x\na=1"` matches only under it — and RE2 has
  the same flag, so it is implemented. `x` is refused: RE2 has no
  ignore-pattern-whitespace mode and DuckDB answers *"invalid perl operator:
  (?x"*.
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
