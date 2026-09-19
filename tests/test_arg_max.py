"""L5 trap tests — `arg_max` / `arg_min` (R4, R10, R12).

`summarize arg_max(ts, value) by key` was refused, and `arg_max(ts, *)` failed
earlier still on the star. DuckDB has an `arg_max` of its own, which is what
makes this worth a trap test: the obvious mapping is wrong twice, and both ways
are silent.

**DuckDB's `arg_max(x, y)` skips rows whose `x` is null.** Measured, for rows
`(t=2, v=null)` and `(t=1, v='y')`, DuckDB answers `'y'` — the value at the
*second* largest `t` — where Kusto answers the value at the largest, which is
null. Boxing the value in a struct fixes it: the struct is never null, so no row
is skipped, and the field comes back null as it should.

**When every maximised value is null, Kusto still returns a row.** DuckDB's
`arg_max` answers null there, so `any_value` supplies the fallback.

The other half is naming (R12). Each output column is named after its own
expression, and an explicit `m = arg_max(...)` renames only the first. `*` is
every input column *except* the maximised one and the grouping keys — Kusto does
not repeat what it has already emitted, though listing a key explicitly does
repeat it under a suffixed name.

A **computed** operand is refused. Kusto names those `max_ts_arg1` and `max_`,
schemes sampled twice rather than established, and a wrong column name is a
divergence like any other.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlUnsupportedError

duckdb = pytest.importorskip("duckdb")

T = "datatable(key:string, ts:long, value:string)['a',1,'old','a',2,'new','b',5,'x']"
U = "datatable(key:string, ts:long, a:string, b:string)['k',1,'x','y','k',2,'p','q']"


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    return c


def _shape(con, kql):
    cursor = duckdb_kql.kql(con, kql)
    return [d[0] for d in cursor.description], sorted(cursor.fetchall())


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def test_the_reported_query(con) -> None:
    assert _shape(con, f"{T} | summarize arg_max(ts, value) by key") == (
        ["key", "ts", "value"],
        [("a", 2, "new"), ("b", 5, "x")],
    )


def test_arg_min_is_the_mirror(con) -> None:
    assert _shape(con, f"{T} | summarize arg_min(ts, value) by key") == (
        ["key", "ts", "value"],
        [("a", 1, "old"), ("b", 5, "x")],
    )


def test_without_a_by_clause(con) -> None:
    assert _shape(con, f"{T} | summarize arg_max(ts, value)") == (
        ["ts", "value"],
        [(5, "x")],
    )


def test_several_returned_expressions(con) -> None:
    assert _shape(con, f"{U} | summarize arg_max(ts, a, b) by key") == (
        ["key", "ts", "a", "b"],
        [("k", 2, "p", "q")],
    )


# ---------------------------------------------------------------------------
# The two null rules DuckDB's own arg_max gets wrong
# ---------------------------------------------------------------------------


def test_a_null_at_the_maximum_is_returned_not_skipped(con) -> None:
    """The trap. DuckDB's `arg_max(v, t)` answers `'y'` here — the value at
    t=1 — because it drops the row whose `v` is null. Kusto answers the value
    at t=2, which is null. Asserted through `isempty`/`strlen` rather than the
    value, since a KQL string has no null distinct from `''`."""
    assert _shape(
        con,
        "datatable(k:string,t:long,v:string)['a',2,dynamic(null),'a',1,'y'] "
        "| summarize arg_max(t,v) by k | project k, t, e = isempty(v), l = strlen(v)",
    ) == (["k", "t", "e", "l"], [("a", 2, True, 0)])


def test_a_null_maximised_value_loses_to_a_real_one(con) -> None:
    """`max` ignores nulls on both sides, so this one needs no help."""
    assert _shape(
        con,
        "datatable(k:string,t:long,v:string)['a',long(null),'n','a',1,'y'] "
        "| summarize arg_max(t,v) by k",
    ) == (["k", "t", "v"], [("a", 1, "y")])


def test_all_maximised_values_null_still_returns_a_row(con) -> None:
    """DuckDB's `arg_max` answers null for the whole group; Kusto returns the
    row. Which row is arbitrary on both sides once there is more than one, so
    this fixture has exactly one."""
    assert _shape(
        con,
        "datatable(k:string,t:long,v:string)['a',long(null),'n'] "
        "| summarize arg_max(t,v) by k",
    ) == (["k", "t", "v"], [("a", None, "n")])


# ---------------------------------------------------------------------------
# `*` and naming (R12)
# ---------------------------------------------------------------------------


def test_the_star_expands_to_the_other_columns(con) -> None:
    """Not *every* column: the maximised one and the grouping keys are already
    in the output and Kusto does not repeat them."""
    assert _shape(con, f"{U} | summarize arg_max(ts, *) by key") == (
        ["key", "ts", "a", "b"],
        [("k", 2, "p", "q")],
    )


def test_the_star_without_a_by_clause_keeps_every_other_column(con) -> None:
    """With no grouping key there is nothing to exclude but the maximised
    column, so `key` comes back — measured."""
    columns, _ = _shape(con, f"{U} | summarize arg_max(ts, *)")
    assert columns == ["ts", "key", "a", "b"]


def test_an_explicit_name_renames_only_the_first_column(con) -> None:
    assert _shape(con, f"{U} | summarize m = arg_max(ts, a) by key")[0] == (
        ["key", "m", "a"]
    )


def test_the_star_needs_a_schema(con) -> None:
    """Layer 0 has no columns to expand it into, and inventing some would give
    a query that runs and returns the wrong shape."""
    with pytest.raises(KqlUnsupportedError, match="input columns"):
        duckdb_kql.to_sql("T | summarize arg_max(ts, *) by key")


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_a_computed_maximised_expression_is_refused(con) -> None:
    with pytest.raises(KqlUnsupportedError, match="must be a column"):
        duckdb_kql.kql(con, f"{T} | summarize arg_max(ts * 2, value) by key")


def test_a_computed_returned_expression_is_refused(con) -> None:
    with pytest.raises(KqlUnsupportedError, match="must be a column"):
        duckdb_kql.kql(con, f"{U} | summarize arg_max(ts, strcat(a,b)) by key")


def test_one_argument_is_refused(con) -> None:
    """Kusto needs something to return as well as something to maximise."""
    with pytest.raises(KqlUnsupportedError, match="at least one to return"):
        duckdb_kql.kql(con, f"{T} | summarize arg_max(ts) by key")


def test_a_star_outside_arg_max_is_still_refused(con) -> None:
    """The star is carried as an IR node, so it must not leak into a position
    that silently drops it."""
    with pytest.raises(Exception, match="(?i)star|wildcard|\\*"):
        duckdb_kql.kql(con, f"{T} | project *")


# ---------------------------------------------------------------------------
# It composes
# ---------------------------------------------------------------------------


def test_it_sits_beside_another_aggregate(con) -> None:
    assert _shape(con, f"{T} | summarize c = count(), arg_max(ts, value) by key") == (
        ["key", "c", "ts", "value"],
        [("a", 2, 2, "new"), ("b", 1, 5, "x")],
    )


def test_a_later_operator_sees_its_columns(con) -> None:
    assert _shape(
        con, f"{T} | summarize arg_max(ts, value) by key | where value == 'new'"
    ) == (["key", "ts", "value"], [("a", 2, "new")])


# ---------------------------------------------------------------------------
# What the next operator can see
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tail,expected",
    [
        ("| project key, value=toint(value)", [("one", 20)]),
        ("| project value", [(20,)]),
        ("| where value > 5 | project key", [("one",)]),
        ("| project key, stamp, value", [("one", 2, 20)]),
        ("| extend doubled = value * 2 | project doubled", [(40,)]),
    ],
)
def test_the_extra_columns_are_visible_downstream(con, tail: str, expected) -> None:
    """Regression. `arg_max` is the only aggregate emitting several columns from
    one call, and the *column walker* counted one per aggregate — so everything
    after the first was invisible to the next operator and
    `summarize arg_max(stamp, *) by key | project value` was refused for a
    `value` the summarize does produce (R21's check, on a column list that was
    wrong rather than a query that was).

    The names now come from one function, `arg_max_names`, which the emitter and
    the walker share, so the columns emitted and the columns predicted cannot
    drift apart again.
    """
    _columns, rows = _shape(
        con,
        "datatable(key:string, stamp:long, value:long)['one',1,10,'one',2,20]"
        f"| summarize arg_max(stamp, *) by key {tail}",
    )
    assert rows == expected


def test_an_explicit_list_is_visible_downstream_too(con) -> None:
    assert _shape(
        con,
        "datatable(key:string, stamp:long, value:long)['one',1,10,'one',2,20]"
        "| summarize arg_max(stamp, value) by key | project key, value=toint(value)",
    ) == (["key", "value"], [("one", 20)])


def test_the_walker_and_the_emitter_agree(con) -> None:
    """Asserted directly, since the two drifting apart is the whole bug."""
    from duckdb_kql.lower import lower
    from duckdb_kql.schema import output_columns

    query = lower(
        "datatable(key:string, stamp:long, value:long)['one',1,10]"
        "| summarize arg_max(stamp, *) by key"
    )
    predicted = output_columns(query)
    emitted = [
        d[0]
        for d in duckdb_kql.kql(
            con,
            "datatable(key:string, stamp:long, value:long)['one',1,10]"
            "| summarize arg_max(stamp, *) by key",
        ).description
    ]
    assert predicted == emitted == ["key", "stamp", "value"]
