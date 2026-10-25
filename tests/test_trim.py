"""Trap tests — `trim`, `trim_start` and `trim_end` take Kusto's own pattern, verbatim.

`trim` and `trim_end` were missing, and `trim_start` was wrong. It rendered
`regexp_replace(s, '^' || regex, '')`, which anchors only the *first
alternative* of an alternation: `^a|x` strips an `x` anywhere in the string, so
`trim_start('a|x', 'bxa')` answered `ba` where Kusto leaves `bxa` alone. Silent,
and the obvious rendering — which is why it was there.

Kusto prints the pattern it builds when the user's regex is ill-formed:
`trim_start('[', s)` is refused with "Regex pattern is ill-formed: ^([)", and
`trim_end` and `trim` print `([)$` and `^([)|([)$`. It then removes **every**
match of that pattern. DuckDB's RE2, handed the same pattern with `'g'`,
answers the same, quirks included, on all 295 probes Kusto answered of 297
measured — the other two it refuses, and DuckDB raises:

* one match per end, not a loop — `trim('a', 'aaaa')` is `aa`;
* `trim` is one pattern, not `trim_end` after `trim_start` — `trim('a$', 'aa')`
  is `a`, where the two applied in turn would leave nothing;
* the user's regex can close Kusto's group — `trim_end('a)|(b', 'xaxbx')` is
  `xxbx`, every `a` removed and the final `b` kept;
* `$` is the end of the text, not before a final newline as in .NET —
  `trim_end('a', "xa\\n")` removes nothing.

The regex must be a string literal (SEM0207) and a non-empty one (SEM0217); a
`let` or a query parameter holding one is accepted, as Kusto accepts it.

**Residue**, shared with every regex function: Kusto's `\\d`, `\\w` and `\\s` are
Unicode classes (`trim_start(@'\\d', '٣x')` is `x`) and RE2's are ASCII.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlError

duckdb = pytest.importorskip("duckdb")


def _rows(query: str, **kwargs: object) -> list[tuple]:
    return duckdb_kql.kql(duckdb_kql.connect(), query, **kwargs).fetchall()


#: `(regex, text, trim, trim_start, trim_end)` — every value measured.
MEASURED = [
    ("a", "aaab", "aab", "aab", "aaab"),
    ("a", "aaaa", "aa", "aaa", "aaa"),
    ("a|x", "bxa", "bx", "bxa", "bx"),
    ("a|x", "axbxa", "xbx", "xbxa", "axbx"),
    ("a$", "aa", "a", "aa", "a"),
    ("^b|a", "ba", "", "a", "b"),
    ("a|^b", "ab", "b", "b", "ab"),
    ("a)|(b", "xaxbx", "xxx", "xaxx", "xxbx"),
    ("a+?", "xaaa", "x", "xaaa", "x"),
    ("a|aa", "xaaa", "xa", "xaaa", "xa"),
    ("|a", "xa", "x", "xa", "x"),
    ("b*", "abc", "abc", "abc", "abc"),
    (".*", "abc", "", "", ""),
    (" ", "   x   ", "  x  ", "  x   ", "   x  "),
    ("(?i)a", "AaxA", "ax", "axA", "Aax"),
    ("(?<n>a)", "aba", "b", "ba", "ab"),
    ("é", "xéé", "xé", "xéé", "xé"),
]


@pytest.mark.parametrize(("regex", "text", "both", "start", "end"), MEASURED)
def test_matches_kusto_over_rows(regex: str, text: str, both: str, start: str, end: str) -> None:
    query = (
        f"datatable(s:string)['{text}'] | project a = trim(@'{regex}', s), "
        f"b = trim_start(@'{regex}', s), c = trim_end(@'{regex}', s)"
    )
    assert _rows(query) == [(both, start, end)]


def test_the_old_rendering_stripped_an_alternative_anywhere() -> None:
    """What `'^' || regex` does, so the regression has a witness."""
    old = duckdb.connect().sql("SELECT regexp_replace('bxa', '^' || 'a|x', '')").fetchall()
    assert old == [("ba",)]
    assert _rows("print x = trim_start('a|x', 'bxa')") == [("bxa",)]


def test_end_means_end_of_text_not_before_a_final_newline() -> None:
    assert _rows('print a = strlen(trim_end("a", "xa\\n")), b = strlen(trim_end(".", "a\\n"))') == [
        (3, 2)
    ]


def test_a_null_string_stays_null() -> None:
    assert _rows("datatable(s:string)[''] | extend n = trim('a', s) | project isempty(n)") == [
        (True,)
    ]
    con = duckdb_kql.connect()
    con.execute("CREATE TABLE T AS SELECT NULL::VARCHAR AS s")
    assert duckdb_kql.kql(con, "T | project x = isnull(trim('a', s))").fetchall() == [(True,)]


@pytest.mark.parametrize(
    "query",
    [
        "print x = trim('', 'abc')",
        "print x = trim_end('', 'abc')",
        "datatable(r:string, s:string)['a', 'aab'] | project x = trim(r, s)",
        "datatable(r:string, s:string)['a', 'aab'] | project x = trim_start(r, s)",
        "print x = trim_start('a', 'aab', 'x')",
        "print x = trim_end('a')",
        "print x = trim_start(@'a(?=b)', 'ab')",
    ],
)
def test_what_kusto_refuses_is_refused(query: str) -> None:
    """SEM0217, SEM0207, SEM0219 and SEM0420 on the emulator, in that order."""
    with pytest.raises(KqlError):
        duckdb_kql.to_sql(query)


def test_a_let_holding_the_regex_is_a_literal() -> None:
    assert _rows("let r = 'a'; print x = trim(r, 'aab')") == [("ab",)]


def test_a_query_parameter_is_spliced_the_way_kusto_splices_it() -> None:
    query = (
        "declare query_parameters(r:string); "
        "print a = trim_start(r, 'bxa'), b = trim(r, 'axbxa')"
    )
    assert _rows(query, parameters={"r": "a|x"}) == [("bxa", "xbx")]


def test_summarize_by_does_not_pass_the_name_through() -> None:
    """Measured: `by trim(' ', s)` is `Column1`, unlike `by tostring(s)`."""
    cursor = duckdb_kql.kql(
        duckdb_kql.connect(), "datatable(s:string)[' a '] | summarize count() by trim(' ', s)"
    )
    assert [d[0] for d in cursor.description] == ["Column1", "count_"]
