"""L5 trap tests — `.NET` named capturing groups, and lookaround.

`extract_all(@'(?: )(?<w>[a-z]+)', s)` reached the caller as a **raw DuckDB
exception** — *"Invalid Input Error: invalid perl operator: (?<"* — not a KQL
error and not an answer. Kusto runs it happily: measured, `(?<name>…)` and
`(?P<name>…)` are the same pattern there for `extract`, `extract_all`,
`matches regex` and `replace_regex` alike. DuckDB's RE2 accepts only the second.

So a literal pattern's `(?<name>` is rewritten to `(?P<name>` on the way out.
Only the spelling changes: the group still captures, in the same position.

**Lookbehind starts with the same three characters and must not be rewritten.**
`(?<=` and `(?<!` are a different construct, RE2 cannot execute either, and
Kusto refuses all four lookaround forms with SEM0420 — so they are refused here
too, which turns the same leak into a KQL error rather than a DuckDB one.

The rewrite is a scan, not a `str.replace`, for the reason `neutralise_groups`
is: an escaped `\\(?<` and a `(` inside a character class open nothing.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlUnsupportedError
from duckdb_kql.translate.regexfrag import find_lookaround, to_re2_named_groups

duckdb = pytest.importorskip("duckdb")

T = "datatable(s:string)[' test word']"


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    return c


def _one(con, kql):
    return duckdb_kql.kql(con, kql).fetchall()[0][0]


# ---------------------------------------------------------------------------
# Every function that hands a pattern to RE2
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,expected",
    [
        (T + r"| project r = extract_all(@'(?: )(?<w>[a-z]+)', s)", '["test","word"]'),
        (T + r"| project r = extract(@'(?: )(?<w>[a-z]+)', 1, s)", "test"),
        (T + r"| project r = replace_regex(s, @'(?<w>[a-z]+)', 'X')", " X X"),
    ],
)
def test_a_named_group_works_wherever_a_pattern_does(con, query: str, expected) -> None:
    assert _one(con, query) == expected


def test_matches_regex_takes_one_too(con) -> None:
    """The one that is an *operator*, not a function, so it needed its own hook."""
    assert _one(con, T + r"| where s matches regex @'(?<w>[a-z]+)' | count") == 1


def test_the_re2_spelling_still_works(con) -> None:
    """Kusto accepts both; rewriting must not break the one that already ran."""
    assert _one(con, T + r"| project r = extract_all(@'(?: )(?P<w>[a-z]+)', s)") == (
        '["test","word"]'
    )


def test_a_named_group_still_counts_for_the_result_shape(con) -> None:
    """`extract_all`'s shape comes from the group *count*, so the rewrite must
    not change what is counted — two named groups still nest."""
    assert _one(con, T + r"| project r = extract_all(@'(?<a>[a-z])(?<b>[a-z]+)', s)") == (
        '[["t","est"],["w","ord"]]'
    )


# ---------------------------------------------------------------------------
# Lookaround — the three characters that must not be rewritten
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        T + r"| project r = extract_all(@'(?<= )([a-z]+)', s)",
        T + r"| project r = extract(@'(?<= )([a-z]+)', 1, s)",
        T + r"| where s matches regex @'te(?=st)' | count",
        T + r"| where s matches regex @'te(?!xx)' | count",
        T + r"| project r = replace_regex(s, @'te(?=st)', 'X')",
    ],
)
def test_lookaround_is_refused_rather_than_leaked(con, query: str) -> None:
    """Measured SEM0420 on the emulator for every one of these, so refusing
    costs nothing — and a `KqlUnsupportedError` is what this package owes a
    caller, where a DuckDB `InvalidInputException` is a leak."""
    with pytest.raises(KqlUnsupportedError, match="lookaround"):
        duckdb_kql.kql(con, query)


# ---------------------------------------------------------------------------
# The rewrite itself, where the scanning matters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern,expected",
    [
        (r"(?<w>[a-z]+)", r"(?P<w>[a-z]+)"),
        (r"(?P<w>x)", r"(?P<w>x)"),
        (r"(?<a>x)(?<b>y)", r"(?P<a>x)(?P<b>y)"),
        (r"(?:a)(b)", r"(?:a)(b)"),
        # lookaround is left exactly as it was, for `find_lookaround` to refuse
        (r"(?<= )([a-z]+)", r"(?<= )([a-z]+)"),
        (r"(?<! )x", r"(?<! )x"),
        # a `(` that opens nothing
        (r"[(?<x]", r"[(?<x]"),
        (r"\(?<w>", r"\(?<w>"),
        ("", ""),
    ],
)
def test_the_rewrite(pattern: str, expected: str) -> None:
    assert to_re2_named_groups(pattern) == expected


@pytest.mark.parametrize(
    "pattern,found",
    [
        (r"(?=a)", "(?="),
        (r"x(?!a)", "(?!"),
        (r"(?<=a)", "(?<="),
        (r"(?<!a)", "(?<!"),
        (r"(?<name>a)", None),
        (r"(?:a)", None),
        # escaped, and inside a class — neither opens anything
        (r"\(?=a", None),
        (r"[(?=]", None),
    ],
)
def test_lookaround_detection_skips_what_opens_nothing(pattern: str, found) -> None:
    assert find_lookaround(pattern) == found


def test_a_non_literal_pattern_is_left_alone(con) -> None:
    """A regex arriving in a column cannot be rewritten at translation time.
    `extract_all` requires a constant anyway (SEM0040); `replace_regex` does
    not, so it must still translate rather than refuse."""
    assert _one(
        con,
        "datatable(s:string, p:string)[' test word', '[a-z]+'] "
        "| project r = replace_regex(s, p, 'X')",
    ) == " X X"
