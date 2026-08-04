"""L1 corpus regression — test-plan layer L1.

Parses every harvested case and asserts the pass count never regresses. This is
the guard on grammar changes: a re-sync with upstream, or a new local patch, must
not reduce how much real-world KQL we can parse.

The corpus **is** committed (~1 MB) so per-push CI stays hermetic — no docs
clone, no network. It is MIT-licensed code samples with per-case provenance
(docs/licensing.md §3). Regenerate with ``tools/harvest_docs.py``; these tests
skip if it is absent.
"""

from __future__ import annotations

import functools
import json
import os
import re
from pathlib import Path

import pytest

import duckdb_kql
from duckdb_kql import fixtures

# Established by the M0 spike + the in-subquery patch (docs/m0-grammar-spike.md).
# This number may only ever go UP.
BASELINE_PARSED = 1285

CORPUS = Path(
    os.environ.get("DUCKDB_KQL_CORPUS", "tests/cases/docs/docs-corpus.json")
)

pytestmark = pytest.mark.skipif(
    not CORPUS.is_file(),
    reason=f"corpus not found at {CORPUS}; run tools/harvest_docs.py to build it",
)


@functools.lru_cache(maxsize=1)
def _cases() -> tuple[dict, ...]:
    return tuple(json.loads(CORPUS.read_text(encoding="utf-8"))["cases"])


@functools.lru_cache(maxsize=1)
def _parse_failures() -> tuple[str, ...]:
    """Parse the whole corpus once; every test below reuses the result."""
    return tuple(c["id"] for c in _cases() if duckdb_kql.validate(c["kql"]))


def test_every_harvested_case_parses() -> None:
    """The harvester only emits blocks the parser accepted, so all must parse."""
    failures = _parse_failures()
    assert not failures, f"{len(failures)} harvested cases no longer parse: {failures[:10]}"


def test_parse_count_has_not_regressed() -> None:
    parsed = len(_cases()) - len(_parse_failures())
    assert parsed >= BASELINE_PARSED, (
        f"parse coverage regressed: {parsed} < {BASELINE_PARSED}. "
        "A grammar change reduced how much real KQL we can parse."
    )


def test_cases_carry_provenance() -> None:
    """Licensing depends on this (docs/licensing.md §11.6)."""
    for c in _cases():
        assert c["source"].startswith("https://github.com/MicrosoftDocs/")
        assert c["source_commit"]
        assert c["source_license"]


def test_no_expectations_harvested_from_docs() -> None:
    """Doc output tables are CC-BY prose and must never be copied in.

    Every expectation must be attributable to the emulator, which is what makes
    it *generated* rather than *copied* (docs/licensing.md §3). A case with an
    expectation but no oracle would mean prose leaked into the corpus.
    """
    for c in _cases():
        if c["expected"] is not None:
            assert c["oracle"] == "kusto-emulator", (
                f"{c['id']} has an expectation with no oracle — possible doc-sourced result"
            )


def test_self_contained_cases_are_a_meaningful_share() -> None:
    """Self-contained cases need no fixture, so they are the primary suite."""
    cases = _cases()
    inline = sum(1 for c in cases if c["inline_input"])
    assert inline / len(cases) > 0.5, "expected >50% of cases to be self-contained"


# --- frozen expectations (test-plan §5.2) ---------------------------------

#: Established by the first full emulator run. May only go UP.
BASELINE_FROZEN = 1036


def _frozen() -> tuple[dict, ...]:
    return tuple(c for c in _cases() if c.get("expected") is not None)


def test_frozen_expectation_count_has_not_regressed() -> None:
    assert len(_frozen()) >= BASELINE_FROZEN, (
        f"frozen expectations regressed: {len(_frozen())} < {BASELINE_FROZEN}"
    )


def test_frozen_expectations_are_well_formed() -> None:
    for c in _frozen():
        exp = c["expected"]
        assert isinstance(exp["columns"], list), c["id"]
        assert isinstance(exp["rows"], list), c["id"]
        # Every row must match the column arity, or comparison is meaningless.
        for row in exp["rows"]:
            assert len(row) == len(exp["columns"]), f"{c['id']}: ragged row"


def test_frozen_expectations_record_their_oracle() -> None:
    """Provenance: which engine produced this, and which image."""
    for c in _frozen():
        assert c["oracle"] == "kusto-emulator", c["id"]
        assert c.get("oracle_image", "").startswith("mcr.microsoft.com/"), c["id"]


def test_frozen_cases_supply_their_own_data() -> None:
    """A frozen expectation must be reproducible from what the repo contains.

    Either the case inlines its input (``datatable``/``print``/``range``) or it
    reads a table the fixture provides. Anything else was frozen against data
    nobody else can reconstruct, so re-running it later compares against a
    result that cannot be regenerated.
    """
    available = {t.lower() for t, _, _ in fixtures.TABLES}
    for c in _frozen():
        if c["inline_input"]:
            continue
        referenced = {t.lower() for t in _table_references(c["kql"])}
        unknown = referenced - available
        assert not unknown, (
            f"{c['id']} was frozen against table(s) the fixture does not "
            f"provide: {sorted(unknown)}"
        )


def test_self_contained_cases_do_not_read_a_fixture_table() -> None:
    """``inline_input`` claims "needs no fixture". It has to be true.

    A case that reads ``StormEvents`` is fixture-backed however much inline input
    it also builds, and four of them said otherwise because they happened to
    contain the word ``range``. That put them in the fixture-free freeze sweep,
    where they were frozen against whatever the emulator was holding at the time
    — a draft of the fixture — and nothing ever re-froze them against the
    committed one. The nightly drift lane found it months later; this finds it
    at harvest time, with no Docker (docs/test-plan.md §5.3).

    Deliberately broader than :func:`_table_references`: a mere mention is enough
    to disqualify the claim, so this cannot be fooled by a table reference in a
    position the conservative parser does not look at.
    """
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(t) for t, _, _ in fixtures.TABLES) + r")\b"
    )
    offenders = [
        c["id"] for c in _cases() if c["inline_input"] and pattern.search(c["kql"])
    ]
    assert not offenders, (
        f"cases claim to be self-contained but read a fixture table: {offenders} "
        "— re-run tools/harvest_docs.py, and re-freeze them with "
        "--include-fixture-cases"
    )


def _table_references(kql: str) -> set[str]:
    """Identifiers used in table position — the start of the query or a pipe.

    Deliberately conservative: it looks only where a table name can appear, so
    a column or function of the same name is not mistaken for a table.
    """
    names = set()
    for m in re.finditer(r"(?:^|\|\s*(?:join|union|lookup)\s*)\s*([A-Za-z_]\w*)", kql):
        name = m.group(1)
        if name.lower() not in _KQL_KEYWORDS:
            names.add(name)
    return names


#: Words that can start a query or follow join/union without being a table.
_KQL_KEYWORDS = {
    "let", "set", "declare", "alias", "print", "range", "datatable", "externaldata",
    "kind", "hint", "with", "materialize", "toscalar", "cluster", "database",
    "find", "search", "evaluate", "union", "join", "lookup", "inner", "innerunique",
    "leftouter", "rightouter", "fullouter", "leftsemi", "rightsemi", "leftanti",
    "rightanti", "restrict", "access",
}


def test_refused_cases_have_no_expectation() -> None:
    """A refusal must leave the expectation empty, never a partial result."""
    for c in _cases():
        if c.get("oracle_note"):
            assert c["expected"] is None, c["id"]
            assert c["oracle"] is None, c["id"]


def test_frozen_expectations_round_trip_through_comparison() -> None:
    """Every frozen result must compare equal to itself.

    Guards against a result shape the comparison engine cannot handle (e.g.
    unhashable payloads in the unordered path).
    """
    from duckdb_kql.comparison import ComparisonOptions, compare

    for c in _frozen():
        opts = ComparisonOptions.for_query(c["kql"])
        result = compare(c["expected"], c["expected"], opts)
        assert result.equal, f"{c['id']} does not compare equal to itself: {result}"
