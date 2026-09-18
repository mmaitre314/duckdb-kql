"""L5 trap tests — `extract_all` (R9, R11).

Reported as one bug: `extract_all(@'(?:^| )([a-z]+)', ' test word')` answered
`[" test"," word"]` where Kusto answers `["test","word"]`. It returned the
**whole match** because DuckDB's `regexp_extract_all` takes the group as a
*third* argument and defaults it to 0, and the mapping passed two.

The loud half had three quiet siblings, all measured on the emulator over rows
and folded:

===========================  ==============================  ==================
call                         Kusto                           was
===========================  ==============================  ==================
one capture group            the group                       the whole match
**no** capture group         refused, SEM0213                answered
**no match**                 null                            ``[]``
**non-literal** regex        refused, SEM0040                assumed one group
===========================  ==============================  ==================

The last is the worst of the four and the least visible: the group count
decides the result *shape*, and guessing 1 for a regex it could not read would
have returned a flat array where a nested one was right. Kusto requires that
argument to be a scalar constant, which is exactly what makes reading it at
translation time sound — the case `docs/column-types-proposal.md` is about does
not arise here, because Kusto forbids it too.

`captureGroups` (the three-argument form) was refused outright. Its selection
rule is not the obvious one: an out-of-range index is **dropped**, not refused,
and if that leaves nothing the call behaves as though the argument were absent.
Measured over a two-group regex, `[1,5]` gives group 1 alone while `[5]` and
`[]` both give every group.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlUnsupportedError

duckdb = pytest.importorskip("duckdb")

#: One group, and the group is a strict substring of the match — so a mapping
#: returning the match is visibly wrong rather than coincidentally right.
P1 = r"@'(?:^| )([a-z]+)'"
P2 = r"@'(?:^| )([a-z])([a-z]+)'"
T = "datatable(s:string)[' test word']"


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    return c


def _one(con, kql):
    return duckdb_kql.kql(con, kql).fetchall()[0][0]


# ---------------------------------------------------------------------------
# The reported bug, and the shape rules around it
# ---------------------------------------------------------------------------


def test_one_capture_group_returns_the_group_not_the_match(con) -> None:
    """The report. `regexp_extract_all(s, p)` is the whole match; `(s, p, 1)`
    is the group, and the third argument is the entire fix."""
    assert _one(con, f"{T} | project r = extract_all({P1}, s)") == '["test","word"]'


def test_the_folded_form_agrees(con) -> None:
    """Kusto's constant folder and its row engine disagree elsewhere; here they
    do not, and the report was written against the folded form."""
    assert _one(
        con, r"print r = extract_all(@'(?:^| )([a-z]+)', ' test word')"
    ) == '["test","word"]'


def test_several_groups_nest_per_match(con) -> None:
    assert _one(con, f"{T} | project r = extract_all({P2}, s)") == (
        '[["t","est"],["w","ord"]]'
    )


def test_no_match_is_null_not_an_empty_array(con) -> None:
    """Quiet sibling. `[]` and null are different values to every caller that
    tests the result, and `isnull` is how a KQL query asks."""
    assert _one(con, rf"{T} | project r = isnull(extract_all(@'(\d+)', s))") is True


def test_a_null_subject_is_null(con) -> None:
    """Falls out of the same emission: `len(NULL)` is null, so the CASE has no
    true branch and yields null rather than a JSON `null`."""
    assert _one(
        con,
        r"datatable(s:string)[dynamic(null)] "
        r"| project r = isnull(extract_all(@'([a-z]+)', tostring(s)))",
    ) is True


# ---------------------------------------------------------------------------
# What Kusto refuses, and what it decides at compile time
# ---------------------------------------------------------------------------


def test_a_regex_with_no_capture_group_is_refused(con) -> None:
    """Measured: SEM0213, "argument 2 must be a valid regex with [1..16]
    matching groups". We answered it — with the whole matches, which is the
    plausible-looking wrong answer the charter is about."""
    with pytest.raises(KqlUnsupportedError, match="capture groups"):
        duckdb_kql.kql(con, rf"{T} | project r = extract_all(@'[a-z]+', s)")


def test_more_than_sixteen_groups_is_refused(con) -> None:
    """The other end of the same measured range."""
    with pytest.raises(KqlUnsupportedError, match="capture groups"):
        duckdb_kql.kql(con, f"{T} | project r = extract_all(@'{'([a-z])' * 17}', s)")


def test_a_non_literal_regex_is_refused(con) -> None:
    """The dangerous one. The group count decides the result's *shape*, so a
    regex this cannot read is not a regex to guess at — and Kusto refuses it
    too (SEM0040, "failed to cast argument 2 to scalar constant"), so nothing
    is lost by refusing."""
    with pytest.raises(KqlUnsupportedError, match="literal"):
        duckdb_kql.kql(
            con,
            "datatable(s:string, p:string)[' test word','([a-z]+)'] "
            "| project r = extract_all(p, s)",
        )


def test_a_named_group_still_counts_as_a_capture(con) -> None:
    """`(?<name>...)` is a capture; the `(?`-prefix test alone called it a
    non-capturing group and reached 0. Counted here even though DuckDB's RE2
    rejects the .NET spelling downstream — the count must be right regardless,
    or the shape is."""
    from duckdb_kql import ir
    from duckdb_kql.translate import _capture_group_count

    assert _capture_group_count(ir.Literal(r"(?<w>[a-z]+)", "string")) == 1
    assert _capture_group_count(ir.Literal(r"(?P<w>[a-z]+)", "string")) == 1
    assert _capture_group_count(ir.Literal(r"(?:a)(b)", "string")) == 1
    assert _capture_group_count(ir.Literal(r"(?=a)(b)", "string")) == 1
    # A parenthesis inside a character class is a literal, not a group.
    assert _capture_group_count(ir.Literal(r"[(](a)", "string")) == 1


# ---------------------------------------------------------------------------
# captureGroups — the three-argument form
# ---------------------------------------------------------------------------


def test_selecting_one_group_gives_the_flat_form(con) -> None:
    """The report's second repro."""
    assert _one(
        con, r"print r = extract_all(@'(?:^| )([a-z]+)', dynamic([1]), ' test word')"
    ) == '["test","word"]'


def test_selection_picks_and_orders(con) -> None:
    assert _one(con, f"{T} | project r = extract_all({P2}, dynamic([2]), s)") == (
        '["est","ord"]'
    )
    assert _one(con, f"{T} | project r = extract_all({P2}, dynamic([2,1]), s)") == (
        '[["est","t"],["ord","w"]]'
    )


def test_a_group_may_be_selected_twice(con) -> None:
    """Measured, and it falls out of treating the selection as a list rather
    than a set."""
    assert _one(con, f"{T} | project r = extract_all({P2}, dynamic([1,1]), s)") == (
        '[["t","t"],["w","w"]]'
    )


def test_an_out_of_range_index_is_dropped_not_refused(con) -> None:
    """Not the obvious rule. `[1,5]` over a two-group regex is group 1 alone —
    and one group left means the *flat* shape, so a dropped index changes the
    result's type, not just its width."""
    assert _one(con, f"{T} | project r = extract_all({P2}, dynamic([1,5]), s)") == (
        '["t","w"]'
    )


@pytest.mark.parametrize("selection", ["dynamic([5])", "dynamic([])"])
def test_an_empty_selection_means_every_group(con, selection: str) -> None:
    """Measured: when nothing survives the filter the argument stops applying,
    rather than the call returning nothing."""
    assert _one(con, f"{T} | project r = extract_all({P2}, {selection}, s)") == (
        '[["t","est"],["w","ord"]]'
    )


def test_selecting_a_group_by_name_is_refused(con) -> None:
    """Kusto accepts a name here (SEM0214 names the failure mode). Refused
    rather than half-implemented: resolving a name needs a second reader of the
    regex, and a refusal costs a query instead of an answer."""
    with pytest.raises(KqlUnsupportedError, match="by name"):
        duckdb_kql.kql(con, f"{T} | project r = extract_all({P2}, dynamic(['w']), s)")


def test_a_non_literal_selection_is_refused(con) -> None:
    with pytest.raises(KqlUnsupportedError, match="captureGroups"):
        duckdb_kql.kql(
            con,
            "datatable(s:string, g:dynamic)[' test word', dynamic([1])] "
            f"| project r = extract_all({P1}, g, s)",
        )
