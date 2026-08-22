# Proposal — column types at translation time

> **Status: proposal.** Nothing here is implemented. The KQL side is read from
> Microsoft's own implementation rather than measured or inferred; every claim
> about what Kusto does cites the file it came from.

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
with completion and diagnostics, and the licence (Apache-2.0) permits reuse but
the cost is the wrong shape: this project's whole thesis is a small, auditable
translator whose rules are data. What is worth taking is the **model**, and the
names — the vocabulary above is better than anything invented here would be,
and it makes the two comparable when a divergence turns up.

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
`_is_timespan_expr`, `_is_real_expr`, `_is_bool_expr`, `_is_dynamic_expr`,
`_is_string_expr`, `_may_be_dynamic`. **Those seven ad-hoc predicates are the
thing being replaced** — each is a partial, hand-rolled answer to "what type is
this", each was written for one caller, and their disagreements are several of
the residues in §0.

### 2.4 Refusals

With `params` constraints in place, an argument whose type is known and does
not satisfy the constraint raises `KqlUnsupportedError` naming the KQL rule.
An `UNKNOWN` argument satisfies everything, so nothing new is refused without
a schema. That is D3 and SEM0447, and it comes for free once §2.3 lands.

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
| 4 | `returns`/`params` on the registry; `infer_type`; retire the seven predicates | **Yes** — drains `reverse()`, and shrinks the R17 guards |
| 5 | Constraint checking → refusals | **Yes** — D3, SEM0447 |
| 6 | Dynamic-family inference: `mv-expand` over a typed array, `parse_json` of a literal | **Yes** — drains the arithmetic and comparison residues |

Phases 1–3 deliver nothing on their own, which is the main risk in this plan:
three phases of plumbing before a single user-visible fix. Splitting differently
does not help — 4 needs 3, 3 needs 2, 2 needs 1 — so the mitigation is to keep
1–3 mechanical and get to 4 quickly rather than to perfect the lattice first.

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
4. **Should the registry carry Microsoft's signatures verbatim?** They are
   Apache-2.0 and could be *transcribed* with attribution rather than
   re-measured. That is a large accuracy win and a licensing/provenance
   decision, not a technical one — and it would make the emulator's role
   confirmation rather than discovery. Worth deciding before phase 4, since it
   changes how that phase is done.
