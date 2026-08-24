"""L5 trap tests — ``let`` bindings.

``let`` was the single largest blocker in the corpus (250 frozen cases), and it
used to be *refused* precisely because it is dangerous to get wrong: a `let` is
not a `QueryStatement`, so an implementation that counts query statements sees
one statement, translates it, and silently drops the binding — producing a query
that runs and returns the wrong rows.

Two binding shapes, resolved differently:

* **scalar** (``let x = 5``) — substituted into the IR, because a `let` is a
  query-scope binding rather than a column;
* **tabular** (``let T = X | where …``) — emitted as a named CTE, so a reference
  to it in the body needs no rewriting.
"""

from __future__ import annotations

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    c.execute("CREATE TABLE T(a INTEGER, s VARCHAR)")
    c.execute("INSERT INTO T VALUES (1,'x'),(5,'y'),(9,'x')")
    c.execute("CREATE TABLE R(a INTEGER, r VARCHAR)")
    c.execute("INSERT INTO R VALUES (1,'p'),(9,'q')")
    return c


def _rows(con, kql):
    rel = duckdb_kql.kql(con, kql)
    return sorted(rel.fetchall(), key=lambda r: tuple(str(x) for x in r))


def test_scalar_let_is_applied_not_dropped(con) -> None:
    """The regression this whole feature exists to prevent.

    Dropping the binding leaves `T | where a > x` with an unbound `x`; if that
    ever resolved to something, the query would return the wrong rows silently.
    """
    assert _rows(con, "let x = 5; T | where a > x") == [(9, "x")]
    assert _rows(con, "let x = 0; T | where a > x") == [(1, "x"), (5, "y"), (9, "x")]


def test_scalar_lets_may_reference_earlier_ones(con) -> None:
    assert _rows(con, "let x = 5; let y = x * 2; print y")[0][0] == 10


def test_scalar_let_in_every_operator_position(con) -> None:
    assert set(_rows(con, "let n = 5; T | extend b = a + n | project b")) == {
        (6,), (10,), (14,)
    }
    assert _rows(con, "let n = 1; T | summarize c = count() by g = a > n")[0] == (
        False, 1
    )


def test_tabular_let_becomes_a_usable_table(con) -> None:
    assert _rows(con, "let T2 = T | where a > 1; T2 | count") == [(2,)]


def test_tabular_let_alias(con) -> None:
    """`let A = T` aliases a table rather than binding a scalar."""
    assert _rows(con, "let A = T; A | count") == [(3,)]


def test_materialize_is_a_hint_and_unwraps(con) -> None:
    """materialize() caches in a distributed engine; it cannot change results."""
    plain = _rows(con, "let m = T | where a > 1; m | summarize count()")
    hinted = _rows(con, "let m = materialize(T | where a > 1); m | summarize count()")
    assert plain == hinted == [(2,)]


def test_tabular_let_can_be_joined(con) -> None:
    """The join needs the let's columns, which are resolved from the binding."""
    rel = duckdb_kql.kql(con, "let A = T; A | join kind=inner (R) on a | project a, s, r")
    assert list(rel.columns) == ["a", "s", "r"]
    assert sorted(rel.fetchall()) == [(1, "x", "p"), (9, "x", "q")]


def test_let_function_declarations_still_refuse(con) -> None:
    """User-defined functions are a later wave; refusing beats guessing."""
    with pytest.raises(duckdb_kql.KqlUnsupportedError):
        duckdb_kql.to_sql("let f = (a:int) { a + 1 }; print f(1)")


# --- timespan handling that `let` cases exposed ----------------------------
def test_timespan_string_with_a_day_part(con) -> None:
    """KQL timespans are `[-][d.]hh:mm:ss`; DuckDB's INTERVAL cast drops the days."""
    import datetime as dt

    assert _rows(con, "print totimespan('4.00:00:00')")[0][0] == dt.timedelta(days=4)
    assert _rows(con, "print totimespan('0.00:03:00')")[0][0] == dt.timedelta(minutes=3)
    assert _rows(con, "print totimespan('00:03:00')")[0][0] == dt.timedelta(minutes=3)
    assert _rows(con, "print totimespan('garbage')")[0][0] is None


def test_totimespan_of_a_timespan_is_identity(con) -> None:
    import datetime as dt

    assert _rows(con, "print totimespan(4d)")[0][0] == dt.timedelta(days=4)


def test_dividing_two_timespans_yields_a_number(con) -> None:
    """`dayofweek()` returns a TIMESPAN, so `dow / 1d` is "how many days"."""
    assert _rows(con, "let dow = dayofweek(datetime(1970-5-12)); print toint(dow / 1d)") == [
        (2,)
    ]


# --- `x in (T)` where T is a tabular let -----------------------------------
#
# A bare `T` inside `in (...)` parses as a column reference; only once the
# bindings are known can it be resolved into a subquery. That resolution used
# to run on the **top-level pipeline only**, so the identical expression one
# line higher — inside another `let` — reached DuckDB as a column and failed
# with "Referenced column "values" not found in FROM clause". Every case below
# is measured against the emulator.


def test_in_a_tabular_let_at_the_top_level(con) -> None:
    assert _rows(con, "let v = T | project a; R | where a in (v)") == [
        (1, "p"), (9, "q")
    ]


def test_in_a_tabular_let_inside_another_let(con) -> None:
    """The reported failure: the same expression, one scope in."""
    assert _rows(
        con, "let v = T | project a; let n = R | where a in (v); n"
    ) == [(1, "p"), (9, "q")]


def test_not_in_a_tabular_let_inside_another_let(con) -> None:
    assert _rows(
        con,
        "let v = T | project a | where a == 1;"
        " let n = R | where a !in (v); n",
    ) == [(9, "q")]


def test_in_a_tabular_let_inside_an_extend(con) -> None:
    assert _rows(
        con,
        "let v = T | project a | where a == 1;"
        " let n = R | extend hit = a in (v) | project a, hit; n",
    ) == [(1, True), (9, False)]


def test_has_any_over_a_tabular_let_inside_a_let(con) -> None:
    """`has_any` takes the same right-hand side and resolves the same way."""
    assert _rows(
        con,
        "let v = T | project s | where s == 'x';"
        " let n = R | where r has_any (v) | project r; n",
    ) == []


def test_lets_referring_to_each_other_through_in(con) -> None:
    assert _rows(
        con,
        "let ones = T | project a | where a == 1;"
        " let step2 = R | where a in (ones) | project a;"
        " let step3 = R | where a in (step2) | project a; step3",
    ) == [(1,)]


def test_a_column_sharing_a_tabular_lets_name_is_a_known_divergence(con) -> None:
    """We resolve the `let`; Kusto resolves the **column** and then refuses.

    `in (v)` where `v` names both a tabular `let` and a column in scope is
    rejected by the emulator with SEM0040 — "failed to cast argument 2 to
    scalar constant" — because it binds the column and a column is not a
    constant list. Matching that needs the input's column names at lowering
    time, which is precisely what lowering does not have: the schema arrives
    one stage later. Recorded rather than hidden, and it is the mild direction
    — we accept a query Kusto rejects, not answer a valid one differently.
    """
    assert _rows(
        con,
        "let v = T | project a | where a == 1;"
        " let n = R | project v = a | where v in (v); n",
    ) == [(1,)]


def test_in_a_tabular_let_inside_a_joins_right_side(con) -> None:
    assert _rows(
        con,
        "let v = T | project a | where a == 1;"
        " T | join kind=inner (R | where a in (v)) on a | project a, r",
    ) == [(1, "p")]


def test_in_a_tabular_let_inside_a_union_branch(con) -> None:
    assert _rows(
        con,
        "let v = T | project a | where a == 1;"
        " let n = union (R | where a in (v)), (R | where a in (v)); n",
    ) == [(1, "p"), (1, "p")]


# ---------------------------------------------------------------------------
# The `let` namespace — redeclaration, and collision with generated CTE names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        # Tabular twice: two CTEs of one name, `Duplicate CTE name` from DuckDB.
        "let X = datatable(x:long)[1,2]; let X = datatable(x:long)[3,4]; X | count",
        # Scalar twice: this one was the quiet half. A dict overwrote, and the
        # query answered 2 where a cluster refuses it.
        "let v = 1; let v = 2; print x = v",
        # ...and across the two kinds.
        "let v = 1; let v = datatable(x:long)[1]; v | count",
        "let v = datatable(x:long)[1]; let v = 1; print x = v",
    ],
)
def test_a_redeclared_let_is_refused(query: str) -> None:
    """Kusto refuses a second `let` of the same name — SEM0079, both kinds.

    Measured before choosing between refusing and shadowing: *"Let with the
    same name was already used in current context"*. So the scalar path's
    silent overwrite was the defect, and "make the tabular path shadow like the
    scalar one" would have spread it rather than fixed it.
    """
    with pytest.raises(duckdb_kql.KqlUnsupportedError) as exc:
        duckdb_kql.to_sql(query)
    assert "SEM0079" in str(exc.value)


def test_a_let_may_still_shadow_a_query_parameter(con) -> None:
    """Not a redeclaration: the parameter scope seeds the `let` scope."""
    sql = duckdb_kql.to_sql(
        "declare query_parameters(p:long); let p = 7; print x = p"
    )
    assert "7" in str(sql)


@pytest.mark.parametrize("name", ["_s0", "_s1", "_s99"])
def test_a_let_named_like_a_generated_cte_still_works(con, name: str) -> None:
    """The internal stage names are `_s0`, `_s1`, … and `let _s0 = …` is legal KQL.

    It used to emit two CTEs called `_s0` and die with DuckDB's `Duplicate CTE
    name` — an error naming nothing the user wrote. The prefix now lengthens
    out of the way instead of the query being refused.
    """
    assert duckdb_kql.kql(
        con, f"let {name} = datatable(x:long)[1,2,3]; {name} | where x > 1 | count"
    ).fetchall() == [(2,)]


def test_the_usual_stage_prefix_is_unchanged(con) -> None:
    """Lengthening applies only when it must — every other query keeps `_s0`.

    Pinned because a prefix that moved unconditionally would rewrite every
    line of generated SQL in the docs and the snapshot for no reason.
    """
    assert "_s0 AS" in str(duckdb_kql.to_sql("T | where a > 1"))
    collided = str(
        duckdb_kql.to_sql("let _s0 = datatable(x:long)[1]; _s0 | count")
    )
    assert "__s0 AS" in collided
