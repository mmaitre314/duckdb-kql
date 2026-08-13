# Area: Public API, layering & typing

**Scope:** `src/duckdb_kql/__init__.py`, the three-layer boundary
(`engine.py`/`schema.py` *as seen from outside*), `errors.py`, `translate`'s
`TranslationResult`, `py.typed` and the type contract, `__all__` surfaces.
**Out of scope:** translation *semantics* (translation area), the Kusto client's
own API shape (kusto area).

**Read first:** the [charter](README.md), the package docstring in
`__init__.py`, and `CONTRIBUTING.md`'s "Things CI will check that are easy to
miss" table.

The promise of this area is that **the public surface tells the truth**: about
what it costs to import, about what types a caller gets, and about what it
raises. A library that ships `py.typed` and lies in a signature is worse than
one with no types — the caller is *told* their code is fine when it isn't. Same
failure mode as a wrong answer: it looks fine.

## The layering contract

The API is three layers, each adding exactly one dependency (§ package
docstring):

```
Layer 0  duckdb_kql          KQL text → DuckDB SQL.   antlr4 only.
Layer 1  duckdb_kql.engine   Run it.                  + duckdb
Layer 2  duckdb_kql.kusto    KustoClient drop-in.     + pandas
```

- [ ] **Importing `duckdb_kql` must not import `duckdb`.** Layer 1 names
  (`connect`, `kql`, `execute`, `df`, `arrow`) are resolved lazily via
  `__getattr__` / `_LAYER1`. Any new top-level function that eagerly imports
  `engine` (or `duckdb`, or `pandas`) breaks Layer 0 — an **S2**. Check new
  imports at module top and inside `to_sql`/`query_parameters` (they import
  `lower`/`translate`/`params` *inside the function* on purpose).
- [ ] **The lazy set stays in sync.** `_LAYER1`, `__all__`, and the
  `TYPE_CHECKING` re-export block must list the same Layer-1 names. A name in
  `__all__` but missing from `_LAYER1`'s `__getattr__` path is an
  `AttributeError` waiting for a caller; a name resolved lazily but absent from
  the `TYPE_CHECKING` import is `Any` to every type checker.
- [ ] **New optional deps are gated.** pandas/pyarrow/azure imports must sit
  behind a lazy path or a guarded `try/except`, never at import time of a lower
  layer.

## The type contract (`py.typed`)

The package ships `py.typed`, so its signatures are load-bearing for *callers'*
type checkers, which `mypy` on our own source cannot catch. `tests/test_typing.py`
checks from the outside — treat it as part of the public API.

- [ ] **No `Any` in a public signature unless declared and explained.** An
  unannotated function silently becomes `Any` for every caller (CONTRIBUTING).
  Where a value is honestly untyped (an ANTLR node, a `dynamic` document, a
  query-parameter value) it is annotated `Any` *with a comment saying why* —
  never left to inference.
- [ ] **Lazy re-exports keep real types.** The `TYPE_CHECKING` block exists so a
  checker sees `connect`/`kql`/`df`/… with their true signatures despite the
  runtime laziness. A new Layer-1 export added without a matching
  `TYPE_CHECKING` import regresses this silently.
- [ ] **Return types are honest subclasses.** `to_sql` returns a `str` subclass
  carrying `.parameters`/`.unbound`/`.parameter_declarations`. If a caller does
  `str(result)` or concatenates, the parameter payload must not vanish in a way
  that produces SQL missing its bindings. Check `with_parameters` preserves the
  string value.

## The error surface (`errors.py`)

- [ ] **The taxonomy is closed and meaningful:** `KqlSyntaxError` (parse),
  `KqlUnsupportedError` (recognized, untranslated), `KqlSchemaError` (unknown
  table/column or identifier collision). A new failure mode should map to one of
  these, not to a bare `ValueError`/`KeyError` leaking from an internal.
- [ ] **Every raise carries a name/construct and, where it can, a source span.**
  A refusal the caller can't locate is a poor refusal (charter principle 5).
- [ ] **`KqlError` hierarchy** — are the public error classes all reachable from
  `__all__` and all rooted at a common base so a caller can `except KqlError`?

## API ergonomics & docstrings

- [ ] **Docstrings are executable and honest.** `__init__.py` uses doctests
  (`>>> duckdb_kql.to_sql(...)`). A changed emission that isn't reflected in the
  doctest either breaks CI or, worse, the doctest was loosened to hide it — check
  which. Doctest output must match real output exactly.
- [ ] **Partial-binding contract.** `to_sql` deliberately *allows* translating
  without supplying every declared parameter (the SQL is worth reading alone);
  the missing names surface in `.unbound` and execution is where they error.
  A change that raises on unbound-at-translate-time breaks a documented promise.
- [ ] **Naming communicates intent** without needing the source (Google): does a
  new public name say what it does? Is a public/private split (`_`-prefix)
  correct for anything newly exposed?
- [ ] **`__dir__`/`__all__` hygiene** — anything new that's meant to be public is
  in `__all__`; nothing internal leaked in.

## What to enumerate as "checked clean"

Confirm: Layer 0 import stays duckdb-free (trace the import graph of any new
top-level symbol); `_LAYER1`/`__all__`/`TYPE_CHECKING` are consistent; no public
signature resolves to `Any`; every new failure path lands on a `Kql*` error with
a locatable message.
