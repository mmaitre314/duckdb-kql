# Proposal — column types at translation time

> **Status: proposal, deliberately not scheduled.** Nothing here is
> implemented. The KQL side is read from Microsoft's own implementation rather
> than measured or inferred; every claim about what Kusto does cites the file it
> came from.
>
> **Read §0.1 first.** This work unblocks **no** corpus coverage — measured, not
> estimated. It is a correctness and generated-SQL-quality change, and the case
> for it has to be made on those terms.

## 0. Why this keeps coming up

Six separate pieces of work have stopped at the same sentence: *the schema
carries names, not types.* They are not related to each other except through
that one missing fact.

| Where | What it costs today |
|---|---|
| R17 residue — `s == 1`, `s < 'y'`, `k == s` over a dynamic | conversion errors where Kusto answers or refuses cleanly |
| R17 residue — arithmetic over a dynamic (`s + 1`) | binder error where Kusto answers `2` |
| R17 residue — `summarize by` / `sort by` a dynamic | we answer; Kusto refuses (SEM0001, SEM0480) |
| R18 — `mv-expand` of a non-dynamic column | we answer; Kusto refuses (SEM0447) |
| R14 residue | null-string handling that needs to know a column is a string |
| `reverse()` of a datetime column | in `KNOWN_DIVERGENCES`: KQL reverses the .NET string form, and we only get it right when the type is statically visible |

Two of those are **silent wrong answers**, which is the failure mode this
project exists to prevent. The rest are loud but wrong-shaped: an engine error
where a cluster would have answered, or an answer where a cluster would have
refused.

Everything here is about the *translator's* view of types. It is not a runtime
change: DuckDB already knows every type at execution, which is what
`typeof(x) = 'JSON'` in R17 exploits. The problem is that decisions have to be
made while *emitting* SQL, and by then the information is a schema lookup away.

### 0.1 It unblocks no coverage — measured

The list above reads like a backlog, which invites the assumption that types
are what stands between this project and a bigger support matrix. They are not.
Every one of the 700 corpus cases that does not translate was re-run and its
refusal bucketed:

```
219  31%  scalar functions (non-geospatial)
202  28%  geospatial functions
108  15%  tabular operators   (evaluate 48, mv-apply 9, partition 8,
                               parse 8, scan 6, fork/find/search/… 20)
 43   6%  make-series and the series_* family
 41   5%  aggregates
 37   5%  expression forms    (between 15, `*` 10, toscalar 4, …)
 24   3%  let user-defined functions
 26   3%  everything else
```

**Not one is blocked on a missing column type.** They are blocked on
unimplemented functions and operators. The type system would move the coverage
number by zero.

What it moves instead, also measured against the corpus:

* **The residues in §0** — six items, two of them silent wrong answers.
* **Generated SQL size.** 95 of the 283 translating cases (**33%**) emit at
  least one run-time `typeof(...)` guard, 136 guards in total. With a schema
  nearly all of them disappear. The commonest predicate in the language,
  `T | where s != 'hello'`, currently renders as 221 characters:

  ```sql
  WHERE CASE WHEN typeof("s") = 'JSON'
             THEN coalesce((coalesce("s" ->> '$', '') <> 'hello'), TRUE)
             ELSE coalesce(("s" <> 'hello'), TRUE) END
  ```

  Readable SQL is a stated feature — the CLI ships it to people who have to
  read it — so this is real, but it is polish.
* **Refusals that match a cluster's.** Worth the most to someone developing
  locally against `duckdb-kql` and running the same query on ADX; worth little
  to someone whose queries only ever run here.

### 0.2 What *would* buy coverage

Recorded here so this document cannot be mistaken for the highest-value work
available. By corpus cases unblocked per feature:

| cases | feature |
|---|---|
| 48 | `evaluate` — a plugin family (`bag_unpack`, `pivot`, `narrow`, …), so divisible |
| 43 | `make-series` and `series_*` |
| 24 | `let` user-defined functions |
| 15 | `between` / `!between` |
| 11 | `parse` / `parse-where` |
| 10 | wildcard `*` in expressions |
| 10 | the `range()` scalar function |
| 9 | `mv-apply` |
| 8 | `partition` |

And one scoping decision worth taking deliberately, because it changes the
headline number: **202 cases — 20% of the corpus — are geospatial.** If those
are out of scope for an embedded DuckDB engine (DuckDB's `spatial` extension
would be the route if they are not), then coverage today is **283/781 = 36%**,
not 27%, and the README is understating the project.

### 0.3 So when is this worth doing?

When the residues start being hit, which is a question about users rather than
about the corpus. The most likely trigger is `mv-expand` over an array of
numbers followed by arithmetic — `s + 1` — which is an ordinary thing to write
and currently fails to bind. Until then, the items in §0.2 are worth more.

## 1. What Microsoft actually does

`microsoft/Kusto-Query-Language`, Apache-2.0, read at commit `12608cc`. Two
things worth knowing before reading further.

**There is one implementation, not two.** The C# `Kusto.Language` library is
the parser, binder and type system. The JavaScript one — `@kusto/language-service-next`,
which `Azure/monaco-kusto` wraps and the ADX web UI runs — is *that same C#
source transpiled through Bridge.NET*, not an independent port. The C# tree
even carries a `bridge.net.help.md` listing the C# constructs that break the
transpiler. So there is no second opinion to cross-check against; the C# source
is the normative reference and the JS behaviour follows from it.

**The parser's type system is necessary but not sufficient.** It is a *language
service* — it powers IntelliSense and error squiggles — and some refusals live
only in the engine. `dynamic == dynamic` is refused by the emulator with
SEM0001, but `Operators.Equal` accepts `(NotBool, Scalar)`, and the string
"Cannot compare dynamic values" appears nowhere in the repository. So the
library is a lower bound on what a cluster rejects, and the emulator stays the
oracle for anything it does not cover.

### 1.1 The type lattice

`Symbols/ScalarTypes.cs`. Each scalar carries **capability flags** rather than
being identified by name alone (`Symbols/ScalarSymbol.cs`):

```
Integer   Numeric   Interval   Summable   Orderable
```

`bool` is Orderable only. `guid` has none. `string` is Orderable. `int`, `long`
are all five; `real` and `decimal` are all but Integer; `datetime` and
`timespan` are Interval + Summable + Orderable.

Those flags are how refusals are expressed. A parameter constrained to
`ParameterTypeKind.Orderable` is what makes `sort by` reject a dynamic;
`Summable` is what makes `sum()` reject one. The constraint vocabulary
(`Symbols/ParameterTypeKind.cs`) is about twenty entries — `Number`,
`NotDynamic`, `NotBool`, `StringOrDynamic`, `DynamicArray`, `Integer`,
`CommonScalar`, … — and it is the whole refusal mechanism.

Widening is declared per type as `widerThan`:

```
long   widerThan int
real   widerThan int, long
decimal widerThan int, long, real
string widerThan dynamic          <-- R17, in Microsoft's own vocabulary
```

That last line is worth sitting with. **A dynamic widening to a string is a
type promotion in the official model**, not a special case anybody had to
discover. R17 was reverse-engineered from the emulator over a long afternoon;
it is one entry in a table here.

There is also `ScalarTypes.Unknown`, carrying `ScalarFlags.All` — a type that
satisfies every constraint, so a gap in type knowledge never produces a
spurious error. And `ScalarTypes.Null`, which takes the other side's type in
any common-type computation. Both are load-bearing design, and both are things
this project needs.

### 1.2 Dynamic is not one type

The single most useful discovery. `dynamic` is a *family*
(`Symbols/DynamicSymbol.cs`):

* `DynamicAnySymbol` — plain `dynamic`, nothing known;
* `DynamicPrimitiveSymbol(T)` — a dynamic known to hold a `bool`/`long`/`real`/
  `datetime`/`timespan`/`guid`/`string`;
* `DynamicArraySymbol(element)` — an array with a known element type, nestable
  (`DynamicArrayOfArrayOfReal` is a declared constant);
* `DynamicBagSymbol(properties)` — an object with *named, typed* properties.

`ScalarTypes.GetDynamic(T)` lifts a scalar into its dynamic counterpart, and
`TypeFacts.GetElementType` projects back out.

This is precisely what R18's `to typeof(T)` and R17's residue were missing.
`mv-expand a` over a `DynamicArrayOfLong` yields a **long** column, not a
dynamic one — so the arithmetic that fails to bind today would simply be
arithmetic on a long.

### 1.3 Common-type computation

`Symbols/TypeFacts.cs`, `TryGetCommonType`. In order:

1. either side null → the other;
2. either side `Null` → the other;
3. either side `Unknown` → `Unknown`;
4. either side plain `Dynamic` → `Dynamic`;
5. one promotable to the other → the wider;
6. two arrays → array of the common element type, else `DynamicArray`;
7. two bags → a bag of the **intersected** properties;
8. dynamic-primitive vs anything → the dynamic of the common underlying type;
9. two tables → a table of the intersected columns;
10. two unrelated scalars → `Dynamic`, but only if `Conversion.Dynamic` is
    permitted.

The conversion ladder (`Symbols/Conversion.cs`) is
`None < Promotable < Dynamic < Compatible < Any`, and callers pass how far they
are willing to go. `union` uses a permissive setting; a strict operator uses
`None`. That parameterisation is why one function serves both "unify these
branches" and "do these two operands match".

### 1.4 How results are typed

Functions and operators are **overload sets**. `Symbols/ReturnTypeKind.cs` is
the vocabulary a signature uses to describe its result:

`Declared`, `Parameter0`, `Parameter1`, `ParameterN`, `Parameter0Promoted`,
`Parameter0Literal` (the `to typeof(T)` shape), `Parameter0Array`, `Widest`,
`Common`, `CommonNonDynamic`, `Computed`, `Custom`.

Worked examples straight from `Operators.cs`:

```csharp
Add:  (Number, Number)              -> Widest
      (timespan, timespan)          -> timespan
      (DateAndTimespan, DateAndTimespan) -> datetime
      (dynamic, dynamic)            -> dynamic
      (dynamic, DynamicAddable)     -> Parameter1     // the NON-dynamic side wins
      (DynamicAddable, dynamic)     -> Parameter0
```

That resolves an open question in `docs/mv-expand-proposal.md` §5: `s + 1` over
a dynamic returns **long**, the other operand's type — not a dynamic, and not
the `DOUBLE` a naive `CASE` over `json_type` would unify to.

```csharp
Has = StringBinary(kind, dynamicRHS: false)
    -> (StringOrDynamic, string)     // left may be dynamic, right may NOT
Contains, StartsWith, EqualTilde = StringBinary(kind)   // dynamicRHS: true
    -> (StringOrDynamic, StringOrDynamic)
```

Which is exactly the asymmetry measured in the R17 work — `k has s` refused,
`k contains s` accepted — and which was recorded there as an unexplained
oddity. It is a deliberate one-word difference in a helper.

```csharp
Equal: (bool, Scalar).Hide()
       (NotBool, Scalar)
       (NotBool[star], Scalar)
```

`.Hide()` marks a signature that works but is kept out of IntelliSense — a
useful third state between "supported" and "refused", and one this project has
no vocabulary for today.

### 1.5 How operators type their output columns

`Binder/Binder_Projection.cs` defines a `ProjectionStyle`:

```
Default   Extend   Print   Rename   Replace   Reorder   Summarize
```

and every tabular operator states which one it uses. `mv-expand`
(`Binder/Binder_NodeBinder.cs:3146`) uses **`ProjectionStyle.Replace`** — which
is R18's replace-in-place rule, discovered here by measurement and stated there
in one argument.

The same method is worth quoting in full as a specimen, because it is the whole
of R18's type behaviour:

```csharp
_binder.CheckIsDynamic(expr.Expression, diagnostics);          // SEM0447
var newType = GetMvExpandResultType(node.Parameters, expr.Expression, expr.ToTypeOf);
_binder.CreateProjectionColumns(..., style: ProjectionStyle.Replace, columnType: newType);
if (parameters.GetParameterNameValue(WithItemIndex) is string indexName)
    builder.Add(new ColumnSymbol(indexName, ScalarTypes.Long));   // appended last
```

and `GetMvExpandResultType`: `to typeof` wins; else an array gives its element
type; else a bag gives `DynamicBag`, or `DynamicArray` under `kind=array`; else
a dynamic gives `Dynamic`; else — "not even dynamic? Error case" — the
expression's own type.

Every one of those branches matches what `docs/mv-expand-proposal.md` measured.
The measurements were right. They also took a day, and this is a 40-line method.

## 2. What to build here

Not a port. `Kusto.Language` is ~100k lines of C#, its type system is entangled
with completion and diagnostics, and the cost is the wrong shape: this project's
whole thesis is a small, auditable translator whose rules are data. What is
worth taking is the **model**, the **names**, and — see §2.5 — the **signature
table**, transcribed rather than re-measured.

### 2.0 Transcribing from upstream is approved

The signatures in `Operators.cs` and `Functions.cs` are the answer to "what type
does this produce, and what does it accept". Re-deriving them from the emulator
is possible — the R17 and R18 work did exactly that — but it costs a day per
family and gets a less complete answer than reading the table. **Transcribing
them, with attribution, is agreed.**

That is a smaller step than it sounds, because this repository already vendors
Apache-2.0 material from *this same upstream* and has the machinery for it:

* `THIRD-PARTY-NOTICES.md` already names `microsoft/Kusto-Query-Language`,
  ships the full licence text at `licenses/Apache-2.0-Kusto-Query-Language.txt`
  (§4(a)), and records that upstream has **no `NOTICE` file** at the pinned
  commit, so §4(d) is moot. That finding holds for the whole repository, not
  just the grammar subtree.
* `docs/licensing.md` already scopes the obligation as *"`microsoft/Kusto-Query-Language`
  (incl. vendored `Kql.g4`) — Apache-2.0 — ship the license text (§4a), mark
  modified files (§4b), retain attribution (§4c)"*. Signatures taken from
  `src/Kusto.Language/` are inside that scope already; nothing new is being
  taken on.
* `grammar/UPSTREAM.md` is the pattern to copy: pinned commit, file hashes,
  and a numbered list of every local deviation.

So the concrete obligations for phase 4 are:

1. A `registry/UPSTREAM.md` (or a section in the existing one) recording the
   pinned commit — `12608cc` as read here — the source files, and their hashes.
2. **State the modifications** (§4(b)). They will be substantial and structural:
   C# overload sets become registry rows, `ParameterTypeKind` becomes a Python
   constraint enum, and any signature that does not survive contact with the
   emulator is *changed* — see §6.2 — with the deviation recorded the way
   `grammar/UPSTREAM.md` records its patches.
3. Keep the repository's existing licence hygiene note that this makes it
   "MIT + Apache-2.0 material", which it already is.

Note the second obligation is also the honest one: upstream is a **lower bound**
on engine behaviour (§1), so a transcribed signature is a strong starting point
and not a frozen expectation. Where the emulator disagrees, the emulator wins
and the deviation is documented.

### 2.1 The type lattice — `src/duckdb_kql/types.py`

A `KqlType` with capability flags, mirroring `ScalarSymbol`:

```python
@dataclass(frozen=True)
class KqlType:
    name: str
    integer: bool = False
    numeric: bool = False
    interval: bool = False
    summable: bool = False
    orderable: bool = False
    wider_than: frozenset[str] = frozenset()
```

with the dynamic family as subclasses (`DynamicOf(T)`, `ArrayOf(T)`, `BagOf(...)`)
and the two escape hatches, `UNKNOWN` (all flags, satisfies everything) and
`NULL` (takes the other side's type).

A module named `types.py` already exists and holds the wire-format mapping; the
new one should either absorb it or be `kqltypes.py`. The overlap is not
accidental — both are "what is a KQL type" — and merging them is probably right.

**`UNKNOWN` is the single most important entry.** Without a schema, every
column is `UNKNOWN`, every constraint is satisfied, and the translator emits
exactly what it emits today. That is what makes this additive rather than a
rewrite: **Layer 0 with no schema must keep working, and keep working
identically.**

### 2.2 Types in the schema — `src/duckdb_kql/schema.py`

`Schema` is `dict[str, list[str]]`. It becomes `dict[str, list[ColumnSymbol]]`
where a `ColumnSymbol` is a name plus a `KqlType`, and `engine.schema()` reads
`data_type` from `information_schema.columns` — one extra column in a query it
already runs — mapping DuckDB types back through the inverse of `TYPE_MAP`.

`output_columns()` and its per-operator functions already compute names through
the pipeline. They grow types alongside. Most operators are trivial
(`where`/`take`/`sort` pass through unchanged); `project`/`extend`/`summarize`
need the expression's type, which is §2.3.

This is the bulk of the mechanical work, and it is where the compatibility risk
lives: `Schema` is a **public** type (callers pass `schema=` to `to_sql`). It
must keep accepting the plain `dict[str, list[str]]` form, treating those
columns as `UNKNOWN`.

### 2.3 Expression types — extending the registry

`FunctionSpec` and `BinarySpec` gain two fields, using Microsoft's vocabulary:

```python
returns: str | KqlType     # "declared" type, or "parameter0" / "widest" / "common" / ...
params: tuple[Constraint, ...]   # ParameterTypeKind, per argument
```

so `strlen` becomes `returns=LONG, params=(STRING_OR_DYNAMIC,)` and `bin`
becomes the overload set it actually is. Most rows take a literal type and are
a one-word change; the interesting ones are `iff`/`coalesce` (`common`),
`bin`/`+`/`-` (`widest`, plus overloads), and `mv-expand`'s `to typeof`
(`parameter0literal`, already special-cased).

Then `infer_type(expr, columns) -> KqlType` walks the IR the way `render_expr`
already does, and the emitter asks it instead of asking `_is_datetime_expr`,
`_is_timespan_expr`, `_is_real_expr`, `_is_dynamic_expr`, `_is_string_expr`,
`_may_be_dynamic`. **Those six ad-hoc predicates are the thing being replaced**
— each is a partial, hand-rolled answer to "what type is this", each was
written for one caller, and their disagreements are several of the residues
in §0.

A seventh, `_is_bool_expr`, has already gone — deleted rather than migrated,
which is the case worth noting here. It answered "is this a bool" for
`tostring`, and its every wrong answer was a silently wrong string. It could
not be completed, because its operand is usually a bare column and the IR
carries no type for one; `render_kql_tostring` now branches on DuckDB's
run-time `typeof` instead. That is the standing alternative to this proposal
for any single caller, and its limits are the argument *for* the proposal:
`typeof` answers only about the value in hand, at execution, in SQL. It cannot
refuse a query, cannot name a KQL rule in the error, and cannot be consulted
while choosing which SQL to emit — which is what §2.4's refusals and the
`iff`/`coalesce` common-type rules need.

### 2.4 Refusals

With `params` constraints in place, an argument whose type is known and does
not satisfy the constraint raises `KqlUnsupportedError` naming the KQL rule.
An `UNKNOWN` argument satisfies everything, so nothing new is refused without
a schema. That is D3 and SEM0447, and it comes for free once §2.3 lands.

### 2.5 How to populate the table (given §2.0)

With transcription agreed, phase 4 stops being "measure ~150 functions" and
becomes a mechanical port with a verification pass:

1. **Generate a first draft.** `Functions.cs`, `Operators.cs` and
   `Aggregates.cs` are declarative — one C# expression per construct — so a
   throwaway script under `tools/` can parse them into a Python literal rather
   than a human retyping 150 rows. Committed output, not a build-time
   dependency: the same rule the vendored parser follows.
2. **Intersect with what we actually map.** Upstream declares far more than this
   project translates. Rows with no `SCALAR_FUNCTIONS` entry are dropped, not
   carried as aspiration — the support matrix is generated from the registry and
   must not start claiming things.
3. **Verify against the emulator, do not trust.** §1 established upstream is a
   lower bound on engine behaviour. A transcribed signature is a hypothesis; the
   trap tests are still what makes it a fact. The difference from today is that
   the hypothesis is now nearly always right, so the emulator confirms in
   minutes what it previously had to discover in a day.
4. **Record every deviation** in the `UPSTREAM.md` §2.0 calls for, in the
   numbered form `grammar/UPSTREAM.md` already uses.

Step 3 is the one that must not be skipped under the temptation of a table that
looks authoritative. `dynamic == dynamic` is the standing example: upstream
accepts it, the emulator refuses it, and only one of those is the behaviour a
user meets.

## 3. What it buys, concretely

Working through §0 with the machinery above:

| Residue | Resolution |
|---|---|
| `s == 1` over a string dynamic | `Equal` accepts `(NotBool, Scalar)`; knowing `s` is `DynamicString` picks the string comparison instead of the numeric cast that fails |
| `s + 1` | `Add`'s `(dynamic, DynamicAddable) -> Parameter1` gives a **long** result, so the emitted SQL casts once and stays integral |
| `sort by`/`summarize by` a dynamic | `Orderable` / group-key constraint refuses, as SEM0480 / SEM0001 do |
| `mv-expand` of a non-dynamic | `CheckIsDynamic` equivalent refuses, as SEM0447 does |
| `reverse()` of a datetime column | the column's type is now visible, so KQL's `.NET` spelling is emitted; leaves `KNOWN_DIVERGENCES` |
| R17's `typeof(x) = 'JSON'` run-time guards | emitted only where the type is genuinely unknown, which with a schema is nowhere — the generated SQL for `col != 'x'` goes back to a plain comparison |

That last row matters beyond tidiness. R17's guard doubles every string
predicate in the emitted SQL, and readable SQL is a stated feature. Types turn
the guard from the default into the fallback.

## 4. Order of work

| Phase | Scope | Independently useful? |
|---|---|---|
| 1 | `types.py`: the lattice, flags, widening, `UNKNOWN`/`NULL`, the dynamic family, `common_type` | No — but small, pure, and exhaustively testable against §1.3 |
| 2 | `Schema` carries types; `engine.schema` reads them; back-compat for the plain-dict form | No |
| 3 | `output_columns` threads types through the operators | No |
| 4 | Transcribe the signature table (§2.0, §2.5); `returns`/`params` on the registry; `infer_type`; retire the seven predicates | **Yes** — drains `reverse()`, and shrinks the R17 guards |
| 5 | Constraint checking → refusals | **Yes** — D3, SEM0447 |
| 6 | Dynamic-family inference: `mv-expand` over a typed array, `parse_json` of a literal | **Yes** — drains the arithmetic and comparison residues |

Phases 1–3 deliver nothing on their own, which is the main risk in this plan:
three phases of plumbing before a single user-visible fix. Splitting differently
does not help — 4 needs 3, 3 needs 2, 2 needs 1 — so the mitigation is to keep
1–3 mechanical and get to 4 quickly rather than to perfect the lattice first.

Read alongside §0.1, that risk is the argument for **not starting**: three
phases of plumbing, then a fix list with no coverage in it, while `between` is
15 cases and `let` functions are 24. This plan is ready to run when the
residues start costing users something; it is not the next thing to run.

## 5. Deliberately out of scope

* **Bag property types.** `DynamicBagSymbol` carries named typed properties, so
  Kusto can type `d.a` when `d`'s shape is known. Nothing here produces a typed
  bag — DuckDB's JSON columns carry no shape — so `PathAccess` stays `Dynamic`.
* **`Computed` return types**, i.e. inferring a `let` function's result from its
  body. Blocked on user-defined functions, which are unsupported anyway.
* **Table-valued types beyond column lists.** `TableSymbol` carries ordering and
  sortedness properties; irrelevant to translation.
* **A port of `Kusto.Language`.** See §2.

## 6. Open questions

1. **Does `int` need to exist separately from `long`?** Microsoft distinguishes
   them and promotes `int` → `long`, and `Parameter0Promoted` exists solely for
   that. DuckDB has `INTEGER` and `BIGINT`. Cheap to model, and skipping it
   would make `Widest` subtly wrong for mixed arithmetic — but it needs a
   measurement to confirm it is observable in a result.
2. **How much does a wrong type cost?** `UNKNOWN` is safe by construction, but a
   type inferred *incorrectly* is worse than no type: it would silently pick a
   wrong rendering. Every inference rule needs a test against the emulator, not
   just against the C# source, since §1 established the library is a lower
   bound on engine behaviour.
3. **Does the `Schema` public type change deserve a major version?** It is
   pre-1.0, so probably not — but `to_sql(schema=...)` is documented, and the
   back-compat shim in §2.2 should be tested rather than assumed.
4. ~~**Should the registry carry Microsoft's signatures verbatim?**~~
   **Resolved: yes, transcribed with attribution.** See §2.0 for the obligations
   and §2.5 for how phase 4 changes as a result. The repository already vendors
   Apache-2.0 material from this same upstream and already carries the licence
   text and the "no upstream `NOTICE`" finding, so this adds hygiene — a pinned
   commit, hashes, and a numbered list of deviations — rather than a new
   licensing position. The one thing it must not change is that the emulator
   stays the oracle: upstream is a lower bound (§1), so a transcribed signature
   is a hypothesis the trap tests still have to confirm.
5. **Is geospatial in scope at all?** Not a type question, but it is the single
   largest determinant of what "coverage" means here — 202 of 1,036 corpus
   cases (§0.2). Deciding it changes the README's headline number without any
   code being written, and it should be decided deliberately rather than left
   to accumulate as unsupported.
