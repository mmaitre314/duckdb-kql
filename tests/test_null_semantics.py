"""R4 — KQL's comparison operators are not SQL's three-valued ones.

The trap: `| where s !contains "err"` reads as "keep the rows that don't mention
err". In SQL, `NOT (s ILIKE '%err%')` on a null `s` is NULL, so the row is
**dropped** — and the query returns a smaller, entirely plausible answer with no
error anywhere. In KQL it is TRUE and the row is kept.

Every expectation here was measured on the Kusto Emulator rather than read off
the documentation, and the measurement is reproducible:

    docker compose up -d kusto
    python - <<'EOF'
    from duckdb_kql.oracle import KustoEmulator
    print(KustoEmulator().query('''
    datatable(id:int, n:int, s:string) [1, 5, "hello"]
    | union (datatable(id:int) [2])
    | extend eq = n == 5, ne = n != 5, gt = n > 5,
             nc = s !contains "ell", nin = n !in (5)
    | order by id asc''').to_dict())
    EOF

What it returns, and therefore what these tests assert:

===================  ==============  =========
operator             non-null row    null row
===================  ==============  =========
``==`` ``=~`` ``in``  as expected     **false**
``!=`` ``!~`` ``!in`` as expected     **true**
``!contains`` etc.    as expected     **true**
``<`` ``>`` ``<=``    as expected     **null**
===================  ==============  =========

So the equality, membership and matching families are *total* — they never
return null — while the ordering comparisons stay three-valued exactly as SQL
does. The one exception, which makes a blanket ``coalesce`` wrong, is null on
**both** sides: `a == b` with both null is null, not false.
"""

from __future__ import annotations

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")


@pytest.fixture(scope="module")
def con():
    """One row with values, one row of nulls — the whole point of the fixture."""
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    c.execute(
        """
        CREATE TABLE T(rid INTEGER, n INTEGER, s VARCHAR, m INTEGER);
        INSERT INTO T VALUES (1, 5, 'hello', 5), (2, NULL, NULL, NULL);
        """
    )
    return c


def kept(con, predicate: str) -> list[int]:
    """The row ids `| where <predicate>` keeps, in order."""
    rel = duckdb_kql.kql(con, f"T | where {predicate} | project rid | sort by rid asc")
    return [row[0] for row in rel.fetchall()]


# ---------------------------------------------------------------------------
# The negated forms must KEEP the null row
# ---------------------------------------------------------------------------

NEGATED = [
    ('s !contains "ell"', "!contains"),
    ('s !contains_cs "ell"', "!contains_cs"),
    ('s !has "hello"', "!has"),
    ('s !has_cs "hello"', "!has_cs"),
    ('s !startswith "he"', "!startswith"),
    ('s !startswith_cs "he"', "!startswith_cs"),
    ('s !endswith "lo"', "!endswith"),
    ('s !endswith_cs "lo"', "!endswith_cs"),
    ('s != "hello"', "!="),
    ('s !~ "HELLO"', "!~"),
    ("n != 5", "!= numeric"),
    ("n !in (5)", "!in"),
    ("n !in (5, 6)", "!in multi"),
]


@pytest.mark.parametrize("predicate,label", NEGATED, ids=[x[1] for x in NEGATED])
def test_negated_operators_keep_the_null_row(con, predicate: str, label: str) -> None:
    """The row that has no value does not contain the term, so KQL keeps it."""
    assert kept(con, predicate) == [2], (
        f"`where {predicate}` dropped the null row — SQL's NOT(NULL) is NULL, "
        "but KQL answers true (R4)"
    )


POSITIVE = [
    ('s contains "ell"', "contains"),
    ('s has "hello"', "has"),
    ('s startswith "he"', "startswith"),
    ('s endswith "lo"', "endswith"),
    ('s == "hello"', "=="),
    ('s =~ "HELLO"', "=~"),
    ("n == 5", "== numeric"),
    ("n in (5)", "in"),
    ('s matches regex "ell"', "matches regex"),
]


@pytest.mark.parametrize("predicate,label", POSITIVE, ids=[x[1] for x in POSITIVE])
def test_positive_operators_still_exclude_the_null_row(
    con, predicate: str, label: str
) -> None:
    """The counterweight: making the negated form total must not leak rows here."""
    assert kept(con, predicate) == [1]


# ---------------------------------------------------------------------------
# The exception that makes a blanket coalesce wrong
# ---------------------------------------------------------------------------


def test_null_on_both_sides_stays_null(con) -> None:
    """`a != b` with both sides null is NULL in KQL — not true.

    This is why the fix cannot be `coalesce(a <> b, TRUE)` everywhere: that
    would keep row 2, inventing a second wrong answer while fixing the first.
    Emulator: `datatable(id:int, a:int, b:int) [1,5,5] | union (datatable(id:int)
    [2]) | extend both_ne = a != b` gives false, then **null**.
    """
    # `where`: row 1 is 5 != 5 (false) and row 2 is null, so nothing survives.
    # Under a blanket coalesce row 2 would become true and come back.
    assert kept(con, "n != m") == []

    # The projected value is where the two differ visibly: null, not false.
    rel = duckdb_kql.kql(con, "T | sort by rid asc | project e = n == m, ne = n != m")
    assert rel.fetchall() == [(True, False), (None, None)]


def test_the_guard_is_only_paid_for_when_it_is_needed(con) -> None:
    """A literal operand cannot be null, so the *null* guard is not emitted.

    Readability of the generated SQL is a feature — the CLI ships it to people
    who have to read it — so `col != 'x'` must stay a plain coalesce.

    Asserted on the both-null test rather than on the string `CASE WHEN`,
    because a second and unrelated guard also renders as one: comparing a
    column to a string has to work whether or not that column turns out to hold
    a `dynamic` (`_in_string_context`), and it says `typeof(...) = 'JSON'`.
    Those two are independent, and only this one is about nulls.
    """
    simple = str(duckdb_kql.to_sql('T | where s != "hello"'))
    assert "IS NULL AND" not in simple, simple
    assert "coalesce" in simple

    both_columns = str(duckdb_kql.to_sql("T | where n != m"))
    assert "IS NULL AND" in both_columns, both_columns


# ---------------------------------------------------------------------------
# Ordering comparisons are NOT total — they must stay three-valued
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("predicate", ["n > 5", "n < 5", "n >= 5", "n <= 5"])
def test_ordering_comparisons_stay_null(con, predicate: str) -> None:
    """KQL leaves these null on a null operand, exactly as SQL does.

    Asserting it keeps the fix from over-reaching: `n > 5` must not start
    keeping the null row just because `n != 5` now does.
    """
    assert 2 not in kept(con, predicate)
    assert "coalesce" not in str(duckdb_kql.to_sql(f"T | where {predicate}"))


def test_a_null_row_survives_a_negated_filter_end_to_end(con) -> None:
    """The shape of the bug as a user would hit it: a count that was too low."""
    rel = duckdb_kql.kql(con, 'T | where s !contains "zzz" | count')
    assert rel.fetchall() == [(2,)], (
        "both rows lack 'zzz', so both must be counted — the null row was the "
        "one silently missing"
    )
