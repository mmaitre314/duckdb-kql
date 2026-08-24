"""L5 trap tests — two column names that differ only in case (R7).

KQL identifiers are case-sensitive, DuckDB's are not, and the gap is not closed
by quoting. Measured against DuckDB itself:

* ``SELECT "FOO" FROM (SELECT 1 AS "foo")`` returns **1** — a quoted name falls
  back to a case-variant when no exact match exists, with no error;
* a SELECT list producing both spellings renames the second to ``Foo_1``.

Both directions are silent, and together they produce wrong *values*. Before
this check, `datatable(Foo:long, foo:long)[1,2] | project Foo, foo` answered
``(1, 1)`` where a cluster answers ``(1, 2)``, and
`... | extend Foo = foo + 100 | project foo, Foo` answered the un-extended
value twice.

There is no rendering that fixes it — the two names are one name to the engine
underneath — so R7's second clause says to raise. **The refused queries are
legal KQL**, which is the trade: a caller who collided by accident is far
commoner than one who meant to, and a caller who meant to has
`project-rename`.

The suite had no column-level case test at all before this file; the one
case-sensitivity test it did have (`test_join.py`) checks a *table* name
against a Python dict and never reaches DuckDB.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlSchemaError

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    return c


def _refused(kql: str) -> KqlSchemaError:
    with pytest.raises(KqlSchemaError) as exc:
        duckdb_kql.to_sql(kql)
    return exc.value


# ---------------------------------------------------------------------------
# The DuckDB behaviours the rule is built on — pinned, not assumed
# ---------------------------------------------------------------------------


def test_duckdb_folds_a_quoted_name_onto_a_case_variant(con) -> None:
    """The premise. If this ever changes, the refusals below can be relaxed."""
    assert con.execute('SELECT "FOO" FROM (SELECT 1 AS "foo")').fetchone() == (1,)


def test_duckdb_renames_and_refolds_a_case_variant_behind_a_subquery(con) -> None:
    """The rename needs a subquery, and this translator always emits one.

    A flat ``SELECT 1 AS "Foo", 2 AS "foo"`` keeps both names, which is why the
    problem is invisible until the CTE chain of §1 goes around it. Put the same
    list behind a CTE — as every translated query is — and the second column is
    renamed *and* the original name now reads the **first** one. That second
    half is the wrong-value mechanism; the rename alone would only be ugly.
    """
    flat = con.sql('SELECT 1 AS "Foo", 2 AS "foo"')
    assert list(flat.columns) == ["Foo", "foo"]

    cte = 'WITH t AS (SELECT 1 AS "Foo", 2 AS "foo") '
    assert list(con.sql(cte + "SELECT * FROM t").columns) == ["Foo", "foo_1"]
    assert con.sql(cte + 'SELECT "foo" FROM t').fetchall() == [(1,)]


# ---------------------------------------------------------------------------
# A source that already carries both spellings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "datatable(Foo:long, foo:long)[1, 2]",
        "datatable(Foo:long, foo:long)[1, 2] | project Foo, foo",
        # Refused even where the collision is not *read*: the result set still
        # cannot carry both names.
        "datatable(Foo:long, foo:long)[1, 2] | project foo",
        "datatable(Foo:long, foo:long)[1, 2] | where Foo == 1 | count",
        "datatable(a:string, A:string)['x','y'] | summarize count() by a, A",
    ],
)
def test_a_source_declaring_both_spellings_is_refused(query: str) -> None:
    exc = _refused(query)
    assert "datatable" in str(exc)
    assert "case" in str(exc)


def test_the_error_names_both_columns_and_the_way_out() -> None:
    exc = _refused("datatable(Foo:long, foo:long)[1, 2]")
    assert "'Foo'" in str(exc) and "'foo'" in str(exc)
    assert "project-rename" in str(exc)


# ---------------------------------------------------------------------------
# An operator that introduces the second spelling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "stage"),
    [
        ("datatable(foo:long)[1,2,3] | extend Foo = foo + 100", "extend"),
        (
            "datatable(foo:long)[1,2,3] | extend Foo = foo + 100 | project foo, Foo",
            "extend",
        ),
        ("datatable(a:long)[1] | project A = a + 1, a", "project"),
        ("datatable(a:long)[1] | distinct A = a + 1, a", "distinct"),
        # The collision exists at `extend` even though `project-away` would
        # have removed it a stage later — the error names where it appeared.
        ("datatable(a:long)[1] | extend A = a + 1 | project-away a", "extend"),
    ],
)
def test_an_operator_introducing_a_case_variant_is_refused(
    query: str, stage: str
) -> None:
    assert stage in str(_refused(query))


# ---------------------------------------------------------------------------
# What must keep working
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        # Renaming *is* the way out, and it is not a collision: `a` is gone.
        ("datatable(a:long)[1] | project-rename A = a", [(1,)]),
        # An aggregate named for a differently-cased key is fine — `A` and `x`
        # do not collide, and this is the shape that made the naive check
        # tempting to write over the wrong list.
        ("datatable(a:long)[1] | summarize x = sum(a) by A = a", [(1, 1)]),
        # Replacing a column under its OWN name is not a collision.
        ("datatable(a:long)[1] | extend a = a + 1", [(2,)]),
        ("datatable(a:long)[1] | project a = a + 1", [(2,)]),
        # Names that differ by more than case. (`ab` and `aB` would NOT be
        # fine — they differ only in case, and are refused above's rule too.)
        ("datatable(a:long)[1] | extend ab = a + 1, ba = a + 2", [(1, 2, 3)]),
    ],
)
def test_a_non_collision_still_translates(con, query: str, expected: list) -> None:
    assert duckdb_kql.kql(con, query).fetchall() == expected


def test_a_join_suffix_is_not_a_case_collision(con) -> None:
    """R5's own disambiguation makes `k` and `k1`, which differ by more than case.

    Pinned because the check runs on join's output list too, and a rule that
    fired here would break every self-join.
    """
    con.execute("CREATE TABLE J AS SELECT 1 AS k, 'x' AS v")
    assert list(duckdb_kql.kql(con, "J | join (J) on k").columns) == ["k", "v", "k1", "v1"]


def test_the_check_needs_a_schema_and_says_nothing_without_one() -> None:
    """No column list, no check — stated so the limit is not mistaken for a fix.

    A bare table with no schema still renders, and a collision inside it is not
    caught here. `duckdb_kql.kql()` always has one.
    """
    assert "SELECT" in str(duckdb_kql.to_sql("SomeTable | where x > 1"))
