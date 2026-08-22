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
import time
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
#:
#: The **floor**, not the typical count. One corpus case (`in-cs-operator-04`)
#: contains a `top` whose cut falls inside a tie, so it sometimes matches the
#: frozen expectation and sometimes is classified nondeterministic by the
#: reproducibility check in `_run` — the count is legitimately 280 or 281. A
#: ratchet set at the higher number would fail on the runs that get the other.
#:
#: Went 277 -> 280 with the `has_any`/`has_all` rewrite: a null needle matches
#: anything and `has_all` over an empty needle set is true, neither of which the
#: old `list_filter` / `bool_and` shapes could express.
BASELINE_PASSING = 280

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
    "reverse-function-01": (
        "reverse() of a datetime *column*. KQL reverses the value's .NET string "
        "form, so the rendering has to be KQL's — and this picks it from what "
        "can be inferred statically. A datetime literal or a datetime-returning "
        "call is rendered right; a bare column reaching reverse() through an "
        "earlier `print` is not, because column types are not carried across "
        "pipeline stages. Every other type in the case matches, and so does a "
        "datetime whose type is visible. Drained by type inference, not by a "
        "wider cast: casting unconditionally would agree for numbers and "
        "disagree for datetimes, which is this same bug wearing a bigger hat."
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
    # Warm DuckDB's first-query overhead — measured at ~200ms, and otherwise
    # charged to whichever case happens to run first, which pytest-randomly
    # reshuffles every run. It would land on `SLOWEST_QUERY_BUDGET` as noise.
    con.execute("SELECT count(*) FROM StormEvents WHERE State = $s", {"s": "X"})
    return con


@functools.lru_cache(maxsize=1)
def _schema() -> dict:
    return engine.schema(_connection())


@functools.lru_cache(maxsize=1)
def _frozen_cases() -> tuple[dict, ...]:
    data = json.loads(CORPUS.read_text(encoding="utf-8"))["cases"]
    return tuple(c for c in data if c.get("expected") is not None)


#: Case id -> seconds DuckDB spent on it, filled in as the sweep runs.
ELAPSED: dict[str, float] = {}

#: How long one corpus query may take against the 5,000-row fixture.
#:
#: The corpus is the only place a generated-SQL performance bug is visible at
#: all — everything else here runs on a handful of rows, where a mapping that is
#: 300x too slow still finishes instantly and looks correct. It has happened:
#: `has_any` over a literal array put the term pattern inside a `list_filter`
#: lambda, so RE2 recompiled it per (row, needle) and one query took **28
#: seconds**. Nothing failed. It was 63% of this suite's runtime and would have
#: been far worse on a real table.
#:
#: Set well above the current worst (~40ms) and well below anything anyone would
#: notice as a bug, so it catches the class without flapping on a slow runner.
SLOWEST_QUERY_BUDGET = 1.0


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

    started = time.perf_counter()
    try:
        actual = _execute(sql)
    except Exception as e:  # noqa: BLE001 - any DuckDB failure is a real signal
        return "sql_error", f"{type(e).__name__}: {e}"
    finally:
        # Timed here rather than in `_execute` so the reproducibility re-runs,
        # which only happen for a case that already failed, stay out of it.
        ELAPSED[case["id"]] = time.perf_counter() - started

    opts = ComparisonOptions.for_query(case["kql"])
    result = compare(case["expected"], actual, opts)
    if result.equal:
        return "pass", ""
    if case["id"] in KNOWN_DIVERGENCES:
        return "known_divergence", str(result)
    if not _is_reproducible(sql, actual):
        # Our own answer changes between runs, so there is nothing here to
        # compare against a frozen expectation. This is R10 arriving through
        # the data rather than the query text: `top N by X` where more than N
        # rows tie at the cut-off picks a different N each time, and if that
        # choice feeds a later operator the whole result moves with it.
        # `is_nondeterministic` cannot see it — the query text is identical to
        # one over data with no tie, which *is* reproducible and must stay
        # compared.
        return "nondeterministic", str(result)
    return "mismatch", str(result)


def _execute(sql: object) -> dict:
    # A `declare query_parameters` query renders placeholders; their values
    # travel beside the SQL rather than inside it (see duckdb_kql.params).
    params = getattr(sql, "parameters", None)
    con = _connection()
    rel = con.sql(str(sql), params=params) if params else con.sql(str(sql))
    return {"columns": list(rel.columns), "rows": [list(r) for r in rel.fetchall()]}


def _is_reproducible(sql: object, first: dict, attempts: int = 3) -> bool:
    """Whether re-running *sql* gives the same answer it just gave.

    Only asked when a case has already failed, so the cost is paid on the few
    that need it. Several attempts rather than one: a tie does not resolve
    differently on *every* run, and calling an unstable query stable would put
    the flake back.
    """
    for _ in range(attempts):
        try:
            if _execute(sql) != first:
                return False
        except Exception:  # noqa: BLE001 - an inconsistent failure is instability too
            return False
    return True


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


def test_no_query_is_pathologically_slow() -> None:
    """A correct answer that takes 28 seconds is still a bug.

    This is the only test in the suite that can see one: every other fixture is
    small enough that a mapping doing 60,000 regex compilations per query still
    returns instantly. See :data:`SLOWEST_QUERY_BUDGET`.
    """
    _results()  # populates ELAPSED
    over = sorted(
        ((d, cid) for cid, d in ELAPSED.items() if d > SLOWEST_QUERY_BUDGET),
        reverse=True,
    )
    detail = "\n".join(f"  {d:7.2f}s  {cid}" for d, cid in over[:10])
    assert not over, (
        f"{len(over)} queries took longer than {SLOWEST_QUERY_BUDGET}s against a "
        f"5,000-row fixture — the generated SQL, not the data, is the problem:\n"
        f"{detail}"
    )


def test_report_coverage(capsys: pytest.CaptureFixture) -> None:
    """Not an assertion — prints the coverage breakdown for visibility."""
    r = _results()
    total = sum(len(v) for v in r.values())
    slowest = max(ELAPSED.items(), key=lambda kv: kv[1], default=("-", 0.0))
    with capsys.disabled():
        print(
            f"\n  L3 slowest query: {slowest[1] * 1000:.0f}ms ({slowest[0]})"
            f" | budget {SLOWEST_QUERY_BUDGET * 1000:.0f}ms"
            f"\n  L3 coverage: {len(r['pass'])}/{total} match ground truth"
            f" | {len(r['unsupported'])} not yet supported"
            f" | {len(r['sql_error'])} sql errors"
            f" | {len(r['mismatch'])} mismatches"
            f" | {len(r['crash'])} crashes"
            f" | {len(r['known_divergence'])} known divergences"
            f" | {len(r['nondeterministic'])} nondeterministic (skipped)"
        )
