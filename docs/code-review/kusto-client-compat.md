# Area: Kusto client compatibility (Layer 2)

**Scope:** `src/duckdb_kql/kusto/` — `client.py`, `_models.py`,
`client_request_properties.py`, `helpers.py`, `response.py`, `exceptions.py`,
`__init__.py`.
**Out of scope:** the translation itself (translation area) and the injection
model (security area) — here the question is *fidelity to `azure-kusto-data`*.

**Read first:** the [charter](README.md), `docs/kusto-client.md`, and the
package docstring's Layer-2 example. The real SDK being mirrored is
`azure.kusto.data`.

This layer is a **drop-in**: code written against `azure-kusto-data` should run
against ours by changing an import. The charter's rule mutates here into: a
drop-in that is *nearly* shaped like the original is worse than an honest gap,
because the caller's existing code silently does something subtly different.
Every check is "does a caller who believes this is `azure-kusto-data` get what
they'd get from the real thing — or a clean, named failure where they wouldn't?"

## Shape fidelity

- [ ] **Public shapes match the SDK.** `KustoClient`, `ClientRequestProperties`,
  `KustoResponseDataSet`/`primary_results`, the result-table row/column access —
  method names, attribute names, and call signatures should match
  `azure-kusto-data`. A renamed attribute or a positional/keyword difference
  breaks the drop-in claim.
- [ ] **`execute(database, query, properties=...)`** accepts what the real
  client accepts and returns a response whose `.primary_results[0]` iterates
  rows the same way. Check `dataframe_from_result_table` produces the same
  column order and dtypes a caller expects.
- [ ] **Unsupported-but-shaped surface refuses loudly.** Where we expose a method
  or property that the SDK has but we can't back (management commands, streaming,
  cloud auth), it must raise a clear error naming the gap — never return an empty
  or plausible-looking stub. A stubbed `primary_results` that's silently empty is
  the S1 of this layer.

## Request properties (`client_request_properties.py`)

- [ ] **`OPTION_SUPPORT` matches the documented table.** CI checks the
  documented request-option table against `OPTION_SUPPORT`
  (CONTRIBUTING → "easy to miss"). A new option added to code but not docs (or
  vice-versa) is a doc-lies-about-code defect. Verify the two still agree.
- [ ] **Unsupported options don't silently no-op.** If a caller sets an option we
  don't honour (e.g. a server-side timeout, a result-truncation limit), does it
  raise or is it dropped? A dropped `truncation`/`limit` option can turn a safe
  query into one that returns partial or oversized results without warning.
- [ ] **`set_option`/parameter passing** — client request parameters that map to
  KQL `declare query_parameters` must flow through the *binding* path
  (security area), not string interpolation.

## Error and exception mapping (`exceptions.py`)

- [ ] **Exceptions mirror the SDK's hierarchy** enough that a caller's
  `except KustoServiceError` (or equivalent) still catches. A translation
  failure surfacing as a raw `KqlUnsupportedError` where the SDK would raise a
  `KustoServiceError`-shaped error may escape a caller's handler.
- [ ] **The mapping is honest about origin.** A local translation refusal and a
  genuine query error are different things; the message should make clear which,
  so a caller isn't debugging a "server" error that's really "we don't translate
  this."

## Response and models (`response.py`, `_models.py`)

- [ ] **Column metadata is faithful.** Column names and declared types in the
  result table drive downstream dataframes; a wrong type label (e.g. a KQL
  `datetime` surfaced as naive-local instead of UTC, R8) propagates into the
  caller's analysis.
- [ ] **`_register_with_sdk` / optional `azure` import is guarded.** `_models`
  touches `azure.kusto.data` only inside a guarded import; it must never become a
  hard dependency (mypy override confirms it's optional). A new unguarded
  `import azure...` breaks install for everyone without the SDK.
- [ ] **Empty/`None`/multi-table results** are handled: no result rows, a query
  that returns zero tables, multiple result sets — each should match the SDK's
  behaviour or refuse clearly.

## Helpers (`helpers.py`)

- [ ] **`dataframe_from_result_table`** requires pandas (Layer 2's added dep) —
  is that dependency reached only here and only when called, so Layer 1 users
  don't pay for it?
- [ ] Data conversions (row → dataframe) preserve nulls as nulls and don't
  coerce types in a way that diverges from the SDK.

## What to enumerate as "checked clean"

The list of `azure-kusto-data` surfaces this layer claims to mirror, and for
each: matched exactly / refuses cleanly with a named gap / **diverges silently**
(the only bad outcome). Confirm `OPTION_SUPPORT` and its doc table still agree,
and that no `azure`/`pandas` import escaped its guard.
