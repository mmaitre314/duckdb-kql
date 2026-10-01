# Proposal — graph semantics: `make-graph`, `graph-match` and the rest of the family

> **Status: phase 1 implemented; phase 2 not started.** The normative rule is
> [`TRANSLATION.md` R25](TRANSLATION.md); the trap tests are
> `tests/test_graph.py`, `tests/test_graph_pushdown.py` and
> `tests/test_grammar_graph.py`, and parser patch `004` is in
> `grammar/UPSTREAM.md`. A sweep of 220 queries — every measurement below, the
> corpus's graph examples and 27 shapes of §3.4's seeding — through both
> engines agrees on 173 answers and 19 refusals. Of the rest, 21 are refusals of ours
> (hash ids, unmeasured names, shapes kept to the documentation, and corpus
> examples blocked by functions outside graphs — `geo_distance_2points`,
> `set_intersect`, `arg_max(*)`); 4 are the run-time type guards, refusing
> where Kusto refuses at compile time; and 3 are Kusto's own arbitrary choices
> (§2.1 twice, §2.7), which fall either way from one run to the next.
> Implementing it corrected two claims below, each marked
> where it stands. Every claim about Kusto in §2 was measured on the pinned
> Kusto Emulator on 2026-10-01; three contradict Microsoft's documentation
> (§2.9), and the emulator wins all three.

## 1. The one-sentence version

A graph exists only **at translation time**: `make-graph` lowers to two
relations — edges keyed by row, nodes keyed by id, with null as a real key —
and every graph operator consumes that pair and emits ordinary SQL, in which a
fixed-length pattern is a chain of joins and a variable-length edge is a
bounded recursive CTE that carries its path. Nothing new exists at run time:
no extension, no UDF, no temporary table.

```kusto
reports
| make-graph employee --> manager with employees on name
| graph-match (manager)<-[reports*1..5]-(employee)
    where manager.name == "Alice" and all(inner_nodes(reports), age < 40)
    project employee = employee.name, reportingPath = map(inner_nodes(reports), name)
```

becomes, in outline:

```sql
WITH RECURSIVE
_g1_e AS MATERIALIZED (SELECT row_number() OVER () AS _eid, employee AS _src, manager AS _dst, * FROM reports),
_g1_n AS MATERIALIZED (/* one row per node id; §3.2 */),
_g1_p(_start, _end, _eids, _inner, _len) AS (
    SELECT e._dst, e._src, [e._eid], [], 1 FROM _g1_e e            -- walked right to left: <-[..]-
    UNION ALL
    SELECT p._start, e._src, list_append(p._eids, e._eid),
           list_append(p._inner, /* the node at p._end, as a struct */), p._len + 1
    FROM _g1_p p JOIN _g1_e e ON e._dst IS NOT DISTINCT FROM p._end
    WHERE p._len < 5 AND NOT list_contains(p._eids, e._eid)        -- cycles=unique_edges
)
SELECT employee.name AS "employee", to_json(list_transform(p._inner, x -> x.name)) AS "reportingPath"
FROM _g1_p p
JOIN _g1_n manager  ON manager._nid  IS NOT DISTINCT FROM p._start
JOIN _g1_n employee ON employee._nid IS NOT DISTINCT FROM p._end
WHERE manager.name = 'Alice'
  AND coalesce(list_bool_and(list_transform(p._inner, x -> coalesce(x.age < 40, false))), true)
```

## 2. What Kusto does

Graphs below are written `A→B` for an edge row `("A", "B")`. "Refused" means
the emulator raised the quoted semantic error.

### 2.1 Building the graph — `make-graph`

| Question | Measured |
|---|---|
| What may follow it? | Only a graph operator. `E \| make-graph s --> t \| count` is SEM0104. A `let` may hold the graph: `let G = E \| make-graph …; G \| graph-match …` works |
| Which rows are edges? | **Every row**, duplicates included. Two identical rows `A→B` match `(a)-->(b)` twice; two parallel edges with different properties pair with each other both ways under `unique_edges` |
| Which ids are nodes? | The union of the node tables' ids and the edges' endpoints. A node-table row with no edges is a node (`graph-match (n)` returns it); an endpoint missing from the node tables is a node **without properties** (`isnull(n.age)` is true) |
| Is a null id a node? | **Yes, and it joins.** `datatable(s:long, t:long)[1,2, long(null),3, 4,long(null)]` has five nodes, one of them null, and `(a)-->(b)-->(c)` finds `4 → null → 3`. With string ids, the empty string is likewise a node |
| Duplicate ids in one node table | One node. The emulator kept the first row (`A,10` over `A,11`); the documentation says the choice is arbitrary |
| The same id in two node tables | **One table's row wins, whole — which one is arbitrary.** `with P on pid, C on cid` gave `{"pid":"A","v":10}` with no `size` in one run and `{"cid":"A","size":99}` with no `v` in the next. *Corrected during implementation: this row first said "the first table listed wins", from a single run.* The documentation says the properties are merged (§2.9); no run merged them |
| A column name in both node tables | Same type: one property. **Different types: no property at all** — `a.v` is SEM1040 "node 'a' doesn't have property 'v'" |
| Id types | Source and target must match (SEM1019), node ids must match the edges' (SEM1079, "StringBuffer and I64 found"), `dynamic` is refused (SEM1006). `int`, `long`, `real`, `string` and `datetime` all work |
| `make-graph s -- t` (undirected) | The grammar accepts it; the emulator refuses with SEM0456 "KQL undirected graphs feature is not enabled". **Unmeasurable** |
| `partitioned-by Col (…)` | One graph per value. With `with N on id` and `tenant` in both tables, `(a)-->(b)-->(c)` finds only paths whose edges share a tenant. An endpoint with no row in its partition's node table is a node without properties. With `with_node_id` instead of a node table, `tenant` does not resolve (SEM0100) |

### 2.2 Matching — `graph-match`

| Question | Measured |
|---|---|
| Is `project` optional? | No: SEM0001 "missing project clause" |
| What do `where` and `project` see? | Pattern variables, and the query's `let`s (`let x = "A"; … where a.id == x` works). **Not** the edge table's columns: `where s == "A"` is SEM0100 |
| An unknown property | Refused at compile time: SEM1040 |
| Direction | `(a)<--(b)` matches `A→B` as `(B, A)`. `(a)--(b)` matches every edge **in both orientations** — so a self-loop `A→A` matches twice, and the two-cycle `A→B, B→A` gives four rows |
| A repeated variable | Binds the same node: `(a)-->(b)-->(a)` finds `A→B→A` |
| An anonymous node `()` | Works: `(a)-->()-->(b)` on `A→B→C` gives `(A, C)` |
| Several patterns | Comma-separated sequences share their variables. Two sequences with no variable in common are SEM1011 "exactly one connected component supported" |
| Variable names | Case-sensitive: `(a)-->(A)` binds two variables, projected as `a_id` and `A_id` |
| A node and an edge with the same name | `(a)-[a]->(b)` is **accepted**, and `a.id` reads the node |
| An aggregate in `project` | SEM0237 |

### 2.3 `cycles=`

| Mode | Measured |
|---|---|
| `unique_edges` (default) | No edge appears twice in one match, **across all edges of the pattern**, fixed and variable-length alike: on `A→B, B→A`, `(a)-[e]->(b)-[p*1..3]->(c)` returns two rows, not the four that uniqueness within each edge alone would allow. Nodes may repeat. An undirected edge is still one edge: `(a)--(b)--(c)` on a single `A→B` is empty |
| `all` | Edges may repeat: `(a)-->(b)<--(c)` on a single `A→B` returns `(A, B, A)`, and `(a)-[p*1..4]->(b)` on the two-cycle reaches `[1, 2, 1, 2]` |
| `none` | Distinct variables bind distinct nodes: `(a)-->(b)-->(c)` on the two-cycle is empty, a self-loop is excluded, the triangle `(a)-->(b)-->(c)-->(a)` is kept. An inner node of a variable-length edge may not equal another pattern node. **But** `(a)-[p*1..3]->(a)` is empty although `(a)-->(b)-->(a)` is not, which the variable rule does not explain |

### 2.4 Variable-length edges and the graph functions

| Question | Measured |
|---|---|
| Bounds | `*lo..hi`, both required (`*2` and `*1..` are SYN0002), each a constant (a `let`-bound scalar works), `lo > hi` is SEM1013. `*1..1000` is accepted |
| `lo = 0` | Every node matches itself — isolated nodes included |
| Projecting `p` | A `dynamic` array of edge bags, in path order |
| `p.w` | In `project`, an array (`[1, 2]`). In `where`, SEM1054: use `map`, `all` or `any` |
| `map(p, e)`, `map(inner_nodes(p), e)` | An array, empty for a zero-length path. Nulls are kept: `[null, 1]` |
| `all(p, c)`, `any(p, c)` | Zero length: `true` and `false`. **A null condition counts as not satisfied in both**: with `w` null, `all(p, w > 0)`, `any(p, w > 0)` and `all(p, not(w > 0))` are all `false` |
| `inner_nodes(p)` | Only as the first argument of `all`, `any` or `map` (SEM0207) |
| `node_degree_in(n)`, `node_degree_out(n)` | Count edges with multiplicity, and a self-loop once each way: `A` with `A→B` twice, `A→A` and `C→A` is in 2, out 3. The no-argument form works inside `all(inner_nodes(p), …)` |
| `labels(x)` | `[]` for any element of a `make-graph` graph; `labels()` works inside `all(p, …)` |
| `node_id(x)` | The id's **string** form — `'1'` for a `long` id. Preview, per the documentation |
| An undirected variable-length edge | Walks either orientation: `-[p*2..2]-` over `A→B, C→B` finds `A…C` both ways |

### 2.5 Output names and shapes

| Expression | Column | Shape |
|---|---|---|
| `a.age` | `a_age` | the column's own type |
| `e.d.x.y`, `e.d["x"]` | `e_d_x_y`, `e_d_x` | `dynamic` |
| `map(p, w)` | `p_w` | `dynamic` array |
| `map(inner_nodes(p), id)` | `inner_nodes_p_id` | `dynamic` array |
| `map(p, strcat(…))` | `p_Column` | `dynamic` array |
| `labels(a)`, `node_id(a)` | `labels_a`, `node_id_a` | `dynamic`, `string` |
| `isnull(n.age)` | `Column1` | `bool` |
| `a`, then `a` again | `a`, `a1` | `dynamic` bag |

A **node bag** holds the node table's columns, **minus every null or
empty-string property** — `{"id":"A"}` when `age` is null, and
`{"id":"A","z":0,"b":false}` when `nm` is `""`: zero and `false` stay. An
edge-only node's bag holds the
id once under each node table's key column: `{"id":"Z"}` with one table,
`{"pid":"Z","cid":"Z"}` with two. With `with_node_id=nid` it is `{"nid":"A"}`;
with neither it is `{}`. An **edge bag** holds every edge column, source and
target included, with the same omission: a row `("A", "B", long(null), "")`
gives `{"s":"A","t":"B"}`.

### 2.6 `graph-shortest-paths`

| Question | Measured |
|---|---|
| Pattern shape | Exactly one sequence (SEM1023). The documentation also requires a variable-length edge; the emulator accepts `(a)-->(b)` (§2.9). Two variable-length edges are accepted |
| Which paths count? | **Simple paths only — no node twice, the start included — whatever `cycles=` says.** On `A→B, B→A, A→C`, `*3..5` from A to C finds nothing, although `graph-match` finds `A→B→A→C`. `A→B→A` is never a path from A to A, even with `cycles=all` |
| The range | Respected, not just checked afterwards: with a direct `A→C`, `*2..5` from A to C returns the length-2 path |
| When is `where` applied? | **Before "shortest"**: it decides which paths are eligible. When the short route fails `all(p, ok)`, the length-3 route is returned; `any(p, w == 2)` returns `[1, 2]`, not the direct edge with `w = 0` |
| One row per…? | Binding of the pattern's node variables: one per `(a, b)`, and one per `(x, a, b)` with a fixed edge in front |
| `output=any` / `output=all` | One of the tied paths (on parallel edges, `[1]`) / every tied path (`[1]` and `[2]`) |
| What is refused? | Comparing two pattern elements: `where a.id == b.id` is CRT0001 "dynamic filters are not supported". An `or` between a predicate on `a` and one on `b` is accepted |

### 2.7 `graph-to-table` and `graph-mark-components`

| Question | Measured |
|---|---|
| `graph-to-table nodes` | One row per node; the columns are the union of the node tables' columns, with nulls where a table did not supply them. An edge-only node has its id under **every** node table's key column (`Z, null, Z, null` for `pid, v, cid, size`). With no node clause and no `with_node_id`, **zero columns** |
| `graph-to-table edges` | The edge table's columns |
| `with_node_id=X`, `with_source_id=`, `with_target_id=` | A `long` column holding **`hash()` of the id**: `hash('Alice')` is `-3122868243544336885`, both standalone and as Alice's node id. `hash(long(null)) == hash('')` |
| `nodes as N, edges as E; N; E` | Several result tables |
| `graph-mark-components` | Adds `ComponentId` (`long`, zero-based, consecutive) and returns a **graph**. Weak by default: on `A↔B, B→C, D→E`, `{A,B,C}` and `{D,E}`. `kind=strong`: `{A,B}`, `{C}`, `{D}`, `{E}`, numbered 1, 0, 3, 2. `with_component_id=` renames it. If the node table already has a column of that name, `graph-to-table nodes` returns **two columns with the same name** |

### 2.8 Persistent graphs (for phase 2)

| Question | Measured |
|---|---|
| Are they there at all? | Yes: `.create-or-alter graph_model`, `.make graph_snapshot S from M`, `.show graph_models`, `.show graph_snapshots M`, `.drop graph_snapshot M.S` (the qualified name is required) and `.drop graph_model` all work |
| Model validation | Each step's query is compiled at `.create-or-alter` (an unbracketed `kind` column fails there, not at query time). At least one `AddEdges` step is required |
| `graph('M')` | The **latest snapshot**, and it is stale: after `.append`ing Carol to the node table, `graph('M')` still returns Alice and Bob |
| `graph('M', true)` | A transient graph built from the model now: Carol is there |
| `graph('M')` with no snapshot | SEM1076 "No snapshots available … use transient=true" (§2.9) |
| Labels | `Labels` (static) and `LabelsColumn` (per row) both become `labels(n)`; the labels column also stays a property |
| A node in two `AddNodes` steps | **Merged**: the later step's `name` wins, the earlier step's `age` survives, and the labels are combined. The opposite of `make-graph`'s first-table-wins (§2.1) |

### 2.9 Where the documentation and the emulator disagree

1. **`make-graph` with two node tables.** The documentation: "If the same node
   ID appears in both … tables, a single node is created by merging their
   properties." Measured: one table's row wins whole, the other contributes
   nothing, and which one varies between runs.
2. **`graph-shortest-paths` without a variable-length edge.** The
   documentation: "Patterns must include at least one variable length edge."
   Measured: `(a)-->(b)` is accepted.
3. **`graph()` before any snapshot exists.** The documentation's FAQ: "the
   graph remains queryable even if the snapshot … has not yet been created."
   Measured: SEM1076.

The emulator wins, as it always does here. Where the documentation forbids
something the emulator accepts (2), phase 1 refuses it anyway: a refusal is
never a wrong answer, and the documented shape is the one users write.

## 3. Design — phase 1, transient graphs

### 3.1 A graph is a translation-time value

New IR, alongside the tabular operators:

- `MakeGraph(edges, source, target, nodes, node_id_name, partition)`: a
  **graph source**. `edges` and each entry of `nodes` (at most two, each a
  pipeline with its key column) are ordinary lowered pipelines.
- `MarkComponents(graph, kind, name)`: graph in, graph out.
- `GraphMatch`, `GraphShortestPaths` and `GraphToTable`: graph in, **table
  out** — from there on, the pipeline is an ordinary pipeline.

The lowering walks a pipeline as it does now; after `make-graph` the running
value is graph-typed, and only a graph operator may consume it (a tabular one
is refused, as SEM0104 refuses it). A `let` bound to a graph is a graph
binding: it never reaches a tabular context, and it is rendered once per query
as the two CTEs of §3.2, which every use of it then reads.

**Where the code goes.** `lower.py` is 3,473 lines and `translate/__init__.py`
4,891; the [maintenance budget](maintenance/README.md) argues against growing
either. Graph lowering goes in `src/duckdb_kql/lower_graph.py` and its
rendering in `src/duckdb_kql/translate/graph.py`, each called from one
dispatch point. Both are layer 0: SQL text, no `duckdb` import.

### 3.2 The two relations

```sql
_g1_e AS MATERIALIZED (
    SELECT row_number() OVER () AS _eid, "s" AS _src, "t" AS _dst, * FROM (<edges>)),
_g1_n AS MATERIALIZED (
    SELECT "id" AS _nid, "id", "v"
      FROM (<nodes 1>) QUALIFY row_number() OVER (PARTITION BY "id") = 1
    UNION ALL BY NAME
    SELECT "cid" AS _nid, "cid", "size"
      FROM (<nodes 2>) n2
     WHERE NOT EXISTS (SELECT 1 FROM (<nodes 1>) n1 WHERE n1."id" IS NOT DISTINCT FROM n2."cid")
    QUALIFY row_number() OVER (PARTITION BY "cid") = 1
    UNION ALL BY NAME
    SELECT DISTINCT x AS _nid, x AS "id", x AS "cid"     -- an edge-only node: its id under every key column
      FROM (SELECT _src AS x FROM _g1_e UNION ALL SELECT _dst FROM _g1_e) ends
     WHERE NOT EXISTS (/* x in either node table, IS NOT DISTINCT FROM */))
```

Each line of that is there because the obvious alternative is a wrong answer:

- **`MATERIALIZED` is not an optimisation.** `row_number() OVER ()` gives edge
  identity, and every reference to the CTE must see the same numbering.
  Whether DuckDB inlines a CTE is an optimiser decision, not a guarantee; if it
  inlines this one, two references can number the same row differently and
  `unique_edges` silently compares unrelated ids. The SQL must say
  `MATERIALIZED`, and a trap test must fail if it does not.
- **A null id is a key** (§2.1). Every join on a node id is
  `IS NOT DISTINCT FROM`; every anti-join is `NOT EXISTS`. `NOT IN` is wrong
  twice over: a null in the subquery rejects every row, and a null on the left
  disappears.
- **A node is one whole row.** `QUALIFY row_number()` keeps one row per id;
  `any_value()` per column could assemble a node from two different rows.
- **One table wins whole** (§2.1), which is what the anti-join on the second
  table gives — not a merge, and not a `COALESCE` of the two. Kusto's choice
  of table is arbitrary; this one always picks the first.
- **Id types are a refusal, not a cast.** SEM1019 and SEM1079 are compile-time
  in Kusto, but this schema has names, not types
  ([column types](column-types-proposal.md)). Left alone, DuckDB would
  implicitly cast and match `'1'` with `1` — an answer where Kusto refuses. So
  the SQL carries a guard that compares `typeof()` on both sides and raises
  Kusto's message, the same run-time guard technique R23 uses. The same guard
  covers a property name shared by two node tables with different types, which
  Kusto drops rather than merging.

### 3.3 Fixed-length patterns

Each edge in the pattern becomes a reference to `_g1_e` — or, for `--`, to
`_g1_eu`, `_g1_e` once in each orientation **under the same `_eid`**. Each
distinct node variable becomes one join of `_g1_n`; an anonymous `()` gets a
hidden variable of its own; a repeated variable becomes an
`IS NOT DISTINCT FROM` between the two edge endpoints. Two traps live here:

- `_g1_eu` is `UNION ALL`. A self-loop then appears twice, and so matches twice,
  which is what Kusto does (§2.2). `UNION`, or a `DISTINCT` added to tidy it
  up, would be the wrong fix.
- Both orientations of an undirected edge share one `_eid`, so `unique_edges`
  refuses to walk it back (§2.3). Numbering the reversed copy separately would
  let `(a)--(b)--(c)` match `A–B–A`.

`cycles=` becomes predicates:

| Mode | SQL |
|---|---|
| `unique_edges` | `e1._eid <> e2._eid` for each pair of fixed edges, `NOT list_contains(p._eids, e._eid)` between a fixed and a variable-length edge, `NOT list_has_any(p._eids, q._eids)` between two variable-length edges |
| `all` | nothing |
| `none` | `IS DISTINCT FROM` between every pair of distinct node variables; each variable-length edge a simple path (as `graph-shortest-paths` uses), whose inner nodes are none of the pattern's nodes nor another path's. *Corrected during implementation: this row first refused variable-length edges, but two of the documentation's own examples use them, and "each path is simple" explains the `(a)-[p*1..3]->(a)` measurement. Inner nodes shared between two paths are unmeasured, and excluded* |

Several comma-separated sequences are one join graph over shared variables; a
disconnected pattern is refused, as SEM1011 refuses it. No `ORDER BY` is
emitted: Kusto's output order is unspecified.

### 3.4 Variable-length edges

One recursive CTE per variable-length edge, carrying `_start`, `_end`, `_eids`,
the edges as a list of structs, the inner nodes as a list of structs, and
`_len`. The seed is the one-edge paths, plus every node as a zero-length path
when `lo = 0`; each step appends an edge whose near end is `_end`, with
`_len < hi` and, under `unique_edges`, `NOT list_contains(_eids, e._eid)`. The
final filter is `_len BETWEEN lo AND hi`. Direction works as in §3.3, with
`_g1_eu` for `-[p*..]-`.

| KQL | SQL |
|---|---|
| `p` | `to_json(_edges)` — each edge as its bag, §2.5 |
| `map(p, expr)` | `list_transform(_edges, x -> expr)`, properties read from `x` |
| `map(inner_nodes(p), expr)` | the same over `_inner` |
| `all(p, c)` | `coalesce(list_bool_and(list_transform(_edges, x -> coalesce(c, false))), true)` |
| `any(p, c)` | `coalesce(list_bool_or(list_transform(_edges, x -> coalesce(c, false))), false)` |
| `node_degree_in(n)` | a count over `_g1_e` by `_dst`, joined on the node id |
| `labels(x)` | `'[]'` — a constant, for a `make-graph` graph |
| `node_id(x)` | `tostring()` of the id, through the existing R20 rendering |

The inner `coalesce(c, false)` is §2.4's null rule; the outer one is the
zero-length rule, because `list_bool_and([])` is null rather than `true`.

**What the walk starts from.** Seeding from every edge is exact, and it can
spend every walk of the graph on a one-row question. Reported: three million
walks of a component the match never touched, enumerated before
`start.Id == 'main-0'` was tested, ran out of 1 GB. So the `where` is split at
its top-level `and`s first:

- conjuncts that read only the path's **start** node become a seed set, the
  `DISTINCT` (node, partition) pairs the one-edge and zero-length rows join;
- if only the **end** node is constrained, the walk is built backwards from
  there — each step prepends an edge whose far end is `_start`, so `_eids`
  stays in pattern order;
- a top-level `all(p, c)` or `all(inner_nodes(p), c)` whose `c` names no
  pattern variable is checked on each edge or inner node as it is added, with
  the same null rule, since a prefix that fails it cannot recover;
- and a step carries only the properties the query reads: `map(p, EdgeId)`
  carries one field per edge and no inner nodes, except that `cycles=none`
  needs their ids.

None of this changes what is returned: the outer `WHERE` still applies every
conjunct, so an earlier copy can only drop walks that it would drop anyway. A
conjunct that relates two variables, one side of an `or`, `any`, `map`, and
anything volatile (a second evaluation would be a second draw) are not moved.

**Path explosion is real and stays visible.** Under `unique_edges` or `all`,
the number of paths grows exponentially with `hi`, and DuckDB keeps every
intermediate path. A cap that dropped paths would be a wrong answer, so there
is no cap: `servertimeout` and DuckDB's own interrupt bound the damage, loudly.
Seeding only helps when the `where` constrains an end. A pattern that nothing
constrains still enumerates, and materializes, every walk in range, and needs
memory or spill space to match. `tests/test_graph_pushdown.py` holds both
sides: the reported query within 256 MB with spilling disabled, and the
unconstrained one over it.

### 3.5 `where` and `project`

- The scope is the pattern's variables plus the query's `let`s and parameters;
  a bare column name is refused as SEM0100 refuses it.
- `v.prop` reads the column of `v`'s join alias. An unknown property is refused
  at translation time, which needs the node and edge column names; when they
  are not known (a table missing from `schema(con)`), the operator is refused
  rather than guessed at.
- A whole variable is its bag (§2.5), built from a filtered key/value list so
  that null **and empty-string** properties are left out while zero and `false`
  stay. `json_object()` is the obvious choice and the wrong one: it keeps them
  all.
- `p.w` in `project` is `map(p, w)`; in `where` it is refused, as SEM1054
  refuses it.
- Column names reuse the existing `project` naming, extended with exactly the
  spellings in §2.5. An unaliased expression whose spelling has not been
  measured is refused with "name this column", because a wrong column name is a
  wrong answer to every downstream operator.
- A node and an edge with the same name are refused. The emulator accepts them,
  but a query that does it is almost certainly a typo, and §2.2 measured one
  reading of it, not the rule.

### 3.6 `graph-shortest-paths`

Phase 1 supports the documented shape: one sequence, **one variable-length edge
and no fixed edges**, in any direction. Everything else is refused, including
the shapes §2.9 found the emulator accepting.

The SQL enumerates simple paths up to `hi` (a list of visited node ids, the
start included, tested with `list_contains`), applies the `where` to complete
paths, then keeps the shortest per `(a, b)`:
`QUALIFY rank() OVER (PARTITION BY a._nid, b._nid ORDER BY _len) = 1` for
`output=all`, and `row_number()` for `output=any`. A breadth-first search
(`USING KEY`) gives the same answer when `lo ≤ 1` and the `where` holds no path
predicate, so it is an optimisation to add later under those conditions, not
the definition.

A comparison whose two sides reference different pattern elements is refused.
That is a superset of what CRT0001 refuses, and a superset is safe.

### 3.7 `graph-to-table`

- `nodes`: the node relation without its hidden columns, as §2.7 measured. A
  graph with no node properties at all is refused: Kusto returns zero columns,
  and a DuckDB result cannot have none.
- `edges`: the edge relation without `_eid`, `_src` and `_dst`.
- `with_node_id=`, `with_source_id=`, `with_target_id=`: **refused** until
  KQL's `hash()` exists here. It is xxhash64, which DuckDB lacks, and
  DuckDB's own `hash()` is a different function
  ([support matrix](kql-support.md)) — mapping to it would print plausible,
  wrong ids.
- `nodes as N, edges as E; …`: refused, as `fork` is: several result tables.

### 3.8 `graph-mark-components`

- **Weak:** label propagation over `_g1_eu` in a recursive CTE — each node
  takes the smallest node rank it can reach — run to its fixed point, then
  `dense_rank() - 1` for zero-based, consecutive ids. `UNION` (set semantics)
  is what makes the recursion terminate.
- **Strong:** needs reachability both ways, which is a transitive closure —
  quadratic in nodes. Phase 1c, and only once a differential sweep has looked
  at a graph bigger than a toy.
- **Numbering** is arbitrary in Kusto, and §2.7's strong example numbers its
  components 1, 0, 3, 2. Tests therefore compare the **partition** a numbering
  induces, never the numbers. The general comparison layer must not learn to
  ignore relabelling, because that would hide a wrong answer anywhere else.
- A pre-existing property with the component name is refused: Kusto returns
  two columns with the same name (§2.7), and that cannot be reproduced
  faithfully.

### 3.9 Parser patch `004`

The vendored grammar — unchanged upstream as of 2026-10-01 — cannot parse
most graph queries. It reads a pattern as **one element per comma**, so
`(a)-->(b)` already fails at the arrow, and it has no anonymous node, no
second node table and no `with_node_id=` on `graph-to-table`. Patch `004`:

- `graphMatchPattern` becomes a sequence: a node, then any number of
  edge-then-node pairs.
- The node name and the named edge's name become optional (`()`, `-[*1..3]->`).
- `makeGraphTablesAndKeysClause` takes a second `, Table on Column`.
- `WITH_NODE_ID` joins `relaxedQueryOperatorParameter`'s accepted tokens.

It is recorded in `grammar/UPSTREAM.md` like `001`–`003`, and the corpus parse
count may not go down.

## 4. Phases

**Phase 1a — fixed-length matching.** Patch `004` and the re-harvest it
unlocks (§7); `make-graph` with
`with_node_id`, one or two node tables, null ids and the run-time type guard;
`graph-match` with fixed-length patterns, all three `cycles=` modes, property
access, bags and names; `graph-to-table nodes|edges` without hash ids. A new
TRANSLATION.md rule, R25, states §3.2 and §3.3 and cites its trap tests.

**Phase 1b — paths.** Variable-length edges; `map`, `all`, `any`,
`inner_nodes`, `node_degree_in`, `node_degree_out`, `labels`, `node_id`.

**Phase 1c — the rest of the transient family.** `graph-shortest-paths` in
the shape §3.6 allows; `graph-mark-components`, weak first;
`partitioned-by`, which is the partition column added to every join key and
every `PARTITION BY` in §3.2–§3.4.

Each phase lands like the stored-functions work did: trap tests first, then a
differential sweep through `tools/differential.py` whose tally is written into
this file's status, then the regenerated support matrix.

**Phase 2 — persistent graphs.** §5.

**Not scheduled.** Undirected `make-graph` (the emulator has it switched off,
so there is nothing to measure against); `cycles=none` with a variable-length
edge, until §2.3's open rule is explained; the hash-id options, until
`hash()` exists; multiple `graph-to-table` outputs, until multiple results
exist anywhere.

## 5. Phase 2 — persistent graphs

### 5.1 Graph models

A graph model is the persistent counterpart of a stored function: a definition
that lives in the database and is expanded where it is used. It is registered
the same way, locally, and never fetched from anywhere:

```python
duckdb_kql.set_graph_models({
    "SocialNet": {                     # the JSON body of .create-or-alter graph_model
        "Schema": {"Nodes": {"Person": {"name": "string", "age": "long"}}, "Edges": {}},
        "Definition": {"Steps": [
            {"Kind": "AddNodes", "Query": "People", "NodeIdColumn": "id", "Labels": ["Person"]},
            {"Kind": "AddEdges", "Query": "Knows", "SourceColumn": "a", "TargetColumn": "b"},
        ]},
    },
})
```

`graph_models=` threads through `kql`, `to_sql`, `KustoClient` and the
server exactly as `functions=` does, and is validated when registered, as Kusto
validates it at `.create-or-alter`: every step query must lower, and there must
be at least one `AddEdges` step.

`graph('M', true)` lowers each step query like a stored function's body and
builds the same two relations as §3.2, with these differences, all from §2.8:

- Steps run in order, and a node in two `AddNodes` steps is **merged** — the
  later step wins property by property, and the earlier step's other
  properties survive. That is the opposite of `make-graph`, so it is a separate
  code path, and each path gets its own trap test.
- Every element carries a `_labels` list: the step's static `Labels` plus the
  value of its `LabelsColumn`, which stays a property as well. `labels(x)`
  stops being a constant, and `labels(n) has "Person"` works.
- Properties declared in the `Schema` exist even when no step produces them,
  so they are typed nulls rather than SEM1040.

### 5.2 Snapshots

`graph('M')` is the latest snapshot, and a snapshot is **stale by design**
(§2.8: Carol missing). Answering `graph('M')` from the live tables would
therefore be a wrong answer, however convenient. So:

- **Phase 2a** supports `graph('M', true)` and refuses `graph('M')` with
  Kusto's own SEM1076 wording — exactly what Kusto answers for a model that has
  no snapshot, so it is faithful rather than merely cautious — and refuses
  `graph('M', 'S')` because no snapshot `S` can exist yet.
- **Phase 2b** makes snapshots real: `.make graph_snapshot S from M`
  materialises the two relations into DuckDB tables in a reserved schema;
  `graph('M')` reads the newest, `graph('M', 'S')` a named one; and
  `.show graph_snapshots M` and `.drop graph_snapshot M.S` come with it. Those
  are writes, so they go through `control.is_write_command`, and where
  snapshots persist between sessions is the same open question as in the
  [session-state proposal](session-state-proposal.md).

### 5.3 What phase 2 will not fix

Thirteen of the sixteen rejected corpus cases that use a graph operator query
Microsoft's hosted sample graphs (`graph('Simple')`,
`graph('BloodHound_Entra')`, `graph('LDBC_*')`), which exist neither in the
emulator nor in a local database. The other three stop at `make-graph` with no
graph operator after it — two inside a `let` and nothing after, one for Kusto
Explorer to draw. None of the sixteen has a ground truth to pass against, so
they stay `xfail`.

## 6. Alternatives considered

- **The DuckPGQ community extension** (SQL/PGQ inside DuckDB). It installs from
  the community repository at run time, as a native binary tied to the DuckDB
  version; `to_sql()` output would stop being SQL that runs on a stock DuckDB;
  and its path modes would still need mapping onto `cycles=` and measuring
  against the emulator, so it would save rendering work, not semantic work. Not
  for phase 1. It is worth revisiting only as an opt-in backend behind the same
  refusals, once the plain-SQL version exists to compare it to.
- **Python UDFs (networkx or similar).** Not SQL, so not `to_sql()`, and not
  layer 0.
- **Temporary tables for the graph.** A transient graph would become a write,
  and one query would become several statements.

## 7. Tests

`tests/test_graph.py` holds the trap tests. Each records what was measured,
what the obvious translation answers instead, and why — the model is
`tests/test_tostring.py`:

| Trap | The obvious translation answers |
|---|---|
| `4 → null → 3` is a path | nothing: `=` never matches a null |
| an edge-only null id is a node | the node is lost to `NOT IN` |
| identical edge rows are two edges | one, after a `DISTINCT` |
| an undirected self-loop matches twice | once, after `UNION` |
| `(a)--(b)--(c)` on one edge is empty | `A–B–A`, with per-orientation edge ids |
| the first node table wins whole | a merged node (the documented behaviour) |
| edge ids are stable across references | random `unique_edges` results, if the CTE is inlined |
| a null condition fails `all` and `any` | `all` true, from `list_bool_and` skipping nulls |
| an empty `all` is true | null, from `list_bool_and([])` |
| a bag omits null and empty-string properties | `{"age": null}`, from `json_object` |
| a shortest path is simple, within range | the walk `A→B→A→C` |
| `where` picks eligible paths before "shortest" | nothing, or the shortest path then filtered |
| `long` id `1` never matches string id `'1'` | a match, from an implicit cast |

A differential sweep, kept in the scratchpad as the stored-functions one was,
runs every query in §2 through both engines, refusals included; its tally goes
into this file's status line.

The corpus will move little, and that should be said up front. Only two graph
cases have ground truth: `graph-to-table-operator-00` needs `hash()`, and
`graph-mark-components-operator-00` asserts Kusto's arbitrary component
numbers, so it can only pass by a coincidence of numbering. The examples on the
`graph-match`, `make-graph`, `graph-shortest-paths` and graph-function pages
are **not in the corpus at all**: `tools/harvest_docs.py` drops a block the
parser rejects, and these fail on the pattern grammar that §3.9 fixes.
Re-harvesting at the pinned docs commit once patch `004` lands is therefore
the second task of phase 1a, right after the patch, so that
`BASELINE_PASSING` measures the work instead of only the trap tests.
