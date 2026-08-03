# Test Plan — `duckdb-kql`

> Status: draft (2026-08-02). Companion to
> [`implementation-plan.md`](./implementation-plan.md). This document addresses
> the project's biggest risk: **KQL is a huge language, and "equivalent"
> KQL/DuckDB operators and functions differ in subtle, silent ways.** A
> hand-written test suite can't cover that surface. The strategy here is to
> **harvest** a large acceptance corpus from Microsoft's own docs and from
> existing KQL implementations, run it **differentially** against reference
> engines, and drive implementation order from real usage signal.

> Related: [`lessons-from-bun-rewrite.md`](./lessons-from-bun-rewrite.md) — Bun's
> rewrite succeeded because a language-independent test suite already existed to
> act as the conformance gate, which independently validates this plan's
> corpus-first sequencing (§10). Its proposals here: treat the harness as a hard
> gate, emit a **ranked worklist** from failing `xfail` cases (a ticket system),
> implement **family-at-once** within each wave (§8), and never report a bare pass
> percentage or weaken a test to make it pass.

## 1. The core risk this plan exists to manage

Two independent problems:

1. **Breadth.** KQL has ~100+ tabular operators/plugins, 200+ scalar functions,
   40+ aggregations, plus statements, literals, and type semantics. We cannot
   hand-author enough tests to be confident.
2. **Silent behavioral divergence.** Many KQL constructs have a DuckDB analogue
   that is *almost* the same — and the differences don't error, they return
   subtly wrong results. A non-exhaustive catalog of known traps is in §6; it is
   the heart of why "it parses and runs" is nowhere near "it's correct."

The plan attacks both: **automated corpus harvesting** for breadth (§3–§4), and
a **differential oracle + a curated trap catalog** for divergence (§5–§6).

## 2. Test architecture (six layers)

| Layer | Question it answers | Source of cases | When |
|-------|--------------------|-----------------|------|
| L1 Parse/round-trip | Does it parse without crashing? | Every KQL string we can scrape (docs + libs) | CI, every push |
| L2 Golden translation | Does KQL→SQL emit the SQL we expect? | Small curated set | CI, every push |
| L3 Acceptance (docs) | Do we match Microsoft's documented outputs? | Scraped `dataexplorer-docs` examples | CI, every push |
| L4 Differential (oracle) | Do we match the *real KQL engine's* results? | Same corpus, run on the **Kusto Emulator** (§5.1); optional ClickHouse-KQL/KustoLoco/kql-to-sql cross-checks | CI-only / nightly |
| L5 Trap catalog | Do we get the known-divergent cases right? | Hand-authored from §6 | CI, every push |
| L6 Fuzz/metamorphic | Does it stay robust on odd inputs? | Grammar-driven + mutations | Nightly |

L3 and L5 are the correctness spine; L4 is what scales correctness beyond what
the docs pin down; L1/L6 keep the parser honest; L2 catches regressions fast.

## 3. Corpus harvesting — Microsoft docs (primary acceptance source)

**Source:** [`MicrosoftDocs/dataexplorer-docs`](https://github.com/MicrosoftDocs/dataexplorer-docs),
path `data-explorer/kusto/query/` — one markdown file per operator/function, plus
management and reference pages. This is public, versioned markdown we can pin to
a commit and re-scrape.

**What the pages give us (confirmed by inspection):**
- KQL queries in fenced ` ```kusto ` code blocks.
- **Expected output rendered as markdown tables** (often truncated: "first N rows").
- A run-link pattern `> [!div class="nextstepaction"]` → `dataexplorer.azure.com`
  with the URL-encoded query (a second way to recover the exact query text).

**Important structural catch:** most examples reference **shared sample
datasets** (`StormEvents`, `Covid19`, `demo_*`, etc.) rather than inline data, so
the output table alone isn't runnable without those datasets. The corpus
therefore splits into:

- **Self-contained examples** — queries whose input is *in* the query:
  `datatable(...)`, `print`, `range`, `let T = datatable(...)`. These are
  **directly runnable and checkable** against the documented output. **Prioritize
  extracting these** — they need no external data and give exact input→output
  pairs.
- **Sample-DB examples** — need `StormEvents` et al. loaded into DuckDB as
  fixtures (§4.3). Higher value (realistic) but require the datasets and tolerate
  the docs' row-truncation.

**Extraction tool (`tools/harvest_docs.py`, dev-time):**
1. Pin a `dataexplorer-docs` commit; walk `query/**.md`.
2. For each page, pair each ` ```kusto ` block with the following output table (if
   present) and its run-link.
3. Classify self-contained vs sample-DB (detect `datatable`/`print`/`range` vs
   bare table refs).
4. Tag each case with the operators/functions it exercises (from the page it
   lives on + a lightweight token scan) — feeds the coverage matrix (§7).
5. Emit machine-readable case files (§4.1).
6. Normalize the documented output tables (types, truncation markers) into
   expected results.

**Licensing (verified — see [`licensing.md`](./licensing.md)):** `dataexplorer-docs` is dual-licensed —
prose **CC-BY-4.0** (`LICENSE`), code samples **MIT** (`LICENSE-CODE`). Since the
example **output tables are prose**, our policy is to **harvest only the queries
(MIT) and generate expectations ourselves on the emulator**, never committing doc
output tables. Attribution + `NOTICE` + per-case provenance still apply.

## 4. Corpus format, fixtures, and sample data

### 4.1 Case file schema (one YAML/JSON per case, or sharded files)
```yaml
id: summarize-count-by-01
source: https://github.com/MicrosoftDocs/dataexplorer-docs/.../summarize-operator.md
source_commit: <sha>
kql: |
  StormEvents | summarize count() by State | sort by count_ desc | take 3
fixtures: [StormEvents]          # or inline: true if datatable/print/range
expected:                        # documented or oracle-derived
  columns: [State, count_]
  rows: [[TEXAS, 4701], [KANSAS, 3166], [FLORIDA, 1042]]
tags:
  operators: [summarize, sort, take]
  functions: [count]
status: xfail                    # supported | xfail(unsupported) | skip(needs-oracle)
oracle: docs                     # docs | adx | clickhouse-kql | kustoloco | kql-to-sql
notes: docs table truncated to first 3 rows
```
- `status: xfail` for not-yet-implemented constructs means **adding coverage is
  free** — cases flip green automatically as features land, and the coverage
  matrix (§7) is generated from these files.
- `tags` are the join key between tests and the coverage/prioritization matrix.

### 4.2 Comparison semantics (avoid false failures)
Result comparison must be tolerant where KQL itself is:
- **Order-insensitive** unless the query has a terminal `sort`/`top` (row order is
  otherwise undefined in both engines).
- **Type-normalized** (KQL `long`↔DuckDB `BIGINT`, `real`↔`DOUBLE`,
  `datetime`↔`TIMESTAMP`, `dynamic`↔`JSON`).
- **Float tolerance** (approximate aggregations: `dcount`, `percentile` — compare
  within epsilon or assert "approx" only).
- **Truncation-aware** for docs outputs that show only the first N rows (compare a
  prefix after an explicit sort, or re-derive full expected via the oracle).

### 4.3 Sample datasets as DuckDB fixtures — **implemented**

`StormEvents` is referenced by 251 corpus cases and `PopulationData` by a
handful more; together they were the single biggest blocker to coverage.
[`duckdb_kql/fixtures.py`](../src/duckdb_kql/fixtures.py) generates both
deterministically and loads them into **both** engines
(`tools/make_fixtures.py [--load]`).

The data is **synthetic**, for two reasons: the real CSV lives behind
`kustosamples.blob.core.windows.net`, which is not reachable from every
environment, and vendoring NOAA/Microsoft sample data adds a licensing question
we do not need. It costs us nothing we were relying on — Microsoft's *published
outputs* were never the oracle (§3), the **emulator** generates expectations
from whatever data it is given. Stated plainly: numbers here will not match the
outputs printed in the docs.

Two properties the fixture must hold, both asserted by
[`tests/test_fixtures.py`](../tests/test_fixtures.py):

- **Non-vacuity.** `State == "FLORIDA"` against a fixture with no Florida rows
  returns empty on both sides and *passes* while proving nothing — the most
  expensive kind of green test. The vocabularies are therefore the real ones
  (actual states, actual NOAA event types, 2007 dates), chosen to cover every
  literal the corpus filters on, and each is asserted to select a strict subset.
- **No ties in sort keys.** `sort`/`top` break ties arbitrarily, so duplicate
  keys make a deterministic query produce engine-specific output and the suite
  reports a divergence that is really just a tie. `StartTime` is unique and
  `DamageProperty` is drawn from a wide range rather than a handful of values.

### 4.4 Sample datasets — general
- `StormEvents` (the canonical ADX demo table) is publicly downloadable as CSV;
  load into DuckDB once and cache as Parquet under `tests/fixtures/`. Add others
  as needed (`Covid19`, `demo_make_series1`, etc.).
- A `tests/fixtures/loader.py` materializes each sample DB into a DuckDB
  connection so both our engine and any local oracle query identical data.
- Keep large fixtures out of git (download+cache in CI; check in only small ones).

## 5. Differential testing against reference engines (L4)

The docs pin *documented* outputs; the **oracle** pins *everything else*. Run the
same KQL against a reference KQL engine and against `duckdb-kql`, compare with the
§4.2 semantics. Candidate oracles, best-fidelity first:

| Oracle | Fidelity | Cost / notes | Role |
|--------|----------|--------------|------|
| **Kusto Emulator (Docker)** ⭐ | **Ground truth** — the real MS engine, "understands KQL the same way the Azure service does" | Local Docker, free (EULA); **x64 only**, no cloud/auth needed | **Primary oracle** — generate canonical expected results for the whole corpus |
| Real ADX / Fabric free cluster | Ground truth | Network, auth, rate limits | Spot-check / cross-cloud confirmation only |
| `saoc90/kql-to-sql` (→ DuckDB SQL) | High for covered set; built on official parser, but a *translator* not an engine | .NET, MIT; runs *on DuckDB* so data matches exactly | Translation cross-check |
| KustoLoco / BabyKusto | Real engine, but a *reimplementation* | .NET, MIT; own semantics | Secondary engine oracle |
| ClickHouse KQL dialect | Partial, **experimental** | C++; Apache-2.0; diverges from ADX | Extra signal only, not ground truth |

### 5.1 The Kusto Emulator — our ground-truth oracle ⭐

The user's suggestion is the key unlock. Microsoft ships the **Kusto Emulator**
(a.k.a. `kustainer`), a Docker image that runs the **real Kusto query engine**
locally and — per Microsoft — *"understands KQL the same way the Azure service
does."* That makes it **ground truth**, unlike a translator (`kql-to-sql`) or a
reimplementation (`KustoLoco`, ClickHouse). It removes the only bad option we had
(cloud ADX with auth/network) and lets us derive expected results for the *entire*
harvested corpus, not just the examples whose output the docs happen to print.

**Mechanics (all confirmed from MS docs):**
- Image: `mcr.microsoft.com/azuredataexplorer/kustainer-linux:latest`
  (the Docker Hub `microsoft/kusto` listing).
- Run: `docker run -e ACCEPT_EULA=Y -m 4G -d -p 8080:8080 -v <host>:/kustodata -t <image>`
- Endpoint: plain **HTTP on `:8080`** — query at `/v1/rest/query`, management at
  `/v1/rest/mgmt`. **No HTTPS, no Entra auth** → trivial to automate: point
  `azure-kusto-python` at `http://localhost:8080` with a connection string that
  drops `AAD Federated Security`.
- Load data with **`.create table …`** + **`.ingest into table … (@"/kustodata/…")`**
  from the mounted volume (and inline `datatable`/`.set-or-append` since it's the
  real engine). This lets us ingest the **exact same fixtures** we load into DuckDB.
- Supports "all commands and queries within its architecture limitations."

**Constraints to design around:**
- **x64 only** (needs SSE4.2/AVX2); **ARM not supported** → CI must use x64 Linux
  runners (GitHub `ubuntu-latest` is fine); **Apple-Silicon dev machines can't run
  it natively** (emulation is slow) — so the emulator is a *CI/generation* tool,
  and frozen expectations (below) keep local dev unblocked without it.
- No queued/managed ingestion, no cross-cluster/external data, transient unless
  the volume is mounted, different performance profile — none of which matter for
  correctness fixtures.
- **Licensing: reviewed and approved** — see [`licensing.md` §5](./licensing.md).
  Free under the MS Software License Terms with `ACCEPT_EULA=Y`. Binding
  constraints on this design: **never redistribute the image** (CI pulls from MCR
  by digest), **never publish performance numbers** from it, and keep it
  **dev/CI-only — never a runtime dependency**.

### 5.2 Freeze-and-compare workflow
1. `tools/regen_expectations.py` spins up the emulator (via Docker Compose or
   `testcontainers`), ingests the shared fixtures (§4.3), runs every corpus case,
   and captures its result table.
2. Those results are **frozen into the case files** as `expected` (with
   `oracle: kusto-emulator` + the image digest for provenance).
3. Per-push CI runs the fast **L3** comparison against the *frozen* expectations —
   **hermetic, pure-Python, DuckDB-only, no Docker/.NET/network**.
4. A **nightly** lane re-runs `regen_expectations.py` on x64 and flags any drift
   (engine version bumps, our fixture changes). Oracles are **never runtime
   dependencies** — CI/generation only.

This gives us ADX-faithful expectations for the whole corpus while keeping the
everyday test loop fast and dependency-free. A dedicated **Testcontainers Kusto
module** exists (and `testcontainers-python` can run the image generically), so
the emulator lifecycle in CI is a solved problem.

**Also harvest existing libraries' test corpora** (their inputs *and* expected
outputs), converting into our case format:
- **`kql-to-sql`** — `KqlToSql.DifferentialTests`, `KqlToSql.Tests`,
  `KqlToSql.DuckDbExtension.Tests`, `IntegrationTests`, plus its `Fuzzer`. Its
  `DifferentialTests` are precisely the KQL-vs-reference shape we want. (MIT.)
- **ClickHouse** — `tests/queries/0_stateless/*kusto*/*kql*` `.sql` + `.reference`
  files: KQL in, fully-expanded expected rows out. (Apache-2.0.)
- **`microsoft/Kusto-Query-Language`** — parser test inputs: excellent **L1 parse
  corpus** (breadth of syntax), even without result expectations. (Apache-2.0.)
- **KustoLoco** — C# test cases (semantics). (MIT, verified.)

Each import records provenance + upstream license in `NOTICE`.

## 6. Behavioral divergence catalog (L5 — hand-authored trap tests)

These are the "almost the same" cases that silently return wrong results. Each
bullet becomes one or more targeted tests. Crucially, we **don't hand-assert the
expected values** — we author the *queries*, then let the **Kusto Emulator (§5.1)
supply the ground-truth output**, so the catalog documents ADX's real behavior
rather than our guess about it. This list is the **living registry of known
KQL↔DuckDB semantic gaps** and should grow as we find more.

**Joins & set ops — `join` measured and implemented, pinned by
[`tests/test_join.py`](../tests/test_join.py)**
- **CONFIRMED — `join`'s default kind is `innerunique`**, which de-duplicates the
  *left* key set before joining. Emitting `INNER JOIN` is wrong. Measured with a
  left side holding two `'a'` rows against a right side holding two `'a'` rows:
  **innerunique gives 2 rows, inner gives 4**. All nine kinds are implemented and
  their row counts pinned.
- **Output schema.** KQL keeps *both* key columns and suffixes the right side's
  colliding names: `k`→`k1`, and `k1`→`k2` when `k1` is taken. No separator, so
  `k_1` would be wrong. Semi/anti kinds return **one side's columns only**.
  Reproducing this needs both sides' column names, which is why `join` — alone
  among the operators — requires a schema; `duckdb_kql.sql(con, …)` supplies it
  from the connection, and `to_sql()` without one raises `KqlSchemaError` rather
  than guessing.
- **Open — `innerunique` picks an arbitrary left row per key.** DuckDB's
  `DISTINCT ON` kept the same row as the emulator on every probe, but neither
  engine *promises* which one survives. A left side with duplicate keys whose
  other columns differ is therefore engine-specific.
- `hint.strategy` / `hint.shufflekey` tune how a *cluster* executes the join.
  They cannot change the result and DuckDB is single-node, so ignoring them is
  correct rather than a shortcut.
- **KQL has no null string distinct from empty**, so an outer join's unmatched
  string column is `''` from the emulator and `NULL` from DuckDB. The comparison
  normalizes this, but only where the *expected* column type says string.
- `union` column unification (differing schemas → superset columns, nulls filled);
  `kind=inner|outer`; `withsource=`.

**Aggregation (`summarize`) — measured, implemented, pinned by
[`tests/test_summarize.py`](../tests/test_summarize.py)**
- **KQL returns a neutral value where SQL returns NULL.** This is not just an
  empty-input edge case — it hits *any group whose values are all null*:

  | | KQL | DuckDB |
  |---|---|---|
  | `sum` / `sumif` | `0` | `NULL` |
  | `avg` | **`NaN`** | `NULL` |
  | `stdev` / `variance` | `0` | `NULL` |
  | `make_list` / `make_set` | `[]` | `NULL`, or a list *of* nulls |
  | `min` / `max` | `null` | `NULL` (agree) |
  | `count` / `dcount` | `0` | `0` (agree) |

- **`make_list`/`make_set` skip nulls;** DuckDB's `list()` keeps them.
- **`dcount` is documented as approximate but is exact at corpus
  cardinalities.** `approx_count_distinct` was ~13% low (32 vs 37), which also
  reorders `top N by dcount`. We emit `count(DISTINCT …)`: it matches the oracle
  and is reproducible.
- **`percentile` is nearest-rank, not interpolated.** `quantile_disc` matched
  all 52 groups exactly; `quantile_cont` was off by up to **39%** — a gap the 5%
  approximate-function tolerance would have hidden on smaller inputs.
- **R12 auto-naming** is not guessable and is user-visible: `count()`→`count_`,
  `countif(p)`→`countif_`, `sum(x)`→`sum_x`, `sum(x+z)`→`sum_`,
  `make_list(x)`→`list_x`, `make_set(y)`→`set_y`, `take_any(x)`→`x`,
  `percentile(x,50)`→`percentile_x_50`. Grouping keys are emitted **first**, and
  a key that is a function keeps the inner column's name (`bin(t,1d)`→`t`).
- **`bin()` bins from the Unix epoch.** DuckDB's `time_bucket` origin is
  2000-01-03, so using it shifts every bucket boundary (R8). Binning a
  *timespan* yields a timespan, not a 1970 date.
- **KQL has no null string distinct from empty**: `isnull('')` is false,
  `isempty('')` is true, and `string(null)` round-trips as `''`.

**Aggregation (`summarize`) — remaining**
- `count()` (all rows) vs `count(Expr)` (**ignores nulls**) vs `countif()`.
- `dcount`/`dcountif` are **approximate** (HLL) — never assert exact equality.
- `percentile`/`percentiles` use a specific estimation algorithm — approximate,
  needs tolerance and possibly a UDF/`quantile_cont` choice documented.
- `avg`/`sum`/`min`/`max` **ignore nulls**; empty group behavior.
- Implicit grouping-key null bucket; result column auto-naming (`count_`,
  `avg_x`) must match KQL's naming.
- `make_list`/`make_set` ordering and null handling; `make_set` distinctness.

**Strings (very high risk)**
- `==` is **case-sensitive**; `=~` is **case-insensitive**; `!=`/`!~` likewise.
- `has`/`hasprefix`/`hassuffix` are **term-based** (tokenized, whole-word) and
  **case-insensitive by default** — *not* substring; `contains` **is** substring
  (case-insensitive). `_cs` variants force case-sensitivity. Emulating `has`'s
  tokenization on DuckDB is nontrivial — dedicated tests required.
- `startswith`/`endswith` default case-insensitive.
- `substring` with negative/out-of-range indices; `strcat` null handling;
  `split` empty-segment behavior; `strlen` counts characters not bytes.

**Statements — `let` implemented, pinned by [`tests/test_let.py`](../tests/test_let.py)**
- `let` was the largest single blocker in the corpus (250 frozen cases) and is
  dangerous to get wrong: it is **not** a `QueryStatement`, so an implementation
  that counts query statements sees one statement, translates it, and silently
  **drops the binding** — a query that runs and returns the wrong rows.
- Scalar bindings are substituted into the IR (a `let` is a query-scope binding,
  not a column); tabular bindings become named CTEs, so a reference in the body
  needs no rewriting. `materialize()` is a caching hint for a distributed engine
  and unwraps.
- User-defined functions (`let f = (a:int) { … }`) still refuse.

**Datetime / timespan**
- **CONFIRMED — KQL timespan strings carry a day part** (`[-][d.]hh:mm:ss`) that
  DuckDB's `INTERVAL` cast rejects, so `totimespan('4.00:00:00')` was silently
  null. Also `totimespan(4d)` receives an INTERVAL, not a string.
- **CONFIRMED — dividing two timespans yields a number.** `dayofweek()` returns
  a *timespan*, not an integer, so `dow / 1d` means "how many days". DuckDB has
  no interval division, so this failed to bind rather than misleading.
- `datetime` is UTC; `bin()`/`floor` semantics vs DuckDB `time_bucket`/`date_trunc`
  origin; week/month binning edge cases.
- `ago()`, `now()` determinism within a query.
- timespan literals (`1d`,`90m`,`100ms`,`1tick`) → `INTERVAL`; arithmetic and
  formatting round-trips.
- `todatetime`/`totimespan` parsing of bad input → **null, not error**.
- **RESOLVED via oracle (2026-08-03)** — `todatetime` accepts date formats
  DuckDB's `TRY_CAST` rejects, and the field order is **`MM-DD-YYYY`**:
  `'12-02-2022'` → `2022-12-02`, while `'13-01-2022'` → `null`, which is what
  rules out `DD-MM`. Also accepted: `MM/DD/YYYY`, `MM.DD.YYYY`, `2 Dec 2022`,
  `Dec 2, 2022`, RFC-1123, and `YYYYMMDD`. Mapped via `try_strptime` fallback in
  `translate/functions.py`; 27/27 formats verified against the emulator. Pinned
  by `tests/test_datetime_traps.py`.
- **RESOLVED via oracle (2026-08-03) — the dangerous one.** `todatetime` with an
  explicit UTC offset **converts** to UTC; DuckDB's plain `TIMESTAMP` cast
  *accepts the same string* and silently keeps the local wall time.
  `'2022-12-02T13:45:56+02:00'` is `11:45:56` in ADX and was `13:45:56` for us —
  a wrong answer with no error, found only because the oracle was consulted on a
  format nothing in the corpus exercised. Fixed by casting via `TIMESTAMPTZ`.
- **Session `TimeZone` is part of the contract (R8).** DuckDB reads it when
  casting an offset-less datetime string, so identical SQL yields different
  answers on a non-UTC machine. Neither candidate formulation was found to be
  session-independent, so `duckdb_kql.sql()` forces `SET TimeZone='UTC'` and
  `to_sql()` documents the requirement for callers running the SQL themselves.

**Numbers & null propagation**
- Integer vs real division; `%` sign behavior; overflow (`long` 64-bit).
- Conversions (`toint`/`tolong`/`todouble`/`tobool`) on unparseable input → **null,
  not error** (DuckDB `CAST` would throw → must use `TRY_CAST`).
- Null propagation in arithmetic/comparison; `isnull`/`isnan`/`isfinite`.

**Dynamic / JSON — implemented, pinned by [`tests/test_dynamic.py`](../tests/test_dynamic.py)**
- **Navigation never errors** (R9): a missing property or an out-of-range index
  is null. A **negative index counts from the end** — DuckDB spells that
  `$[#-1]`, and a bare `$[-1]` silently returns null instead of the last element.
- `array_index_of` returns **-1** when absent, not null: `== -1` is how KQL
  queries test for absence, so a null would silently change the answer.
- `array_slice` endpoints are **inclusive** and count from the end for negatives.
- `array_sort_asc/desc` put **nulls last**, and the result must round-trip
  through `to_json` or SQL NULLs leak out as the invalid JSON token `NULL`.
- **`mv-expand` has three behaviours**: an array gives one row per element; an
  **object gives one row per key**, each a single-key bag; a **null gives one
  row**, while an **empty array gives none**.
- **`make_set` unions dynamic arrays** rather than collecting them — a column of
  `["A1","A2"]` and `["A2","C1"]` yields `{A1, A2, C1}`.
- **A `dynamic` column is typed `Object`** in the emulator's metadata, not
  `dynamic`. Missing that left every dynamic cell compared as raw JSON text
  against a decoded value.

**Strings, numbers, hashing — measured**
- **`%` is a mathematical modulo**: always non-negative. `-10 % 4` is `2` in KQL
  and `-2` in DuckDB — a silent wrong answer wherever negatives appear.
- **`tostring` uses .NET's spelling**, not SQL's: a datetime is
  `2020-01-01T00:00:00.0000000Z` (seven fractional digits and a `Z`), a bool is
  `True`/`False`, and a dynamic string is unquoted.
- **Hashing goes through that string form**, so the spelling is not cosmetic:
  `hash_md5(datetime(2020-01-01))` is the md5 of the .NET rendering. `md5`,
  `sha1` and `sha256` match the engine byte for byte. `hash()` and
  `hash_xxhash64()` are xxhash64 and **refuse** — DuckDB's own `hash()` is a
  *different* function, so mapping to it would return plausible-looking wrong
  digests, the worst possible outcome for a hash. First UDF candidate.
- Unnamed `project`/`extend` columns are numbered from **one** (`Column1`).

**Sources, membership, and visualization — implemented, pinned by
[`tests/test_range_in_render.py`](../tests/test_range_in_render.py)**
- **`range` includes BOTH endpoints.** DuckDB has a function literally named
  `range` that *excludes* the stop, so the obvious mapping is off by one row;
  `generate_series` is the inclusive one. A backwards range is empty in both.
- **`in~` / `!in~` are case-INsensitive** (they follow `=~`, not `==`). The
  right-hand side may also be a whole tabular expression — `x in (T | project c)`
  — which is what the vendored grammar patch (PATCH 001) exists to accept. Both
  sides are lowered for the `~` forms; lowering only one silently misses matches.
- **`render` is a no-op for results.** The emulator returns the primary result
  table unchanged and puts the chart hint in a separate metadata table, so
  dropping the operator is correct rather than a shortcut — asserted by
  comparing against the same query without it.
- **Duplicate `summarize` output names take a KQL suffix**: two `make_set(y)`
  produce `set_y` and `set_y1`. DuckDB's own de-duplication would give `set_y_1`.
- **`sort … | take N` fixes the key values, not which rows carry them.** Rows
  tied at the cut-off are the engine's choice, so only the sort-key sequence is
  comparable.

**Row-shaping operators**
- `take`/`limit` and bare `sample` are **nondeterministic order** — assert as sets.
- `top N by X` = sort+take with **nondeterministic tie-breaking**.
- `sort`/`order by` default **`desc`**; nulls-ordering convention.
- `extend` may **overwrite** an existing column; `project` reorders and can
  compute; `project-away`/`project-keep`/`project-rename` column-set effects.

**Identifiers & typing**
- KQL identifiers are **case-sensitive**; DuckDB is case-insensitive by default —
  column/table name collisions and quoting must be handled.
- Column type inference for `datatable`/`print` literals.

## 7. Coverage matrix & tying tests to the language surface

- Build the **full item inventory** by scraping the doc TOC/pages (§3): every
  tabular operator, scalar function (by family: string, datetime, math,
  dynamic/array, conversion, IP, geo, hash, window), aggregation, plugin, and
  statement → a canonical list in `docs/coverage-matrix.md` (generated).
- Every harvested/authored case carries `tags` (§4.1). A generator cross-joins
  tags × cases to produce, per language item: **# cases, # passing, # xfail,
  supported?**. This is published so users see exactly what works, and it makes
  "breadth" measurable instead of vibes.
- CI gate: implementing a feature must move its row from `xfail`→pass and must not
  regress others.

## 8. Prioritization plan (what to implement first, and why)

Rank each language item by a simple composite signal, then implement in waves.

**Priority signal = (A) doc-example frequency × (B) cross-library support × (C) inverse behavioral risk-to-value.**
- **(A) Frequency** — count occurrences of each operator/function across *all*
  harvested doc examples. High frequency = high user ROI. (The harvester already
  tags every case, so this is a free aggregation.)
- **(B) Cross-library support** — does `kql-to-sql` / ClickHouse-KQL / KustoLoco
  implement it? Implemented-by-many = proven worth + a ready oracle. `kql-to-sql`
  in particular already supports a very broad operator set (`where`, `project`,
  `extend`, `summarize`, `sort`, `take`, `top`, `join`, `union`, `distinct`,
  `parse`, `mv-expand`, `make-series`, `datatable`, `range`, `print`, `search`, …)
  — treat its **supported list as the "worth-it" whitelist**.
- **(C) Behavioral risk** — from §6. High-risk-but-high-frequency items (e.g.
  `join`, `has`, `summarize`) get implemented early *with* their trap tests;
  high-risk-but-rare items get deferred.

**Explicit "defer / hard" bucket (negative signal).** `kql-to-sql` deliberately
did **not** implement `facet`, `find`, `fork`, `invoke`, `macro-expand`,
`partition`, `reduce`, `project-by-names` — because they yield multiple result
sets, need function/catalog registries, or don't fit single-query SQL translation.
That's a strong signal to **defer these** (or design a special path) rather than
fight them early.

**Proposed waves** (refined once the frequency scan runs):
- **Wave 1 (MVP / M1):** `where`, `project`, `project-away`/`keep`/`rename`,
  `extend`, `take`/`limit`, `top`, `sort`/`order`, `distinct`, `count`,
  `summarize` (core aggregates), `union`, `join` (all kinds, incl. correct
  `innerunique`), `let`, `datatable`/`print`/`range`; core scalar families
  (string incl. `has`/`contains`, datetime incl. `ago`/`bin`, conversions,
  `iff`/`case`, null funcs). These are the highest-frequency, broadly-supported,
  self-contained-testable set.
- **Wave 2 (M2):** `dynamic`/JSON access, `mv-expand`/`mv-apply`, `parse`,
  `parse-where`, `make-series`, percentiles, richer datetime/regex, `search`,
  `getschema`.
- **Wave 3 (M3):** `evaluate` plugins (`bag_unpack`, `pivot`), `scan`, `top-nested`,
  `range`-based generation, user-defined `let` functions, `externaldata`/
  `datatable` edge cases.
- **Deferred:** the `kql-to-sql` "unsupported" bucket above.

Each wave ships with its slice of the acceptance corpus flipped from `xfail` to
required-pass, plus its §6 trap tests.

## 9. CI wiring & tooling

- **Every push (hermetic, x64 or ARM):** L1 parse, L2 golden, L3 acceptance
  (frozen expectations), L5 trap catalog. Pure Python, DuckDB only —
  **no Docker/.NET/network** — so it runs anywhere, including Apple-Silicon dev.
- **Nightly (x64 Linux runner):** L4 differential — spin up the **Kusto Emulator**
  and `regen_expectations.py` to regenerate/verify frozen expectations (with
  optional cross-checks against kql-to-sql-on-DuckDB / KustoLoco); L6 fuzz;
  coverage-matrix regeneration + drift check vs pinned `dataexplorer-docs`. Pin
  the emulator **image digest** so expectations are reproducible.
- **Dev tooling:** `tools/harvest_docs.py` (docs → cases), `tools/import_<lib>.py`
  (reference corpora → cases), `tools/gen_coverage.py` (cases → matrix),
  `tools/regen_expectations.py` (**Kusto Emulator** → frozen expected, via
  Docker Compose / testcontainers). All pin upstream commits/digests; all record
  provenance/license into `NOTICE`.
- **Fixtures:** `tests/fixtures/loader.py` for sample DBs; large data cached in CI,
  not committed.

## 10. Deliverables & sequencing

1. **Corpus & harness first (parallel with M0):** case-file schema, comparison
   engine (§4.2), `harvest_docs.py`, and the fixture loader. Land the *whole*
   scraped corpus as `xfail` immediately — so from day one we have a measurable
   breadth target and a green-lighting mechanism.
2. **Import reference corpora** (kql-to-sql, ClickHouse, MS parser inputs).
3. **Trap catalog (§6)** authored up front — these are cheap to write and are the
   highest-value correctness guards.
4. **Coverage matrix generator** + `docs/coverage-matrix.md`.
5. **Frequency scan → finalize Wave 1 list**, then implement per §8, flipping
   `xfail`→pass wave by wave.
6. **Kusto Emulator oracle job** (§5.1–§5.2) stood up early — even before Wave 1
   — so expected results for the whole `xfail` corpus (and the §6 trap catalog)
   are ground-truth from day one, and each feature flips `xfail`→pass against real
   ADX behavior rather than a hand-written guess.

## 11. Licensing & provenance

Moved to its own document: **[`licensing.md`](./licensing.md)**.

Summary of what it settles, for readers of this plan:
- The **frequency scan** (§8) redistributes nothing — it needs no sign-off.
- `dataexplorer-docs` is **dual-licensed**: queries are **MIT**, but prose —
  **including the example output tables** — is **CC-BY-4.0**. So we **harvest the
  queries and generate our own expectations** on the emulator, never committing
  doc output tables.
- All imported corpora and the vendored `Kql.g4` are **permissive** (MIT /
  Apache-2.0) and compatible with shipping under MIT.
- The **Kusto Emulator EULA has been reviewed and approved** by the repo owner for
  dev/CI oracle use. Binding constraints: never redistribute the image, never
  publish performance numbers from it, keep it dev/CI-only and never a runtime
  dependency.

## 12. Open questions

- **Primary oracle: resolved → the Kusto Emulator** (§5.1), ground-truth and
  local. Remaining sub-questions: pin a specific image tag/digest; decide whether
  the nightly emulator lane also cross-checks against KustoLoco for engine-version
  differences.
- **Emulator in CI:** confirm the x64 GitHub runner + Docker works within time
  budget; document the Apple-Silicon local-dev story (rely on frozen expectations;
  emulator optional via slow emulation or a remote x64 box).
- **Sample-dataset hosting:** where to cache `StormEvents` et al. so both the
  emulator (`/kustodata` mount) and DuckDB load identical data.
- **Approx-aggregation policy:** exact tolerance/marking for `dcount`/`percentile`
  (the emulator gives the reference values, but they're still algorithm-specific).
- **Licensing: closed.** See [`licensing.md`](./licensing.md) — docs dual-license
  verified, harvest-queries/generate-expectations policy set, and the emulator
  EULA reviewed and **approved by the repo owner**.

## 13. Sources

- **Kusto Emulator (ground-truth oracle):** https://learn.microsoft.com/en-us/azure/data-explorer/kusto-emulator-overview · install/ingest: https://learn.microsoft.com/en-us/azure/data-explorer/kusto-emulator-install · image: `mcr.microsoft.com/azuredataexplorer/kustainer-linux` (Docker Hub: https://hub.docker.com/r/microsoft/kusto) · Testcontainers module: https://testcontainers.com/modules/kusto/
- Kusto docs source (harvest target): https://github.com/MicrosoftDocs/dataexplorer-docs — query reference under `data-explorer/kusto/query/`
- Syntax conventions (entry point requested): https://learn.microsoft.com/en-us/kusto/query/syntax-conventions
- kql-to-sql (differential tests, checklists, DuckDB oracle): https://github.com/saoc90/kql-to-sql
- ClickHouse KQL tests (`.sql`/`.reference`): https://github.com/ClickHouse/ClickHouse/tree/master/tests/queries/0_stateless · KQL experimental status: https://github.com/ClickHouse/ClickHouse/pull/74224
- Microsoft official parser (parse corpus): https://github.com/microsoft/Kusto-Query-Language
- KustoLoco / BabyKusto (engine oracle): https://github.com/NeilMacMullen/kusto-loco · https://github.com/davidnx/baby-kusto-csharp
- StormEvents sample dataset (fixtures): referenced throughout `dataexplorer-docs` query examples
