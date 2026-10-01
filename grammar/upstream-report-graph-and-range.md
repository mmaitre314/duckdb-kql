# Upstream bug report — draft for `microsoft/Kusto-Query-Language`

Ready to paste as a GitHub issue. It describes `grammar/Kql.g4` and
`grammar/KqlTokens.g4` at upstream commit
`6ad55002f78cc6a99870a524bb3b5c796b170b23`, unchanged on `master` as of
2026-10-01. Our local fix is `PATCH duckdb-kql/004` (see `UPSTREAM.md`).

---

**Title:** ANTLR reference grammar rejects documented graph queries (`graph-match` patterns, `make-graph`) and `1..3` ranges

### Summary

The ANTLR reference grammar in `grammar/` cannot parse most of the documented
graph-semantics queries, nor a numeric range written without spaces
(`between (1..3)`, `-[e*1..3]->`). All of the queries below are taken from, or
follow the syntax of, the Microsoft Learn KQL reference, and Kusto accepts them.
The hand-written parser in `src/Kusto.Language` is not affected; this is about
the `.g4` files only.

Found while generating a Python parser from the grammar with ANTLR 4.13.2
(`java -jar antlr-4.13.2-complete.jar -Dlanguage=Python3 -visitor Kql.g4`).

### 1. A graph pattern is parsed as one element per comma

```antlr
graphMatchOperator:
    GRAPHMATCH
    (Parameters+=relaxedQueryOperatorParameter)*
    Patterns+=graphMatchPattern (',' Patterns+=graphMatchPattern)*
    ...

graphMatchPattern:
      Node=graphMatchPatternNode
    | UnnamedEdge=graphMatchPatternUnnamedEdge
    | NamedEdge=graphMatchPatternNamedEdge;
```

Each comma-separated pattern is a *single* node or edge, so any pattern with an
edge in it fails. `graph-shortest-paths` reuses the rule and fails the same way.

```kusto
let E = datatable(s:string, t:string)["A","B"];
E | make-graph s --> t with_node_id=id
| graph-match (a)-[e]->(b) project a.id, b.id
```

```
line 3:16 mismatched input '-[' expecting {<EOF>, ';'}
```

Documented syntax: [graph-match operator](https://learn.microsoft.com/kusto/query/graph-match-operator),
"Graph pattern notation" — a pattern is a sequence of nodes joined by edges,
and several such sequences may be separated by commas.

**Suggested fix:**

```antlr
graphMatchPattern:
    Nodes+=graphMatchPatternNode (Edges+=graphMatchPatternEdge Nodes+=graphMatchPatternNode)*;

graphMatchPatternEdge:
      UnnamedEdge=graphMatchPatternUnnamedEdge
    | NamedEdge=graphMatchPatternNamedEdge;
```

### 2. Anonymous nodes and anonymous variable-length edges are rejected

`graphMatchPatternNode` and `graphMatchPatternNamedEdge` both require a name,
but the notation table documents `()` for an anonymous node and
`-[*3..5]-` for an anonymous variable-length edge.

```kusto
E | make-graph s --> t with_node_id=id
| graph-match (a)-->()-[*1..3]->(b) project a.id, b.id
```

**Suggested fix:** make both names optional.

```antlr
graphMatchPatternNode:
    '(' (Name=identifierOrKeywordOrEscapedName)? ')';

graphMatchPatternNamedEdge:
    OpenBracket=(DASH_OPENBRACKET | LESSTHAN_DASH_OPENBRACKET)
    (Name=identifierOrKeywordOrEscapedName)?
    (Range=graphMatchPatternRange)?
    CloseBracket=(CLOSEBRACKET_DASH_GREATERTHAN | CLOSEBRACKET_DASH)
    ;
```

### 3. `1..3` lexes as the real `1.` followed by `.3`

`NonIntegerNumber` accepts a trailing-dot real (`1.`), and the lexer's longest
match prefers it to the integer `1`. So `1..3` becomes `1.` then `.3`, and both
the variable-length edge range and `between` fail whenever the range is written
without spaces:

```kusto
range x from 1 to 3 step 1 | where x between (2..3)
```

```
no viable alternative at input '.3'
```

```kusto
E | make-graph s --> t with_node_id=id
| graph-match (a)-[p*1..3]->(b) project a.id, b.id
```

The graph documentation writes every range this way (`-[e*1..5]-`,
`-[reports*1..5]-`), so this blocks most of its examples even after fix 1.

**Suggested fix:** a trailing-dot real must not be followed by a second dot.
ANTLR has no target-neutral negative lookahead, so this needs a semantic
predicate in each target's language; for the Python target:

```antlr
fragment NonIntegerNumber:
      ('0'..'9')+ '.' {self._input.LA(1) != 46}? ('0'..'9')* Exponent?
    | ('0'..'9')+ Exponent
;
```

(`46` is `'.'`. The C# and Java spelling is `{_input.LA(1) != '.'}?`.) `1.`,
`1.5`, `1.5e3` and `1.e2` all still lex as reals.

### 4. `make-graph` accepts only one node table

```antlr
makeGraphTablesAndKeysClause:
    WITH Table=invocationExpression ON Column=simpleNameReference;
```

The documented syntax is `with Nodes1 on NodeId1 [, Nodes2 on NodeId2]`
([make-graph operator](https://learn.microsoft.com/kusto/query/make-graph-operator)):

```kusto
E | make-graph s --> t with People on pid, Companies on cid
| graph-match (a)-->(b) project a, b
```

```
mismatched input ',' expecting {<EOF>, ';'}
```

**Suggested fix:**

```antlr
makeGraphTablesAndKeysClause:
    WITH Tables+=makeGraphTableAndKey (',' Tables+=makeGraphTableAndKey)?;

makeGraphTableAndKey:
    Table=invocationExpression ON Column=simpleNameReference;
```

### 5. `partitioned-by` requires a dotted path

```antlr
makeGraphPartitionedByClause:
    PARTITIONEDBY Entity=entityPathOrElementExpression '(' SubQuery=contextualSubExpression ')';
```

`entityPathOrElementExpression` needs at least one `.` or `[...]`, but the
documented form is a plain column, `partitioned-by PartitionColumn (GraphOperator)`:

```kusto
E | make-graph s --> t with N on id partitioned-by tenant (graph-match (a)-->(b) project a.id)
```

```
mismatched input '(' expecting {'.', '['}
```

**Suggested fix:** `PARTITIONEDBY Column=simpleNameReference '(' ... ')'`.

### 6. `with_node_id=` and `output=` are not accepted as operator parameters

`with_node_id` and `output` are keyword tokens (`WITH_NODE_ID`, `OUTPUT`), and
`relaxedQueryOperatorParameter` lists neither, so these documented forms fail:

```kusto
E | make-graph s --> t with_node_id=id | graph-to-table nodes with_node_id=NodeId
```

```kusto
E | make-graph s --> t with_node_id=id
| graph-shortest-paths output=all (a)-[p*1..3]->(b) project a.id, b.id
```

**Suggested fix:** add `WITH_NODE_ID` and `OUTPUT` to the `NameToken`
alternatives of `relaxedQueryOperatorParameter`.

### Verification

With all six fixes, ANTLR generation is clean, every query above parses, and
the 1,285 documentation queries we already parsed produce identical parse
results. Re-harvesting the KQL reference documentation then also parses the
code blocks on the `graph-match`, `graph-shortest-paths`, `make-graph` and graph
function pages, which were previously all rejected.
