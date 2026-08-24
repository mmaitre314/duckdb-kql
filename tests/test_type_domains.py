"""L5 trap tests — two places one table served jobs with different domains.

* **`datetime_add` / `datetime_diff` / `datetime_part`** shared a single period
  table. They do not share a domain: the arithmetic pair take `week` and refuse
  `week_of_year` and `dayofyear`, the extraction one is the mirror image. So
  the shared table broke *both* ways — `datetime_add("dayofyear", …)` asked
  DuckDB for a function called `to_dayofyears` and crashed, and
  `datetime_add("week_of_year", …)` quietly **answered**.
* **`case` / `iff` / `coalesce`** must have branches of one type. Mixing them
  reached DuckDB, which tried to reconcile them and raised
  `Could not convert string 'positive' to INT64` — a message about SQL types
  for a rule that is KQL's.

Every domain below was enumerated against the emulator rather than taken from
the documentation.
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


# ---------------------------------------------------------------------------
# The period domains
# ---------------------------------------------------------------------------

#: Accepted by both `datetime_add` and `datetime_diff`, measured.
ARITHMETIC = [
    "year", "quarter", "month", "week", "day", "hour", "minute", "second",
    "millisecond", "microsecond",
]

#: Accepted by `datetime_part`, measured. Note `week_of_year` and `dayofyear`
#: are here and NOT above, and `week` is above and not here.
EXTRACTION = [
    "year", "quarter", "month", "week_of_year", "day", "dayofyear", "hour",
    "minute", "second", "millisecond", "microsecond",
]


@pytest.mark.parametrize("part", ARITHMETIC)
def test_the_arithmetic_domain(con, part: str) -> None:
    assert duckdb_kql.kql(
        con, f'print x = datetime_add("{part}", 1, datetime(2024-01-01))'
    ).fetchall()
    assert duckdb_kql.kql(
        con,
        f'print x = datetime_diff("{part}", datetime(2024-03-05), datetime(2024-01-01))',
    ).fetchall()


@pytest.mark.parametrize("part", EXTRACTION)
def test_the_extraction_domain(con, part: str) -> None:
    assert duckdb_kql.kql(
        con, f'print x = datetime_part("{part}", datetime(2024-03-05 06:07:08.9))'
    ).fetchall()


@pytest.mark.parametrize(
    ("fn", "part"),
    [
        # The crash: no `to_dayofyears` in DuckDB.
        ("datetime_add", "dayofyear"),
        ("datetime_diff", "dayofyear"),
        # The quiet half — this used to answer, and a cluster refuses it.
        ("datetime_add", "week_of_year"),
        ("datetime_diff", "week_of_year"),
        # ...and the mirror image, which the shared table also let through.
        ("datetime_part", "week"),
        # Neither domain has these.
        ("datetime_add", "tick"),
        ("datetime_part", "weekday"),
        ("datetime_part", "dayofweek"),
    ],
)
def test_a_part_outside_the_domain_is_refused(fn: str, part: str) -> None:
    args = (
        f'"{part}", datetime(2024-01-01)'
        if fn == "datetime_part"
        else f'"{part}", 1, datetime(2024-01-01)'
    )
    with pytest.raises(duckdb_kql.KqlUnsupportedError) as exc:
        duckdb_kql.to_sql(f"print x = {fn}({args})")
    assert part in str(exc.value)


def test_week_and_week_of_year_are_not_interchangeable(con) -> None:
    """The pair that makes the split necessary rather than tidy.

    Each is valid for exactly one domain, so a table serving both had to accept
    both everywhere — and 'add one week-of-year' is not a question.
    """
    assert duckdb_kql.kql(
        con, 'print x = datetime_add("week", 1, datetime(2024-01-01))'
    ).fetchall() == [(__import__("datetime").datetime(2024, 1, 8),)]
    assert duckdb_kql.kql(
        con, 'print x = datetime_part("week_of_year", datetime(2024-03-05))'
    ).fetchall() == [(10,)]


def test_nanosecond_is_still_refused_everywhere(con) -> None:
    """A cluster accepts it; we refuse, and that is the intended direction.

    DuckDB stores microseconds and has no `to_nanoseconds` at all, so the
    100 ns tick is not there to return. Answering would mean quietly rounding a
    value the caller asked for *because* they wanted that precision.
    """
    for call in [
        'datetime_add("nanosecond", 1, datetime(2024-01-01))',
        'datetime_part("nanosecond", datetime(2024-01-01))',
    ]:
        with pytest.raises(duckdb_kql.KqlUnsupportedError):
            duckdb_kql.to_sql(f"print x = {call}")


# ---------------------------------------------------------------------------
# Conditional branch types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        'case(1>0, "positive", 5)',
        'iff(1>0, "a", 5)',
        'iif(1>0, "a", 5)',
        'coalesce("a", 5)',
        # Stricter than SQL, and stricter than it looks: an integer and a real
        # are different types to Kusto. Measured — `coalesce(5, 1.5)` is SEM0525.
        "coalesce(5, 1.5)",
        "iff(1>0, 5, 1.5)",
        'iff(1>0, true, "a")',
        "iff(1>0, datetime(2024-01-01), 3h)",
        # A later pair in a `case`, not just the first.
        'case(1>0, "a", 1<0, "b", 5)',
    ],
)
def test_mixed_branch_types_are_refused(call: str) -> None:
    with pytest.raises(duckdb_kql.KqlUnsupportedError) as exc:
        duckdb_kql.to_sql(f"print x = {call}")
    assert "SEM0525" in str(exc.value)


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        ("iff(1>0, 1, 2)", 1),
        ("iff(1>0, int(3), 5)", 3),          # int and long are one type
        ("coalesce(5, long(null))", 5),      # a null literal claims no type
        ('iff(1>0, "a", "b")', "a"),
        ('coalesce("a", "b")', "a"),
        # `dynamic` is KQL's universal branch type — measured, it pairs with
        # every other one, so it must never trip the check.
        ('iff(1>0, dynamic([1]), "a")', "[1]"),
        ("coalesce(dynamic([1]), 5)", "[1]"),
    ],
)
def test_compatible_branches_still_translate(con, call: str, expected) -> None:
    assert duckdb_kql.kql(con, f"print x = {call}").fetchall()[0][0] == expected


def test_a_non_literal_branch_is_not_judged(con) -> None:
    """The residue, stated rather than implied.

    Only literals are classified, so a mismatch between two *columns* still
    reaches DuckDB. That needs the column types
    (`docs/column-types-proposal.md`); what this check buys is that the
    first mismatch anyone hits — two literals — refuses cleanly instead.
    """
    q = "datatable(s:string, n:long)['a', 5] | project v = iff(1>0, s, n)"
    with pytest.raises(Exception) as exc:
        duckdb_kql.kql(con, q).fetchall()
    assert not isinstance(exc.value, duckdb_kql.KqlUnsupportedError)
