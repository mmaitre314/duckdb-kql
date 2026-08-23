#!/usr/bin/env python3
"""Measure coverage of a KQL dialect profile.

A *profile* is a published list of the KQL surface some product supports —
currently Azure Monitor's transformation dialect
(``tests/profiles/azure-monitor.json``). Tracking against one turns "how much
KQL do we handle?" from an unanswerable question into a number.

Coverage is **measured, not asserted**: every entry carries a `probe`, a KQL
snippet that has to translate *and* execute on DuckDB. A function present in
the registry but broken in practice counts as missing, which is the point — the
registry is a claim, the probe is the evidence.

Usage:
    python tools/check_profile.py                 # summary
    python tools/check_profile.py --missing       # just the gaps
    python tools/check_profile.py --json          # machine-readable
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import duckdb_kql
from duckdb_kql import engine
from duckdb_kql.errors import KqlError

DEFAULT_PROFILE = Path("tests/profiles/azure-monitor.json")


def _connection():
    import duckdb

    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    # The profile's probes use `source` as the input table, mirroring Azure
    # Monitor's own convention.
    con.execute("CREATE TABLE source(TimeGenerated TIMESTAMP, Message VARCHAR)")
    return con


def probe(con, kql: str) -> tuple[bool, str]:
    """Run one probe. Returns ``(supported, reason)``."""
    try:
        sql = duckdb_kql.to_sql(kql, schema=engine.schema(con))
    except KqlError as e:
        return False, f"{type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001 - a crash is a result to report, not to raise
        return False, f"crash: {type(e).__name__}: {e}"
    try:
        con.sql(sql).fetchall()
    except Exception as e:  # noqa: BLE001 - a crash is a result to report, not to raise
        return False, f"sql: {type(e).__name__}: {str(e).splitlines()[0]}"
    return True, ""


def _entries(profile: dict):
    """Flatten the profile into ``(group, entry)`` pairs."""
    for group in ("tabular_operators", "string_operators"):
        for entry in profile.get(group, []):
            yield group, entry
    for category, entries in profile.get("scalar_functions", {}).items():
        for entry in entries:
            yield f"scalar_functions.{category}", entry


def evaluate(profile: dict) -> dict:
    con = _connection()
    results: dict[str, list[dict]] = {}
    for group, entry in _entries(profile):
        supported, reason = probe(con, entry["probe"])
        results.setdefault(group, []).append(
            {**entry, "supported": supported, "reason": reason}
        )
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    ap.add_argument("--missing", action="store_true", help="list only the gaps")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    if not args.profile.is_file():
        print(f"error: profile not found: {args.profile}", file=sys.stderr)
        return 1

    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    results = evaluate(profile)

    if args.as_json:
        print(json.dumps(results, indent=1))
        return 0

    total = supported = 0
    print(f"{profile['title']}")
    print(f"  source: {profile['source']}")
    print(f"  doc dated {profile['source_date']}, captured {profile['captured']}\n")

    for group, entries in results.items():
        ok = [e for e in entries if e["supported"]]
        gaps = [e for e in entries if not e["supported"]]
        total += len(entries)
        supported += len(ok)
        if args.missing and not gaps:
            continue
        print(f"  {group:34} {len(ok):3}/{len(entries):<3}")
        for e in gaps:
            print(f"      MISSING  {e['name']:24} {e['reason'][:64]}")

    pct = 100.0 * supported / total if total else 0.0
    print(f"\n  TOTAL {supported}/{total} probes pass ({pct:.0f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
