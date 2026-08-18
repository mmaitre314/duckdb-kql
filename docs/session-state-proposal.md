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
| KQL's UTC semantics | **Emit session-independent SQL** | `SET TimeZone` … restore |
| Ingest via `.set-or-replace` | Fully-qualified DDL | any session switching |

Both replace mutable session state with something the generated SQL carries on
its own. That is what makes `connect()` droppable — not the restore step.

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

## 5. Proposal B — make the SQL session-independent, then retire `connect()`

The timezone half is better than the database half, because the dependence can
be removed rather than managed.

**How bad it is today.** Six of fourteen probe queries change answer between
`UTC` and `America/Los_Angeles`:

```
todatetime("2024-01-01 12:00:00")   UTC=12:00   LA=20:00
datetime(2024-01-01 12:00:00)       UTC=12:00   LA=20:00
bin(datetime(...), 1h)              UTC=12:00   LA=20:00
datetime_part("hour", ...)          UTC=12      LA=20
tostring(datetime(...))             UTC=...T12  LA=...T20
datetime(2024-01-01) + 1d           UTC=00:00   LA=08:00
```

**All six share one root.** Every one flows through `_TODATETIME`
(`functions.py:91`) and its `TRY_CAST({0} AS TIMESTAMPTZ)`, which interprets
offset-less text in the *session* zone. `now()` is already
session-independent, so this really is the single site.

**A session-independent form exists**, and returns identical results under
`UTC`, `America/Los_Angeles` and `Asia/Kolkata`:

```sql
CASE WHEN regexp_matches({0}, '(Z|[+-][0-9]{2}:?[0-9]{2})$')
     THEN CAST({0} AS TIMESTAMPTZ) AT TIME ZONE 'UTC'   -- offset is explicit
     ELSE TRY_CAST({0} AS TIMESTAMP)                    -- naive text is UTC
END
```

with the correct values in both branches (`12:00` stays `12:00`;
`13:45:56+02:00` becomes `11:45:56`).

**Then `SET TimeZone='UTC'` becomes unnecessary**, and with it:

- `_prepare()` stops mutating the caller's connection at all;
- `connect()` loses its only reason to exist and can be deprecated honestly —
  not "we now restore it for you", but "the SQL no longer cares";
- `to_sql()` output becomes correct standalone, which today it is not. The CLI
  currently has to emit a `SET TimeZone='UTC'` header into generated `.sql`
  files and document that callers must keep it. That header could go.

The last point is the strongest argument for doing B at all: build-time
translation currently ships a caveat, and this removes it.

**Risks.** This is the highest-traffic mapping in the project — the datetime
corpus is large — so it must be driven by the emulator, not by unit tests:
freeze the current expectations, change the mapping, and require zero corpus
movement. The `try_strptime` fallback list must keep its ordering (the
TIMESTAMPTZ branch is deliberately first today). Expect the output *type* to
need checking: `TRY_CAST(... AS TIMESTAMP)` and `... AT TIME ZONE 'UTC'` should
both be naive `TIMESTAMP`, but `getschema` asserts type names, so that is a real
test and not a formality.

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
3. **Proposal B** (session-independent datetimes) — larger and needs the oracle,
   but retires `connect()`, removes the CLI's `SET TimeZone` caveat, and deletes
   a whole class of "works on my machine, wrong in CI" bugs. Do it second, on
   its own, with frozen expectations.
4. **Proposal C** (`.set-or-replace`) — after A and B, with measured schemas and
   a default-off write policy in `serve`.

A and B are independent; either can land alone.
