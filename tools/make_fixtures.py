#!/usr/bin/env python3
"""Write (or verify) the StormEvents fixture.

The generator itself lives in ``duckdb_kql.fixtures`` so the emulator loader,
the DuckDB loader, and the schema all sit next to the data that defines them.

Usage:
    python tools/make_fixtures.py            # write the CSV
    python tools/make_fixtures.py --check    # verify the committed CSV matches
    python tools/make_fixtures.py --load     # also ingest into a running emulator
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from duckdb_kql import fixtures
from duckdb_kql.oracle import DEFAULT_ENDPOINT, EmulatorError, KustoEmulator


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=fixtures.OUT)
    ap.add_argument(
        "--check",
        action="store_true",
        help="verify the committed file matches this generator (exit 1 if not)",
    )
    ap.add_argument(
        "--load",
        action="store_true",
        help="create and ingest the table into a running Kusto Emulator",
    )
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    args = ap.parse_args()

    rows = fixtures.generate()

    if args.check:
        if not args.out.is_file():
            print(f"error: {args.out} missing — run tools/make_fixtures.py", file=sys.stderr)
            return 1
        tmp = args.out.with_suffix(".check.tmp")
        fixtures.write(tmp, rows)
        same = fixtures.checksum(tmp) == fixtures.checksum(args.out)
        tmp.unlink()
        if not same:
            print(
                f"error: {args.out} does not match the generator.\n"
                "Frozen expectations were produced from the committed file, so "
                "regenerating it invalidates them. Either restore the file or "
                "re-freeze deliberately (tools/regen_expectations.py).",
                file=sys.stderr,
            )
            return 1
        print(f"{args.out} matches the generator ({fixtures.checksum(args.out)[:16]}…)")
        return 0

    if not args.check:
        fixtures.write(args.out, rows)
        fixtures.write_population()
        print(
            f"wrote {args.out}  rows={len(rows)}  "
            f"sha256={fixtures.checksum(args.out)[:16]}…"
        )
        print(
            f"wrote {fixtures.POPULATION_OUT}  "
            f"rows={len(fixtures.generate_population())}"
        )

    if args.load:
        kusto = KustoEmulator(endpoint=args.endpoint)
        print(f"waiting for emulator at {args.endpoint} ...")
        try:
            kusto.wait_until_ready(timeout=300)
            counts = fixtures.load_emulator(kusto)
        except EmulatorError as e:
            print(f"error: {e}", file=sys.stderr)
            print("hint: docker compose up -d kusto", file=sys.stderr)
            return 2
        for table, n in counts.items():
            print(f"ingested {n} rows into {table}")
        if counts.get(fixtures.TABLE) != fixtures.ROWS:
            print(
                f"error: expected {fixtures.ROWS} rows in {fixtures.TABLE}, got "
                f"{counts.get(fixtures.TABLE)} — expectations frozen from this "
                "would be wrong",
                file=sys.stderr,
            )
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
