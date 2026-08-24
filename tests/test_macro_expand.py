"""L5 trap tests — ``macro-expand`` and entity groups (docs/TRANSLATION.md R16).

`macro-expand` is not a second way to combine results. Measured on the emulator
against two databases, it is **`union` with the source rewritten per entity**:
the body runs once per entity and the results concatenate, columns unify by
R15's rule in first-appearance order, and `isfuzzy` behaves as it does there.
So it lowers to an `ir.Union` and inherits all of that rather than growing a
parallel implementation that has to be kept in step.

The trap that shapes the implementation: `scope.T` is **not** a table reference
to the lowerer. Outside a macro-expand it is dynamic property access on a
column called `scope`, and nothing in the syntax distinguishes the two — so the
body has to be lowered once per entity *with the scope bound*, not lowered once
and rewritten afterwards.

Three ways to name the entities, and only one needs configuration: inline and
`let`-bound groups carry their entities in the query text, while a **named**
group is cluster-side state with no local equivalent and must be mapped, on the
same reasoning as `cluster()` — see `duckdb_kql.entity_groups`.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlSchemaError, KqlUnsupportedError

duckdb = pytest.importorskip("duckdb")

GROUPS = {"EG": ["database('d1')", "database('d2')"]}
EG = "entity_group [database('d1'), database('d2')]"
REV = "entity_group [database('d2'), database('d1')]"


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    c.execute("ATTACH ':memory:' AS d1")
    c.execute("ATTACH ':memory:' AS d2")
    # Same table name, different rows — so a result that came from only one
    # entity is visibly wrong rather than plausibly right.
    c.execute("CREATE TABLE d1.MT(x BIGINT, s VARCHAR)")
    c.execute("INSERT INTO d1.MT VALUES (1,'a'),(2,'b')")
    c.execute("CREATE TABLE d2.MT(x BIGINT, s VARCHAR)")
    c.execute("INSERT INTO d2.MT VALUES (7,'z')")
    # Same name, different schema — for the column-unification rule.
    c.execute("CREATE TABLE d1.Diff(x BIGINT, only1 VARCHAR)")
    c.execute("INSERT INTO d1.Diff VALUES (1,'a')")
    c.execute("CREATE TABLE d2.Diff(x BIGINT, only2 BIGINT)")
    c.execute("INSERT INTO d2.Diff VALUES (2,25)")
    # Present in d2 only — for isfuzzy.
    c.execute("CREATE TABLE d2.Extra(q BIGINT)")
    c.execute("INSERT INTO d2.Extra VALUES (9)")
    return c


def _cols(con, kql, **kw):
    return list(duckdb_kql.kql(con, kql, **kw).columns)


def _rows(con, kql, **kw):
    return sorted(
        duckdb_kql.kql(con, kql, **kw).fetchall(), key=lambda r: tuple(str(x) for x in r)
    )


# ---------------------------------------------------------------------------
# R16a — the body runs once per entity, and the results union
# ---------------------------------------------------------------------------


def test_every_entitys_rows_come_back(con) -> None:
    assert _rows(con, f"macro-expand {EG} as s (s.MT)") == [
        (1, "a"), (2, "b"), (7, "z")
    ]


def test_an_aggregate_inside_runs_per_entity(con) -> None:
    """`count` inside the parentheses is per entity; outside it is over all."""
    assert _rows(con, f"macro-expand {EG} as s (s.MT | count)") == [(1,), (2,)]
    assert _rows(con, f"macro-expand {EG} as s (s.MT) | count") == [(3,)]


def test_summarize_inside_and_outside(con) -> None:
    assert _rows(con, f"macro-expand {EG} as s (s.MT | summarize n = count())") == [
        (1,), (2,)
    ]
    assert _rows(con, f"macro-expand {EG} as s (s.MT) | summarize n = count()") == [(3,)]


def test_a_filter_applies_within_each_entity(con) -> None:
    assert _rows(con, f"macro-expand {EG} as s (s.MT | where x > 1)") == [
        (2, "b"), (7, "z")
    ]


def test_operators_after_the_macro_see_the_union(con) -> None:
    assert _rows(con, f"macro-expand {EG} as s (s.MT | distinct x) | where x > 1") == [
        (2,), (7,)
    ]


# ---------------------------------------------------------------------------
# R16b — column unification is R15's
# ---------------------------------------------------------------------------


def test_differing_schemas_unify_in_first_appearance_order(con) -> None:
    assert _cols(con, f"macro-expand {EG} as s (s.Diff)") == ["x", "only1", "only2"]
    assert _cols(con, f"macro-expand {REV} as s (s.Diff)") == ["x", "only2", "only1"]


def test_a_column_missing_in_one_entity_is_null(con) -> None:
    assert _rows(con, f"macro-expand {EG} as s (s.Diff)") == [
        (1, "a", None), (2, None, 25)
    ]


# ---------------------------------------------------------------------------
# R16c — the scope
# ---------------------------------------------------------------------------


def test_the_scope_can_be_used_twice_in_one_body(con) -> None:
    assert _rows(
        con, f"macro-expand {EG} as s (s.MT | join kind=inner (s.MT) on x | project x)"
    ) == [(1,), (2,), (7,)]


def test_a_let_inside_the_body_sees_the_scope(con) -> None:
    """`let t = s.MT` binds a TABLE, though `s.MT` reads as property access."""
    assert _rows(con, f"macro-expand {EG} as s (let t = s.MT; t | count)") == [
        (1,), (2,)
    ]


def test_a_bare_scope_is_refused(con) -> None:
    """Kusto refuses it too (SEM0608): the scope is a database, not a table."""
    with pytest.raises(KqlUnsupportedError):
        duckdb_kql.kql(con, f"macro-expand {EG} as s (s)")


def test_a_deep_scope_path_is_refused(con) -> None:
    with pytest.raises(KqlUnsupportedError):
        duckdb_kql.kql(con, f"macro-expand {EG} as s (s.a.b)")


def test_a_nested_macro_expand_is_refused(con) -> None:
    """Kusto: SEM0611. Guessing what it would mean is not worth the ambiguity."""
    with pytest.raises(KqlUnsupportedError):
        duckdb_kql.kql(con, f"macro-expand {EG} as a (macro-expand {EG} as b (b.MT))")


# ---------------------------------------------------------------------------
# R16c2 — a `let` may bind a macro-expand
# ---------------------------------------------------------------------------


def test_a_let_can_bind_a_macro_expand(con) -> None:
    """Trap. Reported as "multi-statement", and that was the third of three bugs.

    **What was measured.** On the emulator, against a real entity group::

        let MyTable2 = macro-expand MyEntityGroup as X ( X.MyTable );
        MyTable2

    returns the body's rows, exactly as the bare `macro-expand` does. It is
    ordinary KQL. Here it raised `KqlUnsupportedError: 'multi-statement'`.

    **Three bugs, stacked, each hidden by the one in front of it.**

    1. `lower()` counted query statements with `_find_all(tree,
       "QueryStatement")`. A `macro-expand` body is itself a `Statement >
       QueryStatement`, and `_find_all` returns the *shallowest* matches — so a
       bare `macro-expand` stopped at the top-level statement and counted 1,
       while the same operator under a `let` was no longer shielded by an
       enclosing QueryStatement and its body counted as a second statement.
       The refusal named `multi-statement`, which is why this reads as a parser
       limitation and not as a `macro-expand` bug.

    2. `_lower_lets` ran *before* `_macro_context` was established, so a
       macro-expand inside a binding resolved its group against no context at
       all and reported every group as unmapped.

    3. `_TABULAR_VALUE` held `UnionOperator` under a comment calling `union`
       "the one operator that can also start a query". `macro-expand` starts one
       too, so the binding was classified as a **scalar** and lowered through
       `_lower_expr`, which refused the operator.

    **Why fixing one was not enough.** Each fix only exposed the next, and the
    error text moved from "multi-statement" to "unknown entity group" to
    "expression:MacroExpandOperator" — three unrelated-looking messages for one
    unsupported shape. A fix validated on the first error alone would have
    shipped a query that still did not run.
    """
    assert _rows(
        con, f"let T = macro-expand {EG} as s (s.MT); T"
    ) == [(1, "a"), (2, "b"), (7, "z")]


def test_the_binding_may_shadow_the_table_it_expands(con) -> None:
    """The reported query's exact shape: the `let` name is the body's table name.

    Measured on the emulator, which answers it rather than recursing: `s.MT`
    inside the body is the *entity's* table, so the binding does not capture its
    own reference. Worth pinning — the emitted CTE is named `MT` and reads from
    `d1.MT`/`d2.MT`, and an unqualified emission here would silently self-join.
    """
    assert _rows(
        con, f"let MT = macro-expand {EG} as s (s.MT); MT"
    ) == [(1, "a"), (2, "b"), (7, "z")]


def test_a_bound_macro_expand_is_still_one_statement(con) -> None:
    """The guard has to keep refusing what it was written for.

    Counting only *top-level* query statements is a narrower question than
    "how many QueryStatement nodes are there", not a weaker one.
    """
    with pytest.raises(KqlUnsupportedError, match="multi-statement"):
        duckdb_kql.kql(con, f"let T = macro-expand {EG} as s (s.MT); T; T")
    with pytest.raises(KqlUnsupportedError, match="multi-statement"):
        duckdb_kql.kql(con, "d1.MT; d2.MT")


def test_a_bound_macro_expand_matches_the_unbound_one(con) -> None:
    """The binding is a name, not a second code path — same rows, same columns."""
    bound = f"let T = macro-expand {EG} as s (s.Diff); T"
    bare = f"macro-expand {EG} as s (s.Diff)"
    assert _rows(con, bound) == _rows(con, bare)
    assert _cols(con, bound) == _cols(con, bare)


def test_a_named_group_resolves_from_inside_a_binding(con) -> None:
    """Bug 2 on its own: the mapping has to reach a group named in a binding."""
    assert _rows(
        con, "let T = macro-expand EG as s (s.MT); T", entity_groups=GROUPS
    ) == [(1, "a"), (2, "b"), (7, "z")]


def test_an_unmapped_group_in_a_binding_still_refuses(con) -> None:
    """And when it is genuinely unmapped, the error says so — not "multi-statement"."""
    with pytest.raises(KqlSchemaError, match="entity group"):
        duckdb_kql.kql(con, "let T = macro-expand Nope as s (s.MT); T")


# ---------------------------------------------------------------------------
# R16d — the three ways to name a group
# ---------------------------------------------------------------------------


def test_an_inline_group(con) -> None:
    assert len(_rows(con, f"macro-expand {EG} as s (s.MT)")) == 3


def test_a_let_bound_group(con) -> None:
    assert _rows(
        con, f"let G = {EG}; macro-expand G as s (s.MT)"
    ) == [(1, "a"), (2, "b"), (7, "z")]


def test_a_named_group_from_the_mapping(con) -> None:
    assert _rows(con, "macro-expand EG as s (s.MT)", entity_groups=GROUPS) == [
        (1, "a"), (2, "b"), (7, "z")
    ]


def test_an_unmapped_named_group_is_refused(con) -> None:
    """A named group is cluster-side state; expanding it to nothing would be a
    short answer that looks like data."""
    with pytest.raises(KqlSchemaError) as exc:
        duckdb_kql.kql(con, "macro-expand NoSuchGroup as s (s.MT)")
    assert "entity_groups" in str(exc.value)


def test_the_global_mapping_is_used_when_no_argument_is_passed(con) -> None:
    saved = duckdb_kql.get_entity_groups()
    try:
        duckdb_kql.set_entity_groups(GROUPS)
        assert len(_rows(con, "macro-expand EG as s (s.MT)")) == 3
    finally:
        duckdb_kql.set_entity_groups(None)
        assert saved is None


def test_a_per_call_mapping_replaces_the_global(con) -> None:
    try:
        duckdb_kql.set_entity_groups({"EG": ["database('d1')"]})
        assert len(_rows(con, "macro-expand EG as s (s.MT)")) == 2
        assert len(_rows(con, "macro-expand EG as s (s.MT)", entity_groups=GROUPS)) == 3
    finally:
        duckdb_kql.set_entity_groups(None)


def test_a_duplicate_entity_is_refused(con) -> None:
    """Kusto: SEM0614, "Entity group doesn't allow duplicate values"."""
    with pytest.raises(KqlUnsupportedError):
        duckdb_kql.kql(
            con, "macro-expand entity_group [database('d1'), database('d1')] as s (s.MT)"
        )
    with pytest.raises(ValueError):
        duckdb_kql.set_entity_groups({"D": ["database('d1')", "database('d1')"]})


def test_a_bare_catalog_name_is_not_an_entity() -> None:
    """One language in the mapping. `database('d1')` is barely longer."""
    with pytest.raises(ValueError) as exc:
        duckdb_kql.set_entity_groups({"EG": ["d1"]})
    assert "database('d1')" in str(exc.value)


def test_an_empty_group_is_refused() -> None:
    with pytest.raises(ValueError):
        duckdb_kql.set_entity_groups({"EG": []})


# ---------------------------------------------------------------------------
# R16e — isfuzzy, hints, and withsource
# ---------------------------------------------------------------------------


def test_isfuzzy_drops_an_entity_missing_the_table(con) -> None:
    assert _rows(con, f"macro-expand isfuzzy=true {EG} as s (s.Extra)") == [(9,)]


def test_isfuzzy_can_drop_the_first_entity(con) -> None:
    """The first branch is the query's own source, so dropping it needs the
    schema — `union isfuzzy=true NoSuchTable, UT1` used to raise where
    `union isfuzzy=true UT1, NoSuchTable` worked."""
    assert _rows(con, f"macro-expand isfuzzy=true {REV} as s (s.Extra)") == [(9,)]


def test_a_missing_table_without_isfuzzy_is_an_error(con) -> None:
    with pytest.raises(KqlSchemaError):
        duckdb_kql.kql(con, f"macro-expand {EG} as s (s.Extra)")


def test_hints_are_accepted_and_cannot_change_the_answer(con) -> None:
    assert _rows(con, f"macro-expand hint.concurrency=2 {EG} as s (s.MT)") == _rows(
        con, f"macro-expand {EG} as s (s.MT)"
    )


def test_withsource_is_refused(con) -> None:
    """Measured, Kusto qualifies every label as soon as one branch is in a
    database other than the current one — and here every branch is. Producing
    the bare table name instead would label every entity identically, which is
    the one thing `withsource` is asked not to do. See the proposal for the
    queries that would settle the rest."""
    with pytest.raises(KqlUnsupportedError) as exc:
        duckdb_kql.kql(con, f"macro-expand withsource=Src {EG} as s (s.MT)")
    assert "withsource" in str(exc.value)


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def test_a_cluster_entity_resolves_through_the_clusters_map(con) -> None:
    """The two mappings compose: entity groups speak KQL, `clusters=` resolves
    the cluster half of it."""
    groups = {"X": ["cluster('prod.eastus.kusto.windows.net').database('Sec')",
                    "database('d2')"]}
    clusters = {("prod.eastus.kusto.windows.net", "Sec"): "d1"}
    assert _rows(
        con, "macro-expand X as s (s.MT)", entity_groups=groups, clusters=clusters
    ) == [(1, "a"), (2, "b"), (7, "z")]


def test_a_cluster_entity_without_a_clusters_map_is_refused(con) -> None:
    groups = {"X": ["cluster('prod.eastus.kusto.windows.net').database('Sec')"]}
    with pytest.raises(KqlSchemaError):
        duckdb_kql.kql(con, "macro-expand X as s (s.MT)", entity_groups=groups)


def test_database_does_not_change_which_entities_expand(con) -> None:
    """`database=` qualifies *unqualified* tables. Every table inside a body is
    qualified by the scope rewrite, so the two do not interact — asserted
    rather than assumed, because a future `.create entity_group` would put the
    group itself in a database and change this."""
    plain = _rows(con, f"macro-expand {EG} as s (s.MT)")
    assert _rows(con, f"macro-expand {EG} as s (s.MT)", database="d2") == plain


# ---------------------------------------------------------------------------
# .show entity_groups
# ---------------------------------------------------------------------------


def test_show_entity_groups_reports_the_mapping(con) -> None:
    assert _rows(con, ".show entity_groups", entity_groups=GROUPS) == [
        ("EG", '["database(\'d1\')","database(\'d2\')"]')
    ]


def test_show_entity_groups_is_empty_without_a_mapping(con) -> None:
    assert _rows(con, ".show entity_groups") == []
    assert _cols(con, ".show entity_groups") == ["Name", "Entities"]


def test_show_entity_groups_can_be_piped(con) -> None:
    assert _rows(
        con, ".show entity_groups | project Name", entity_groups=GROUPS
    ) == [("EG",)]


def test_create_entity_group_is_still_refused(con) -> None:
    """Deliberately not implemented: a group that exists only for the session is
    a different object from one the cluster has, and blurring that is worse
    than the inconvenience."""
    with pytest.raises(Exception):  # noqa: B017 - Layer 0 and Layer 2 differ
        duckdb_kql.kql(con, ".create entity_group EG (database('d1'))")
