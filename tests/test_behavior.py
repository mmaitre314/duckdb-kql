"""L3 acceptance — translated KQL vs ground truth from the Kusto Emulator.

This is the layer that actually proves translation correctness: run each corpus
case through the full pipeline (parse → lower → SQL → DuckDB) and compare the
result against the expectation the *real KQL engine* produced
(``docs/test-plan.md`` §5.2).

Cases outside the current wave raise ``KqlUnsupportedError`` and are counted as
not-yet-covered rather than failures — partial coverage is expected. What is
**not** tolerated is a case that translates and then produces the wrong answer.
"""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path

import pytest

import duckdb_kql
from duckdb_kql import engine, fixtures
from duckdb_kql.comparison import ComparisonOptions, compare, is_nondeterministic
from duckdb_kql.errors import KqlError

duckdb = pytest.importorskip("duckdb")

CORPUS = Path(os.environ.get("DUCKDB_KQL_CORPUS", "tests/cases/docs/docs-corpus.json"))

pytestmark = pytest.mark.skipif(not CORPUS.is_file(), reason=f"no corpus at {CORPUS}")

#: Cases that translate AND match ground truth. May only go UP.
BASELINE_PASSING = 246

#: Cases we translate but knowingly get wrong, with the reason. This is an
#: **admission of a bug**, not a waiver: each entry is a real KQL↔DuckDB gap
#: recorded in ``docs/test-plan.md`` §6 and meant to be drained, not to grow.
#:
#: Entries are checked for staleness — a case listed here that starts passing
#: fails the build, so the list cannot rot into a silent allowlist.
KNOWN_DIVERGENCES: dict[str, str] = {
    "base64-decode-tostring-function-01": (
        "base64_decode_tostring of bytes that are NOT valid UTF-8: KQL returns "
        "an empty string, DuckDB's BLOB->VARCHAR cast returns the bytes with "
        "\\x escapes. DuckDB has no UTF-8 validity predicate to switch on, and "
        "sniffing for '\\x' in the output would misfire on a legitimate "
        "backslash. Valid UTF-8 — the case that matters — is correct."
    ),
}


@functools.lru_cache(maxsize=1)
def _connection():
    """One DuckDB connection with the fixture tables loaded.

    The emulator froze its expectations against exactly these rows, so our side
    must see exactly these rows too — that equality is the entire basis for
    comparing the two engines.
    """
    con = duckdb.connect()
    # R8 — KQL datetimes are UTC, and DuckDB reads the session TimeZone when
    # casting offset-less strings. duckdb_kql.kql() sets this; the harness calls
    # to_sql() directly, so it must set it itself.
    con.execute("SET TimeZone='UTC'")
    fixtures.load_duckdb(con)
    return con


@functools.lru_cache(maxsize=1)
def _schema() -> dict:
    return engine.schema(_connection())


@functools.lru_cache(maxsize=1)
def _frozen_cases() -> tuple[dict, ...]:
    data = json.loads(CORPUS.read_text(encoding="utf-8"))["cases"]
    return tuple(c for c in data if c.get("expected") is not None)


def _run(case: dict) -> tuple[str, str]:
    """Return ``(status, detail)`` for one case.

    status: ``pass`` | ``unsupported`` | ``sql_error`` | ``mismatch``
    """
    if is_nondeterministic(case["kql"]):
        # now()/rand() have no stable answer, so the frozen expectation cannot
        # be reproduced by anyone — comparing it would test nothing.
        return "nondeterministic", ""

    try:
        # `join` needs base-table columns to reproduce KQL's column renaming;
        # without the schema every join case would report as unsupported.
        sql = duckdb_kql.to_sql(case["kql"], schema=_schema())
    except KqlError as e:
        return "unsupported", type(e).__name__
    except Exception as e:  # noqa: BLE001
        # The public API must never leak an internal exception; surface these
        # rather than letting one abort the whole sweep.
        return "crash", f"{type(e).__name__}: {e}"

    try:
        # A `declare query_parameters` query renders placeholders; their values
        # travel beside the SQL rather than inside it (see duckdb_kql.params).
        params = getattr(sql, "parameters", None)
        rel = _connection().sql(str(sql), params=params) if params else _connection().sql(str(sql))
        actual = {
            "columns": list(rel.columns),
            "rows": [list(r) for r in rel.fetchall()],
        }
    except Exception as e:  # noqa: BLE001 - any DuckDB failure is a real signal
        return "sql_error", f"{type(e).__name__}: {e}"

    opts = ComparisonOptions.for_query(case["kql"])
    result = compare(case["expected"], actual, opts)
    if result.equal:
        return "pass", ""
    if case["id"] in KNOWN_DIVERGENCES:
        return "known_divergence", str(result)
    return "mismatch", str(result)


@functools.lru_cache(maxsize=1)
def _results() -> dict[str, list[tuple[str, str]]]:
    buckets: dict[str, list[tuple[str, str]]] = {
        "pass": [], "unsupported": [], "sql_error": [], "mismatch": [],
        "crash": [], "nondeterministic": [], "known_divergence": [],
    }
    for case in _frozen_cases():
        status, detail = _run(case)
        buckets[status].append((case["id"], detail))
    return buckets


def test_translated_cases_match_ground_truth() -> None:
    """Anything we translate must agree with the real KQL engine.

    A mismatch is the worst possible outcome — a query that runs and silently
    returns the wrong answer — so it fails the build.
    """
    mismatches = _results()["mismatch"]
    detail = "\n".join(f"  {cid}: {msg[:160]}" for cid, msg in mismatches[:15])
    assert not mismatches, f"{len(mismatches)} cases disagree with ground truth:\n{detail}"


def test_translation_does_not_produce_invalid_sql() -> None:
    """If we claim to translate a query, the SQL must at least execute.

    Emitting SQL that DuckDB rejects means the mapping is wrong; the honest
    outcome for an unsupported construct is KqlUnsupportedError.
    """
    errors = _results()["sql_error"]
    detail = "\n".join(f"  {cid}: {msg[:160]}" for cid, msg in errors[:15])
    assert not errors, f"{len(errors)} cases produced invalid SQL:\n{detail}"


def test_no_internal_exceptions_leak() -> None:
    """A construct we can't handle must raise KqlError, never a raw TypeError."""
    crashes = _results()["crash"]
    detail = "\n".join(f"  {cid}: {msg[:160]}" for cid, msg in crashes[:15])
    assert not crashes, f"{len(crashes)} cases leaked internal exceptions:\n{detail}"


def test_known_divergences_are_not_stale() -> None:
    """A divergence that no longer diverges must leave the list.

    Without this the list degrades into an allowlist that hides regressions:
    a case could start failing for a brand-new reason and stay silent.
    """
    r = _results()
    still_diverging = {cid for cid, _ in r["known_divergence"]}
    seen = still_diverging | {cid for cid, _ in r["mismatch"]}
    resolved = sorted(set(KNOWN_DIVERGENCES) & {cid for cid, _ in r["pass"]})
    vanished = sorted(set(KNOWN_DIVERGENCES) - seen - set(resolved))

    assert not resolved, (
        f"{len(resolved)} known divergences now match ground truth — remove them "
        f"from KNOWN_DIVERGENCES: {resolved}"
    )
    assert not vanished, (
        "known divergences no longer reach the comparison (they now fail earlier, "
        f"or the id changed): {vanished}"
    )


def test_coverage_has_not_regressed() -> None:
    passing = len(_results()["pass"])
    assert passing >= BASELINE_PASSING, (
        f"Wave-1 coverage regressed: {passing} < {BASELINE_PASSING} cases "
        "matching ground truth"
    )


def test_report_coverage(capsys: pytest.CaptureFixture) -> None:
    """Not an assertion — prints the coverage breakdown for visibility."""
    r = _results()
    total = sum(len(v) for v in r.values())
    with capsys.disabled():
        print(
            f"\n  L3 coverage: {len(r['pass'])}/{total} match ground truth"
            f" | {len(r['unsupported'])} not yet supported"
            f" | {len(r['sql_error'])} sql errors"
            f" | {len(r['mismatch'])} mismatches"
            f" | {len(r['crash'])} crashes"
            f" | {len(r['known_divergence'])} known divergences"
            f" | {len(r['nondeterministic'])} nondeterministic (skipped)"
        )
