# Proposal — supporting `cluster()`

> Status: **brainstorm**, nothing implemented. The problem: test queries are
> written against real clusters —
> `cluster('mycluster.eastus.kusto.windows.net').database('mydb').MyTable` — and
> need to run unchanged against local DuckDB data.
>
> Measured on DuckDB 1.5.5 and the pinned Kusto Emulator.

## What is true today

`cluster()` **already parses**. It is refused on purpose, in `lower.py`:

> `cluster() — there is no cluster here; attach the database locally and
> reference it as database("Name").Table`

The reasoning in that code is worth keeping in view, because it is the thing any
design here has to answer: quietly treating `cluster("prod").database("Sales")`
as the local `Sales` **answers a question about production with local data**.
That is the failure this package exists to prevent, so "just ignore the cluster"
cannot be the silent default. It can, however, be something the caller *asks*
for — which is most of what makes this tractable.

## Four measurements that shape the design

**1. The three-part form is the only form.** `cluster('c').T` without a database
is a semantic error:

```
SEM0048: database(''): database name must be explicit if the cluster value is set
```

So there is no `cluster(...).Table` case to handle. Every reference is
`cluster(...).database(...).Table`, which means a mapping keyed on
*(cluster, database)* covers the whole surface.

**2. Kusto normalizes the cluster to a URI**, and does *not* expand a short name
(at least not in the emulator):

| written | resolved to |
|---|---|
| `cluster('mycluster')` | `https://mycluster/` |
| `cluster('mycluster.eastus.kusto.windows.net')` | `https://mycluster.eastus.kusto.windows.net/` |
| `cluster('https://mycluster.eastus.kusto.windows.net')` | same as above |

Two consequences. The scheme and trailing slash are noise and should be
normalized away before matching, so all three spellings of one host key the same
entry. But the **short name is a different host**, not an abbreviation — so
expanding `mycluster` to `mycluster.kusto.windows.net` would be inventing a
resolution rule the engine does not apply. Better to let the caller map both
spellings if their queries use both.

**3. An unreachable cluster fails loudly** — `Error getting schema for
Cluster='https://.../': Name or service not known` — not as an empty result. So
refusing an unmapped cluster is the behaviour-preserving choice, not a
regression.

**4. DuckDB's namespace is the same shape as Kusto's.** This is the useful
surprise:

| Kusto | DuckDB |
|---|---|
| cluster | catalog (attached database) |
| database | schema |
| table | table |

All three levels are real and addressable, dotted catalog aliases work when
quoted, and `information_schema` exposes each level:

```sql
ATTACH 'c1.db' AS "mycluster.eastus.kusto.windows.net";
SELECT * FROM "mycluster.eastus.kusto.windows.net".mydb.MyTable;   -- works
```

So a *faithful* mapping is available, not only a lossy one.

## Options

### A. Explicit mapping — `clusters={...}`

```python
duckdb_kql.kql(con, query, clusters={
    ("mycluster.eastus.kusto.windows.net", "mydb"): "mydb_local",
    ("other.westus.kusto.windows.net", "mydb"): "other_mydb",
})
```

Unmapped cluster → `KqlSchemaError` naming it and listing what is mapped.

*For:* explicit, safe by construction, and the two-clusters-one-database-name
collision is impossible. Reads like a test fixture, which is the actual use.
*Against:* verbose when a suite touches many databases on one cluster.

A cluster-level shorthand removes most of the verbosity, since the database name
usually survives unchanged:

```python
clusters={"mycluster.eastus.kusto.windows.net": "*"}   # keep the database name
```

### B. A resolver callable

```python
clusters=lambda cluster, database: database if cluster.startswith("mycluster") else None
```

*For:* covers regex, environment variables, prefix rules, and anything the dict
cannot express. Returning `None` means refuse, so strictness is preserved.
*Against:* not serializable — no use from the CLI or the REST server, which need
a declarative form. Worth having as the escape hatch, not as the main road.

### C. Structural — cluster becomes a catalog, database becomes a schema

`cluster('c').database('d').T` → `"c"."d"."T"`, with the caller attaching one
DuckDB file per cluster and one schema per database.

*For:* it is the honest mapping; nothing is collapsed, and two clusters sharing
a database name stay distinct without any configuration. Zero-config for anyone
willing to lay their fixtures out this way.
*Against:* attaching a file as `"mycluster.eastus.kusto.windows.net"` is
awkward, and it forces a fixture layout on the user. It also **needs a fix
first** — see the bug below.

### D. `clusters="ignore"` — collapse to the database name

`cluster(anything).database('mydb').T` → `"mydb"."T"`.

*For:* one word in a fixture, and it is what most test suites actually want —
they have one cluster and care about the database.
*Against:* two clusters with the same database name silently become the same
table. Acceptable **only because the caller typed it**: the danger the existing
code guards against is doing this *silently*, not doing it on request.

## Recommendation

One parameter, `clusters=`, accepting all of the above, with **refusal as the
default**:

| value | meaning |
|---|---|
| *omitted* | `cluster()` is refused, exactly as today |
| `{(cluster, db): name}` | explicit per-pair mapping |
| `{cluster: "*"}` | that cluster's databases keep their names |
| `"ignore"` | drop the cluster, use the database name |
| `callable` | resolver; `None` means refuse |
| `"structural"` | option C, once the schema bug is fixed |

with cluster spellings normalized (strip `https://`, strip the trailing slash,
lowercase the host) so one entry covers all three forms Kusto accepts.

Threading is the same as `database=`, which is already in place: a translate-time
rewrite of `TableRef`, no session state, works in `to_sql()` with no connection.
`TableRef` grows a `cluster` field; `qualify()` learns to resolve it.

For the REST server and CLI the declarative forms carry over directly — a
`--cluster-map` file, or a `clusters` argument to `serve()`. The callable does
not, which is fine.

**Start with A + D.** Between them they cover the described use — a fixture that
either names the mapping explicitly or says "ignore the cluster, I have one" —
and neither requires the fix below. B and C can follow if they are wanted.

## A bug this uncovered, independent of `cluster()`

`engine.schema()` reads `table_catalog` and `table_name` and **ignores
`table_schema`**, so two same-named tables in different DuckDB schemas collapse
into one entry whose column list is the *union* of both:

```python
# main.MyTable(a), other.MyTable(b)
schema(con)["clu.MyTable"]   # ['a', 'b'] — but main.MyTable has only 'a'
```

A `join` against it then fails with `Binder Error: Values list "_l" does not have
a column named "b"` — loud, so no wrong answers are known to come from it, but
the error blames the query for a mistake in the catalog read.

It matters here because option C depends on the middle level being addressable,
and it should be fixed regardless: key by `catalog.schema.table`, keep the
existing `catalog.table` and bare `table` keys for the `main` schema so nothing
that works today stops working.

## Open questions

1. **`database('mydb')` with no cluster, under a mapping.** Should it consult
   the mapping for the connection's "own" cluster, or stay local? Proposed:
   stay local, since that is what it means today and what Kusto means by it.
2. **Case sensitivity.** Kusto database names are case-insensitive; DuckDB
   identifiers here are treated case-sensitively (R7). The mapping keys should
   probably match the host case-insensitively and the database name exactly —
   worth confirming against the emulator before deciding.
3. **Does the ADX web UI ever send `cluster()`** to `duckdb-kql serve`? If it
   does, the server needs a mapping too, not just the library.
