# Proposal — `macro-expand` and entity groups

> **Status: proposal.** Nothing here is implemented. Every semantic claim in §2
> was measured on the pinned Kusto Emulator against two databases
> (`NetDefaultDB` and a `.create database DB2 volatile`), and the measurement is
> quoted next to the rule it justifies.

## 1. The one-sentence version

`macro-expand` is not a new execution model. It is **`union` with the source
rewritten per entity** — measured, not assumed — so it lands on top of the R15
union machinery that already exists, and the only genuinely new thing is
resolving an *entity group* to a list of DuckDB databases. That resolution is
the same shape of problem as `cluster()`, and it should reuse the same
caller-supplied-mapping design.

```kusto
macro-expand entity_group [database('A'), database('B')] as scope (
    scope.Events | where Level == 'Error' | count
)
```
means
```kusto
union (database('A').Events | where Level == 'Error' | count),
      (database('B').Events | where Level == 'Error' | count)
```

## 2. Measured semantics

The evidence for treating this as a union desugar, rather than something new.

### 2.1 It is a union, branch per entity

| Query | Result |
|---|---|
| `macro-expand EG as s (s.MT)` | `x,s` = `[7,'z'], [1,'a'], [2,'b']` — every entity's rows |
| `macro-expand EG as s (s.MT \| count)` | `Count` = `[2], [1]` — **one row per entity**; the body runs N times |
| `macro-expand EG as s (s.MT) \| count` | `Count` = `[3]` — piping *after* sees the union |
| `macro-expand EG as s (s.MT \| summarize n=count())` | `n` = `[1], [2]` |

So the body is evaluated **once per entity** and the results are concatenated.
An aggregate inside the parentheses does not see the other entities.

### 2.2 Column unification is R15's, exactly

With `Diff` declared `(x, only1)` in one database and `(x, only2)` in the other:

| Group order | Columns |
|---|---|
| `[database('NetDefaultDB'), database('DB2')]` | `x, only1, only2` |
| `[database('DB2'), database('NetDefaultDB')]` | `x, only2, only1` |

Outer union of the column names in **first-appearance order** — the same rule
R15 already implements, down to the ordering. Missing columns are null.

### 2.3 `withsource=` follows R15's labelling rule, including the fallback

| Body | Label |
|---|---|
| `s.MT` (bare table), entity is *another* database | `database("DB2").MT` |
| `s.MT` (bare table), entity is the *current* database | `MT` |
| `s.MT \| where x > 1` (a pipeline) | `union_arg0`, `union_arg1` |
| `s.MT \| count` | `union_arg0`, `union_arg1` |

This is R15's `_table_label` verbatim: a bare table branch reports its table
name, everything else reports `union_argN` counting from zero. The only
addition is that the rewritten qualifier appears in the name, and is **elided
when the entity is the current database** — confirmed against all three of
single-entity-current, single-entity-other, and two-entity.

### 2.4 What Kusto refuses

| Query | Error |
|---|---|
| a group containing the same entity twice | `SEM0614: Entity group doesn't allow duplicate values` |
| an entity whose database does not exist | `SEM0056: Errors occurred while resolving remote entities` |
| a table missing in one entity, without `isfuzzy` | `SEM0100: Failed to resolve table expression` |
| `macro-expand` nested inside another | `SEM0611: macro '<name>' name is invalid` |
| the scope name used bare, as a source | `SEM0608: Unexpected entity in entity_group` |
| an empty group `entity_group []` | `SYN0002: Missing expression` |

`isfuzzy=true` drops an entity whose *table* is missing, and the surviving
entity's rows come back normally — measured. `hint.*` parameters are accepted
and cannot change the result.

### 2.5 Row order

`[database('A'), database('B')]` and `[database('B'), database('A')]` returned
rows in the same order, which was not the group's order in either case. R10
already says row order is undefined without a terminal `sort`; nothing here
changes that, and no test should pin it.

## 3. Where the entity list comes from

Three syntaxes, all of which the vendored grammar already parses
(`MacroExpandOperator`, `MacroExpandEntityGroup`, `EntityGroupExpression`,
`LetEntityGroupDeclaration` — no grammar work is needed):

```kusto
-- (a) inline
macro-expand entity_group [cluster('c1').database('d1'), database('d2')] as s (…)

-- (b) let-bound
let EG = entity_group [database('d1'), database('d2')];
macro-expand EG as s (…)

-- (c) named, defined on the cluster by `.create entity_group EG (…)`
macro-expand EG as s (…)
```

**(a) and (b) are self-describing** — the entities are in the query text, and
each one is a `database(...)` or `cluster(...).database(...)` reference that the
existing `qualify()` pass already knows how to resolve. Nothing new is required
beyond the desugar.

**(c) is not.** A named entity group is cluster-side state created by a control
command; there is no cluster here, so the name resolves to nothing. This is
precisely the `cluster()` situation, and it should get precisely the
`cluster()` treatment.

## 4. Proposed API — `entity_groups=`, mirroring `clusters=`

A new module `entity_groups.py` shaped like `clusters.py`:

```python
EntityGroupMap = dict[str, list[str]]      # group name -> entity references

duckdb_kql.set_entity_groups({
    "SecurityDatabases": [
        "cluster('prod.eastus.kusto.windows.net').database('Security')",
        "database('SecurityArchive')",
    ],
})
duckdb_kql.get_entity_groups()
duckdb_kql.kql(con, q, entity_groups={...})   # overrides the global, per call
```

The entries are **KQL entity references as text**, not pre-resolved DuckDB
names, and they are parsed by the same lowering path as an inline group. That
matters for three reasons:

* it is what `.show entity_groups` returns — `["database('NetDefaultDB')"]` —
  so a user can copy the real definition across verbatim;
* a `cluster(...)` entity then resolves through the **existing** `clusters=`
  mapping, so the two features compose instead of each having its own notion of
  what a remote database is;
* an entity group and an inline group become the same thing after resolution,
  so there is one code path and not two that must agree.

Precedence follows `clusters=` exactly: a per-call argument replaces the global
rather than merging, and `set_entity_groups(None)` clears it.

**An unmapped named group must raise**, for the same reason an unmapped
`cluster()` raises: silently treating `macro-expand SecurityDatabases` as the
current database would answer a question about several databases with one, and
return plausible rows while doing it. The error should name the group and list
the ones that are mapped.

The server (`serve`) and `KustoClient` take the same argument and pass it
through, as they already do for `clusters`.

## 5. Implementation sketch

The desugar means there is no new emitter and no new column-tracking code.

1. **IR.** No new node. Lowering produces an `ir.Union`. Optionally an
   `ir.EntityGroupRef` is unnecessary — resolution happens during lowering,
   where the mapping is already threaded (`qualify()` takes `clusters`; it
   grows an `entity_groups` parameter beside it).

2. **Lowering** (`_lower_macro_expand`):
   - read the optional `isfuzzy` / `withsource` / `hint.*` parameters off the
     `RelaxedQueryOperatorParameter` children, reusing `_lower_union`'s parser;
   - resolve `MacroExpandEntityGroup` to a list of entity references — inline
     and `let`-bound come from the tree, a named one from the mapping;
   - refuse a duplicate entity (SEM0614) and an empty group;
   - lower the body **once per entity**, passing the scope name and that
     entity's target so a path rooted at the scope becomes a `TableRef` (see 3);
   - return `ir.Union(branches=…, kind="outer", with_source=…, isfuzzy=…)`.

3. **The scope rewrite** is the one genuinely new mechanism, and it must happen
   **during** lowering rather than as a pass over the IR. `scope.MT` is not a
   table reference to the lowerer: outside a macro-expand it lowers to
   `PathAccess(ColumnRef("scope"), [MT])` — dynamic property access on a column
   — because nothing distinguishes the two syntactically. Rewriting it
   afterwards would mean pattern-matching a `PathAccess` and hoping it was
   meant as a table.

   In the tree it is a `FunctionCallOrPathPathExpression` whose root is a bare
   `SimpleNameReference`, which is the same node class `_lower_qualified_table`
   already handles for `database("X").T` — the only difference is a bare name
   where that has a `database(...)` call. So the body is lowered once **per
   entity** with the scope name in hand, and a path expression rooted at that
   name becomes `TableRef(name, database=…, cluster=…)` directly. Lowering the
   body N times is also what makes the `let`-inside-the-body case work, which is
   measured: `macro-expand EG as s (let t = s.MT; t | count)` returns one count
   per entity.

4. **Refusals**, each because Kusto refuses it and a silent answer would be
   worse: a nested `macro-expand` (SEM0611), a bare scope reference (SEM0608),
   an unmapped named group, and a duplicate entity.

5. **`withsource` labels** need one addition to R15's `_table_label`: the
   rewritten qualifier is part of the name unless the entity is the current
   database. That is a change to one function with a measured rule.

## 6. Phasing

| Phase | Scope | Why this order |
|---|---|---|
| 1 | inline + `let`-bound groups; `isfuzzy`; `withsource`; the refusals | No new configuration surface, and it is the whole desugar. Testable end to end against the emulator today. |
| 2 | `entity_groups=` mapping, global setter, CLI/server/client pass-through | Only phase 2 needs a design decision from the user; phase 1 does not block on it. |
| 3 | `.show entity_groups` as a control command over the mapping | Cheap once phase 2 exists, and it makes the mapping inspectable the way `.show databases` is. |

## 7. Open questions

1. **Should a named group also be definable in KQL?** `.create entity_group`
   is a control command that writes cluster state. `duckdb-kql` has ingestion
   commands behind `allow_write`, so it *could* store one in memory — but a
   group that exists only for the session is a different object from one the
   real cluster has, and I would rather not blur that. Recommendation: no,
   mapping only.

2. **Should an entity be allowed to name a DuckDB catalog directly**, e.g.
   `{"EG": ["mydb"]}` rather than `["database('mydb')"]`? It is friendlier, but
   it makes the map's values two languages at once. Recommendation: accept both,
   treating a bare identifier as `database('…')`, and say so in one line of
   docs.

3. **`withsource` label for a `cluster(...)` entity** is unmeasured — the
   emulator cannot resolve a remote cluster (it fails at DNS), so the label
   format for that case is a guess. Phase 1 should either refuse `withsource`
   together with a cluster-qualified entity, or land it with the divergence
   recorded, rather than inventing a format.

4. **Does `macro-expand` compose with `database=`?** The per-call `database=`
   qualifies unqualified tables. Inside a macro-expand body every table is
   qualified by the scope rewrite, so the two should not interact — but that
   deserves a test rather than an assumption.
