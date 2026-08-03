# Gap analysis — Azure Monitor transformation KQL

> Tracked live. Run `python tools/check_profile.py` for the current numbers;
> `tests/test_profile_azure_monitor.py` fails the build if coverage regresses or
> the gap list drifts.

## Why this profile

[Azure Monitor's data-collection transformations][doc] publish an exact list of
the KQL they support. That makes it a **useful external yardstick** — a real
product's real subset, published and dated, rather than our own idea of what
matters. Measuring against it turns "how much KQL do we handle?" from an
unanswerable question into a number that moves.

It is a *narrow* subset by design: transformations run **row by row**, so
`summarize`, `join`, `sort` and `take` are deliberately absent from it. We
support those anyway — the profile is a floor to clear, not a ceiling.

| | |
|---|---|
| Source | [`data-collection-transformations-kql`][doc] ([markdown][md]) |
| Doc dated | 2026-05-15 |
| Captured | 2026-08-03 |
| Probes | 119 |
| **Passing** | **114 (96%)** |

## How it is measured

Coverage is **measured, not declared**. Every entry in
[`tests/profiles/azure-monitor.json`](../tests/profiles/azure-monitor.json)
carries a `probe` — a KQL snippet that has to translate *and* execute on DuckDB.
A function sitting in the registry but broken in practice counts as missing,
which is the point: the registry is a claim, the probe is the evidence.

The gaps are **enumerated, not counted**, in
`tests/test_profile_azure_monitor.py::KNOWN_GAPS`. That list is checked in both
directions — a gap that starts passing fails the build (remove it), and an
unlisted failure fails the build too (it is a regression, or a feature upstream
added since this snapshot). Neither can hide inside a percentage.

## Coverage

| Group | Passing |
|---|---|
| Tabular operators | 7/9 |
| String operators | 23/23 |
| Bitwise functions | 6/6 |
| Conversion functions | 9/9 |
| Datetime/timespan functions | 23/23 |
| Dynamic & array functions | 6/7 |
| Mathematical functions | 16/16 |
| Conditional functions | 4/4 |
| String functions | 17/17 |
| Type functions | 3/3 |
| Transformation-only functions | 0/2 |

## The five gaps

| Gap | Why it is open |
|---|---|
| `parse` operator | Needs a pattern-to-regex compiler. Worth doing — it is also ~27 corpus cases — but it is a feature, not a mapping. **Most valuable next step.** |
| `columnifexists` | Needs the input schema at translation time to decide whether the column exists. The plumbing exists (`join` uses it); this just is not wired through. |
| `parse_xml` | DuckDB has no XML parser. Would need a Python UDF, like `xxhash64`. |
| `parse_cef_dictionary` | Azure Monitor only — not KQL proper, so the emulator cannot supply ground truth for it either. |
| `geo_location` | Azure Monitor only, **and it calls an external IP geolocation service**. Out of scope for an offline transpiler by construction. |

Two of the five are not really ours to close: `parse_cef_dictionary` and
`geo_location` exist only inside Azure Monitor, and the second one is a network
call. A realistic ceiling for this profile is **117/119**.

## What building it found

Every mapping added here was verified against the Kusto Emulator rather than
inferred from the docs. That caught six things:

- **`now()` and `ago()` were broken.** DuckDB's `now()` returns `TIMESTAMPTZ`,
  and rendering that needs a module which is not always present. KQL's `now()`
  is a naive UTC timestamp anyway. Found by the probes, not by the corpus — no
  frozen case exercised it, because `now()` is skipped as nondeterministic.
- **`%` is a mathematical modulo**: always non-negative. `-10 % 4` is `2` in KQL
  and `-2` in DuckDB.
- **`extract`'s argument order is reversed.** KQL takes `(regex, group, text)`,
  DuckDB takes `(text, regex, group)`.
- **`startof*`/`endof*` take an optional offset** in whole periods. Ignoring it
  returns a plausible datetime for the *wrong* period.
- **KQL weeks start on Sunday**; DuckDB's `date_trunc('week')` starts Monday —
  a one-day error for every Sunday.
- **`endof*` is the last instant _inside_ the period**, not the start of the
  next one.
- **`make_datetime` truncates** the sub-second part where `make_timestamp`
  rounds, landing a microsecond out.
- **`zip` needs positional indexing**: DuckDB's `list_zip` builds STRUCTs, so it
  renders as `[{"":1,"":3}]` rather than `[[1,3]]`.

Two things were deliberately **refused** rather than approximated:

- **`datetime_part('nanosecond')`** — KQL keeps 100ns ticks, DuckDB stores
  microseconds. The last digit is not there to return, and someone asking for
  nanoseconds wants that precision.
- **`hash()` / `hash_xxhash64()`** — xxhash64, which DuckDB lacks. Its own
  `hash()` is a *different* function, so mapping to it would return
  plausible-looking wrong digests.

One divergence is recorded rather than fixed:
`base64_decode_tostring` of bytes that are **not valid UTF-8** returns `''` in
KQL and escaped bytes in DuckDB. DuckDB has no UTF-8 validity predicate to
switch on. Valid UTF-8 — the case that matters — is correct.

## Adding another profile

`tools/check_profile.py --profile <file>` takes any file in the same shape, so
Sentinel's or Log Analytics' published subsets could be tracked the same way.

[doc]: https://learn.microsoft.com/en-us/azure/azure-monitor/data-collection/data-collection-transformations-kql
[md]: https://github.com/MicrosoftDocs/azure-monitor-docs/blob/main/articles/azure-monitor/data-collection/data-collection-transformations-kql.md
