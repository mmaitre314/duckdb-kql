#!/usr/bin/env python3
"""Generate ground-truth expectations by running cases on the Kusto Emulator.

This is the freeze half of the freeze-and-compare workflow (``docs/test-plan.md``
§5.2): the emulator produces the expected results **once**, they are frozen into
the case files, and per-push CI then compares against the frozen values with no
Docker, no network, and no model in the loop.

Only **self-contained** cases are handled by default — those that inline their
own input via ``datatable`` / ``print`` / ``range`` and need no fixture. The M0
scan found 810 of them, which is the bulk of the corpus.

Dev/CI only; never part of the shipped runtime (``docs/licensing.md`` §5).

Usage:
    docker compose up -d kusto
    python tools/regen_expectations.py                  # all self-contained cases
    python tools/regen_expectations.py --limit 50       # a sample
    python tools/regen_expectations.py --only-missing   # skip cases already frozen
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from duckdb_kql.comparison import (
    ComparisonOptions,
    compare,
    is_nondeterministic,
)
from duckdb_kql.oracle import DEFAULT_ENDPOINT, EmulatorError, KustoEmulator

DEFAULT_CORPUS = Path("tests/cases/docs/docs-corpus.json")


def regen(
    corpus_path: Path,
    kusto: KustoEmulator,
    *,
    limit: int | None = None,
    only: set[str] | None = None,
    only_missing: bool = False,
    include_fixture_cases: bool = False,
    image_digest: str | None = None,
    check: bool = False,
) -> dict:
    """Freeze expectations from the emulator, or (``check=True``) verify them.

    In check mode nothing is written: each case is re-run and compared against
    what is already frozen, so a changed emulator image or a changed harvest
    shows up as drift instead of quietly rewriting the ground truth our whole
    test suite is judged against.
    """
    doc = json.loads(corpus_path.read_text(encoding="utf-8"))
    cases = doc["cases"]

    stats = {"attempted": 0, "frozen": 0, "rejected": 0, "skipped": 0, "drifted": 0}
    rejections: list[dict] = []
    drifts: list[dict] = []

    def checkpoint() -> None:
        """Persist progress mid-sweep.

        A full fixture-backed sweep is hundreds of emulator round-trips and takes
        many minutes. Writing only at the end means one timeout throws all of it
        away, which is how a 20-minute job becomes a 20-minute job you run twice.
        """
        if not check:
            corpus_path.write_text(json.dumps(doc, indent=1), encoding="utf-8")

    for case in cases:
        if only is not None and case["id"] not in only:
            stats["skipped"] += 1
            continue
        if not case.get("inline_input") and not include_fixture_cases:
            stats["skipped"] += 1
            continue
        if only_missing and case.get("expected") is not None:
            stats["skipped"] += 1
            continue
        if limit is not None and stats["attempted"] >= limit:
            break

        if check:
            if case.get("expected") is None:
                # Nothing frozen to drift from.
                stats["skipped"] += 1
                continue
            if is_nondeterministic(case["kql"]):
                # rand()/now() cases return different values every run. Diffing
                # them reports drift on every single nightly, which is how a
                # drift lane becomes noise everyone learns to ignore.
                stats["skipped"] += 1
                continue

        stats["attempted"] += 1
        try:
            result = kusto.query(case["kql"])
        except EmulatorError as e:
            # The emulator refusing a case is information, not a failure of this
            # script: it means the query is invalid, uses an unsupported
            # feature, or needs data we did not mount. Record and move on.
            stats["rejected"] += 1
            rejections.append({"id": case["id"], "error": str(e)[:300]})
            if check:
                # A case that used to freeze and now errors IS drift.
                stats["drifted"] += 1
                drifts.append({"id": case["id"], "was": "frozen", "now": f"rejected: {e}"[:200]})
                continue
            case["expected"] = None
            case["oracle"] = None
            case["oracle_note"] = "emulator rejected this query"
            continue

        fresh = result.to_dict()
        if check:
            # Use the same comparison the acceptance suite uses, not `!=`. Row
            # order is meaningless without a terminal sort (R10) and floats need
            # tolerance, so a byte-comparison flags differences that are not
            # drift at all.
            verdict = compare(case["expected"], fresh, ComparisonOptions.for_query(case["kql"]))
            if not verdict.equal:
                stats["drifted"] += 1
                drifts.append({"id": case["id"], "was": case["expected"], "now": fresh,
                               "why": str(verdict)[:200]})
            continue

        case["expected"] = fresh
        case["oracle"] = "kusto-emulator"
        if image_digest:
            case["oracle_image"] = image_digest
        case.pop("oracle_note", None)
        stats["frozen"] += 1

        if stats["frozen"] % 50 == 0:
            checkpoint()

    if check:
        return {"stats": stats, "rejections": rejections, "drifts": drifts}

    doc.setdefault("oracle", {})
    doc["oracle"]["source"] = "kusto-emulator"
    if image_digest:
        doc["oracle"]["image"] = image_digest

    corpus_path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return {"stats": stats, "rejections": rejections, "drifts": drifts}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only-missing", action="store_true")
    ap.add_argument(
        "--only",
        nargs="+",
        metavar="CASE_ID",
        help="re-freeze only these case ids. A diagnosed drift usually needs a "
        "handful of cases put right; rewriting the whole corpus to do it buries "
        "the three lines that matter in a thousand-line diff nobody can review",
    )
    ap.add_argument(
        "--include-fixture-cases",
        action="store_true",
        help="also run cases that need a mounted sample database",
    )
    ap.add_argument("--image-digest", default=None, help="record the pinned image digest")
    ap.add_argument("--wait", type=float, default=300.0)
    ap.add_argument(
        "--check",
        action="store_true",
        help="verify frozen expectations against the emulator without writing "
        "(exit 3 on drift) — the nightly CI lane",
    )
    args = ap.parse_args()

    if not args.corpus.is_file():
        print(f"error: corpus not found: {args.corpus}", file=sys.stderr)
        print("hint: run tools/harvest_docs.py first", file=sys.stderr)
        return 1

    kusto = KustoEmulator(endpoint=args.endpoint)
    print(f"waiting for emulator at {args.endpoint} ...")
    try:
        kusto.wait_until_ready(timeout=args.wait)
    except EmulatorError as e:
        print(f"error: {e}", file=sys.stderr)
        print("hint: docker compose up -d kusto", file=sys.stderr)
        return 2
    print("emulator ready\n")

    r = regen(
        args.corpus,
        kusto,
        limit=args.limit,
        only=set(args.only) if args.only else None,
        only_missing=args.only_missing,
        include_fixture_cases=args.include_fixture_cases,
        image_digest=args.image_digest,
        check=args.check,
    )
    s = r["stats"]

    if args.check:
        print(f"checked {s['attempted']} frozen expectation(s)")
        print(f"  drifted  {s['drifted']}")
        print(f"  skipped  {s['skipped']} (nothing frozen, or needs fixture)")
        if r["drifts"]:
            print("\nDRIFT — the emulator no longer produces the frozen result:")
            for x in r["drifts"][:20]:
                print(f"  {x['id']}")
                print(f"    frozen: {str(x['was'])[:160]}")
                print(f"    now:    {str(x['now'])[:160]}")
            print(
                "\nGround truth changed. Do NOT just re-freeze: work out whether the "
                "emulator image moved, the fixture changed, the harvest changed, or "
                "ADX behaviour actually differs. Record the finding in the drift log "
                "(docs/test-plan.md §5.3); a real KQL/DuckDB divergence also belongs "
                "in the catalog (§6)."
            )
            return 3
        print("\nno drift — frozen expectations still match the emulator")
        return 0

    print(f"attempted {s['attempted']}")
    print(f"  frozen    {s['frozen']}")
    print(f"  rejected  {s['rejected']}")
    print(f"skipped     {s['skipped']} (needs fixture, or already frozen)")

    if r["rejections"]:
        print("\nfirst rejections:")
        for x in r["rejections"][:10]:
            print(f"  {x['id']:<38} {x['error'][:90]}")

    print(f"\nwrote {args.corpus}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
