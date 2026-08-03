"""Fixture integrity — the data the acceptance suite is measured against.

Two things must hold, and neither is about translation:

1. **The committed CSV matches the generator.** Frozen expectations were
   produced by running the emulator over exactly these rows, so a changed
   fixture silently invalidates every fixture-backed expectation.
2. **The data is not vacuous.** A case filtering ``State == "FLORIDA"`` against
   a fixture with no Florida rows returns empty on both sides and *passes* while
   proving nothing. That is the most expensive kind of green test — it costs a
   real test and reports success — so the predicates the corpus actually uses
   are asserted to select something.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from duckdb_kql import fixtures

duckdb = pytest.importorskip("duckdb")

CORPUS = Path(os.environ.get("DUCKDB_KQL_CORPUS", "tests/cases/docs/docs-corpus.json"))


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    fixtures.load_duckdb(c)
    return c


def test_committed_csv_matches_the_generator() -> None:
    """A drifted fixture invalidates every fixture-backed expectation."""
    path = fixtures.OUT
    assert path.is_file(), f"{path} missing — run tools/make_fixtures.py"

    tmp = path.with_suffix(".test.tmp")
    fixtures.write(tmp, fixtures.generate())
    try:
        assert fixtures.checksum(tmp) == fixtures.checksum(path), (
            f"{path} does not match tools/make_fixtures.py. Expectations were "
            "frozen from the committed file, so regenerating it invalidates "
            "them — re-freeze deliberately or restore the file."
        )
    finally:
        tmp.unlink()


def test_generation_is_deterministic() -> None:
    """Both engines must see identical rows; that requires a stable generator."""
    assert fixtures.generate() == fixtures.generate()
    assert fixtures.generate_population() == fixtures.generate_population()


def test_row_counts(con) -> None:
    assert con.sql("SELECT count(*) FROM StormEvents").fetchone()[0] == fixtures.ROWS
    assert con.sql("SELECT count(*) FROM PopulationData").fetchone()[0] == len(
        fixtures.STATES
    )


def test_sort_keys_are_unique(con) -> None:
    """`sort`/`top` break ties arbitrarily.

    With duplicate sort keys a deterministic query produces engine-specific row
    order, and the suite reports a divergence that is really just a tie.
    """
    dupes = con.sql(
        "SELECT count(*) FROM (SELECT StartTime FROM StormEvents "
        "GROUP BY 1 HAVING count(*) > 1)"
    ).fetchone()[0]
    assert dupes == 0, f"{dupes} duplicate StartTime values will make sort ties ambiguous"


# The literals the corpus actually filters on. If any of these selects nothing,
# the cases using it are passing vacuously.
@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("State", "FLORIDA"), ("State", "TEXAS"), ("State", "KANSAS"),
        ("State", "CALIFORNIA"), ("State", "VIRGINIA"), ("State", "NEW YORK"),
        ("State", "NEBRASKA"), ("State", "ALASKA"), ("State", "GUAM"),
        ("EventType", "Hail"), ("EventType", "Flood"), ("EventType", "Tornado"),
        ("EventType", "Thunderstorm Wind"), ("EventType", "Strong Wind"),
        ("EventType", "Extreme Cold/Wind Chill"), ("EventType", "Heavy Rain"),
        ("EventType", "Drought"), ("EventType", "Flash Flood"),
        ("EventType", "Lightning"), ("EventType", "Excessive Heat"),
        ("EventType", "Wildfire"),
    ],
)
def test_corpus_predicates_are_not_vacuous(con, column: str, value: str) -> None:
    n = con.sql(
        f'SELECT count(*) FROM StormEvents WHERE "{column}" = ?', params=[value]
    ).fetchone()[0]
    assert n > 0, f"no rows with {column}={value!r} — cases filtering on it prove nothing"


def test_substring_predicates_are_not_vacuous(con) -> None:
    """The corpus also uses partial matches (`State has "nor"`, `contains "sas"`)."""
    for needle in ("nor", "enn", "sas", "W", "A"):
        n = con.sql(
            "SELECT count(*) FROM StormEvents WHERE State ILIKE '%' || ? || '%'",
            params=[needle],
        ).fetchone()[0]
        assert n > 0, f"no State contains {needle!r}"


def test_numeric_predicates_select_a_subset(con) -> None:
    """Selecting *everything* is as uninformative as selecting nothing."""
    total = fixtures.ROWS
    for sql in (
        "SELECT count(*) FROM StormEvents WHERE DamageProperty > 0",
        "SELECT count(*) FROM StormEvents WHERE DeathsDirect > 0",
        "SELECT count(*) FROM StormEvents WHERE InjuriesDirect > 0",
        "SELECT count(*) FROM PopulationData WHERE Population > 5000000",
    ):
        n = con.sql(sql).fetchone()[0]
        assert 0 < n < total, f"{sql} selected {n} of {total} — not a discriminating filter"


@pytest.mark.skipif(not CORPUS.is_file(), reason="no corpus")
def test_fixture_backed_expectations_are_mostly_non_empty() -> None:
    """Ground truth itself must not be a wall of empty tables.

    If the emulator returned nothing for most fixture-backed cases, the fixture
    does not resemble the data the docs were written against, and every one of
    those cases would 'pass' against our equally-empty result.
    """
    cases = json.loads(CORPUS.read_text(encoding="utf-8"))["cases"]
    backed = [
        c for c in cases
        if not c.get("inline_input") and c.get("expected") is not None
    ]
    assert backed, "no fixture-backed expectations frozen"
    empty = [c["id"] for c in backed if not c["expected"].get("rows")]
    ratio = len(empty) / len(backed)
    assert ratio < 0.25, (
        f"{len(empty)}/{len(backed)} fixture-backed expectations are empty "
        f"({ratio:.0%}) — the fixture likely lacks the values the corpus filters on"
    )
