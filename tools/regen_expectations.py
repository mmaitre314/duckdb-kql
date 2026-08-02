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

from duckdb_kql.oracle import DEFAULT_ENDPOINT, EmulatorError, KustoEmulator  # noqa: E402

DEFAULT_CORPUS = Path("tests/cases/docs/docs-corpus.json")


def regen(
    corpus_path: Path,
    kusto: KustoEmulator,
    *,
    limit: int | None = None,
    only_missing: bool = False,
    include_fixture_cases: bool = False,
    image_digest: str | None = None,
) -> dict:
    doc = json.loads(corpus_path.read_text(encoding="utf-8"))
    cases = doc["cases"]

    stats = {"attempted": 0, "frozen": 0, "rejected": 0, "skipped": 0}
    rejections: list[dict] = []

    for case in cases:
        if not case.get("inline_input") and not include_fixture_cases:
            stats["skipped"] += 1
            continue
        if only_missing and case.get("expected") is not None:
            stats["skipped"] += 1
            continue
        if limit is not None and stats["attempted"] >= limit:
            break

        stats["attempted"] += 1
        try:
            result = kusto.query(case["kql"])
        except EmulatorError as e:
            # The emulator refusing a case is information, not a failure of this
            # script: it means the query is invalid, uses an unsupported
            # feature, or needs data we did not mount. Record and move on.
            stats["rejected"] += 1
            rejections.append({"id": case["id"], "error": str(e)[:300]})
            case["expected"] = None
            case["oracle"] = None
            case["oracle_note"] = "emulator rejected this query"
            continue

        case["expected"] = result.to_dict()
        case["oracle"] = "kusto-emulator"
        if image_digest:
            case["oracle_image"] = image_digest
        case.pop("oracle_note", None)
        stats["frozen"] += 1

    doc.setdefault("oracle", {})
    doc["oracle"]["source"] = "kusto-emulator"
    if image_digest:
        doc["oracle"]["image"] = image_digest

    corpus_path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return {"stats": stats, "rejections": rejections}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only-missing", action="store_true")
    ap.add_argument(
        "--include-fixture-cases",
        action="store_true",
        help="also run cases that need a mounted sample database",
    )
    ap.add_argument("--image-digest", default=None, help="record the pinned image digest")
    ap.add_argument("--wait", type=float, default=300.0)
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
        only_missing=args.only_missing,
        include_fixture_cases=args.include_fixture_cases,
        image_digest=args.image_digest,
    )
    s = r["stats"]
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
