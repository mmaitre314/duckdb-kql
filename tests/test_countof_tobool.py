"""L5 trap tests — two scalars whose DuckDB equivalent is subtly the wrong one.

Both were silently wrong values, and both were wrong for the same reason: the
nearest SQL primitive answers a *similar* question.

* **``countof``** counts **overlapping** occurrences in its default `normal`
  kind — `countof("aaaa", "aa")` is 3 — while the `regex` kind does not, and
  neither does `replace()`, which the substring mode was built on. The code,
  its own docstring, and this repo's published support matrix all disagreed
  with each other; the matrix was the one that had it right.
* **``tobool``** reads text as `true`/`false` **or an integer**, so `'2'` is
  true and `'1.5'` is null. DuckDB's `TRY_CAST(… AS BOOLEAN)` is wrong in both
  directions: it accepts `yes`/`no`/`y`/`n`/`t`/`f`, which Kusto answers null
  for, and rejects `'2'`, which Kusto answers true for.

Every expectation was measured on the Kusto Emulator.
"""

from __future__ import annotations

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    return c


def _one(con, kql: str):
    return duckdb_kql.kql(con, kql).fetchall()[0][0]


# ---------------------------------------------------------------------------
# countof
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        # The trap. `replace()`-based counting answers 2 for the first two.
        ('countof("aaaa", "aa")', 3),
        ('countof("aaaa", "aa", "normal")', 3),
        ('countof("aaa", "aa")', 2),
        # Non-overlapping data agrees either way, which is why this hid.
        ('countof("abcabc", "abc")', 2),
        ('countof("aaaa", "a")', 4),
        ('countof("abc", "abc")', 1),
        # Edges.
        ('countof("", "a")', 0),
        ('countof("ab", "abc")', 0),
        ('countof("AAAA", "aa")', 0),      # case-sensitive
        ('countof("abc", "")', 0),         # empty needle, not a divide-by-zero
        ('countof("", "")', 0),
        # R11: characters, not bytes.
        ('countof("ααα", "αα")', 2),
    ],
)
def test_countof_normal_counts_overlapping(con, call: str, expected: int) -> None:
    assert _one(con, f"print x = {call}") == expected


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        # The regex kind is NOT overlapping — the two kinds genuinely differ,
        # so this is the control that stops the fix being applied to both.
        ('countof("aaaa", "aa", "regex")', 2),
        ('countof("aaaa", "a+", "regex")', 1),
    ],
)
def test_countof_regex_does_not_overlap(con, call: str, expected: int) -> None:
    assert _one(con, f"print x = {call}") == expected


def test_a_null_haystack_counts_zero_rather_than_propagating(con) -> None:
    """The one place this function does not follow R4. Measured."""
    q = "datatable(s:string)[dynamic(null)] | project c = countof(s, 'a')"
    assert duckdb_kql.kql(con, q).fetchall() == [(0,)]


def test_countof_over_a_haystack_column(con) -> None:
    """Pinned separately: the position scan reads the haystack several times."""
    q = "datatable(s:string)['aaaa', 'abcabc'] | project c = countof(s, 'aa')"
    assert duckdb_kql.kql(con, q).fetchall() == [(3,), (0,)]


def test_a_needle_column_is_accepted_here_and_refused_on_a_cluster(con) -> None:
    """A known **mild** divergence: we answer where Kusto rejects the query.

    Measured — Kusto wants the search term as a literal and returns a 400 for
    `countof(s, n)`. Recorded rather than fixed: refusing it would need the
    argument's origin, and answering is the harmless direction (a query that
    works here and fails on a cluster, not a wrong number).
    """
    q = "datatable(s:string, n:string)['aaaa', 'aa'] | project c = countof(s, n)"
    assert duckdb_kql.kql(con, q).fetchall() == [(3,)]


def test_countof_rejects_an_unknown_kind(con) -> None:
    with pytest.raises(duckdb_kql.KqlUnsupportedError):
        duckdb_kql.to_sql('print x = countof("a", "a", "fuzzy")')


# ---------------------------------------------------------------------------
# tobool / toboolean
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("true", True),
        ("TRUE", True),
        ("True", True),
        ("false", False),
        ("FALSE", False),
        # Whitespace is trimmed, and `trim()` alone would not have done it —
        # DuckDB's default trim set is spaces, so a tab survived and cast null.
        (" true ", True),
        ("TrUe ", True),
        # Integers convert by nonzero-ness. TRY_CAST(... AS BOOLEAN) rejects
        # every one of these.
        ("1", True),
        ("0", False),
        ("2", True),
        ("-1", True),
        ("+1", True),
        ("0002", True),
        (" 2 ", True),
        # ...but only *integer* text. A TRY_CAST to BIGINT would round '1.5'
        # to 2 and answer true.
        ("1.5", None),
        ("0.0", None),
        # DuckDB's cast accepts all of these; Kusto does not.
        ("yes", None),
        ("no", None),
        ("y", None),
        ("n", None),
        ("t", None),
        ("f", None),
        # Neither engine takes these.
        ("on", None),
        ("off", None),
        ("abc", None),
        ("", None),
        ("99999999999999999999", None),   # overflow is null, not true
    ],
)
def test_tobool_of_text(con, text: str, expected: bool | None) -> None:
    assert _one(con, f"print x = tobool('{text}')") is expected
    assert _one(con, f"print x = toboolean('{text}')") is expected


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        # A NUMBER is its nonzero-ness, fractions included. This is the half
        # that makes the text rule impossible to apply everywhere: `1.5` is
        # true and `'1.5'` is null, the same value spelled two ways.
        ("1", True),
        ("0", False),
        ("2", True),
        ("-1", True),
        ("1.5", True),
        ("0.0", False),
        ("true", True),
        ("false", False),
    ],
)
def test_tobool_of_a_number_or_bool(con, literal: str, expected: bool) -> None:
    assert _one(con, f"print x = tobool({literal})") is expected


def test_tobool_over_columns_of_each_type(con) -> None:
    """The dispatch is DuckDB's run-time `typeof`, so a column is the real test.

    A literal could in principle be typed statically; a `ColumnRef` cannot, and
    it is the shape that made this look like it needed the column-types work
    before it could be fixed.
    """
    strings = "datatable(s:string)['true','yes','2','1.5',''] | project b = tobool(s)"
    assert duckdb_kql.kql(con, strings).fetchall() == [
        (True,), (None,), (True,), (None,), (None,)
    ]
    longs = "datatable(n:long)[0, 1, 2, -1] | project b = tobool(n)"
    assert duckdb_kql.kql(con, longs).fetchall() == [
        (False,), (True,), (True,), (True,)
    ]
    reals = "datatable(n:real)[0.0, 1.5] | project b = tobool(n)"
    assert duckdb_kql.kql(con, reals).fetchall() == [(False,), (True,)]


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("tobool(dynamic(true))", True),
        ("tobool(dynamic('true'))", True),
        ("tobool(dynamic(1))", True),
        ("tobool(dynamic(null))", None),
    ],
)
def test_tobool_of_a_dynamic(con, expr: str, expected: bool | None) -> None:
    assert _one(con, f"print x = {expr}") is expected


def test_parse_and_tobool_now_share_one_rule(con) -> None:
    """`parse … : bool` had the exact rule; `tobool` had DuckDB's cast.

    They are the same question about the same text, so they are one helper now.
    This pins that they answer alike — including the tab, which both got wrong
    before, `trim()` not being whitespace-complete.
    """
    for text, expected in [("true", True), ("yes", None), ("2", True),
                           ("1.5", None), ("\ttrue", True)]:
        literal = text.replace("\t", "\\t")
        parsed = _one(
            con,
            f"datatable(s:string)['v={literal}'] "
            '| parse s with "v=" b: bool | project b',
        )
        assert parsed is expected, text
        assert _one(con, f"print x = tobool('{literal}')") is expected, text
