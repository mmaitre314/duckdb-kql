"""Trap tests — the right-hand side of `in` and `has_any` must be constant (SEM0040).

Measured on the emulator (2026-11-08), for `in`, `!in`, `in~`, `has_any` and
`has_all` alike: each item must be a constant — a literal, a `let` constant, a
query parameter, a `toscalar`, or a function of those. Anything that reads the
row is refused, SEM0040, however it is wrapped: `s in (w)`, `s has_any (n)`,
`pack_array(w)`, `split(w, ',')`, `n[0]`, `strcat(w, '')`, `tostring(n)`.

This translator answered every one of them, and some wrongly even on its own
terms: `s has_any (n)` was 0 with the needle in `n`, and `s in (n)` leaked a
DuckDB conversion error. A query that passes here and fails on a cluster is the
case the `lookup` kinds are refused for; these are refused now too, naming the
column.

**Why the obvious check is wrong.** Refusing any `ColumnRef` below the item
refuses `toscalar(T | project w)`, which reads a column of *its own* query and
is accepted. And a `let` whose name a column shadows (R23) is that column: `let
n = dynamic([…]); T | where s has_any (n)` is SEM0040 when `T` has a column `n`,
measured — the check runs after that resolution, not before it.

**Residue:** graph-match is not covered. There Kusto refuses properties,
`node_degree_*` and `any`/`all` as operands but accepts dynamic ones —
`'B' in (map(p, t))`, `labels(a)`, a whole node — for `in`, not `has_any`; this
translator answers the first kind and leaks a DuckDB error on the second.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlUnsupportedError

duckdb = pytest.importorskip("duckdb")

T = "datatable(s:string, n:dynamic, w:string)['world', dynamic(['world']), 'world']"


def _rows(query: str, **kwargs: object) -> list[tuple]:
    return duckdb_kql.kql(duckdb_kql.connect(), query, **kwargs).fetchall()


@pytest.mark.parametrize(
    ("tail", "construct"),
    [
        ("s in (w)", "in:w"),
        ("s in (n)", "in:n"),
        ("s in ('x', w)", "in:w"),
        ("s in~ (w)", "in~:w"),
        ("s !in (w)", "!in:w"),
        ("s has_any (n)", "has_any:n"),
        ("s has_all (n)", "has_all:n"),
        ("s has_any (w)", "has_any:w"),
        ("s has_any ('x', w)", "has_any:w"),
        ("s has_any (pack_array(w))", "has_any:w"),
        ("s has_any (split(w, ','))", "has_any:w"),
        ("s has_any (n[0])", "has_any:n"),
        ("s has_any (tostring(n))", "has_any:n"),
        ("s has_all (strcat(w, ''))", "has_all:w"),
    ],
)
def test_an_operand_that_reads_a_column_is_refused(tail: str, construct: str) -> None:
    """Every one SEM0040 on the emulator."""
    with pytest.raises(KqlUnsupportedError, match="SEM0040") as caught:
        duckdb_kql.to_sql(f"{T} | where {tail} | count")
    assert caught.value.construct == construct


@pytest.mark.parametrize(
    "query",
    [
        f"let n = dynamic(['world']); {T} | where s has_any (n) | count",
        f"let w = 'world'; {T} | where s in (w) | count",
    ],
)
def test_a_let_shadowed_by_a_column_is_the_column(query: str) -> None:
    """R23, then SEM0040 — both measured."""
    with pytest.raises(KqlUnsupportedError, match="SEM0040"):
        duckdb_kql.to_sql(query)


@pytest.mark.parametrize(
    ("tail", "expected"),
    [
        ("s has_any (dynamic(['world']))", 1),
        ("s has_any (pack_array('world'))", 1),
        ("s has_any ('x', strcat('wor', 'ld'))", 1),
        ("s has_any (dynamic(['x']), 'world')", 1),
        (f"s has_any (toscalar({T} | project w))", 1),
        (f"s in (toscalar({T} | project w))", 1),
        ("s has_all (toscalar(print dynamic(['world'])))", 1),
    ],
)
def test_a_constant_operand_is_answered(tail: str, expected: int) -> None:
    """Measured, Kusto's answer each time."""
    assert _rows(f"{T} | where {tail} | count") == [(expected,)]


def test_a_let_constant_and_a_query_parameter_are_constants() -> None:
    assert _rows(f"let q = 'world'; {T} | where s in (q) | count") == [(1,)]
    query = f"declare query_parameters(p:dynamic); {T} | where s has_any (p) | count"
    assert _rows(query, parameters={"p": ["world"]}) == [(1,)]
