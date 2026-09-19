"""L5 trap tests — scalar functions declared with `let`.

`let Normalize = (value:string) { tolower(value) };` was refused outright.

Inlined at the call site rather than registered as a DuckDB UDF. A `let` is a
**query-scope** binding — it disappears with the query — and §7's UDF policy is
for mappings with no SQL form, which is not this: the body already lowers to an
expression, so substituting it is both simpler and correctly scoped. The
project registers no Python UDF anywhere, and this is not the feature to start
with one.

Measured on the emulator, and three of the rules shape the implementation:

* a **parameter shadows a column** of the same name, which substituting over
  the body's `ColumnRef`s gives for free;
* a body **closes over the scope it was written in** — `let K = 7; let F =
  (x:long) { x + K }` answers 8 for `F(1)` — so the body is lowered where it is
  declared, with the scalars already in scope substituted;
* **recursion is refused** (SEM0260, "Unknown function"), which registering the
  name only after lowering its own body reproduces exactly.

**The leak this nearly shipped with.** The registry is a module global, and the
first version restored it rather than clearing it, so a function declared by one
query stayed callable from the *next* one — a second `print r = F('AB')`, with
no `let` in it at all, answered `'ab'`. A binding that outlives its query is a
wrong answer to a query that never declared it.
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


def _one(con, kql):
    return duckdb_kql.kql(con, kql).fetchall()[0][0]


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def test_the_reported_declaration(con) -> None:
    assert _one(
        con, "let Normalize = (value:string) { tolower(value) }; print Result = Normalize('ABC')"
    ) == "abc"


def test_it_applies_to_a_column(con) -> None:
    assert _one(
        con, "let F = (v:string) { tolower(v) }; datatable(s:string)['AB'] | project r = F(s)"
    ) == "ab"


def test_several_parameters(con) -> None:
    assert _one(con, "let Add = (a:long, b:long) { a + b }; print r = Add(2,3)") == 5


def test_no_parameters(con) -> None:
    assert _one(con, "let F = () { 42 }; print r = F()") == 42


# ---------------------------------------------------------------------------
# Scoping — the part substitution has to get right
# ---------------------------------------------------------------------------


def test_a_parameter_shadows_a_column_of_the_same_name(con) -> None:
    """The trap. A column `s` is in scope and the parameter is also `s`; the
    body must read the *argument*. Measured: 2, the length of `'xy'`, not 4."""
    assert _one(
        con,
        "let F = (s:string) { strlen(s) }; datatable(s:string)['abcd'] | project r = F('xy')",
    ) == 2


def test_a_body_closes_over_earlier_lets(con) -> None:
    assert _one(con, "let K = 7; let F = (x:long) { x + K }; print r = F(1)") == 8


def test_a_function_may_call_an_earlier_one(con) -> None:
    assert _one(
        con, "let G = (x:long) { x * 2 }; let F = (x:long) { G(x) + 1 }; print r = F(5)"
    ) == 11


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_a_default_is_used_when_the_argument_is_absent(con) -> None:
    assert _one(con, "let F = (a:long, b:long = 10) { a + b }; print r = F(2)") == 12


def test_a_default_is_overridden_when_it_is_given(con) -> None:
    assert _one(con, "let F = (a:long, b:long = 10) { a + b }; print r = F(2, 5)") == 7


def test_too_few_arguments_is_refused(con) -> None:
    with pytest.raises(KqlUnsupportedError, match="arguments"):
        duckdb_kql.kql(con, "let F = (a:long, b:long) { a + b }; print r = F(1)")


def test_too_many_arguments_is_refused(con) -> None:
    with pytest.raises(KqlUnsupportedError, match="arguments"):
        duckdb_kql.kql(con, "let F = (a:long) { a }; print r = F(1, 2)")


# ---------------------------------------------------------------------------
# It is query-scoped, and that is load-bearing
# ---------------------------------------------------------------------------


def test_a_declaration_does_not_leak_into_the_next_query(con) -> None:
    """The bug this nearly shipped with. The registry is a module global, so
    the second query below resolved `F` and answered `'ab'` — for a query that
    declares nothing."""
    assert _one(con, "let F = (v:string) { tolower(v) }; print r = F('AB')") == "ab"
    with pytest.raises(KqlUnsupportedError, match="function:F"):
        duckdb_kql.kql(con, "print r = F('AB')")


def test_recursion_is_refused(con) -> None:
    """Not a limitation to apologise for: Kusto refuses it too, SEM0260. The
    name is registered only after its own body is lowered, so the rule and the
    implementation are the same thing."""
    with pytest.raises(KqlUnsupportedError, match="function:F"):
        duckdb_kql.kql(con, "let F = (x:long) { F(x) }; print r = F(1)")


def test_a_tabular_body_is_refused(con) -> None:
    """Kusto supports these; they are a *source*, which is a different feature
    from substituting an expression into a scalar position."""
    with pytest.raises(KqlUnsupportedError, match="tabular body"):
        duckdb_kql.kql(
            con, "let F = (n:long) { range x from 1 to n step 1 }; F(3) | count"
        )


# ---------------------------------------------------------------------------
# It composes with the rest of the pipeline
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,expected",
    [
        ("let F = (x:long) { x * x }; datatable(n:long)[3] | project r = F(n)", 9),
        ("let F = (x:long) { x * 2 }; datatable(n:long)[1,2,3] | summarize r = sum(F(n))", 12),
        ("let F = (x:long) { x + 1 }; datatable(n:long)[1,2,3] | where F(n) > 3 | count", 1),
        ("let F = (x:long) { iff(x > 0, 'pos', 'neg') }; print r = F(-1)", "neg"),
    ],
)
def test_it_works_wherever_an_expression_does(con, query: str, expected) -> None:
    assert _one(con, query) == expected


# ---------------------------------------------------------------------------
# The bug this uncovered
# ---------------------------------------------------------------------------


def test_a_comma_form_datetime_literal(con) -> None:
    """Not a `let` bug at all — found *by* fixing one.

    Two corpus cases (`startofweek-function-01`, `endofweek-function-01`)
    declare a `let` function over `datetime(2025, 6, 14)`. Both were refused for
    the function, so neither ever ran, and neither noticed that the comma form
    of the literal reached the emitter as the text `'2025, 6, 14'` — which no
    date parser accepts, making it **null**. Implementing the function turned
    two refusals into two silently wrong answers until this was fixed too.

    A fourth part is an error in Kusto, so this is not `make_datetime` spelled
    differently.
    """
    import datetime as dt

    assert _one(con, "print d = datetime(2025, 6, 14)") == dt.datetime(2025, 6, 14)
    assert _one(con, "print d = datetime(2025, 6)") == dt.datetime(2025, 6, 1)


def test_the_single_number_form_is_left_alone(con) -> None:
    """Deliberately not guessed at. Measured, `datetime(2025)` is 2025-01-01,
    `datetime(20250614)` is 2025-06-14 and `datetime(1)` is one second past the
    epoch — three rules selected by magnitude and digit count, which the six
    samples taken do not establish. It stays null rather than being invented."""
    assert _one(con, "print d = datetime(2025)") is None


# ---------------------------------------------------------------------------
# A `let` inside the body
# ---------------------------------------------------------------------------


def test_the_reported_local_let(con) -> None:
    assert _one(
        con,
        "let normalize=(value:string) {\n"
        "    let lowered=tolower(value);\n"
        "    lowered\n"
        "};\n"
        "print result=normalize('TEST')",
    ) == "test"


@pytest.mark.parametrize(
    "query,expected",
    [
        # each local sees the ones before it
        ("let f=(x:long) {\n let a=x+1;\n let b=a*2;\n b\n};\nprint r = f(3)", 8),
        # and an outer `let`
        ("let K=5;\nlet f=(x:long) {\n let a=x+K;\n a\n};\nprint r = f(1)", 6),
        # the final expression may use a local in a larger expression
        ("let f=(x:long) {\n let a=x*2;\n a+1\n};\nprint r = f(5)", 11),
    ],
)
def test_locals_chain(con, query: str, expected) -> None:
    assert _one(con, query) == expected


def test_a_local_shadows_a_column(con) -> None:
    """Same rule as a parameter: the binding wins over a column of that name."""
    assert _one(
        con,
        "let f=(v:string) {\n let s=toupper(v);\n s\n};\n"
        "datatable(s:string)['q'] | project r = f(s)",
    ) == "Q"


def test_a_local_may_not_take_a_parameter_s_name(con) -> None:
    """Measured SEM0079, "Let with the same name was already used in current
    context". Allowing it would silently shadow the argument the call site is
    about to bind."""
    with pytest.raises(KqlUnsupportedError, match="already bound"):
        duckdb_kql.kql(con, "let f=(x:long) {\n let x=x+1;\n x\n};\nprint r = f(3)")


def test_a_body_with_no_locals_is_unchanged(con) -> None:
    assert _one(con, "let f=(v:string) { tolower(v) };\nprint r = f('AB')") == "ab"
