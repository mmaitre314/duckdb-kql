"""L5 trap tests — a `dynamic` where KQL expects a **string**.

A KQL `dynamic` is DuckDB `JSON`, and the two disagree about what a value's text
form is: JSON quotes a string, KQL does not. That one difference reaches a long
way, because Kusto silently coerces a dynamic to its text before every string
operator and several string functions — so the mismatch shows up as a *wrong
answer*, not as an error, on the line after `mv-expand`.

Every expectation here was measured on the Kusto Emulator. Before this file
existed:

* ``strlen(tostring(s))`` answered **3** for `'x'`, where Kusto says 1;
* ``s startswith 'x'`` answered **false**, and `s contains '"'` **true** —
  both silently wrong, the quote getting in the way;
* ``s == 'x'`` **crashed** with `Malformed JSON at byte 0`, DuckDB having cast
  the literal to JSON to match the column.

The remaining divergences are listed at the bottom of the file, each with the
direction it fails in, so the residue is visible rather than implied.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlUnsupportedError

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    return c


def _one(con, kql: str):
    return duckdb_kql.kql(con, kql).fetchall()[0][0]


#: A one-row table whose `s` is the dynamic string `'x'` — the shape
#: `mv-expand` over a list of names produces, and the one every gap here
#: needed. `mv-expand` rather than `dynamic('x')` on purpose: the column
#: carries no static type, which is exactly what defeats a static check.
EXPANDED = "datatable(s:dynamic)[dynamic(['x'])] | mv-expand s | project p = "


# ---------------------------------------------------------------------------
# D1 — tostring() of a dynamic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expr", "text", "length"),
    [
        ("dynamic('x')", "x", 1),
        ("dynamic(1)", "1", 1),
        ("dynamic(1.5)", "1.5", 3),
        ("dynamic(true)", "true", 4),
        ("dynamic([1,2])", "[1,2]", 5),
        ("dynamic({'a':1})", '{"a":1}', 7),
    ],
)
def test_tostring_of_a_dynamic(con, expr: str, text: str, length: int) -> None:
    """`strlen` is asserted too, so this pins the value and not the display."""
    assert _one(con, f"print tostring({expr})") == text
    assert _one(con, f"print strlen(tostring({expr}))") == length


def test_tostring_of_a_dynamic_null_is_empty_not_null(con) -> None:
    """The one row of the table above that does not follow the obvious rule.

    Measured three ways because it is surprising — every other `to*` conversion
    propagates null: the value is `''`, its `strlen` is 0, and `isnull` is
    **false**. It is also why the unwrap is a `coalesce`: DuckDB's `->> '$'`
    gets every other JSON form right and returns SQL null for a JSON null.
    """
    assert _one(con, "print tostring(dynamic(null))") == ""
    assert _one(con, "print strlen(tostring(dynamic(null)))") == 0
    assert _one(con, "print isnull(tostring(dynamic(null)))") is False


def test_the_unwrap_reaches_the_whole_string_family(con) -> None:
    """`tostring` is one caller of the conversion, not the only one.

    `strcat` and the hash functions render through the same helper, so fixing
    the quoting fixed the family — which is the reason it lives in one place.
    """
    assert _one(con, "print strcat('a', dynamic('x'), 'b')") == "axb"
    assert _one(con, "print strcat('a', dynamic(null), 'b')") == "ab"
    assert _one(con, "print strcat_delim('-', 'a', dynamic(null), 'b')") == "a--b"
    # A dynamic that is not a string keeps its JSON text, as Kusto does.
    assert _one(con, "print strcat('a', dynamic([1,2]))") == "a[1,2]"


def test_a_genuine_string_holding_a_quoted_word_is_left_alone(con) -> None:
    """The guard has to unwrap a `dynamic` without touching a `string`.

    A VARCHAR column whose value is literally `"q"` — quotes and all — must
    stay `"q"`. This is the case a blanket unwrap would corrupt, and it is why
    the discrimination is `typeof(...) = 'JSON'` at run time rather than a cast.
    """
    assert _one(con, "datatable(s:string)['\"q\"'] | project p = tostring(s)") == '"q"'
    assert _one(con, "datatable(s:string)['\"q\"'] | project p = strlen(s)") == 3


# ---------------------------------------------------------------------------
# D2 — a dynamic reaching a string operator
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("predicate", "expected"),
    [
        # Equality: crashed before, DuckDB casting `'x'` to JSON to match.
        ("s == 'x'", True),
        ("s != 'x'", False),
        ("s == 'y'", False),
        # These answered the *wrong* thing, which is worse than crashing.
        ("s startswith 'x'", True),
        ("s endswith 'x'", True),
        ("s matches regex '^x$'", True),
        ("s =~ 'X'", True),
        ("s !~ 'X'", False),
        ("s in ('x')", True),
        ("s !in ('x')", False),
        ("s in~ ('X')", True),
        # `contains` and `has` looked right only because `"x"` contains `x`.
        ("s contains 'x'", True),
        ("s has 'x'", True),
        ("s has_any ('x')", True),
        ("s has_all ('x')", True),
        # The quote itself is the case that exposed the accident.
        ("s contains '\"'", False),
    ],
)
def test_a_dynamic_in_a_string_operator(con, predicate: str, expected: bool) -> None:
    assert _one(con, EXPANDED + predicate) is expected


def test_the_dynamic_may_be_on_the_right(con) -> None:
    """`k contains s` is the same coercion, and answered false before.

    Not every operator allows it — measured, `k has s` is refused there
    (SEM0001, "'has' operator requires string arguments") while `contains`,
    `startswith` and `==` all coerce. Telling those apart needs the column's
    type, so `has` is accepted here; that is the mild direction.
    """
    q = "datatable(s:dynamic, k:string)[dynamic(['x']), 'x'] | mv-expand s | project p = "
    assert _one(con, q + "k contains s") is True
    assert _one(con, q + "k startswith s") is True


def test_equality_between_two_columns_is_not_coerced(con) -> None:
    """`k == s` — one string column, one dynamic — crashes rather than answers.

    Equality is polymorphic, so the coercion is applied only when the *other*
    side is visibly a string; a column is not. Guarding both operands instead
    would nest four branches around every `col == col` in every query, to fix a
    case that already fails loudly. Kusto answers **true** here.
    """
    q = "datatable(s:dynamic, k:string)[dynamic(['x']), 'x'] | mv-expand s | project p = "
    with pytest.raises(Exception):  # noqa: B017 - DuckDB's own conversion error
        duckdb_kql.kql(con, q + "k == s").fetchall()


@pytest.mark.parametrize(
    ("predicate", "expected"),
    [
        ("s == 'x'", False),
        ("s != 'x'", True),
        # A JSON null's text form is `''`, so this is an equality and not a
        # null-propagation: it answers true rather than null.
        ("s == ''", True),
        ("s != ''", False),
        ("s contains 'x'", False),
        ("s !contains 'x'", True),
        ("isnull(s)", True),
        ("isempty(s)", True),
    ],
)
def test_a_dynamic_null_in_a_string_operator(con, predicate, expected) -> None:
    """R4's totality still holds, and the empty-string rule rides on top of it."""
    q = "datatable(s:dynamic)[dynamic([null])] | mv-expand s | project p = "
    assert _one(con, q + predicate) is expected


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("strlen(s)", 1),
        ("toupper(s)", "X"),
        ("tolower(s)", "x"),
        ("substring(s,0,1)", "x"),
        ("indexof(s,'x')", 0),
        ("isempty(s)", False),
        ("isnotempty(s)", True),
    ],
)
def test_a_dynamic_in_a_string_function(con, expr: str, expected) -> None:
    """The functions the emulator *accepts* over a dynamic, and coerces.

    An allow-list rather than a rule: `countof`, `extract`, `replace_string`,
    `trim` and `url_encode` all refuse a dynamic there (SEM02xx), so treating
    "looks stringy" as the test would have invented coercions Kusto does not do.
    """
    assert _one(con, EXPANDED + expr) == expected


def test_split_of_a_dynamic_splits_the_text_not_the_json(con) -> None:
    import json

    v = _one(con, EXPANDED + "split(s,'x')")
    assert (json.loads(v) if isinstance(v, str) else v) == ["", ""]


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("isempty(dynamic(''))", True),
        ("isnotempty(dynamic(''))", False),
        # `[]` is not empty — its *text* is two characters, not zero. The
        # difference only appears once the quoting is gone.
        ("isempty(dynamic([]))", False),
        ("isempty(dynamic(null))", True),
        ("isempty(dynamic(0))", False),
    ],
)
def test_isempty_asks_about_the_text_form(con, expr: str, expected: bool) -> None:
    assert _one(con, f"print p = {expr}") is expected


# ---------------------------------------------------------------------------
# The typed cases the guard exists to protect
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "predicate"),
    [
        ("datatable(t:datetime)[datetime(2020-01-01)]", "t == '2020-01-01'"),
        ("datatable(n:long)[1]", "n == '1'"),
        ("datatable(n:real)[1.5]", "n == '1.5'"),
        ("datatable(b:bool)[true]", "b == 'true'"),
        ("datatable(n:long)[1]", "n in ('1')"),
    ],
)
def test_a_typed_column_still_compares_as_its_type(con, table, predicate) -> None:
    """Measured: Kusto coerces the *literal* to the column's type here, and
    answers true. So the unwrap cannot simply be emitted for every column —
    stringifying a datetime would compare `2020-01-01 00:00:00` to
    `2020-01-01` and answer false. Hence one run-time branch per operand
    rather than a cast, with the whole comparison written on both sides of it.
    """
    assert _one(con, f"{table} | project p = {predicate}") is True


def test_the_null_guard_and_the_dynamic_guard_are_independent(con) -> None:
    """Two guards render as a `CASE`; only one of them is about nulls.

    Pinned because they read alike in the generated SQL and a change to one
    should not quietly disable the other.
    """
    sql = str(duckdb_kql.to_sql("T | where s != 'hello'"))
    assert "typeof" in sql          # the dynamic guard: `s` might be JSON
    assert "IS NULL AND" not in sql  # the null guard: a literal cannot be null


# ---------------------------------------------------------------------------
# Refusals, and the residue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr",
    [
        "print p = dynamic('a') == dynamic('a')",
        "print p = dynamic(1) != dynamic(2)",
        "datatable(d:dynamic)[dynamic({'a':1,'b':1})] | project p = d.a == d.b",
    ],
)
def test_comparing_two_dynamics_is_refused(expr: str) -> None:
    """Kusto refuses this outright — SEM0001, "Cannot compare dynamic values
    without explicit cast" — because it cannot know which comparison is meant.
    DuckDB compares the JSON text and answers, so without the refusal the query
    would run here and fail on a cluster.
    """
    with pytest.raises(KqlUnsupportedError) as exc:
        duckdb_kql.to_sql(expr)
    assert "dynamic" in str(exc.value)


@pytest.mark.parametrize(
    "predicate",
    [
        # Kusto answers **false** (a non-numeric dynamic is not 1); we raise.
        "s == 1",
        "s == true",
        # Kusto **refuses** (SEM0064, dynamic vs string); we raise.
        "s < 'y'",
    ],
)
def test_the_comparisons_still_out_of_reach_fail_loudly(con, predicate) -> None:
    """Recorded rather than fixed, and asserted so the direction cannot drift.

    Each of these needs the column's *type* at translation time, which the
    schema does not carry (names only). What matters is that they fail: a
    DuckDB error is a bad answer to a query Kusto accepts, but it is not a
    **wrong** answer, which is the failure this project exists to prevent.
    """
    with pytest.raises(Exception):  # noqa: B017 - DuckDB's own conversion error
        duckdb_kql.kql(con, EXPANDED + predicate).fetchall()


def test_membership_against_a_subquery_keeps_the_json_form(con) -> None:
    """The one string context left un-coerced, and deliberately.

    Guarding renders the test twice, and DuckDB flattens `IN (subquery)` into a
    semi-join whose key is computed for **every** row — so the unreached
    branch's JSON call ran anyway and a plain `State in~ (...)` died on
    `Malformed JSON ... "PENNSYLVANIA"`. Coercing only the left operand would
    keep one subquery but break `ts in (T)`, which must still compare
    datetimes. So the subquery form is left alone and the gap is recorded.
    """
    q = (
        "datatable(s:dynamic)[dynamic(['x'])] | mv-expand s "
        "| where s in ((datatable(k:string)['x'])) | count"
    )
    assert duckdb_kql.kql(con, q).fetchall() == [(0,)]
