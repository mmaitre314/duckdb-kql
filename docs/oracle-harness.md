# Oracle Harness — Ground-Truth Expectations

> Built and run 2026-08-02. The Kusto Emulator is live, **785 self-contained
> cases now carry ground-truth expectations** produced by Microsoft's real KQL
> engine, and the freeze-and-compare loop from
> [`test-plan.md`](./test-plan.md) §5.2 is operational.

## What runs

| | |
|---|---|
| Image | `mcr.microsoft.com/azuredataexplorer/kustainer-linux` |
| Pinned digest | `sha256:de542bb2eda4ca71330c707b13c8b4cb77d46d202cf96b129ac1390e2c6ea5b2` |
| Size | 1.18 GB on disk (measured on the pinned digest) |
| Endpoint | `http://localhost:8080` — plain HTTP, no auth |
| Requirements | x86‑64 with SSE4.2/AVX2, ≥4 GB RAM |

```bash
docker compose up -d kusto
python tools/make_fixtures.py --load                         # ingest StormEvents
python tools/regen_expectations.py --image-digest <digest>   # freeze
python tools/regen_expectations.py --check --include-fixture-cases  # verify, writes nothing
```

## Where it runs

The emulator is x86‑64 only and the image is over a gigabyte, so it is
deliberately **not** on the per-push path. Three places can run it:

| Environment | How | Notes |
|---|---|---|
| **GitHub Actions** | [`.github/workflows/oracle.yml`](../.github/workflows/oracle.yml) | `ubuntu-latest` only — the ARM runners cannot run it. Nightly drift check plus a `workflow_dispatch` **regenerate** mode that uploads the corpus as an artifact instead of committing it. |
| **Dev container / Codespaces** | [`.devcontainer/devcontainer.json`](../.devcontainer/devcontainer.json) | Enables docker-in-docker, which is the only reason the file exists. |
| **Claude Code web session** | `sudo -n dockerd &` then `docker compose up -d kusto` | Docker is installed but **the daemon is not started**, so `docker compose` fails with "Cannot connect to the Docker daemon" and the oracle looks unavailable when it is not. Roughly 30 s to first query. |

Per-push CI ([`ci.yml`](../.github/workflows/ci.yml)) never touches any of this —
it compares against frozen expectations, so it is fast, hermetic, and runs on any
architecture.

## Drift checking

`--check` re-runs every frozen case and compares, writing nothing. It exists
because the frozen expectations *are* the yardstick for the entire acceptance
suite: if they silently move, every measurement built on them is wrong.

It must use the same comparison semantics as the acceptance suite rather than a
byte diff — the first run proved why. Comparing raw JSON reported **hundreds** of
false drifts: `rand()`-seeded plugin queries return new numbers every run, and
unordered results came back in a different row order (R10). A lane that is red
every night is a lane nobody reads. With `compare()` + `is_nondeterministic()`
the same sweep reports **737 checked, 0 drifted**.

Two genuinely nondeterministic constructs were found this way and added to
`NONDETERMINISTIC_FUNCTIONS`: `cursor_current()` (a commit position — a clock in
disguise) and the `sample` operator (re-rolls on every execution, unlike `take`).

## The pieces

| Component | Role |
|---|---|
| [`docker-compose.yml`](../docker-compose.yml) | Emulator service, `ACCEPT_EULA=Y`, fixtures mounted read-only at `/kustodata` |
| [`src/duckdb_kql/oracle.py`](../src/duckdb_kql/oracle.py) | Emulator client — stdlib only, no auth, `query` / `command` / `wait_until_ready` |
| [`src/duckdb_kql/comparison.py`](../src/duckdb_kql/comparison.py) | Comparison semantics (test-plan §4.2) |
| [`tools/regen_expectations.py`](../tools/regen_expectations.py) | The freeze half: run cases, write expectations into case files |

Both modules are **dev/CI only** — never imported by the translation path, never
a runtime dependency. That constraint comes from the emulator's licence terms
([`licensing.md`](./licensing.md) §5).

## Results

| | Count |
|---|---:|
| Self-contained cases attempted | 810 |
| **Frozen with ground-truth expectations** | **785** |
| Refused by the emulator | 25 |
| Skipped (need a sample-DB fixture) | 475 |

Each frozen case records `oracle: "kusto-emulator"` and the pinned
`oracle_image` digest, so every expectation is attributable.

**The 25 refusals are all legitimate** — they are not harness failures:

| Reason | Examples |
|---|---|
| Deliberately failing queries | `assert-function-*` (the query asserts false *by design*) |
| Needs a real cluster | `cross-cluster-*`, `shuffle-query-*` |
| Needs authentication | `current-principal-*` |
| Graph semantics | `graph-scenarios`, `graph-visualization` |
| Plugins absent from the emulator | `r-plugin`, `percentile-array-tdigest` |
| Access control | `restrict-statement-*` |
| Network egress from the container | `external_data(...)` fetching a URL |

## Two bugs caught while building this

Both were found by tests, and both were of the "silently wrong" class this
project is specifically built to prevent.

### 1. Partial query failures were being frozen as valid results

The emulator answers a *partial* failure with **HTTP 200** and the error
embedded in the row list as an object:

```json
{"Exceptions": ["Partial query failure: Bad HTTP request ..."]}
```

instead of a normal array row. The client read that as success and froze it as
an expectation — baking a **failed** query into the corpus as though it had
succeeded. Caught by a ragged-row assertion (9 columns, 1 value).

`oracle.py` now rejects any non-list row, any `Exceptions` payload, and any row
whose arity doesn't match the column count.

### 2. Float tolerance was bypassed in the default comparison mode

`compare()` matched unordered rows via exact hashing, which **skips tolerance
entirely** — so every approximate aggregate (`dcount`, `percentile` — R11) would
have failed in the default unordered mode. Now the exact multiset pass is
followed by a tolerance-aware pairing of leftovers.

Fixing that surfaced a third: `_values_equal` treated `True == 2` as equal,
because `bool(2) is True` and `bool` subclasses `int` in Python. Bool now
compares equal only to bool, matching KQL's distinct types.

## Comparison semantics (implemented)

- **Order-insensitive by default.** Order is asserted only when the query ends
  in a terminal `sort`/`order`/`top` — and *not* when a later `summarize`,
  `join`, or `union` discards it (R10).
- **Type-name normalization** — `long`/`BIGINT`, `real`/`DOUBLE`,
  `datetime`/`TIMESTAMP`, `dynamic`/`JSON` compare as equal buckets.
- **Approximation tolerance** — a query using `dcount`/`percentile`/`tdigest`
  automatically gets a 5% relative tolerance (R11).
- **Prefix mode** for the docs' truncated "first N rows" outputs.
- **Column names checked by default**, since `summarize` auto-naming is
  user-visible (R12).

## Test coverage added

98 tests pass. New guards:

- frozen count may only go **up** (baseline **785**)
- every expectation is well-formed and non-ragged
- every expectation records its oracle **and** image digest
- only self-contained cases were frozen
- a refused case has **no** partial expectation
- every frozen result **compares equal to itself** — catches result shapes the
  comparison engine can't handle
- the licensing invariant, restated correctly: an expectation without an oracle
  would mean doc prose leaked in

## Next

- [ ] Load `StormEvents` and friends into `/kustodata` to unlock the 475
      fixture-dependent cases
- [ ] Pin the image **by digest** in `docker-compose.yml` for CI
- [ ] Nightly CI lane that re-runs the harness and flags expectation drift
- [ ] Begin Wave 1 mappings — each one now has ground truth to verify against
