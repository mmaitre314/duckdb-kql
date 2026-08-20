"""L5 trap tests — ``union`` (``docs/TRANSLATION.md`` R15).

``union`` looks like SQL's ``UNION ALL`` and is not. Every expectation below was
measured on the Kusto Emulator; they are what the reference engine returned.

1. **Branches are matched by column NAME, not by position.** SQL's ``UNION ALL``
   pairs the first column with the first column. KQL pairs ``x`` with ``x``, so
   two branches listing the same names in a different order still line up. This
   is why the emitter uses ``UNION ALL BY NAME``.
2. **The default kind is ``outer``** — the *union* of the branches' columns,
   with nulls where a branch has no such column. ``kind=inner`` keeps only the
   columns every branch has.
3. **Column order is first appearance, left to right.** ``union A, B`` gives
   ``x, y, z`` and ``union B, A`` gives ``x, z, y``. It is not sorted, and it is
   user-visible.
4. **No de-duplication.** ``union UT1, UT1`` returns each row twice.
5. **``withsource=`` labels a branch by its table name**, but only for a bare
   table: a subquery branch, a ``let``-bound name, or a piped left side is
   ``union_argN``, counting the left side as 0.

The leading form ``union A, B`` and the piped form ``A | union B`` are the same
thing — measured identical — so both lower to one ``ir.Union`` whose left side
is branch 0.
"""

from __future__ import annotations

import pytest

import duckdb_kql
import duckdb_kql.engine
from duckdb_kql.errors import KqlSchemaError, KqlUnsupportedError

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    # UT1 and UT2 share `x` and differ elsewhere, so outer/inner disagree.
    # UT3 is a superset, and Other shares nothing — a union with it has no
    # column in common at all, which is the case `kind=inner` must handle.
    c.execute("CREATE TABLE UT1(x BIGINT, y VARCHAR)")
    c.execute("INSERT INTO UT1 VALUES (1,'a'),(2,'b')")
    c.execute("CREATE TABLE UT2(x BIGINT, z DOUBLE)")
    c.execute("INSERT INTO UT2 VALUES (2, 2.5),(3, 3.5)")
    c.execute("CREATE TABLE UT3(x BIGINT, y VARCHAR, z DOUBLE)")
    c.execute("INSERT INTO UT3 VALUES (9,'c', 9.5)")
    c.execute("CREATE TABLE Other(k BIGINT)")
    c.execute("INSERT INTO Other VALUES (7)")
    return c


def _cols(con, kql):
    return list(duckdb_kql.kql(con, kql).columns)


def _rows(con, kql):
    rel = duckdb_kql.kql(con, kql)
    return sorted(rel.fetchall(), key=lambda r: tuple(str(x) for x in r))


# ---------------------------------------------------------------------------
# R15a — outer is the default, and columns match by name
# ---------------------------------------------------------------------------


def test_default_kind_keeps_every_column(con):
    assert _cols(con, "union UT1, UT2") == ["x", "y", "z"]


def test_missing_columns_are_null_not_dropped(con):
    assert _rows(con, "union UT1, UT2") == [
        (1, "a", None),
        (2, None, 2.5),
        (2, "b", None),
        (3, None, 3.5),
    ]


def test_columns_pair_by_name_not_position(con):
    # UT3 lists (x, y, z); a positional UNION ALL against UT2's (x, z) would
    # pair UT2's `z` with UT3's `y` and answer with a string in a float column.
    assert _cols(con, "union UT2, UT3") == ["x", "z", "y"]
    assert _rows(con, "union UT2, UT3") == [
        (2, 2.5, None),
        (3, 3.5, None),
        (9, 9.5, "c"),
    ]


def test_union_does_not_deduplicate(con):
    assert len(_rows(con, "union UT1, UT1")) == 4


# ---------------------------------------------------------------------------
# R15b — column order is first appearance, left to right
# ---------------------------------------------------------------------------


def test_column_order_follows_the_branches(con):
    assert _cols(con, "union UT1, UT2") == ["x", "y", "z"]
    assert _cols(con, "union UT2, UT1") == ["x", "z", "y"]


def test_column_order_across_three_branches(con):
    assert _cols(con, "union UT1, UT2, Other") == ["x", "y", "z", "k"]
    assert _cols(con, "union Other, UT2, UT1") == ["k", "x", "z", "y"]


# ---------------------------------------------------------------------------
# R15c — kind=inner is the intersection
# ---------------------------------------------------------------------------


def test_inner_keeps_only_shared_columns(con):
    assert _cols(con, "union kind=inner UT1, UT2") == ["x"]
    assert _rows(con, "union kind=inner UT1, UT2") == [(1,), (2,), (2,), (3,)]


def test_inner_across_a_superset(con):
    assert _cols(con, "union kind=inner UT1, UT3") == ["x", "y"]


def test_inner_with_no_shared_column_yields_no_columns(con):
    # Nothing is shared, so the result has zero columns. DuckDB cannot select
    # zero columns, so this has to fail loudly rather than quietly return `x`.
    with pytest.raises(Exception):  # noqa: B017 - engine-level, message varies
        duckdb_kql.kql(con, "union kind=inner UT1, Other").fetchall()


def test_outer_can_be_named_explicitly(con):
    assert _cols(con, "union kind=outer UT1, UT2") == ["x", "y", "z"]


def test_unknown_kind_is_refused(con):
    with pytest.raises(KqlUnsupportedError):
        duckdb_kql.kql(con, "union kind=sideways UT1, UT2")


# ---------------------------------------------------------------------------
# R15d — withsource
# ---------------------------------------------------------------------------


def test_withsource_is_the_leading_column(con):
    assert _cols(con, "union withsource=Src UT1, UT2") == ["Src", "x", "y", "z"]


def test_withsource_labels_are_table_names(con):
    rows = _rows(con, "union withsource=Src UT1, UT2")
    assert {r[0] for r in rows} == {"UT1", "UT2"}


def test_withsource_survives_kind_inner(con):
    # The intersection is over the DATA columns; `Src` is not one of them and is
    # not dropped for being absent from the branches.
    assert _cols(con, "union kind=inner withsource=Src UT1, UT2") == ["Src", "x"]


def test_withsource_names_a_bare_table_on_the_left(con):
    rows = _rows(con, "UT1 | union withsource=Src UT2")
    assert {r[0] for r in rows} == {"UT1", "UT2"}


def test_withsource_falls_back_to_union_arg0_after_an_operator(con):
    # The left side stops being a bare table the moment anything pipes into it.
    rows = _rows(con, "UT1 | where x == 1 | union withsource=Src UT2")
    assert {r[0] for r in rows} == {"union_arg0", "UT2"}


def test_withsource_falls_back_to_union_argn_for_a_subquery(con):
    rows = _rows(con, "union withsource=Src UT1, (UT2 | where x == 2)")
    assert {r[0] for r in rows} == {"UT1", "union_arg1"}


def test_withsource_does_not_name_a_let_bound_table(con):
    # Measured: a `let` name is not a table name, so it gets the positional
    # label even though it looks exactly like a bare table reference.
    rows = _rows(con, "let A = datatable(x:long)[9]; union withsource=Src UT1, A")
    assert {r[0] for r in rows} == {"UT1", "union_arg1"}


def test_withsource_strips_the_database_qualifier(con):
    rows = _rows(con, "union withsource=Src database('memory').UT1, UT2")
    assert {r[0] for r in rows} == {"UT1", "UT2"}


def test_withsource_repeats_a_label_for_a_repeated_table(con):
    rows = _rows(con, "union withsource=Src UT1, UT1")
    assert [r[0] for r in rows] == ["UT1"] * 4


# ---------------------------------------------------------------------------
# R15e — wildcards
# ---------------------------------------------------------------------------


def test_wildcard_matches_every_table_with_the_prefix(con):
    assert _cols(con, "union UT*") == ["x", "y", "z"]
    assert len(_rows(con, "union UT*")) == 5


def test_wildcard_does_not_match_an_unrelated_table(con):
    assert "k" not in _cols(con, "union UT*")


def test_wildcard_labels_each_matched_table_separately(con):
    # One arm per table, not one label for the pattern.
    rows = _rows(con, "union withsource=Src UT*")
    assert {r[0] for r in rows} == {"UT1", "UT2", "UT3"}


def test_wildcard_combines_with_a_named_branch(con):
    assert _cols(con, "union UT*, Other") == ["x", "y", "z", "k"]


def test_bare_star_is_every_table(con):
    # Name order, not Kusto's creation order — see R15's residue. `Other` sorts
    # first, so `k` leads.
    assert _cols(con, "union *") == ["k", "x", "y", "z"]
    assert len(_rows(con, "union *")) == 6


def test_a_table_named_twice_is_read_twice(con):
    # `*` includes UT1 even though it is already named, and the union does not
    # de-duplicate, so UT1's rows appear twice.
    assert len(_rows(con, "union UT1, *")) == 8


def test_wildcard_matching_nothing_is_refused(con):
    # Kusto raises SEM0100 rather than returning an empty result, and so does
    # this: an empty union is indistinguishable from a table with no rows.
    with pytest.raises(KqlSchemaError):
        duckdb_kql.kql(con, "union NoSuchPrefix*")


def test_wildcard_across_clusters_is_refused(con):
    with pytest.raises(KqlUnsupportedError):
        duckdb_kql.kql(con, "union cluster('c').database('d').UT*")


# ---------------------------------------------------------------------------
# R15f — isfuzzy
# ---------------------------------------------------------------------------


def test_missing_table_is_an_error_by_default(con):
    with pytest.raises(KqlSchemaError):
        duckdb_kql.kql(con, "union UT1, NoSuchTable")


def test_isfuzzy_drops_a_missing_table(con):
    assert _rows(con, "union isfuzzy=true UT1, NoSuchTable") == [(1, "a"), (2, "b")]


def test_isfuzzy_keeps_the_labels_of_what_survives(con):
    rows = _rows(con, "union isfuzzy=true withsource=Src UT1, NoSuchTable")
    assert {r[0] for r in rows} == {"UT1"}


def test_isfuzzy_does_not_fire_without_a_schema():
    # "I have no catalog" is not "this table does not exist". Dropping every
    # branch here would return a short answer that looks like data.
    with pytest.raises(KqlSchemaError):
        duckdb_kql.to_sql("union isfuzzy=true UT1, NoSuchTable")


# ---------------------------------------------------------------------------
# The leading and piped forms are one thing
# ---------------------------------------------------------------------------


def test_leading_and_piped_forms_agree(con):
    assert _rows(con, "union UT1, UT2") == _rows(con, "UT1 | union UT2")
    assert _cols(con, "union UT1, UT2") == _cols(con, "UT1 | union UT2")


def test_operators_after_a_union_see_the_unioned_columns(con):
    assert _rows(con, "union UT1, UT2 | where x >= 2 | project x") == [(2,), (2,), (3,)]


def test_count_after_a_union(con):
    assert _rows(con, "union UT1, UT2 | count") == [(4,)]


def test_summarize_after_a_union(con):
    assert _rows(con, "union UT1, UT2 | summarize c = count() by x") == [
        (1, 1),
        (2, 2),
        (3, 1),
    ]


def test_union_of_datatables_needs_no_schema():
    sql = duckdb_kql.to_sql(
        "union (datatable(x:long)[1]), (datatable(y:long)[2])"
    )
    assert "UNION ALL BY NAME" in sql


def test_union_can_be_a_subquery(con):
    # `union` is the one operator that can also *start* a query, so every place
    # that takes a subquery has to accept one: a nested branch, a join's right
    # side, and a `let` binding. Each used to report
    # "unsupported construct 'source:UnionOperator'".
    assert _cols(con, "union (union UT1, UT2), UT3") == ["x", "y", "z"]
    assert len(_rows(con, "union (union UT1, UT2), UT3")) == 5


def test_union_as_a_joins_right_side(con):
    assert _rows(con, "UT3 | join kind=inner (union UT1, UT2) on x") == []


def test_union_bound_by_let(con):
    assert _rows(con, "let U = union UT1, UT2; U | count") == [(4,)]


def test_parenthesized_tabular_let(con):
    # Not union-specific: a bracketed tabular `let` was read as a *scalar*
    # binding and reported as an unsupported PipeExpression.
    assert _rows(con, "let U = (UT1 | where x == 1); U") == [(1, "a")]
    assert _rows(con, "let U = (union UT1, UT2); U | count") == [(4,)]


def test_union_branch_can_itself_be_a_pipeline(con):
    assert _rows(con, "union UT1, (UT2 | where x == 3)") == [
        (1, "a", None),
        (2, "b", None),
        (3, None, 3.5),
    ]


# ---------------------------------------------------------------------------
# Emitted SQL
# ---------------------------------------------------------------------------


def test_emits_union_all_by_name_not_plain_union(con):
    sql = duckdb_kql.to_sql("union UT1, UT2", schema=duckdb_kql.engine.schema(con))
    assert "UNION ALL BY NAME" in sql
    # `UNION` without `ALL` would de-duplicate, which KQL does not do.
    assert "UNION\n" not in sql and "UNION (" not in sql


def test_output_columns_are_named_explicitly(con):
    # Column order is user-visible, so it comes from the measured rule rather
    # than from whatever DuckDB's BY NAME happens to produce.
    sql = duckdb_kql.to_sql("union UT2, UT1", schema=duckdb_kql.engine.schema(con))
    assert '"x", "z", "y"' in sql
