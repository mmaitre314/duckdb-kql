"""Unit tests for the regex-fragment rewrite used by `parse kind=regex`.

Separate from `test_parse.py` on purpose. This is a string-to-string function
with a lot of edge cases and no database in sight, and every one of those edges
is a place where a user's regex silently binds to the wrong column: DuckDB maps
the group-name list **by position**, so one stray capturing group shifts
everything after it.

The property being tested is therefore mechanical and checkable: after the
rewrite, the fragment must contain **no capturing groups at all**, and must
still match exactly what it matched before. Both halves are asserted, the
second against DuckDB itself.
"""

from __future__ import annotations

import pytest

from duckdb_kql.errors import KqlUnsupportedError
from duckdb_kql.translate.regexfrag import neutralise_groups

duckdb = pytest.importorskip("duckdb")


@pytest.fixture(scope="module")
def con():
    return duckdb.connect()


def _group_count(con, pattern: str) -> int:
    """How many capturing groups DuckDB sees — the thing that must reach zero."""
    for n in range(0, 12):
        names = [f"g{i}" for i in range(n)]
        if not names:
            continue
        try:
            con.execute("SELECT regexp_extract('', ?, ?)", [pattern, names])
        except duckdb.Error:
            return n - 1
    return 11


@pytest.mark.parametrize(
    ("fragment", "expected"),
    [
        # the plain case
        ("a(b)c", "a(?:b)c"),
        ("(a)(b)", "(?:a)(?:b)"),
        ("((a))", "(?:(?:a))"),
        # already non-capturing, or not a group at all
        ("a(?:b)c", "a(?:b)c"),
        ("(?i)abc", "(?i)abc"),
        ("(?is:abc)", "(?is:abc)"),
        ("a\\(b\\)c", "a\\(b\\)c"),
        # named groups capture too, in all three spellings
        ("(?P<n>a)", "(?:a)"),
        ("(?<n>a)", "(?:a)"),
        ("(?'n'a)", "(?:a)"),
        # a parenthesis inside a character class is a literal
        ("[(]", "[(]"),
        ("[()]x(y)", "[()]x(?:y)"),
        ("[^()]", "[^()]"),
        # ...including the awkward class forms
        ("[]]", "[]]"),
        ("[]()]", "[]()]"),
        ("[^]()]", "[^]()]"),
        ("[a\\]b(]", "[a\\]b(]"),
        ("[[:alpha:]](x)", "[[:alpha:]](?:x)"),
        # quantifiers and alternation are left alone
        ("(a|b)+", "(?:a|b)+"),
        ("a{2,3}(b)", "a{2,3}(?:b)"),
        # nothing to do
        ("", ""),
        ("abc", "abc"),
        (".*?", ".*?"),
    ],
)
def test_rewrites(fragment: str, expected: str) -> None:
    assert neutralise_groups(fragment) == expected


@pytest.mark.parametrize(
    "fragment",
    [
        "a(b)c", "(a)(b)", "((a))", "(?P<n>a)", "(?<n>a)", "(?'n'a)",
        "[()]x(y)", "(a|b)+", "a\\(b\\)(c)", "[[:alpha:]](x)", "(?i)(a)",
    ],
)
def test_no_capturing_group_survives(con, fragment: str) -> None:
    """The mechanical property, checked against DuckDB rather than by eye.

    A fragment with any capturing group left in it would shift the column
    mapping, which is silent — so this is asserted for every rewrite above,
    not only the ones that look risky.
    """
    assert _group_count(con, neutralise_groups(fragment)) == 0


@pytest.mark.parametrize(
    ("fragment", "subject"),
    [
        ("a(b)c", "abc"),
        ("(a|b)+", "abab"),
        ("[()]x(y)", "(xy"),
        ("(?P<n>a)b", "ab"),
        ("[[:alpha:]]+(1)", "abc1"),
        ("a{2,3}(b)", "aab"),
        ("[]()]+(z)", "])(z"),
    ],
)
def test_the_rewrite_matches_the_same_text(con, fragment: str, subject: str) -> None:
    """Defusing the groups must not change *what* the fragment matches."""
    before = con.execute("SELECT regexp_matches(?, ?)", [subject, fragment]).fetchone()
    after = con.execute(
        "SELECT regexp_matches(?, ?)", [subject, neutralise_groups(fragment)]
    ).fetchone()
    assert before == after == (True,)


@pytest.mark.parametrize(
    "fragment", ["(?=a)", "(?!a)", "(?<=a)", "(?<!a)", "x(?=a)y"]
)
def test_lookaround_is_refused(fragment: str) -> None:
    """RE2 cannot run it, and Kusto's own analysis refuses it (SEM0476), so a
    refusal here is not a limitation of this translator."""
    with pytest.raises(KqlUnsupportedError) as exc:
        neutralise_groups(fragment)
    assert "lookaround" in str(exc.value)


@pytest.mark.parametrize("fragment", ["(a)\\1", "\\1", "x\\9y", "(?P=n)"])
def test_backreferences_are_refused(fragment: str) -> None:
    """Kusto accepts these; RE2 rejects the whole pattern. Without the check the
    user gets a DuckDB binder error naming an escape sequence they did not
    write, because the pattern they see is assembled."""
    with pytest.raises(KqlUnsupportedError) as exc:
        neutralise_groups(fragment)
    assert "backreference" in str(exc.value)


@pytest.mark.parametrize("fragment", ["\\0", "\\d", "\\w", "\\\\1", "a\\\\", "[\\1]"])
def test_things_that_only_look_like_backreferences(fragment: str) -> None:
    """`\\0` is not a group reference, `\\\\1` is an escaped backslash then a
    digit, and a digit inside a class is just a digit."""
    assert neutralise_groups(fragment) == fragment


@pytest.mark.parametrize("fragment", ["(a", "[abc", "a\\", "(?P<n", "(?"])
def test_malformed_input_is_left_for_duckdb_to_report(fragment: str) -> None:
    """Not a crash and not a guess. RE2's own message names the real problem,
    and inventing one here would be worse than passing it through."""
    neutralise_groups(fragment)  # must not raise
