"""L5 trap tests — the `parse` family (`docs/parse-proposal.md`).

`parse` turns unstructured text into columns, and almost none of how it does
that is guessable. Every expectation here was measured on the pinned Kusto
Emulator; the ones that corrected the proposal while it was being implemented
are called out, because they are the ones a re-implementation would get wrong
the same way.

The single most surprising rule: **`kind=simple` is all-or-nothing.** If any
declared column fails to convert, the whole row is blanked — including columns
that converted perfectly well.
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


def _rows(con, kql: str):
    return duckdb_kql.kql(con, kql).fetchall()


def _one(con, table: str, clause: str):
    return _rows(con, f"{table} | {clause}")


# ---------------------------------------------------------------------------
# The shape of the output
# ---------------------------------------------------------------------------


def test_a_non_match_keeps_the_row_with_empty_strings(con) -> None:
    """Not null, and not dropped — `''` for a string column, null for a typed one."""
    q = ("datatable(s:string)['a=1, b=xy', 'nomatch'] "
         '| parse s with "a=" a ", b=" b')
    assert _rows(con, q) == [("a=1, b=xy", "1", "xy"), ("nomatch", "", "")]


def test_parse_where_drops_the_non_matching_row_instead(con) -> None:
    q = ("datatable(s:string)['a=1, b=xy', 'nomatch'] "
         '| parse-where s with "a=" a ", b=" b')
    assert _rows(con, q) == [("a=1, b=xy", "1", "xy")]


def test_a_declared_name_replaces_an_existing_column_in_place(con) -> None:
    """`extend`'s rule — the third operator in this codebase to want it, hence
    `schema.replacing`. Column order is user-visible (R1)."""
    rel = duckdb_kql.kql(
        con, "datatable(s:string, a:string)['a=1', 'zz'] | parse s with \"a=\" a"
    )
    assert list(rel.columns) == ["s", "a"]
    assert rel.fetchall() == [("a=1", "1")]


def test_new_columns_are_appended_in_declaration_order(con) -> None:
    rel = duckdb_kql.kql(
        con, "datatable(s:string, z:long)['a=1,b=2', 9] "
        '| parse s with "a=" a ",b=" b'
    )
    assert list(rel.columns) == ["s", "z", "a", "b"]


# ---------------------------------------------------------------------------
# What a capture matches
# ---------------------------------------------------------------------------


def test_a_column_is_lazy_and_stops_at_the_first_following_literal(con) -> None:
    """`"a" v "c" *` over `aXcYc` is `X`, the first `c` and not the last.

    The trailing `*` is load-bearing in this example: without it the pattern
    has to reach the end of the input and the same capture answers `XcY`. See
    `test_simple_reaches_the_end_of_the_input_and_regex_does_not`.
    """
    q = "datatable(s:string)['aXcYc'] | parse s with \"a\" v \"c\" *"
    assert _rows(con, q) == [("aXcYc", "X")]


def test_a_star_skips_to_the_first_match_too(con) -> None:
    q = "datatable(s:string)['y=1,x=2,x=9'] | parse s with * \"x=\" v \",\" *"
    assert _rows(con, q) == [("y=1,x=2,x=9", "2")]


def test_a_trailing_column_runs_to_the_end(con) -> None:
    q = "datatable(s:string)['a=1, b=xy'] | parse s with \"a=\" a"
    assert _rows(con, q) == [("a=1, b=xy", "1, b=xy")]


def test_literals_are_escaped_not_treated_as_regex(con) -> None:
    """`"a.b"` must not match `axbZ`. `kind=regex` is where the dot *is* a
    metacharacter — see `test_a_literal_is_a_regex_in_regex_mode_and_text_otherwise`."""
    q = "datatable(s:string)['a.bZ', 'axbZ'] | parse s with \"a.b\" v"
    assert _rows(con, q) == [("a.bZ", "Z"), ("axbZ", "")]


def test_literals_are_case_sensitive(con) -> None:
    assert _rows(con, "datatable(s:string)['A=1'] | parse s with \"a=\" v") == [
        ("A=1", "")
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # A typed column before a `*` uses a **type-shaped** capture — the one
        # position where a lazy wildcard has nothing to stop it. This is *why*
        # Kusto allows `*` after a typed column and refuses it after a string
        # one (SEM0476).
        ("n=27 junk m=23", (27, 23)),
        # ...and when the shape cannot match, the whole pattern fails.
        ("n=xx junk m=23", (None, None)),
    ],
)
def test_a_typed_column_before_a_star_is_shaped(con, value: str, expected) -> None:
    q = (f"datatable(s:string)['{value}'] "
         '| parse s with "n=" n: long * "m=" m: long | project n, m')
    assert _rows(con, q) == [expected]


def test_a_trailing_star_makes_the_shape_optional(con) -> None:
    """A `*` at the very end is a no-op, so the shape may match nothing.

    Measured *through `relaxed`*, which is the only mode that can show it:
    `"p|" a: string "-q|" b: long *` over `p|1-q|xx` answers `('1', null)`, so
    the pattern still matched and `b`'s shape was allowed to match nothing. A
    required shape would have failed the match and left `a` empty too — which
    is what `simple` answers either way, because the all-or-nothing rule blanks
    `a` on `b`'s failed conversion. The same `*` in the *middle* is not
    optional and blanks the row.
    """
    q = ("datatable(s:string)['p|1-q|xx'] "
         '| parse kind=relaxed s with "p|" a: string "-q|" b: long * | project a, b')
    assert _rows(con, q) == [("1", None)]


def test_an_anchored_typed_column_is_not_shaped(con) -> None:
    """With a literal after it there is nothing to disambiguate, so the capture
    is the same lazy wildcard a string column gets.

    Measured through `relaxed`, which is the only mode that can show it: the
    pattern still matches and only the conversion fails.
    """
    q = ("datatable(s:string)['n=xx,m=23'] "
         '| parse kind=relaxed s with "n=" n: long ",m=" m: long | project n, m')
    assert _rows(con, q) == [(None, 23)]


# ---------------------------------------------------------------------------
# The all-or-nothing rule — the trap
# ---------------------------------------------------------------------------


def test_simple_blanks_the_whole_row_when_any_conversion_fails(con) -> None:
    """Three columns are needed to see this; two are ambiguous.

    `a=1` converts perfectly well and is blanked anyway, because `c=zz` did
    not. A two-column example cannot tell "stop at the failure" from "blank
    the row", which is how the proposal came to say the wrong one.
    """
    q = ("datatable(s:string)['a=1,b=2,c=zz,d=4'] "
         '| parse s with "a=" a: long ",b=" b ",c=" c: long ",d=" d'
         " | project a, b, c, d")
    assert _rows(con, q) == [(None, "", None, "")]


def test_relaxed_converts_each_column_independently(con) -> None:
    q = ("datatable(s:string)['a=1,b=2,c=zz,d=4'] "
         '| parse kind=relaxed s with "a=" a: long ",b=" b ",c=" c: long ",d=" d'
         " | project a, b, c, d")
    assert _rows(con, q) == [(1, "2", None, "4")]


def test_an_empty_capture_into_a_typed_column_is_a_failure_too(con) -> None:
    """No "empty is not a failure" exception — measured, and the draft had it
    wrong until it was."""
    q = "datatable(s:string)['a=,b=2'] | parse s with \"a=\" a: long \",b=\" b"
    assert _rows(con, q) == [("a=,b=2", None, "")]
    untyped = "datatable(s:string)['a=,b=2'] | parse s with \"a=\" a \",b=\" b"
    assert _rows(con, untyped) == [("a=,b=2", "", "2")]


def test_parse_where_is_matched_and_every_conversion_succeeded(con) -> None:
    q = ("datatable(s:string)['a=1,b=2', 'a=zz,b=2', 'nope'] "
         '| parse-where s with "a=" a: long ",b=" b | project a, b')
    assert _rows(con, q) == [(1, "2")]


# ---------------------------------------------------------------------------
# Conversions — where DuckDB's cast is not KQL's
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2", 2), ("-3", -3), ("+5", 5), (" 9", 9), ("012", 12),
        # DuckDB's TRY_CAST *rounds* these; Kusto answers null. The integer
        # shape test is what keeps them null.
        ("1.5", None), (".5", None), ("1e3", None), ("xx", None),
    ],
)
def test_an_integer_column_takes_integer_text_only(con, value: str, expected) -> None:
    q = f"datatable(s:string)['n={value}|'] | parse s with \"n=\" n: long \"|\""
    assert _rows(con, q)[0][1] == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True), ("TRUE", True), ("false", False),
        # A whole number decides by being nonzero...
        ("2", True), ("-1", True), ("0", False),
        # ...but a fractional one is null, not true, which is why this cannot
        # be a bare TRY_CAST to BIGINT (that would round 1.5 to 2).
        ("1.5", None), ("0.0", None), ("yes", None), ("", None),
    ],
)
def test_a_bool_column_takes_true_false_or_a_whole_number(con, value, expected) -> None:
    q = f"datatable(s:string)['b={value}|'] | parse s with \"b=\" b: bool \"|\""
    assert _rows(con, q)[0][1] == expected


def test_a_datetime_column_accepts_the_formats_todatetime_does(con) -> None:
    """Including `MM/DD/YYYY`, which a bare `TRY_CAST` rejects.

    The corpus depends on it, and a bare cast made the conversion fail — which
    under the all-or-nothing rule blanked the entire row rather than one column.
    """
    q = ("datatable(s:string)['t=02/17/2016 08:40:01|'] "
         '| parse s with "t=" t: date "|"')
    assert _rows(con, q)[0][1].isoformat() == "2016-02-17T08:40:01"


def test_type_aliases_are_accepted(con) -> None:
    """`date` for `datetime`, and the rest of the alias table Kusto ships."""
    # The grammar accepts a narrower set here than TYPE_MAP knows.
    for alias in ("long", "int64", "int", "int8"):
        q = f"datatable(s:string)['n=5|'] | parse s with \"n=\" n: {alias} \"|\""
        assert _rows(con, q)[0][1] == 5


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "clause",
    [
        'parse s with "n=" n * "m=" m',            # bare name
        'parse s with "n=" n: string * "m=" m',    # explicit :string, same thing
        'parse s with "n=" n *',                   # trailing star counts
    ],
)
def test_a_star_after_a_string_column_is_refused(clause: str) -> None:
    """Two lazy wildcards in a row have no defined split, and Kusto says so —
    SEM0476, "Using '*' after string column is ambiguous"."""
    with pytest.raises(KqlUnsupportedError) as exc:
        duckdb_kql.to_sql(f"T | {clause}")
    assert "ambiguous" in str(exc.value)


def test_parse_where_kind_relaxed_is_refused() -> None:
    """Kusto refuses it too — SEM0477."""
    with pytest.raises(KqlUnsupportedError) as exc:
        duckdb_kql.to_sql('T | parse-where kind=relaxed s with "a=" a')
    assert "SEM0477" in str(exc.value)


@pytest.mark.parametrize(
    "clause",
    [
        'parse s with "a=" a: decimal ","',
        'parse s with "a=" a: datetime * "b=" b',
        'parse kind=regex flags=x s with "a=" a ","',
        'parse kind=regex s with "a=" a: datetime',
        'parse kind=regex s with "a=" a: timespan',
        'parse kind=regex flags=U s with "a=" a: datetime ","',
        'parse kind=regex s with "(?=a)a=" a ","',
        'parse kind=regex s with "(a)\\\\1=" a ","',
        'parse kind=simple flags=i s with "a=" a ","',
        'parse s with "a=" a "" b',
        'parse s with "" v',
        'parse s with "a=" v ""',
    ],
)
def test_the_unimplemented_surface_refuses_rather_than_guessing(clause: str) -> None:
    """Each of these is a refusal rather than a guess, and for a stated reason.

    `decimal` renders its scale (`1.000000000`, not `1`); a `datetime` before a
    `*` in simple mode has no measured shape; RE2 has no `x` flag, no
    lookaround and no backreferences; Kusto itself refuses `flags=` outside
    regex mode (SEM0472) and an empty string literal anywhere in the
    pattern (SEM0476);
    and a temporal capture at the end of a regex pattern, or under `flags=U`,
    stops behaving like a shape at all (see `_parse_regex_capture`).
    """
    with pytest.raises(KqlUnsupportedError):
        duckdb_kql.to_sql(f"T | {clause}")


def test_a_repeated_column_name_is_refused() -> None:
    with pytest.raises(KqlUnsupportedError) as exc:
        duckdb_kql.to_sql('T | parse s with "a=" a ",a=" a')
    assert "twice" in str(exc.value)


# ---------------------------------------------------------------------------
# Anchoring — `kind=simple` reaches the end of the input and `kind=regex` does
# not. This was wrong in the first three commits of `parse` and the sweep did
# not catch it, because every generated pattern happened to end on a *column*.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "clause", "simple", "regex"),
    [
        # a lazy capture with a literal after it stops at the *last* match in
        # simple mode, because the pattern has to reach the end of the input
        ("aXcYc", '"a" v "c"', "XcY", "XcY"),
        # ...and where it cannot reach the end, simple mode does not match at all
        ("abcbd", '"a" v "b"', "", "bc"),
        ("k=1,k=2,end", '"k=" v ","', "", "1,k=2"),
    ],
)
def test_simple_reaches_the_end_of_the_input_and_regex_does_not(
    con, value: str, clause: str, simple: str, regex: str
) -> None:
    table = f"datatable(s:string)['{value}']"
    assert _rows(con, f"{table} | parse s with {clause} | project v") == [(simple,)]
    assert _rows(con, f"{table} | parse kind=regex s with {clause} | project v") == [
        (regex,)
    ]


def test_the_anchor_is_end_of_text_not_before_a_final_newline(con) -> None:
    """RE2's bare `$` is `\\z`, which is what Kusto does here and what Python's
    and .NET's `$` would not: measured, `'aXc\\n'` parsed with `"a" v "c"` is a
    non-match, so the trailing newline counts."""
    table = "datatable(s:string)['aXc\\n']"
    assert _rows(con, f'{table} | parse s with "a" v "c" | project v') == [("",)]
    assert _rows(con, f'{table} | parse kind=regex s with "a" v "c" | project v') == [
        ("X",)
    ]


def test_relaxed_is_anchored_like_simple(con) -> None:
    q = ("datatable(s:string)['aXcYc'] "
         '| parse kind=relaxed s with "a" v "c" | project v')
    assert _rows(con, q) == [("XcY",)]


# ---------------------------------------------------------------------------
# kind=regex
# ---------------------------------------------------------------------------


def test_a_literal_is_a_regex_in_regex_mode_and_text_otherwise(con) -> None:
    table = "datatable(s:string)['axbZ']"
    assert _rows(con, f'{table} | parse s with "a.b" v | project v') == [("",)]
    assert _rows(con, f'{table} | parse kind=regex s with "a.b" v | project v') == [
        ("Z",)
    ]


def test_a_users_capturing_group_does_not_shift_the_columns(con) -> None:
    """DuckDB maps the name list by **position**, so an extra group would bind
    `v` to the user's text. Kusto matches by name and is immune; the rewrite in
    `translate.regexfrag` is what closes the gap."""
    q = ("datatable(s:string)['foo=9,bar=8'] "
         '| parse kind=regex s with "(foo)=" v "," "(bar)=" w | project v, w')
    assert _rows(con, q) == [("9", "8")]


def test_an_unbalanced_parenthesis_is_a_literal(con) -> None:
    """Measured on the emulator; RE2 would reject the pattern outright."""
    q = "datatable(s:string)['a)b'] | parse kind=regex s with \")\" v | project v"
    assert _rows(con, q) == [("b",)]


@pytest.mark.parametrize(
    ("flag", "value", "clause", "expected"),
    [
        ("i", "A=5", '"a=" v', "5"),
        ("s", "a=x\\ny", '"a=x." v', "y"),
        ("m", "x\\na=1", '"^a=" v', "1"),
        # `U` swaps the greediness of *everything*, the `*` skip included: the
        # skip reaches the last `x=` and the column then stops at the first `,`.
        ("U", "y=1,x=2,x=9,", '* "x=" v ","', "9"),
    ],
)
def test_each_flag_does_what_it_says(
    con, flag: str, value: str, clause: str, expected: str
) -> None:
    q = (f"datatable(s:string)['{value}'] "
         f"| parse kind=regex flags={flag} s with {clause} | project v")
    assert _rows(con, q) == [(expected,)]


def test_the_flags_are_inline_because_duckdb_rejects_U_as_an_option(con) -> None:
    """`regexp_extract`'s options argument takes `i`, `s` and `m` and answers
    *"Unrecognized Regex option U"* — so the prefix has to be `(?…)` in the
    pattern. Kusto composes one too: `flags=I` is refused as `(?I)`."""
    sql = str(duckdb_kql.to_sql('T | parse kind=regex flags=Ui s with "a=" a ","'))
    assert "(?iU)" in sql


def test_a_typed_column_is_shaped_everywhere_in_regex_mode(con) -> None:
    """The rule simple mode applies only before a `*`. Measured both ways."""
    table = "datatable(s:string)['n=27x']"
    assert _rows(con, f'{table} | parse kind=regex s with "n=" n: long | project n') == [
        (27,)
    ]
    assert _rows(con, f'{table} | parse s with "n=" n: long | project n') == [(None,)]


@pytest.mark.parametrize(
    ("declared", "value", "expected"),
    [
        # bool is true/false only here, where simple mode also takes an integer
        ("bool", "true", True),
        ("bool", "12", None),
        # real has no leading dot here, where simple mode allows one
        ("real", "1.5", 1.5),
        ("real", ".5", None),
        ("real", "1.", None),
        ("long", "27", 27),
        ("long", "1.5", None),
    ],
)
def test_the_regex_shapes_are_not_the_simple_mode_shapes(
    con, declared: str, value: str, expected: object
) -> None:
    q = (f"datatable(s:string)['v={value}|Q'] "
         f'| parse kind=regex s with "v=" c: {declared} "\\\\|Q" | project c')
    assert _rows(con, q) == [(expected,)]


def test_a_braced_guid_survives_flags_U(con) -> None:
    """The braces are an alternation rather than `\\{?…\\}?` for exactly this:
    under `U` the optional closing brace would go lazy and drop off, leaving
    `{74be…3642` for the cast."""
    guid = "{74be27de-1e4e-49d9-b579-fe0b331d3642}"
    q = (f"datatable(s:string)['v={guid}|Q'] "
         '| parse kind=regex flags=U s with "v=" c: guid "\\\\|Q" | project c')
    assert [str(r[0]) for r in _rows(con, q)] == [
        "74be27de-1e4e-49d9-b579-fe0b331d3642"
    ]


# ---------------------------------------------------------------------------
# Layer 0
# ---------------------------------------------------------------------------


def test_it_translates_without_a_schema() -> None:
    """No connection, no column list — the `COLUMNS(...)` form handles both the
    replace and the append case, at the cost of `extend`'s position residue."""
    sql = str(duckdb_kql.to_sql('T | parse s with "a=" a ",b=" b'))
    assert "regexp_extract" in sql
    assert "COLUMNS(" in sql
