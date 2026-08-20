"""L5 trap tests — ``distinct`` targets and auto-generated output names.

The documented syntax for `distinct` is a column list. The emulator accepts an
**expression**, named or not, and this used to lower the target list with
`_find_names` — a helper whose own docstring warns it is only safe where the
grammar allows nothing but names. A function name is a ``KeywordName`` too, so
``distinct B2 = tostring(B)`` collected ``tostring`` as a column, threw the
alias away, and reached DuckDB as ``SELECT DISTINCT "tostring", "B"``.

The auto-generated names are the other trap here, and they are shared with
``summarize``'s ``by`` keys — measured identical on every function probed.
The rule is an **allow-list, not a principle**:

* ``tostring(B)`` is ``B`` but ``tolower(B)`` is ``Column1``;
* ``startofday(T)`` is ``T`` but ``startofweek(T)`` is ``Column1``;
* ``log2(C)`` and ``exp2(C)`` pass through; ``pow(C,2)`` and ``exp10(C)`` do not.

A call outside the list also **breaks the chain**: ``abs(-C)`` and
``tolower(tostring(B))`` are both ``Column1``, even though the inner call would
have resolved on its own.
"""

from __future__ import annotations

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    # 'a' twice so `distinct` has something to collapse, and 'A' so a
    # case-changing function genuinely changes the row set.
    c.execute("CREATE TABLE DT(B VARCHAR, C BIGINT, N VARCHAR)")
    c.execute("INSERT INTO DT VALUES ('a',1,'x'),('a',2,''),('b',1,'x'),('A',1,'x')")
    return c


def _cols(con, kql):
    return list(duckdb_kql.kql(con, kql).columns)


def _rows(con, kql):
    return sorted(
        duckdb_kql.kql(con, kql).fetchall(), key=lambda r: tuple(str(x) for x in r)
    )


# ---------------------------------------------------------------------------
# Expressions as targets
# ---------------------------------------------------------------------------


def test_the_reported_query(con) -> None:
    """`distinct B2 = tostring(B)` named a column after the *function*."""
    assert _cols(con, "DT | distinct B2 = tostring(B)") == ["B2"]
    assert _rows(con, "DT | distinct B2 = tostring(B)") == [("A",), ("a",), ("b",)]


def test_a_bare_function_call(con) -> None:
    assert _cols(con, "DT | distinct tostring(B)") == ["B"]


def test_an_aliased_bare_column(con) -> None:
    assert _cols(con, "DT | distinct B2 = B") == ["B2"]
    assert _rows(con, "DT | distinct B2 = B") == [("A",), ("a",), ("b",)]


def test_a_multi_argument_call(con) -> None:
    assert _cols(con, "DT | distinct strcat(B, '!')") == ["Column1"]
    assert _rows(con, "DT | distinct strcat(B, '!')") == [("A!",), ("a!",), ("b!",)]


def test_an_expression_actually_deduplicates_on_its_value(con) -> None:
    """`tolower` collapses 'a' and 'A', which a column list could not express."""
    assert _rows(con, "DT | distinct tolower(B)") == [("a",), ("b",)]


def test_a_mix_of_columns_and_expressions(con) -> None:
    assert _cols(con, "DT | distinct B, B2 = tostring(C)") == ["B", "B2"]


def test_a_plain_column_list_still_works(con) -> None:
    assert _cols(con, "DT | distinct B, C") == ["B", "C"]
    assert len(_rows(con, "DT | distinct B, C")) == 4


def test_a_single_plain_column(con) -> None:
    assert _cols(con, "DT | distinct B") == ["B"]
    assert _rows(con, "DT | distinct B") == [("A",), ("a",), ("b",)]


def test_an_expression_with_a_spaced_operator_inside(con) -> None:
    """The grammar mis-parse is repaired from the original source text.

    Rebuilding it from `getText()` would concatenate token text and turn
    `B has 'x y'` into `Bhas'x y'` — a different expression that still parses.
    """
    assert _cols(con, "DT | distinct B2 = tostring(N has 'x')") == ["B2"]
    assert _rows(con, "DT | distinct B2 = tostring(N has 'x')") == [
        ("false",), ("true",)
    ]


def test_distinct_star_is_still_refused(con) -> None:
    with pytest.raises(duckdb_kql.KqlUnsupportedError):
        duckdb_kql.kql(con, "DT | distinct *")


def test_operators_after_a_distinct_see_the_new_name(con) -> None:
    assert _rows(con, "DT | distinct B2 = tolower(B) | where B2 == 'a'") == [("a",)]


def test_a_scalar_let_substitutes_into_a_distinct_target(con) -> None:
    assert _rows(con, "let n = 2; DT | distinct C2 = C * n") == [(2,), (4,)]


def test_a_tabular_let_resolves_inside_a_distinct_target(con) -> None:
    assert _rows(
        con, "let v = DT | project B | where B == 'b'; DT | distinct hit = B in (v)"
    ) == [(False,), (True,)]


# ---------------------------------------------------------------------------
# Auto-generated names — the allow-list (shared with `summarize by`)
# ---------------------------------------------------------------------------

#: ``(expression, expected column name)``, each measured on the emulator.
NAMES = [
    # pass the inner column's name through
    ("tostring(B)", "B"), ("toint(C)", "C"), ("tolong(C)", "C"),
    ("todouble(C)", "C"), ("toreal(C)", "C"), ("bin(C, 2)", "C"),
    ("ceiling(C)", "C"), ("round(C)", "C"), ("round(C, 1)", "C"),
    ("abs(C)", "C"), ("sqrt(C)", "C"), ("log(C)", "C"), ("log10(C)", "C"),
    ("log2(C)", "C"), ("exp(C)", "C"), ("exp2(C)", "C"),
    # and nest, while a call outside the list breaks the chain
    ("tostring(toint(C))", "C"), ("bin(tolong(C), 2)", "C"),
    ("abs(-C)", "Column1"), ("tolower(tostring(B))", "Column1"),
    # fall back to the positional name
    ("tolower(B)", "Column1"), ("toupper(B)", "Column1"),
    ("strcat(B, B)", "Column1"), ("isempty(B)", "Column1"),
    ("isnull(B)", "Column1"), ("sign(C)", "Column1"), ("pow(C, 2)", "Column1"),
    ("exp10(C)", "Column1"),
]


@pytest.mark.parametrize(("expr", "expected"), NAMES)
def test_distinct_target_names(con, expr: str, expected: str) -> None:
    assert _cols(con, f"DT | distinct {expr}") == [expected]


@pytest.mark.parametrize(("expr", "expected"), NAMES)
def test_summarize_key_names_follow_the_same_rule(con, expr: str, expected: str) -> None:
    assert _cols(con, f"DT | summarize n = count() by {expr}") == [expected, "n"]


def test_the_positional_fallback_counts_only_unnamed_targets(con) -> None:
    """`C, tolower(B)` is `C, Column1` — not `C, Column2`.

    Numbering by absolute position shifted every fallback name that followed a
    resolvable one.
    """
    assert _cols(con, "DT | distinct C, tolower(B)") == ["C", "Column1"]
    assert _cols(con, "DT | distinct tolower(B), C") == ["Column1", "C"]
    assert _cols(con, "DT | distinct tolower(B), toupper(B)") == [
        "Column1", "Column2"
    ]
    assert _cols(con, "DT | summarize n = count() by C, tolower(B)") == [
        "C", "Column1", "n"
    ]
