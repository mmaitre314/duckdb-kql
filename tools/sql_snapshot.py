#!/usr/bin/env python3
"""Snapshot the SQL this translator emits for the whole frozen corpus.

This is the gate that makes "behaviour-preserving" a claim instead of a hope.
The test suite proves the cases somebody thought of; the snapshot proves the
other ~1,200. A refactor that does not change behaviour produces a
**byte-identical** snapshot, and any line of the diff is either a bug it just
introduced or a behaviour change that does not belong in a refactoring commit
(``docs/maintenance/README.md``, rule 1).

    python tools/sql_snapshot.py --out before.txt   # on the base commit
    # ... refactor ...
    python tools/sql_snapshot.py --compare before.txt

A **refusal is behaviour too**, so it is recorded like any other output: turning
a `KqlUnsupportedError` into plausible-looking SQL is precisely the change this
project exists to prevent, and here it shows up as a diff line.

Layer 0 only — translation, no database — so this runs anywhere `to_sql` does.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests/cases"


def _cases() -> list[tuple[str, str]]:
    """Every ``(id, kql)`` in the frozen corpus, sorted and de-duplicated."""
    found: dict[str, str] = {}
    for path in sorted(CORPUS.rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for case in payload.get("cases", []):
            kql = case.get("kql")
            if kql:
                found[case.get("id") or f"{path.stem}:{len(found)}"] = kql
    return sorted(found.items())


def snapshot() -> str:
    """One block per case: the emitted SQL, or the refusal it raised."""
    from duckdb_kql import to_sql  # noqa: PLC0415 - keeps --help import-free
    from duckdb_kql.errors import KqlError  # noqa: PLC0415

    lines: list[str] = []
    for case_id, kql in _cases():
        lines.append(f"### {case_id}")
        try:
            lines.append(str(to_sql(kql)))
        except KqlError as exc:
            # The class *and* the message: a refusal that stops naming what it
            # refused has regressed even though it still refuses.
            lines.append(f"!! {type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 - an unexpected raise is a finding
            lines.append(f"?? {type(exc).__name__}: {exc}")
        lines.append("")
    return "\n".join(lines)


def compare(baseline: Path, current: str, limit: int) -> int:
    import difflib  # noqa: PLC0415 - only needed on this path

    before = baseline.read_text(encoding="utf-8").splitlines()
    after = current.splitlines()
    diff = list(difflib.unified_diff(before, after, str(baseline), "current", lineterm=""))
    if not diff:
        print(f"identical: {len(_cases())} cases translate exactly as before")
        return 0
    changed = sum(
        1
        for line in diff
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    )
    print("\n".join(diff[: limit * 8]))
    if len(diff) > limit * 8:
        print(f"... {len(diff) - limit * 8} more diff lines")
    print(f"\nCHANGED: {changed} lines differ. A refactor may not change any of them.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, help="write the snapshot here instead of stdout")
    parser.add_argument(
        "--compare", type=Path, help="diff against a snapshot; non-zero if it moved"
    )
    parser.add_argument("--limit", type=int, default=10, help="cases of diff to print")
    args = parser.parse_args()

    if not CORPUS.is_dir():
        print(
            f"{CORPUS} is absent — the frozen corpus is excluded from the sdist, "
            "so run this from a git checkout.",
            file=sys.stderr,
        )
        return 2

    text = snapshot()
    if args.compare:
        return compare(args.compare, text, args.limit)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(_cases())} cases)")
        return 0
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
