"""Trap tests — what `has_any`/`has_all` take as needles from an operand that is not a string.

Measured on the emulator (2026-11-08) over the one row `'hello world 2'`. An
operand's needles are:

* a **scalar's** string form, one needle — and a null is `''`, which every
  value has: `has_any (long(null))` and `has_any (dynamic(null))` are true;
* an **array's** leaves, flattened however deep — `dynamic([['x'], ['world']])`
  has the needles `x` and `world` — a null leaf being `''`;
* never a **bag**, at any depth, and through a `toscalar` too: SEM0022.

**What was wrong.** Whether an operand is an array was decided by a predicate
over the IR that knew three functions — `parse_json`, `todynamic`,
`pack_array` — so `split('world', ',')` was taken for a scalar and its *text*,
`["world"]`, was the one needle: 0 where Kusto has 1, silently. The same for
`extract_all`, `array_concat`, `array_slice`. Beside that: a nested array was
not flattened (0 for 1), a `toscalar` null matched nothing (0 for 1), a scalar
`dynamic('world')` leaked a DuckDB conversion error, and a bag was answered.

**Why the obvious fix is wrong.** Adding `split` to the predicate fixes `split`
and leaves the next array-valued function as wrong as it was — CLAUDE.md's
static-predicate trap. The shape is asked of DuckDB's `typeof`
at run time, and only a JSON or LIST operand is flattened. A literal, a
declared scalar parameter and a literal array of strings keep their fast
paths.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlUnsupportedError

duckdb = pytest.importorskip("duckdb")

T = "datatable(s:string)['hello world 2']"


def _count(where: str) -> int:
    ((n,),) = duckdb_kql.kql(duckdb_kql.connect(), f"{T} | where {where} | count").fetchall()
    return n


#: `(operand, has_any, has_all)` — Kusto's counts, every one measured.
MEASURED = [
    ("split('world', ',')", 1, 1),
    ("split('x,world', ',')", 1, 0),
    ("split('hello,world', ',')", 1, 1),
    ("split('', ',')", 1, 1),
    ("extract_all(@'(\\w+)', 'xx world')", 1, 0),
    ("array_concat(dynamic(['x']), dynamic(['world']))", 1, 0),
    ("array_slice(dynamic(['x','world']), 1, 1)", 1, 1),
    ("parse_json('[\"world\"]')", 1, 1),
    ("parse_json('\"world\"')", 1, 1),
    ("todynamic('[\"world\"]')", 1, 1),
    ("dynamic('world')", 1, 1),
    ("dynamic([['x'],['world']])", 1, 0),
    ("dynamic([['hello', 'world']])", 1, 1),
    ("dynamic([[['world']]])", 1, 1),
    ("dynamic(null)", 1, 1),
    ("dynamic([])", 0, 1),
    ("dynamic([2])", 1, 1),
    ("dynamic([2.0])", 0, 0),
    ("dynamic([true])", 0, 0),
    ("dynamic(['wor'])", 0, 0),
    ("pack_array('x', 'world')", 1, 0),
    ("long(null)", 1, 1),
    ("2", 1, 1),
    ("1+1", 1, 1),
    ("strcat('wor', 'ld')", 1, 1),
    ("tostring(dynamic(['world']))", 0, 0),
    ("toscalar(print dynamic(null))", 1, 1),
    ("toscalar(print dynamic([null]))", 1, 1),
    ("toscalar(print dynamic([['world']]))", 1, 1),
    ("toscalar(print 'world')", 1, 1),
]


@pytest.mark.parametrize(("operand", "any_", "all_"), MEASURED)
def test_the_needles_are_kustos(operand: str, any_: int, all_: int) -> None:
    assert (_count(f"s has_any ({operand})"), _count(f"s has_all ({operand})")) == (any_, all_)


@pytest.mark.parametrize(
    "operand", ["dynamic({'a':'world'})", "dynamic([{'a':'world'}])", "dynamic([['x', {'a': 1}]])"]
)
def test_a_literal_bag_is_refused(operand: str) -> None:
    """SEM0022 on the emulator, all three."""
    with pytest.raises(KqlUnsupportedError, match="SEM0022"):
        duckdb_kql.to_sql(f"{T} | where s has_any ({operand}) | count")


@pytest.mark.parametrize(
    "operand",
    ["toscalar(print dynamic({'a':'world'}))", "parse_json('{\"a\":\"world\"}')"],
)
def test_a_bag_known_only_at_run_time_is_an_error(operand: str) -> None:
    """SEM0022 on the emulator; here an error at run time, never an answer."""
    with pytest.raises(duckdb.Error, match="SEM0022"):
        _count(f"s has_any ({operand})")


def test_where_the_type_is_declared_the_question_is_not_asked() -> None:
    """A literal or a declared scalar parameter is one needle, with no
    `json_tree`; a literal string array still unrolls into one test per needle."""
    for query in [
        f"{T} | where s has_any (2)",
        f"declare query_parameters(p:string); {T} | where s has_any (p)",
        f"{T} | where s has_any (dynamic(['hello', 'world']))",
    ]:
        sql = str(duckdb_kql.to_sql(query))
        assert "json_tree" not in sql and "list_filter" not in sql, query


def test_a_declared_scalar_parameter_is_one_needle() -> None:
    query = f"declare query_parameters(p:string); {T} | where s has_any (p) | count"
    rows = duckdb_kql.kql(duckdb_kql.connect(), query, parameters={"p": "world"}).fetchall()
    assert rows == [(1,)]


def test_the_text_of_an_array_was_the_needle() -> None:
    """The old reading of `split('world', ',')`: one needle, its text."""
    con = duckdb.connect()
    assert con.sql("SELECT CAST(str_split('world', ',') AS VARCHAR)").fetchall() == [("[world]",)]
