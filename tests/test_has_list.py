"""L5 trap tests — ``has_any`` / ``has_all`` / ``not()`` and the term boundary (R3).

Started from a real bug report:

    MyTable | where not(Column1 has_any ("value1", "value2"))

which is valid KQL — confirmed on the emulator — and failed to translate. Three
separate defects were behind it, and each has tests here.

1. **`has_any` / `has_all` were unlowered.** They share the `in` family's
   grammar rule (``listEqualityExpression``), so the parse succeeded and
   lowering fell through.
2. **`not()` had no mapping.** It is a *function* in KQL, not the `!` prefix.
3. **The error named the wrong operator** — see `test_diagnostics` below.

Along the way the term boundary itself turned out to be wrong for the whole
`has` family: the mapping used regex ``\\b``, which counts ``_`` as a word
character, so ``"a_b" has "a"`` was false here and **true** on the emulator.

Every expected value below was measured against the Kusto Emulator.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlError

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    c.execute("CREATE TABLE T(s VARCHAR)")
    c.execute(
        "INSERT INTO T VALUES ('alpha beta'),('alpha'),('beta gamma'),(''),('a_b')"
    )
    c.execute("CREATE TABLE MyTable(Column1 VARCHAR)")
    c.execute(
        "INSERT INTO MyTable VALUES "
        "('value1 here'),('other'),('VALUE2'),('nothing'),('value1_x'),('xvalue1')"
    )
    return c


def _col(con, kql):
    return [r[0] for r in duckdb_kql.kql(con, kql).fetchall()]


# ---------------------------------------------------------------------------
# The reported query
# ---------------------------------------------------------------------------


def test_the_reported_query_translates_and_runs(con) -> None:
    """`not(x has_any (...))` — valid KQL that used to raise.

    Emulator, row by row: 'value1_x' does NOT come back, because `_` delimits a
    term so that row still *has* "value1"; 'xvalue1' DOES, because it is a
    single term and no substring of it counts.
    """
    rows = _col(con, 'MyTable | where not(Column1 has_any ("value1","value2"))')
    assert sorted(rows) == ["nothing", "other", "xvalue1"]


def test_the_positive_form_is_the_complement(con) -> None:
    rows = _col(con, 'MyTable | where Column1 has_any ("value1","value2")')
    assert sorted(rows) == ["VALUE2", "value1 here", "value1_x"]


# ---------------------------------------------------------------------------
# has_any / has_all are the `has` family, not the `in` family (R3)
# ---------------------------------------------------------------------------


def test_has_any_matches_terms_not_substrings(con) -> None:
    """The whole reason these cannot be aliased to `in` or to `contains`."""
    con.execute("CREATE TABLE E(s VARCHAR)")
    con.execute("INSERT INTO E VALUES ('error'),('errors'),('xerrorx'),('ERROR')")
    assert sorted(_col(con, 'E | where s has_any ("error")')) == ["ERROR", "error"]


def test_has_all_requires_every_item(con) -> None:
    assert _col(con, 'T | where s has_all ("alpha","beta")') == ["alpha beta"]


def test_has_any_requires_only_one(con) -> None:
    assert sorted(_col(con, 'T | where s has_any ("alpha","beta")')) == [
        "alpha", "alpha beta", "beta gamma",
    ]


def test_case_insensitive_by_default(con) -> None:
    assert _col(con, 'MyTable | where Column1 has_any ("value2")') == ["VALUE2"]


def test_dynamic_array_right_hand_side(con) -> None:
    """The needles are inside one item, so they are only known at runtime."""
    assert sorted(_col(con, 'T | where s has_any (dynamic(["alpha","zeta"]))')) == [
        "alpha", "alpha beta",
    ]
    assert _col(con, 'T | where s has_all (dynamic(["alpha","beta"]))') == ["alpha beta"]


def test_mixed_literal_and_dynamic_items(con) -> None:
    assert sorted(_col(con, 'T | where s has_any ("zeta", dynamic(["beta"]))')) == [
        "alpha beta", "beta gamma",
    ]


def test_subquery_right_hand_side(con) -> None:
    con.execute("CREATE TABLE D(t VARCHAR)")
    con.execute("INSERT INTO D VALUES ('alpha'),('zeta')")
    assert sorted(_col(con, "T | where s has_any (D | project t)")) == [
        "alpha", "alpha beta",
    ]
    assert _col(con, "T | where s has_all (D | project t)") == []


def test_a_let_bound_dynamic_array_is_substituted(con) -> None:
    """Real corpus KQL. Missing this produced SQL naming a nonexistent column."""
    rows = _col(
        con, 'let names = dynamic(["alpha"]); T | where s has_any (names)'
    )
    assert sorted(rows) == ["alpha", "alpha beta"]


def test_numeric_needle(con) -> None:
    con.execute("CREATE TABLE N(s VARCHAR)")
    con.execute("INSERT INTO N VALUES ('code 42 here')")
    assert _col(con, "N | where s has_any (42)") == ["code 42 here"]


def test_null_left_operand_is_false_not_null(con) -> None:
    """R4: `where` would silently drop the row if this stayed NULL."""
    con.execute("CREATE TABLE Z(s VARCHAR)")
    con.execute("INSERT INTO Z VALUES (NULL)")
    assert _col(con, 'Z | where s has_any ("x")') == []
    assert _col(con, 'Z | where s has_all ("x")') == []
    assert _col(con, 'Z | where not(s has_any ("x"))') == [None]


@pytest.mark.parametrize("expr", ["s !has_any (\"a\")", "s has_any_cs (\"a\")"])
def test_forms_kusto_does_not_have_are_refused(con, expr: str) -> None:
    """Kusto has no negated or case-sensitive list form; neither do we."""
    with pytest.raises(KqlError):
        duckdb_kql.kql(con, f"T | where {expr}")


# ---------------------------------------------------------------------------
# The term boundary (R3) — `_` is a delimiter, `\b` says otherwise
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a_b", True),   # the regression: `\b` made this False
        ("a-b", True),
        ("a.b", True),
        ("a b", True),
        ("a", True),
        ("ab", False),
        ("a1", False),
        ("éa", False),   # accented letters are term characters
    ],
)
def test_term_boundary_treats_underscore_as_a_delimiter(
    con, value: str, expected: bool
) -> None:
    con.execute("CREATE TABLE B(s VARCHAR)")
    con.execute("INSERT INTO B VALUES (?)", [value])
    assert bool(_col(con, 'B | where s has "a"')) is expected


def test_the_same_boundary_applies_to_has_any(con) -> None:
    """One term definition, shared — not two that can drift apart."""
    assert _col(con, 'T | where s has_any ("a")') == ["a_b"]


def test_has_still_rejects_substrings(con) -> None:
    """The boundary fix must not turn `has` into `contains`."""
    con.execute("CREATE TABLE E(s VARCHAR)")
    con.execute("INSERT INTO E VALUES ('errors')")
    assert _col(con, 'E | where s has "error"') == []
    assert _col(con, 'E | where s contains "error"') == ["errors"]


def test_needle_containing_a_delimiter(con) -> None:
    con.execute("CREATE TABLE H(s VARCHAR)")
    con.execute("INSERT INTO H VALUES ('x a-b y'),('x ab y'),('x a_b y')")
    assert _col(con, 'H | where s has "a-b"') == ["x a-b y"]


# ---------------------------------------------------------------------------
# not()
# ---------------------------------------------------------------------------


def test_not_on_booleans(con) -> None:
    rel = duckdb_kql.kql(con, "print a = not(true), b = not(false), c = not(1)")
    assert rel.fetchall() == [(False, True, False)]


def test_not_of_null_is_null_not_true(con) -> None:
    """The one place in this neighbourhood R4's totality does NOT apply.

    Measured: `not(bool(null))` is null on the emulator. Coalescing it to true
    would be a plausible-looking wrong answer.
    """
    assert duckdb_kql.kql(con, "print a = not(bool(null))").fetchall() == [(None,)]


def test_not_composes_with_and_or(con) -> None:
    rows = _col(con, 'T | where not(s has_any ("alpha")) or s has_any ("gamma")')
    assert sorted(rows) == ["", "a_b", "beta gamma"]


def test_not_wraps_in_as_well(con) -> None:
    assert sorted(_col(con, 'T | where not(s in ("alpha","a_b",""))')) == [
        "alpha beta", "beta gamma",
    ]


# ---------------------------------------------------------------------------
# Diagnostics — the reported error named the wrong operator
# ---------------------------------------------------------------------------


def test_an_unsupported_list_operator_is_named_by_its_own_spelling() -> None:
    """The bug: the message said 'in' for a `has_any` query.

    `_lower_in_list` raised ``_unsupported(node, "in")`` with the string
    hardcoded — the handler's name, not the operator in the query — so the
    error pointed at the wrong half of the expression. Now the token is read
    off the tree. Checked directly, since every operator the grammar admits is
    supported today and the fallback is otherwise unreachable.
    """
    from duckdb_kql.lower import _list_operator_text
    from duckdb_kql.parser import parse

    def list_node(kql: str):
        stack = [parse(kql).tree]
        while stack:
            node = stack.pop()
            if type(node).__name__ == "ListEqualityExpressionContext":
                return node
            stack.extend(getattr(node, "children", None) or [])
        raise AssertionError("no listEqualityExpression in " + kql)

    assert _list_operator_text(list_node('T | where a has_any ("x")')) == "has_any"
    assert _list_operator_text(list_node('T | where a has_all ("x")')) == "has_all"
    assert _list_operator_text(list_node('T | where a in ("x")')) == "in"
    assert _list_operator_text(list_node('T | where a !in~ ("x")')) == "!in~"


# ---------------------------------------------------------------------------
# LIKE metacharacters in the needle
# ---------------------------------------------------------------------------


def test_contains_treats_underscore_as_a_literal(con) -> None:
    """`_` is a LIKE wildcard, and the escaping was inert without ESCAPE.

    DuckDB's LIKE has no default escape character, so the `\\_` the translator
    added required a *literal backslash* in the data and matched nothing:
    `s contains "user_id"` silently returned zero rows. Found by a random
    differential sweep, not by hand.
    """
    con.execute("CREATE TABLE L(s VARCHAR)")
    con.execute("INSERT INTO L VALUES ('user_id'),('userXid'),('100%'),('100pct')")
    assert _col(con, 'L | where s contains "user_id"') == ["user_id"]
    assert _col(con, 'L | where s contains "%"') == ["100%"]


def test_the_wildcard_does_not_leak_through_startswith_or_endswith(con) -> None:
    con.execute("CREATE TABLE L(s VARCHAR)")
    con.execute("INSERT INTO L VALUES ('a_b'),('axb'),('_ab'),('xab')")
    assert _col(con, 'L | where s startswith "a_"') == ["a_b"]
    assert _col(con, 'L | where s endswith "_b"') == ["a_b"]


def test_delimiter_only_needles_fall_back_to_substring(con) -> None:
    """`has " "` is **true** on the emulator — wrapping it in term boundaries
    would make it false, since a space is never at a term boundary.
    """
    con.execute("CREATE TABLE W(s VARCHAR)")
    con.execute("INSERT INTO W VALUES ('b .b-'),('11'),('a-b')")
    assert _col(con, 'W | where s has " "') == ["b .b-"]
    assert sorted(_col(con, 'W | where s has "-"')) == ["a-b", "b .b-"]
    assert len(_col(con, 'W | where s has ""')) == 3


def test_the_boundary_applies_only_at_term_character_edges(con) -> None:
    """Measured: `'xa b' has "a "` is false but `'a b' has "a "` is true."""
    con.execute("CREATE TABLE E2(s VARCHAR)")
    con.execute("INSERT INTO E2 VALUES ('a b'),('xa b'),('x ab y'),('x a-b y')")
    assert _col(con, 'E2 | where s has "a "') == ["a b"]
    assert _col(con, 'E2 | where s has " a"') == ["x a-b y"]
