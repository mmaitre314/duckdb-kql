"""Trap tests — an unnamed column is named the way Kusto names it, in every operator.

Reported as a silent wrong answer: `T | extend abs(x) | where x > 1` read the
**un-extended** `x`. Kusto names `abs(x)` after its column, so `extend`
*replaces* `x`; this added a `Column1` beside it, and every later reference to
`x` saw the old values, with no error.

Measured on the emulator, 693 probes over every supported function in
`project`, `extend`, `summarize … by` and `distinct`, and the rule is in
`translate/naming.py`. Before the fix 301 of them disagreed; after, every
naming disagreement left is `distinct`'s residue below. Four parts, and none is
the obvious one:

* **A name comes from a column through an allow-list**, the one `summarize …
  by` already used, now shared, completed and applied to `project` and
  `extend` too: `tostring(x)`, `abs(x)`, `sin(x)`, `binary_and(i, 1)` are named
  after the column, `tolower(s)`, `pow(x, 2)`, `cot(x)` are positional.
  `strlen(s)` is `strlen_s`, `gettype(x)` `type_x`, a path its steps joined —
  `d.b[-1]` is `d_b__1`. The same rule names an aggregate inside a scalar:
  `pow(min(x), 2)` is `Column1` and `strlen(tostring(count()))`
  `strlen_count_`, where this followed any first argument and said `min_x` and
  `count_`.
* **A positional name counts only the targets that need one** — `project a,
  a+1` is `a, Column1`; this said `Column2`.
* **A repeat gets `1`, `2`, … with no separator, and a spelled-out name is
  reserved but not first served.** `project abs(x), sqrt(x), x1 = 100` is `x,
  x2, x1`; `project i + 1, Column1 = 100` is `Column1, Column11` — the explicit
  name is the one that moves. DuckDB's own de-duplication said `x_1`.
* **`extend` and `summarize` never reuse an input column's name for a
  `ColumnN`**, and `project` does: `extend Column1 = 5 | extend i + 1` adds
  `Column2`. `extend` also never renames: `extend abs(x), sqrt(x)` is refused,
  as Kusto refuses it (SEM0034).

**Residue:** `distinct` numbers some positional names one higher by no rule the
measurements give — `distinct i + 1` and `distinct cot(x)` are `Column2` — and
here they count from one, as they did before.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlSchemaError

duckdb = pytest.importorskip("duckdb")

T = (
    "datatable(x:real, i:long, s:string, d:dynamic)"
    "[1.5, 2, 'a,b', dynamic({'a': 1, 'b': [1, 2]})]"
)


def _columns(tail: str, table: str = T) -> list[str]:
    cursor = duckdb_kql.kql(duckdb_kql.connect(), f"{table} | {tail}")
    return [d[0] for d in cursor.description]


def test_extend_with_a_column_named_expression_replaces_the_column() -> None:
    """The reported bug: the `where` must see the extended values."""
    query = (
        "datatable(x:real, s:string)[-0.5, 'a', -2.0, 'b', 3.0, 'c']"
        " | extend abs(x) | where x > 1 | order by s asc"
    )
    rows = duckdb_kql.kql(duckdb_kql.connect(), query).fetchall()
    assert rows == [(2.0, "b"), (3.0, "c")]


#: `(operator tail, Kusto's column names)` — every one measured.
MEASURED = [
    # the allow-list, and what falls outside it
    ("project tostring(x), toint(x), abs(x), sin(x), binary_and(i, 1)",
     ["x", "x1", "x2", "x3", "i"]),
    ("project tolower(s), pow(x, 2), cot(x), degrees(x)",
     ["Column1", "Column2", "Column3", "Column4"]),
    ("project parse_json(s), split(s, ','), gamma(x)", ["s", "s1", "x"]),
    ("extend tostring(i)", ["x", "i", "s", "d"]),
    ("extend abs(x), tolower(s)", ["x", "i", "s", "d", "Column1"]),
    # prefixed names, paths and composition
    ("project strlen(s), gettype(x), strlen('abc'), gettype(1)",
     ["strlen_s", "type_x", "strlen_", "type_"]),
    ("project strlen(tostring(s)), gettype(strlen(s)), strlen(gettype(x))",
     ["strlen_s", "type_strlen_s", "strlen_type_x"]),
    ("project d.a, d['a'], d.b[0], d.b[-1], tostring(d.a)",
     ["d_a", "d_a1", "d_b_0", "d_b__1", "d_a2"]),
    ("project pack_array(1, 2)[0], parse_json(s).a", ["0", "s_a"]),
    ("project array_sort_asc(d), array_sort_asc(d.b)", ["d_sorted", "array0_sorted"]),
    ("project abs(x + 1), abs(-x), abs(1)", ["Column1", "Column2", "Column3"]),
    # positional numbering, and renaming
    ("project i + 1, abs(x), i + 2", ["Column1", "x", "Column2"]),
    ("project x, abs(x)", ["x", "x1"]),
    ("project abs(x), x", ["x", "x1"]),
    ("project abs(x), sqrt(x), x1 = 100", ["x", "x2", "x1"]),
    ("project abs(x), sqrt(x), x1 = 100, x2 = 200", ["x", "x3", "x1", "x2"]),
    ("project i + 1, Column1 = 100", ["Column1", "Column11"]),
    ("project Column1 = 100, i + 1, Column11 = 200", ["Column1", "Column12", "Column11"]),
    ("project i + 1, i + 2, Column2 = 100", ["Column1", "Column2", "Column21"]),
    ("project d.a, d_a = 1", ["d_a", "d_a1"]),
    ("extend i + 1, i + 2", ["x", "i", "s", "d", "Column1", "Column2"]),
    ("extend Column1 = 5 | extend i + 1, i + 2",
     ["x", "i", "s", "d", "Column1", "Column2", "Column3"]),
    ("extend strlen(s) | extend strlen(s)", ["x", "i", "s", "d", "strlen_s"]),
    # summarize: keys first, one namespace, the same allow-list for wrappers
    ("summarize count() by abs(x), sqrt(x)", ["x", "x1", "count_"]),
    ("summarize sum(x) by sum_x = i", ["sum_x", "sum_x1"]),
    ("summarize x1 = count() by x, abs(x)", ["x", "x2", "x1"]),
    ("summarize count() + 1, x = count(), count() * 2", ["Column1", "x", "Column2"]),
    ("summarize count() + 1 by i + 1", ["Column1", "Column2"]),
    ("summarize abs(min(x)), strlen(tostring(count())), round(avg(x), 2)",
     ["min_x", "strlen_count_", "avg_x"]),
    ("summarize pow(min(x), 2), degrees(max(x)), sin(avg(x))", ["Column1", "Column2", "avg_x"]),
    ("summarize array_length(make_set(x)), todouble(dcount(x)), sqrt(variance(x))",
     ["Column1", "dcount_x", "variance_x"]),
    ("summarize sum(strlen(s)), max(tostring(d.a)), max(tolower(s)), max(gettype(x))",
     ["sum_strlen_s", "max_d_a", "max_", "max_type_x"]),
    ("summarize max(x), max(x) by bin(i, 1)", ["i", "max_x", "max_x1"]),
    # distinct follows `by`, except where a gettype names the column itself
    ("distinct abs(x), x", ["x", "x1"]),
    ("distinct strlen(s), strlen(s)", ["strlen_s", "strlen_s1"]),
    ("distinct gettype(s), tostring(gettype(x)), strlen(gettype(x))",
     ["Column1", "Column2", "strlen_type_x"]),
    ("summarize count() by gettype(x)", ["type_x", "count_"]),
]


@pytest.mark.parametrize(("tail", "expected"), MEASURED)
def test_names_match_kusto(tail: str, expected: list[str]) -> None:
    assert _columns(tail) == expected


WITH_COLUMN1 = "datatable(i:long, Column1:long)[2, 7]"


@pytest.mark.parametrize(
    ("tail", "expected"),
    [
        ("extend i + 1", ["i", "Column1", "Column2"]),
        ("summarize count() by i + 1, Column1", ["Column2", "Column1", "count_"]),
        ("summarize count() + 1 by Column1", ["Column1", "Column2"]),
        # `project`'s does not step past it — renamed instead
        ("project i + 1, Column1", ["Column1", "Column11"]),
    ],
)
def test_a_positional_name_steps_past_an_input_column_except_in_project(
    tail: str, expected: list[str]
) -> None:
    assert _columns(tail, WITH_COLUMN1) == expected


@pytest.mark.parametrize(
    "tail",
    [
        "project x, x",
        "project y = x, y = i",
        "project i = 2, i",
        "extend abs(x), sqrt(x)",
        "extend x = 1, abs(x)",
        "extend strlen(s), strlen(s)",
        "extend i + 1, Column1 = 5",
        "summarize count() by i, i",
        "summarize x = count() by x",
        "distinct x, x",
    ],
)
def test_a_name_spelled_or_generated_twice_where_kusto_refuses_it(tail: str) -> None:
    """SEM0034 on the emulator, every one. `extend` used to keep the last of two
    same-named columns and drop the other silently."""
    with pytest.raises(KqlSchemaError):
        duckdb_kql.to_sql(f"{T} | {tail}")


def test_an_index_by_expression_is_refused_unnamed() -> None:
    """Kusto names `project d[i]` after a fresh GUID, so no name could match."""
    from duckdb_kql.errors import KqlUnsupportedError

    with pytest.raises(KqlUnsupportedError):
        duckdb_kql.to_sql(f"{T} | project d[i]")
    assert _columns("project v = d[i]") == ["v"]


def test_the_schema_walker_predicts_the_same_names() -> None:
    """A later operator resolves columns through `schema.output_columns`, so it
    must agree with what was emitted — `x1` here exists only if both say so."""
    assert _columns("project abs(x), sqrt(x) | project x1") == ["x1"]
    assert _columns("summarize count() by abs(x), sqrt(x) | project x1, count_") == [
        "x1", "count_",
    ]
