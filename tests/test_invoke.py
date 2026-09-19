"""L5 trap tests — tabular `let` functions and `invoke`.

`let normalize = (rows:(value:string)) { rows | extend value = tolower(value) }`
was refused at the parameter. A tabular function is a **pipeline**, not an
expression: measured, `rows` stands for whatever is piped into `invoke`, and the
body replaces the input rather than extending it — `rows | summarize s=sum(v)`
over three rows returns one.

So it is stored as an **operator list** with the source stripped, and `invoke`
splices that list onto the pipeline already in hand. That is only sound while
the body starts *at* the tabular parameter, which the declaration checks; a body
sourced anywhere else is refused rather than spliced into the wrong table.

**A `let` inside a tabular body is refused, and the first version dropped it.**
The corpus's `clipped_average` binds `low` and `high` with `toscalar(T |
summarize percentiles(...))` and filters on them; ignoring the bindings emitted
SQL that referenced a column which does not exist — invalid SQL, not a wrong
answer, but exactly the silent-drop shape the charter is about. Those locals are
scalar subqueries over the tabular parameter, which is a different feature from
a scalar function's locals: those close over values, not over the pipeline being
spliced.
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


def _shape(con, kql):
    cursor = duckdb_kql.kql(con, kql)
    return [d[0] for d in cursor.description], cursor.fetchall()


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def test_the_reported_query(con) -> None:
    assert _shape(
        con,
        "let normalize=(rows:(value:string)) {\n"
        "    rows | extend value=tolower(value)\n"
        "};\n"
        "datatable(value:string)['TEST'] | invoke normalize()",
    ) == (["value"], [("test",)])


def test_a_scalar_argument_binds_alongside_the_table(con) -> None:
    assert _shape(
        con,
        "let f=(rows:(v:string), n:long) {\n"
        "    rows | extend v=strcat(v, tostring(n))\n"
        "};\n"
        "datatable(v:string)['a'] | invoke f(7)",
    ) == (["v"], [("a7",)])


def test_the_body_replaces_the_input_rather_than_extending_it(con) -> None:
    """The reason this is an operator list and not an expression: three rows in,
    one row out, with a column the input never had."""
    assert _shape(
        con,
        "let f=(rows:(v:long)) {\n    rows | summarize s=sum(v)\n};\n"
        "datatable(v:long)[1,2,3] | invoke f()",
    ) == (["s"], [(6,)])


@pytest.mark.parametrize(
    "body,expected",
    [
        ("rows | where v > 1", [(2,), (3,)]),
        ("rows", [(1,), (2,), (3,)]),
        ("rows | take 3 | where v > 2", [(3,)]),
    ],
)
def test_body_shapes(con, body: str, expected) -> None:
    _columns, rows = _shape(
        con,
        f"let f=(rows:(v:long)) {{\n    {body}\n}};\n"
        "datatable(v:long)[1,2,3] | invoke f() | sort by v asc",
    )
    assert sorted(rows) == sorted(expected)


def test_invoke_in_the_middle_of_a_pipeline(con) -> None:
    """The spliced operators are ordinary ones, so what follows sees them."""
    assert _shape(
        con,
        "let f=(rows:(v:long)) {\n    rows | extend d = v * 10\n};\n"
        "datatable(v:long)[1,2] | where v > 1 | invoke f() | project d",
    ) == (["d"], [(20,)])


# ---------------------------------------------------------------------------
# Refusals — each one a shape the splice cannot represent
# ---------------------------------------------------------------------------


def test_a_let_inside_a_tabular_body_is_refused(con) -> None:
    """Refused, not dropped. Dropping it is what produced invalid SQL for the
    corpus's `clipped_average`, and a dropped binding is invisible."""
    with pytest.raises(KqlUnsupportedError, match="let` inside a tabular"):
        duckdb_kql.kql(
            con,
            "let f=(rows:(v:long)) {\n"
            "    let lo = 1;\n"
            "    rows | where v > lo\n"
            "};\n"
            "datatable(v:long)[1,2] | invoke f()",
        )


def test_a_body_not_starting_at_the_parameter_is_refused(con) -> None:
    """`invoke` continues the caller's pipeline, so a body that sources its own
    table has nowhere to put it."""
    with pytest.raises(KqlUnsupportedError, match="must start at"):
        duckdb_kql.kql(
            con,
            "let f=(rows:(v:long)) {\n    datatable(w:long)[9]\n};\n"
            "datatable(v:long)[1] | invoke f()",
        )


def test_invoking_a_scalar_function_is_refused(con) -> None:
    """A scalar function has no tabular parameter to be the pipeline."""
    with pytest.raises(KqlUnsupportedError, match="invoke:f"):
        duckdb_kql.kql(
            con, "let f=(x:long) { x + 1 };\ndatatable(v:long)[1] | invoke f()"
        )


def test_invoking_an_unknown_name_is_refused(con) -> None:
    with pytest.raises(KqlUnsupportedError, match="invoke:nope"):
        duckdb_kql.kql(con, "datatable(v:long)[1] | invoke nope()")


def test_two_tabular_parameters_are_refused(con) -> None:
    with pytest.raises(KqlUnsupportedError, match="one tabular parameter"):
        duckdb_kql.kql(
            con,
            "let f=(a:(v:long), b:(w:long)) {\n    a\n};\n"
            "datatable(v:long)[1] | invoke f()",
        )


def test_the_wrong_argument_count_is_refused(con) -> None:
    with pytest.raises(KqlUnsupportedError, match="arguments"):
        duckdb_kql.kql(
            con,
            "let f=(rows:(v:long), n:long) {\n    rows | extend d = v + n\n};\n"
            "datatable(v:long)[1] | invoke f()",
        )


# ---------------------------------------------------------------------------
# Scalar functions are unaffected
# ---------------------------------------------------------------------------


def test_a_scalar_function_still_works(con) -> None:
    """The two share a registry and an argument binder, so this is the guard
    that adding the tabular kind did not disturb the scalar one."""
    assert duckdb_kql.kql(
        con, "let f=(v:string) { tolower(v) };\nprint r = f('AB')"
    ).fetchall() == [("ab",)]
