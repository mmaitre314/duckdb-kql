"""`duckdb_kql.script` — running several statements in order, and `query`.

A script is the Azure Data Explorer shape (`docs/api.md`, and Microsoft's
"Configure a database using a Kusto Query Language script"): statements
separated by blank lines, run top to bottom, for getting a database into a
known state.

Most of what can go wrong is in the **splitting**, so most of what is tested
here is splitting. The execution half is deliberately thin — each statement
goes through the same path as `duckdb_kql.kql`, which has its own tests.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlScriptError, KqlSyntaxError

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def con():
    return duckdb_kql.connect()


# ---------------------------------------------------------------------------
# query() — the alias
# ---------------------------------------------------------------------------


def test_query_is_kql() -> None:
    """The same object, not a wrapper: the two cannot drift, and a caller who
    patches or stubs one has patched both."""
    assert duckdb_kql.query is duckdb_kql.kql


def test_query_runs(con) -> None:
    assert duckdb_kql.query(con, "print x = 1 + 1").fetchall() == [(2,)]


# ---------------------------------------------------------------------------
# Splitting
# ---------------------------------------------------------------------------


def test_a_blank_line_separates_statements() -> None:
    assert duckdb_kql.split_script("print x = 1\n\nprint y = 2") == [
        (1, "print x = 1"),
        (3, "print y = 2"),
    ]


def test_a_statement_may_span_lines() -> None:
    """The reason the rule is a *blank* line and not any line break: a
    `datatable(...)` literal, or the query on the right of a `<|`, routinely
    runs to several lines."""
    script = (
        ".set-or-replace T <|\n"
        "    datatable(a: long, b: string)\n"
        "    [\n"
        "        1, 'x',\n"
        "    ]\n"
    )
    (line, statement), = duckdb_kql.split_script(script)
    assert line == 1
    assert statement.startswith(".set-or-replace T <|")
    assert statement.endswith("]")


@pytest.mark.parametrize(
    "script",
    [
        "print x = 1\n\n\n\nprint y = 2",       # several blank lines
        "print x = 1\n   \nprint y = 2",        # a line of spaces reads as blank
        "print x = 1\n\t\nprint y = 2",
        "\n\nprint x = 1\n\nprint y = 2\n\n",   # leading and trailing blanks
    ],
)
def test_blank_is_blank_however_it_is_spelled(script: str) -> None:
    assert [s for _line, s in duckdb_kql.split_script(script)] == [
        "print x = 1",
        "print y = 2",
    ]


def test_line_numbers_point_at_the_statement() -> None:
    """What a caller needs when statement 7 of a file they wrote fails."""
    script = "// a header\n\nprint x = 1\n\n\nprint y = 2"
    assert duckdb_kql.split_script(script) == [(3, "print x = 1"), (6, "print y = 2")]


def test_a_comment_only_chunk_is_not_a_statement() -> None:
    """A comment between two commands is a comment on the script. Sending it
    anywhere produces a syntax error about `<EOF>`, which is nobody's idea of a
    useful message."""
    assert duckdb_kql.split_script("// just a note\n\nprint x = 1") == [
        (3, "print x = 1")
    ]
    assert duckdb_kql.split_script("// a\n// b\n\n// c") == []


def test_a_comment_attached_to_a_statement_stays_with_it() -> None:
    """No blank line between them, so it is one chunk — which is how a comment
    documenting a command is written."""
    (line, statement), = duckdb_kql.split_script("// load it\nprint x = 1")
    assert line == 1
    assert statement == "// load it\nprint x = 1"


def test_a_double_slash_inside_a_string_is_not_a_comment() -> None:
    """`_has_code` answers on the first non-comment character, so it never
    reaches a string literal — this is the case that would prove otherwise."""
    (_line, statement), = duckdb_kql.split_script("print x = 'http://example'")
    assert statement == "print x = 'http://example'"


def test_a_blank_line_cannot_hide_inside_a_string_literal() -> None:
    """Why splitting on blank lines needs no knowledge of KQL: a raw newline is
    a lexer error inside a literal, in all three spellings, so a blank line in
    the script text is always between statements."""
    for text in ["print x = 'a\n\nb'", 'print x = "a\n\nb"', "print x = @'a\n\nb'"]:
        with pytest.raises(KqlSyntaxError):
            duckdb_kql.to_sql(text)


def test_an_empty_script_is_no_statements() -> None:
    assert duckdb_kql.split_script("") == []
    assert duckdb_kql.split_script("\n  \n\t\n") == []


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def test_it_initializes_a_database(con) -> None:
    """The use the operator exists for, end to end."""
    results = duckdb_kql.script(
        con,
        """
        .set-or-replace Events <|
            datatable(level: string)["Error", "Warning", "Error"]

        .set Levels <| Events | summarize n = count() by level

        Levels | order by n desc
        """,
    )
    assert [r.index for r in results] == [1, 2, 3]
    assert all(r.ok for r in results)
    assert results[-1].columns == ["level", "n"]
    assert results[-1].rows == [("Error", 2), ("Warning", 1)]


def test_statements_see_what_earlier_ones_did(con) -> None:
    """Each runs to completion before the next starts, which is the whole point
    — and the reason `rows` is materialized rather than left as a relation."""
    results = duckdb_kql.script(
        con,
        ".set T <| datatable(a: long)[1]\n\n"
        ".set-or-replace T <| datatable(a: long)[1, 2]\n\n"
        "T | count",
    )
    assert results[-1].rows == [(2,)]


def test_a_comment_above_a_command_does_not_hide_it(con) -> None:
    """Command families are recognized by a text prefix, not by the parser, so
    without `strip_leading_comments` this reaches the query path and dies on the
    leading dot. It is how most of an init script is written."""
    results = duckdb_kql.script(
        con, "// seed it\n.set T <| datatable(a: long)[1]\n\n// check it\nT | count"
    )
    assert results[-1].rows == [(1,)]


def test_the_first_failure_stops_the_script(con) -> None:
    """ADX's default, and the safer one: a script that half-ran is worse than a
    script that stopped where it broke."""
    with pytest.raises(KqlScriptError) as exc:
        duckdb_kql.script(
            con,
            ".set T <| datatable(a: long)[1]\n\nNoSuchTable | count\n\n.set U <| T",
        )
    assert exc.value.index == 2
    assert exc.value.line == 3
    assert exc.value.statement == "NoSuchTable | count"
    # The original is not swallowed; a caller can still discriminate on it.
    assert exc.value.__cause__ is not None
    # ...and the third statement never ran.
    assert duckdb_kql.kql(con, ".show tables | project TableName").fetchall() == [("T",)]


def test_continue_on_errors_reports_instead_of_raising(con) -> None:
    results = duckdb_kql.script(
        con,
        ".set T <| datatable(a: long)[1]\n\nNoSuchTable | count\n\n.set U <| T",
        continue_on_errors=True,
    )
    assert [r.ok for r in results] == [True, False, True]
    assert results[1].error is not None
    assert results[1].rows == []
    assert duckdb_kql.kql(con, "U | count").fetchall() == [(1,)]


def test_nothing_limits_which_commands_a_script_may_run(con) -> None:
    """ADX restricts a script to `.create`/`.alter`/`.add` verbs at database
    level. Here `.set-or-replace` is the point — seeding a database *is*
    ingestion — and a plain query is allowed too, so a script can check itself."""
    results = duckdb_kql.script(
        con,
        ".set-or-replace T <| datatable(a: long)[1, 2, 3]\n\n"
        "T | summarize n = count()",
    )
    assert results[0].text.startswith(".set-or-replace")
    assert results[1].rows == [(3,)]


def test_allow_write_false_refuses_the_writes(con) -> None:
    """A way to check a script without running it: everything that would write
    refuses, and the refusal names the statement."""
    with pytest.raises(KqlScriptError) as exc:
        duckdb_kql.script(
            con, ".set T <| datatable(a: long)[1]", allow_write=False
        )
    assert exc.value.index == 1
    assert "writes are disabled" in str(exc.value)


def test_an_empty_script_runs_nothing(con) -> None:
    assert duckdb_kql.script(con, "\n// nothing here\n\n") == []


def test_the_result_carries_the_statement_as_written(con) -> None:
    """So a caller can log or report it without re-splitting the file."""
    (result,) = duckdb_kql.script(con, "  print x = 1  ")
    assert result.text == "print x = 1"
    assert result.index == 1
    assert result.line == 1
    assert result.error is None
