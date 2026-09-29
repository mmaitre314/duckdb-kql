# Proposal — stored functions, registered locally

> **Status: accepted, in implementation.** Written for the feature request
> "register local tabular KQL functions for offline query testing". Every
> semantic claim in §2 was measured on the pinned Kusto Emulator on 2026-09-27
> and 2026-09-29, against two databases (`NetDefaultDB` and a
> `.create database FpOther volatile`), and the measurement is quoted next to
> the rule it justifies. The management commands (`.create-or-alter function`
> and the rest) are deliberately **out of scope**; §7 says why and what would
> bring them in.

## 1. The one-sentence version

A stored function is a **macro expanded at the call site**, measured, not a
view evaluated in its own scope — so it is registered as KQL text, parsed once,
and lowered afresh at every call into a CTE of its own, which reproduces both
halves of Kusto's name resolution with machinery that already exists.

```python
duckdb_kql.kql(
    con,
    "ReadEvents() | summarize Total = sum(Value)",
    functions=".create-or-alter function ReadEvents() { Events }",
)
```

## 2. What Kusto does

| Question | Measured |
|---|---|
| How is it called? | `F()`, and bare `F` when every parameter has a default — `FpAbove` with a required parameter is SEM0219. `database('D').F()`, `database('D').F` and `cluster(...).database(...).F()` all work, as does a call in `join`, `union`, `in (…)` and `toscalar` |
| Names | Case-sensitive: `fpread()` is SEM0260. A built-in's name is reserved: `.create function strlen(...)` and `count()` are SEM0515 |
| A function and a table of the same name | Both may exist, in either creation order. A bare name reaches the **function**; only `table('X')` reaches the table |
| A caller's `let` inside the body | **Captured.** `F() { FpT }` answers 100 for `let FpT = <100>; F()`, not the table's 7 — and still 100 through `database('FpOther').F()`. A caller's `let` *function* is captured the same way: 3 rows, not 1 |
| A caller's *scalar* `let` | Never reaches the body: a body is validated when it is created (`FpG() { FpT \| extend k = Kx }` is SEM0100 at `.create`), so it only reads columns, its parameters and its own `let`s |
| A body's own `let` vs the caller's | The body's wins: `let k = 100; FpLets()` still filters on its own `k = 5` |
| A column vs a parameter or body `let` of the same name | **The column wins**, 7 not 0 and 7 not 5 — as it does against a top-level `let` in a query (§4.2) |
| Whose database | The **function's**: `database('FpOther').FpF()` reads FpOther's `FpT` (50, not 7), a table only FpOther has works (9), and a nested call resolves there too (50). An unqualified call to another database's function is SEM0260 |
| Recursion | Self-reference fails at `.create` (SEM0260, the name does not exist yet). A cycle stored with `skipvalidation` fails at the **call**: SEM0057, "Recursive call to 'FpA' in database 'NetDefaultDB' is not allowed" |
| Validation | An unresolved table is SEM0100 at `.create`; with `skipvalidation = 'true'` the same error arrives at the call. A dropped callee makes its caller SEM0260 at the call |
| Parameters | Scalar, with defaults; named arguments (`F(x=5)`) work; tabular parameters work through `invoke` or `F(T)` |
| Views | `with (view=true)` puts a function into `union Fp*` wildcards; a plain function is not matched (SEM0100) |
| The export form | `.show database D schema as csl script` emits `.create-or-alter function with (folder = "Tests", docstring = "Reads it", skipvalidation = "true") FpF(x:long=0) { FpT \| where Value > x }` |
| `.show functions` | Columns `Name, Parameters, Body, Folder, DocString` |
| A query-local `let F = () { FpT }` | **Lexical**, the opposite: `let F = () { FpT }; let FpT = <100>; F()` answers **7** |

The last row is why a stored function cannot be implemented as an implicit
`let` prepended to the query, which is the obvious design: it binds lexically
and answers 7 where Kusto answers 100.

## 3. Design

### 3.1 Expansion at the call site, as a CTE

Each call becomes a tabular `let` under a generated name — one per call site,
its arguments bound — placed immediately **before** the binding, or the query,
that contains the call. Then:

- **capture is Kusto's**, because the body resolves where it is used: a caller's
  `let` declared earlier is in scope and one declared later is not, which is
  how a `WITH` list resolves too (measured on DuckDB: a forward reference in a
  `WITH` list reaches the *table*);
- **schema discovery is free**: `_schema_with_lets` already hands a `let`'s
  columns to a downstream `join` or `project`;
- **the caller's scalars cannot leak in**, because the body is a separate
  binding that the caller's substitution pass never visits — which is §2's rule.

Rejected:

| Design | Why not |
|---|---|
| An implicit `let` per function, prepended to every query | Lexical — answers 7 where Kusto answers 100 (§2, last row) |
| DuckDB table macros (`CREATE MACRO … AS TABLE`) | The body is frozen as SQL when it is defined, so nothing decided at the call — capture, the database, a pinned `query_now` — can reach it, and the translator cannot see its columns |
| Substituting the body into the query *text* | Names collide with the caller's, and error positions stop pointing at what the user wrote |

### 3.2 Name resolution at a call site

In order: the caller's `let`s (exact name) → the functions registered for the
database in context → tables. A registered function beats a same-named table,
as in Kusto. A bare name is a call only when no parameter is required; a bare
name for a function that has one is refused (SEM0219), **never** read as the
table of that name. Unknown names in a call are refused (SEM0260) with the
registered names listed, never treated as a table.

### 3.3 Databases

Definitions are keyed by `(database, name)`, where the database `None` means
"whichever database the query runs in". A body's unqualified tables, and its
nested calls, resolve in the function's database — except a name a caller's
`let` captures. For a call in the current database nothing needs doing:
`qualify()` already gives unqualified names the `database=` default and already
skips `let`-bound names. For `database('D').F()` the expansion qualifies the
body's tables with `D` itself, skipping the same names. `cluster(...)` resolves
through the existing `clusters=` map or is refused: never a remote call.

### 3.4 Scalars inside a body

The parameters, bound to the call's arguments, and the body's own `let`s seed
the body's scalar scope — the caller's do not. At a pipeline position each is a
`let`-bound name like any other, so a column of the same name wins (§4.2, which
this depends on).

### 3.5 Errors

| Case | Answer |
|---|---|
| Unknown function in a table position | Refused, naming the registration mechanism and listing what is registered for the database (a case-only mismatch is pointed out) |
| Wrong number of arguments, or a bare name with required parameters | Refused, SEM0219's wording |
| Recursion | Detected on the expansion stack; refused naming the cycle, `A → B → A`, SEM0057's wording |
| A built-in's name | Refused at registration, SEM0515 |
| An unsupported signature or body | Refused at registration, so a bad fixture fails at the line that registered it |
| A table the body reads that does not exist | Fails at the call, as `skipvalidation` does in Kusto — loudly, from DuckDB or from the schema |

## 4. Two bugs this depends on, fixed first

Both are silent wrong answers in plain `let` handling, found while measuring the
above. Neither was recorded anywhere. Each lands as its own commit with its trap
test.

### 4.1 A `let` captured a table whose name differs only in case

```
let fpt = datatable(Value:long)[100]; FpT | summarize s = sum(Value)
    Kusto 7 (the table)     here 100 (the let)
```

DuckDB matches quoted identifiers case-insensitively, so a CTE named `fpt`
answered for the table `FpT` (R7). The same capture made a generated stage name
like `_s0` answer for a user's table of that name.

**Fix:** a tabular `let` is rendered as a CTE under a reserved, collision-free
name, and a table reference reaches it only by **exact** KQL name and only
where the binding is in scope — so the choice is made by the translator in
KQL's terms and never by DuckDB's name resolution. Each binding's body sees
only the bindings declared before it. Stage prefixes avoid every name the query
reads as a table. The SQL of every query with a tabular `let` changes, only in
its CTE names.

### 4.2 A scalar `let` shadowed a column of the same name

```
let Value = 5; datatable(Value:long)[7] | extend k = Value
    Kusto k = 7 (the column)     here k = 5 (the let)
```

Measured rule: **a name refers to a column of the operator's input if there is
one, and otherwise to the `let`** — in `extend`, `where`, `summarize`, a
`bin(x, n)`, a same-`extend` assignment (`extend v = 10, k = v` gives 5, R21),
an `invoke` body, a tabular `let` body, and against a declared **query
parameter** too (7, not 5). Not in a constant position (`take n`, `top n` use
the `let`), not in a scalar `let` function's closure (8: the closure wins), and
not against a function's own parameters and locals (they win). A bare reference
is named by the `let` in `project`/`extend` (`project v` gives a column `v`, not
`Column1`) and not in `summarize by` (`Column1`).

**Fix:** a top-level scalar `let` or query parameter used at a pipeline position
lowers to a `LetRef(name, value)` that keeps both, and translation resolves it
against the operator's input columns. Where those columns are **unknown** — a
table with no schema — the answer cannot be decided, so the reference is
refused with the same hint `join` gives: pass `schema=`, or use
`duckdb_kql.kql(con, …)`, which always has one.

## 5. The API

`functions=` beside `entity_groups=`, with the same shape of defaults:

```python
duckdb_kql.set_functions(prod_schema_script)   # process default, for a fixture
duckdb_kql.kql(con, query, functions={...})    # a call's map REPLACES it; {} is none
KustoClient(con, functions=...)                # per client
duckdb-kql serve --functions schema.csl        # per server; --functions Sales=sales.csl
```

- An entry is **Kusto's own export form**, verbatim: `.create-or-alter function
  [with (…)] Name(params) { body }` (`.create function` is accepted too). One
  string may hold several, separated by blank lines as a database script is — so
  the output of `.show database D schema as csl script` pastes across unchanged,
  the same argument that made entity-group entries KQL text.
- A string or list means the current database; a mapping keys them by database,
  `None` for the current one.
- Parsed and checked at registration. `folder`, `docstring` and
  `skipvalidation` are accepted; `view = true` is refused (§6); a name defined
  twice for one database is refused.
- `set_functions` / `get_functions` / `effective_functions` mirror the entity
  group trio, process-wide and not thread-local, and a call's argument replaces
  rather than merges.
- Threaded through `to_sql`, `kql` / `query` / `df` / `arrow` / `execute` /
  `script`, `KustoClient`, `serve`, and **every** branch of `to_sql` — the
  ingestion branch is the one that dropped `entity_groups` once.
- `.show functions` and `.show function F` answer from the registry.

## 6. Scope

Supported:

- tabular functions with **zero or more scalar parameters**, positional, with
  defaults;
- `F()` and bare `F`, anywhere a table can appear: a pipeline's source, a
  `join`/`lookup` right side, a `union` branch, an `in`/`has_any` subquery,
  `toscalar`, a `let` body, an ingestion source;
- nested calls, `database('D').F()`, `cluster(...).database(...).F()`;
- scalar `let`s inside a body.

Refused with a message saying so, for later:

- named arguments (`F(x=5)`);
- tabular parameters, and `invoke` of a stored function;
- **scalar** stored functions (`AddOne(x:long) { x + 1 }`);
- `view = true` (it changes what a wildcard matches);
- a tabular `let` inside a body;
- a query-local `let F = () { … }` — a different, lexical rule (§2), which must
  not share this path.

## 7. Out of scope: the management commands

`.create-or-alter function`, `.create`, `.alter` and `.drop` stay refused; the
refusal names `functions=`. Supporting them needs a place for the definitions
to live, and both candidates cost something: in the DuckDB file (Kusto's
semantics, but a metadata table hidden from `schema(con)`, `.show tables` and
wildcards, and a write), or in memory on a client (no pollution, but `kql()`
and `script()` have nowhere to put it and two clients on one file disagree).
`functions=` is the Layer 0 input either way, so this lands first regardless.

## 8. Tests

`tests/test_stored_functions.py` — one trap test per row of §2 that is in scope,
each quoting its measurement: capture (100, and the prepended-`let` design's 7),
the lexical query-local contrast, function over table, the function's database
(50, 9), recursion, arity and bare names, case, reserved names, columns through
a `join`, isolation of per-call and per-client maps and of `set`/`get`, and a
`cluster()` call with no map refused. `tests/test_let_case_capture.py` and
`tests/test_let_column_shadowing.py` for §4.
