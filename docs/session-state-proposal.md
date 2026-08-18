# Proposal — targeting a database, and retiring the timezone dance

> Status: **proposal**, nothing implemented. Written in response to two ideas:
> give `kql()` an optional `database=` that switches the connection and switches
> it back, and do the same for `TimeZone` so `connect()` can be dropped.
>
> Every number below was measured on DuckDB 1.5.5 in this repo, not reasoned
> about. The reproductions are short enough to paste into a REPL.

## Summary

The **goal** is right and worth doing. The **mechanism** — save, switch, run,
restore — cannot be made correct on the caller's connection, for a reason that
has nothing to do with careful coding: `kql()` returns a *lazy relation*, and
DuckDB reads session state when the relation is **fetched**, not when it is
built. Restoring before the caller fetches silently changes the answer.

There is a better mechanism for each half, and it happens to be simpler:

| Goal | Proposed here | Instead of |
|---|---|---|
| Target a database | **Qualify names at translate time** (`"sales"."T"`) | `USE sales` … `USE prev` |
| KQL's UTC semantics | **Emit session-independent SQL** (partial — see §5.4) | `SET TimeZone` … restore |
| Ingest via `.set-or-replace` | Fully-qualified DDL | any session switching |

Both replace mutable session state with something the generated SQL carries on
its own. Note that B turns out **not** to retire `connect()` by itself: a
caller's own `TIMESTAMPTZ` column still reads the session zone, and that needs
column *types* at render time, which the translator does not have (§5.4).

---

## 1. Why save/restore cannot work: relations are lazy

`kql()` returns `con.sql(...)`, a `DuckDBPyRelation`. Nothing has executed yet.

```python
c.execute("SET TimeZone='UTC'")
rel = c.sql("SELECT CAST('2024-01-01 12:00:00' AS TIMESTAMPTZ) AS t")
c.execute("SET TimeZone='America/Los_Angeles'")   # the "restore"
rel.fetchall()
# → 2024-01-01 12:00:00-08:00   (an instant 8 hours from the intended one)
```

The relation observes the zone in force at `fetchall()`. Confirmed directly:
a relation built as `SELECT current_setting('TimeZone')` under UTC returns
`America/Los_Angeles` after the restore.

`USE` is worse, because it changes *which data you get*:

```python
c.execute("USE dba")
rel = c.sql("SELECT * FROM T")     # unqualified
c.execute("USE dbb")               # the "restore"
rel.fetchall()
# → [('from_b',)]   — the other database's row
```

The table name is resolved at fetch time too. A `database="dba"` implemented by
switching would hand back `dbb`'s data whenever the caller fetched after the
call returned — which is the normal way to use a relation.

This is not a bug to code around. It is what makes `kql()` composable, and it is
incompatible with restoring state before the caller is done.

> Materializing inside `kql()` would fix it and cost composability — the ability
> to write `duckdb_kql.kql(con, q).filter(...)`, which the Kusto client layer and
> the notebook demo both use. Not recommended.

## 2. And on a shared connection it also races

Two threads, one connection, each doing save → switch → run → restore, 150
iterations each:

```
asked for dba: {'from_a': 143, 'from_b': 1}     ← one silently wrong answer
asked for dbb: {'from_b': 140}
errors: 16  (NoneType is not subscriptable — execute/fetch interleaved)
```

The wrong row is the point. There is no error, no warning; one query in 144
answered from the wrong database. Note the errors too: `execute()` followed by
`fetchone()` is **not atomic** on a shared DuckDB connection, so this is a
hazard the library would be introducing into callers' code, not merely tolerating.

A lock inside `duckdb-kql` would not fix it either — the caller's own threads
also touch the connection, and we do not own it.

## 3. What is actually true about DuckDB session state

Measured, because the design depends on it:

| Question | Answer |
|---|---|
| Are `SET`/`USE` per connection or global? | **Per connection.** A cursor's changes are invisible to its parent. |
| Does `con.cursor()` inherit `current_database()`? | **No** — it starts at the default (`memory`), not the parent's. |
| Does a relation outlive the cursor that built it? | **Yes** if the cursor is merely garbage-collected; **no** after `cursor.close()`. |
| Does a cursor see the parent's *uncommitted* rows? | **No.** Parent in a transaction sees 2 rows, cursor sees 1. |
| Do fully-qualified names need `USE`? | **No.** `CREATE OR REPLACE TABLE dba.main.T AS …` works with `current_database()` unchanged. |

The cursor-per-call idea is worth naming because it is the obvious fix to §1 and
§2 — a private cursor, set up once, never restored, so laziness and races both
go away. It is rejected here for the last two rows: a caller who writes rows in
an open transaction and then queries them through `kql()` would silently get
stale data. Trading a loud concurrency bug for a quiet visibility bug is not an
improvement.

## 4. Proposal A — `database=` by qualification, not by `USE`

The translator already models this. `TableRef` carries an optional database and
`database("sales").T` already renders as `"sales"."T"` — verified end to end
against an attached file. Two-part naming is enough; DuckDB resolves the schema.

So `database=` becomes a **translate-time default** for unqualified table
references, not a session change:

```python
duckdb_kql.kql(con, "T | count", database="sales")
#   → SELECT ... FROM "sales"."T"
```

Properties that fall out for free:

- **No session mutation**, so nothing to restore and nothing to race.
- **Lazy-safe**: the database is baked into the SQL text, so fetch time cannot
  drift.
- **Thread-safe** to the same degree the caller's connection already is.
- **Composes with `database("X").T`**: an explicit qualifier in the query wins
  over the parameter, which is what Kusto does.
- Works for `to_sql()` too — Layer 0 keeps working with no connection at all,
  which a `USE`-based design could never offer.

Open questions to settle before implementing:

1. Does `database=` also qualify the *right* side of `join` / `lookup`, and the
   tables inside a tabular `let`? (Proposed: yes, uniformly — it is a default
   for unqualified names, applied at one place in lowering.)
2. `schema(con)` keys attached tables as `db.Table` already, so column
   resolution should need no change — worth a test rather than an assumption.
3. Error text when the database is not attached: should name the database and
   list what is, as the server's 404 already does.

### This closes a real gap in the server

`server.py` reads `db` from the request, validates it with `serves()`, and then
**drops it** — `run()` is called with only the query. An Azure Data Explorer
user who picks `sales` from the database list and runs `T | count` is answered
from whichever database the server happened to start in. With Proposal A the
server passes `database=` through and the request means what it says.

## 5. Proposal B — make the SQL session-independent

> **Corrected after measurement.** An earlier draft of this section claimed B
> retires `connect()`. It does not, on its own. The reason is in §5.4, and it is
> the most important paragraph here.

### 5.1 Why the dependence exists

KQL has one datetime type and it is an **absolute instant, always UTC**. DuckDB
has two:

| DuckDB type | Holds | Session zone affects |
|---|---|---|
| `TIMESTAMP` | a naive wall-clock reading | nothing |
| `TIMESTAMPTZ` | an absolute instant | how it is *rendered*, *extracted*, and compared against a naive value |

The KQL datetime maps onto naive `TIMESTAMP` holding **UTC wall time**, and the
pipeline already lands there — `AT TIME ZONE 'UTC'` converts `TIMESTAMPTZ` to
`TIMESTAMP`, so `todatetime(...)` returns a naive value today. The output type
is not the problem.

The problem is the **middle step**. `_TODATETIME` (`functions.py:91`) is:

```sql
COALESCE(TRY_CAST({0} AS TIMESTAMPTZ) AT TIME ZONE 'UTC', try_strptime({0}, [...]))
```

For text with no offset, `CAST('2024-01-01 12:00:00' AS TIMESTAMPTZ)` has to
answer *"12:00 in which zone?"* — and it answers **the session zone**. The
following `AT TIME ZONE 'UTC'` then faithfully converts that instant to UTC wall
time, giving `20:00` when the session said Los Angeles. The round trip is

```
naive text --(session zone)--> instant --(UTC)--> naive UTC
```

and the session zone cancels out **only when it is already UTC**. That is the
entire dependence: a detour through an instant that never needed to happen.

### 5.2 The fix: don't take the detour

Text that already carries an offset does not need the session — the offset
determines the instant. Text without one is UTC by definition in KQL, so a plain
naive cast is exactly right:

```sql
COALESCE(
  CASE WHEN regexp_matches({0}, '<offset>')
       THEN TRY_CAST({0} AS TIMESTAMPTZ) AT TIME ZONE 'UTC'   -- offset decides
       ELSE TRY_CAST({0} AS TIMESTAMP)                        -- naive text is UTC
  END,
  try_strptime({0}, [...]))                                   -- unchanged
)
```

Measured, per input, `TRY_CAST(... AS TIMESTAMPTZ) AT TIME ZONE 'UTC'` versus
`TRY_CAST(... AS TIMESTAMP)`:

| input | UTC session | LA session | naive cast |
|---|---|---|---|
| `2024-01-01 12:00:00` | 12:00 | **20:00** | 12:00 |
| `2024-01-01` | 00:00 | **08:00** | 00:00 |
| `2024-01-01T12:00:00Z` | 12:00 | 12:00 | 12:00 |
| `2024-01-01T13:45:56+02:00` | 11:45:56 | 11:45:56 | **13:45:56** (wrong) |

The two columns that matter: for offset-less text the naive cast equals the
UTC-session answer, and for offset-bearing text it is wrong — which is precisely
why the branch exists rather than a blanket replacement.

### 5.3 The detector, derived rather than guessed

A first attempt at the regex was wrong in a way worth recording: `[+-][0-9]{2}$`
as an alternative matches the tail of **`2024-01-01`**, so a date-only string was
read as offset-bearing.

The detector was then derived against DuckDB itself, using "the `TIMESTAMPTZ`
cast is *not* session-dependent" as ground truth for "carries an offset", over 22
spellings including `Z`, `+02:00`, `+0200`, bare `+02`, `+05:30`, `-00:30`,
date-only, and 7-digit fractional seconds:

```
[0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]+)?)?(Z|z|[+-][0-9]{2}(:?[0-9]{2})?)$
```

Anchoring the offset to a preceding **time** is what keeps `2024-01-01` out.
**0 disagreements** across the 22 spellings.

**Equivalence check.** 27 inputs (including every `try_strptime` format, junk,
empty string, `0001-01-01`, `9999-12-31`) across 5 zones — UTC, Los Angeles,
Kolkata, Chatham (a 45-minute offset), Etc/GMT+12 — is 135 comparisons. The
candidate equals *today's answer under a UTC session* in **all 135**. So this is
not a semantic change; it makes the already-correct answer independent of
configuration.

### 5.4 Why this does **not** retire `connect()` on its own

`SET TimeZone='UTC'` in `_prepare()` is doing a second job I had missed: it also
pins how a **caller's own `TIMESTAMPTZ` column** behaves. Running the translated
SQL without it, against a table whose column is `TIMESTAMPTZ`:

| construct | UTC | Asia/Kolkata | |
|---|---|---|---|
| `Aware \| project t` | 12:00+00:00 | 17:30+05:30 | diverges |
| `datetime_part('hour', t)` | 12 | 17 | diverges |
| `tostring(t)` | 12:00+00 | 17:30+05:30 | diverges |
| `Aware \| where t == datetime(2024-01-01 12:00:00) \| count` | **1** | **0** | diverges |
| naive `TIMESTAMP` column | 12:00 | 12:00 | same |

The last row is the good news and the fourth is the bad news: a filter returned
**one row under UTC and none under Kolkata**, because comparing a naive literal
against an aware column makes DuckDB convert the naive side using the session
zone. Rows silently disappear.

§5.2 cannot fix this, because it is about the *caller's column type*, not our
generated text. Fixing it means knowing column types at render time — and
`render_expr()` is a pure function of the IR with no schema in scope. That is the
same architectural limit that blocks the outer-join null-string residue in R14.
`schema()` could carry types cheaply (it already reads
`information_schema.columns`); **threading them into expression rendering is the
real work**, and it is a much larger change than B.

### 5.5 What B is still worth

Even without retiring `connect()`:

- **`to_sql()` becomes correct standalone** for KQL-authored datetimes. Today the
  CLI must emit a `SET TimeZone='UTC'` header into generated `.sql` and document
  that callers keep it; after B that header is no longer load-bearing for
  literals and `todatetime()`.
- **Defence in depth**: `kql()` stops depending on a `SET` having succeeded.
- It is a prerequisite for ever retiring `connect()`, not a substitute for it.

**Risks.** This is the highest-traffic mapping in the project, so it must be
driven by the emulator: freeze expectations, change the mapping, require zero
corpus movement. Keep the `try_strptime` fallback ordering. Check the output type
with `getschema`, which asserts type names.

## 6. Proposal C — `.set-or-replace` needs neither

With A and B, ingestion is unremarkable. The command renders fully-qualified
DDL and touches no session state:

```kql
.set-or-replace Events <| datatable(t:datetime, v:long) [ ... ]
```

```sql
CREATE OR REPLACE TABLE "sales"."Events" AS SELECT ... ;
```

`.set` → `CREATE TABLE` (fails if it exists), `.append` → `INSERT INTO`,
`.set-or-append` → create-if-absent then insert. The database comes from
`database=` or the connection default, exactly as for a query.

Two things to decide, both of which want the emulator first:

1. **The result schema.** These commands return a table (extent id, row count,
   …), and control-command schemas in this project are *measured*, not invented
   — `control.py` already does that for the five commands it supports.
2. **Write policy.** Everything shipped so far is read-only. A KQL string that
   can `CREATE OR REPLACE TABLE` is a genuine escalation, especially inside
   `serve`, which answers unauthenticated loopback requests. Recommendation:
   ingestion commands are **refused by the server unless explicitly enabled**
   (`--allow-write`), and the refusal is explicit rather than a parse failure.

## 7. Recommendation

1. **Do not** implement save/restore for either setting. §1 and §2 are
   disqualifying, and both failure modes are silent-wrong-answer rather than
   error.
2. **Proposal A** (`database=` by qualification) — small, reuses `TableRef`,
   fixes a live server bug. Good first step.
3. **Proposal B** (session-independent datetimes) — worth doing for `to_sql()`
   correctness and as defence in depth, but it does **not** retire `connect()`:
   keep `SET TimeZone='UTC'` in `_prepare()`, now justified by caller-owned
   `TIMESTAMPTZ` columns rather than by our own casts. Do it second, on its own,
   with frozen expectations.
   Retiring `connect()` is a separate, larger project: type-aware schema threaded
   into expression rendering. Worth scoping only if someone wants it.
4. **Proposal C** (`.set-or-replace`) — after A and B, with measured schemas and
   a default-off write policy in `serve`.

A and B are independent; either can land alone.
