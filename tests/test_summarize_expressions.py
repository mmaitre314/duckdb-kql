"""`summarize` over an *expression* containing aggregates, not just a bare call.

`summarize Revenue = round(sum(Total), 2)` is a scalar expression over an
aggregate, and SQL writes it the same way — so the translation is to render the
aggregates, put each back where it stood, and render the surround normally.

Everything here was **measured on the Kusto Emulator** against one datatable:

    datatable(g:string, x:long, y:real)['a',1,1.5, 'a',2,2.25, 'b',3,3.75]

Two parts of it are not guessable and were wrong in the first draft:

* **The default column name comes from the aggregate, not the wrapper.**
  `round(sum(y), 2)` is `sum_y`, `tostring(count())` is `count_`. The rule is
  positional — follow *first arguments* — so `strcat('n=', tostring(count()))`
  is `Column1` because the first argument is a literal.
* **Kusto refuses more than SQL does.** `sum(x) + x` and
  `strcat(g, tostring(count()))` are rejected there *even when `g` is a
  grouping key*, and DuckDB would happily accept the second. Translating it
  would mean a query that works here and fails in production.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlUnsupportedError

duckdb = pytest.importorskip("duckdb")

TABLE = "datatable(g:string, x:long, y:real)['a',1,1.5, 'a',2,2.25, 'b',3,3.75]"

#: `(tail, columns, rows)` — the emulator's answer for `TABLE | tail`.
MEASURED: list[tuple[str, list[str], list[tuple]]] = [
    # The one that started this.
    ("summarize R = round(sum(y), 2) by g", ["g", "R"], [("a", 3.75), ("b", 3.75)]),
    ("summarize R = sum(x) + 1 by g", ["g", "R"], [("a", 4), ("b", 4)]),
    ("summarize R = sum(x) + max(x) by g", ["g", "R"], [("a", 5), ("b", 6)]),
    ("summarize R = max(x) - min(x) by g", ["g", "R"], [("a", 1), ("b", 0)]),
    ("summarize R = abs(sum(x)) by g", ["g", "R"], [("a", 3), ("b", 3)]),
    ("summarize R = todouble(count()) by g", ["g", "R"], [("a", 2.0), ("b", 1.0)]),
    ("summarize R = round(avg(y), 1) by g", ["g", "R"], [("a", 1.9), ("b", 3.8)]),
    ("summarize R = strcat('n=', tostring(count())) by g", ["g", "R"],
     [("a", "n=2"), ("b", "n=1")]),
    ("summarize R = iff(count() > 1, 'many', 'one') by g", ["g", "R"],
     [("a", "many"), ("b", "one")]),
    # No `by` at all.
    ("summarize R = round(sum(y), 2)", ["R"], [(7.5,)]),
    ("summarize R = count() * 2", ["R"], [(6,)]),
    # ... and the default names.
    ("summarize round(sum(y), 2) by g", ["g", "sum_y"], [("a", 3.75), ("b", 3.75)]),
    ("summarize abs(sum(x)) by g", ["g", "sum_x"], [("a", 3), ("b", 3)]),
    ("summarize tostring(count()) by g", ["g", "count_"], [("a", "2"), ("b", "1")]),
    ("summarize round(round(sum(y),1),2) by g", ["g", "sum_y"],
     [("a", 3.8), ("b", 3.8)]),
    ("summarize (sum(x)) by g", ["g", "sum_x"], [("a", 3), ("b", 3)]),
    ("summarize round(percentile(y, 50), 1) by g", ["g", "percentile_y_50"],
     [("a", 1.5), ("b", 3.8)]),
    ("summarize sum(x) + max(x) by g", ["g", "Column1"], [("a", 5), ("b", 6)]),
    ("summarize count() * 2 by g", ["g", "Column1"], [("a", 4), ("b", 2)]),
    ("summarize -sum(x) by g", ["g", "Column1"], [("a", -3), ("b", -3)]),
    ("summarize iff(count() > 1, 1, 2) by g", ["g", "Column1"], [("a", 1), ("b", 2)]),
    ("summarize strcat('n=', tostring(count())) by g", ["g", "Column1"],
     [("a", "n=2"), ("b", "n=1")]),
    # Two of them, including the collision suffix.
    ("summarize round(sum(y), 2), sum(x) by g", ["g", "sum_y", "sum_x"],
     [("a", 3.75, 3), ("b", 3.75, 3)]),
    ("summarize round(sum(y), 2), round(sum(y), 1) by g", ["g", "sum_y", "sum_y1"],
     [("a", 3.75, 3.8), ("b", 3.75, 3.8)]),
]

#: Kusto refuses these. Accepting them would mean a query that runs here and
#: fails against a real cluster — the second one *would* run in plain SQL.
REFUSED_BY_KUSTO = [
    "summarize R = sum(sum(x)) by g",
    "summarize R = sum(x) + x by g",
    "summarize R = strcat(g, tostring(count())) by g",
    "summarize 1 + 2 by g",
]


@pytest.fixture(scope="module")
def con():
    return duckdb_kql.connect()


@pytest.mark.parametrize("tail,columns,rows", MEASURED, ids=[m[0] for m in MEASURED])
def test_it_matches_the_emulator(con, tail, columns, rows) -> None:
    rel = duckdb_kql.kql(con, f"{TABLE} | {tail}")
    assert list(rel.columns) == columns
    assert sorted(map(str, (tuple(r) for r in rel.fetchall()))) == sorted(map(str, rows))


@pytest.mark.parametrize("tail", REFUSED_BY_KUSTO)
def test_what_kusto_refuses_is_refused(con, tail) -> None:
    with pytest.raises(KqlUnsupportedError):
        duckdb_kql.to_sql(f"{TABLE} | {tail}")


def test_a_column_outside_an_aggregate_says_which_column() -> None:
    """The message should name the mistake, not the language."""
    with pytest.raises(KqlUnsupportedError) as caught:
        duckdb_kql.to_sql(f"{TABLE} | summarize R = sum(x) + x by g")
    assert "'x'" in str(caught.value)
    assert "outside an aggregate" in str(caught.value)


def test_an_unsupported_aggregate_still_reports_as_one() -> None:
    """Widening this must not turn `aggregate:foo` into a message about `foo`'s
    arguments — in summarize position an unknown call is most likely an
    aggregate nobody has mapped yet."""
    with pytest.raises(KqlUnsupportedError) as caught:
        duckdb_kql.to_sql(f"{TABLE} | summarize R = stdevp_not_real(x) by g")
    assert "aggregate:" in str(caught.value)


def test_a_bare_column_is_still_refused() -> None:
    with pytest.raises(KqlUnsupportedError):
        duckdb_kql.to_sql(f"{TABLE} | summarize x by g")


def test_the_aggregate_is_rendered_once_not_repeated(con) -> None:
    """`sum(x) / count()` must not expand `sum(x)` twice under the divisor."""
    sql = str(duckdb_kql.to_sql(f"{TABLE} | summarize R = sum(x) + sum(x) by g"))
    assert sql.count("sum(") == 2  # once per occurrence in the KQL, no more
