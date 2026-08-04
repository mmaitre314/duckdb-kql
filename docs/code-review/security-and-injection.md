# Area: Security & injection safety

**Scope:** `src/duckdb_kql/params.py`, every path that turns a *value* or an
*identifier* into SQL text (`translate/`, `lower.py`, `engine.py` at the
emission seam), the CLI's handling of input files/paths (`cli.py`), and any
`con.execute`/`con.sql` call site.
**Out of scope:** whether the emitted SQL is *semantically* right (translation
area) — here we only ask whether an untrusted byte can change *what query runs*.

**Read first:** the [charter](README.md), the module docstring of `params.py`
(it states the security model precisely), and OWASP's injection guidance
(string concatenation into queries is the canonical defect).

The one-line threat model: **no caller-controlled byte may reach the SQL
string.** KQL's `declare query_parameters` binds values server-side; we
reproduce that property rather than approximate it — a declared parameter
becomes a prepared-statement placeholder and the value crosses through DuckDB's
binding API, so "the generated SQL contains no caller-controlled bytes, so
there is no escaping to get wrong." Every finding here is whether that property
still holds.

## The injection invariant

- [ ] **Values become placeholders, never text.** A `declare query_parameters`
  value must render as a DuckDB placeholder and travel via the binding dict, not
  be formatted into the SQL. The canonical protective test is
  `test_payload_never_reaches_the_sql_text` — a value like
  `' | project secret` must appear *nowhere* in `to_sql(...)`'s output. If a
  change makes any parameter value appear in the SQL string, that's an **S2**
  (S1 if it changes the query's meaning).
- [ ] **`slot` is generated, not derived from the caller's name.**
  `ParameterDeclaration.slot` is deliberately independent of `name`, because a
  KQL identifier can be an escaped name containing arbitrary text. A refactor
  that starts building the placeholder from `name` reopens the hole.
- [ ] **`dynamic` values stay values.** A dict/list crosses as JSON text via
  `json.dumps` (a total function on JSON-shaped input that emits no SQL syntax)
  and is cast back on the other side. Check that no code path stringifies a
  `dynamic` into inline SQL instead.
- [ ] **String literals from the *query itself*** (not parameters) are
  single-quoted with `''` escaping, uniformly. A literal path that forgets the
  doubling is an injection seam even when the input is "just the query," because
  a downstream tool may feed attacker-authored KQL.

## Identifier emission (the R7 seam, from the safety side)

Translation owns whether quoting is *correct*; security owns whether any
untrusted byte can break *out* of the quotes.

- [ ] **Every identifier is double-quoted with `"` doubled on escape.** A column
  or table name containing a `"` must be escaped (`"a""b"`), or it breaks the
  quoting and becomes an injection vector. Check the identifier-emit helper has
  one code path and everything routes through it — a second, un-escaping path is
  the classic regression.
- [ ] **Collisions raise, not resolve.** Two KQL identifiers that fold together
  in DuckDB must raise `KqlSchemaError` (R7), never be silently merged — a merge
  is a wrong answer, and a resolver that guesses is worse than a refusal.

## The parameter-coercion boundary (`params.py` `coerce`/`bind`)

This is defense-in-depth: even though values are bound not spliced, coercion is
where a type-confused value is caught before it reaches the engine.

- [ ] **Type mismatch refuses, never coerces silently.** A float handed to a
  `long`, a `true` handed to a number — refused with `KqlSchemaError`, not
  truncated/reinterpreted. "Picking one silently is how wrong numbers get into
  reports."
- [ ] **Unknown supplied name is refused** (`bind`): a value for an undeclared
  parameter raises — it's far more likely a typo (and a filter that silently
  does nothing) than an intentional no-op. A change that downgrades this to a
  warning weakens a real guard.
- [ ] **Parsers don't over-accept.** `parse_timespan`, `_to_datetime`,
  `normalize_type` — do the regexes/`fromisoformat` paths reject junk, or can a
  crafted string slip through as a surprising value? A `datetime` parser that
  accepts a partial match is a data-integrity bug.
- [ ] **`int8` (and friends) stay refused.** `_TYPE_ALIASES` deliberately omits
  legacy/ambiguous spellings; adding one back to be "helpful" reintroduces the
  silent-wrongness the project refuses.

## CLI and process-level surface (`cli.py`)

- [ ] **Path and file handling.** Does the CLI open/write paths from arguments
  without following untrusted symlinks into somewhere surprising, and without
  a path-traversal join? Output written where the user didn't intend is a
  safety issue.
- [ ] **No shell/`eval`/`exec` on input.** The CLI does build-time translation;
  it must never execute translated SQL or shell out with interpolated input.
- [ ] **The CLI does not import `duckdb`** — also a layering rule, but a security
  one too: the build-time image is meant to be minimal, and a smaller surface is
  a smaller attack surface.

## Dependency & secret hygiene

- [ ] **No secrets, tokens, or connection strings** committed or logged. The
  Kusto client takes a local `.duckdb` path, not a cloud credential — confirm no
  change starts accepting or echoing one.
- [ ] **New dependency justified and pinned appropriately.** The ANTLR runtime is
  pinned to match the generator (`grammar/UPSTREAM.md`); a new runtime dep in
  Layer 0 widens the trusted base — flag it.

## What to enumerate as "checked clean"

The exact set of call sites that build SQL text and, for each, what makes its
inputs safe (placeholder-bound / translator-constructed / escaped-and-quoted).
Name the payload strings you traced through `to_sql` and confirmed absent from
its output.
