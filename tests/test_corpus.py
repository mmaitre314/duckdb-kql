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
from pathlib import Path

import pytest

import duckdb_kql

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

    Expectations come from the emulator (docs/licensing.md §3).
    """
    for c in _cases():
        assert c["expected"] is None, f"{c['id']} has a doc-sourced expectation"
        assert c["oracle"] is None


def test_self_contained_cases_are_a_meaningful_share() -> None:
    """Self-contained cases need no fixture, so they are the primary suite."""
    cases = _cases()
    inline = sum(1 for c in cases if c["inline_input"])
    assert inline / len(cases) > 0.5, "expected >50% of cases to be self-contained"
