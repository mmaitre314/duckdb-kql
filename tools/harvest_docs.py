#!/usr/bin/env python3
"""Harvest KQL test cases from the Kusto documentation.

Extracts the ``kusto`` code samples from a pinned ``dataexplorer-docs`` checkout
and writes them as case files for the acceptance corpus (schema:
``docs/test-plan.md`` §4.1).

**Licensing (docs/licensing.md §3).** Only the *queries* are harvested — those
are code samples under MIT. Documentation prose, including the rendered example
**output tables**, is CC-BY-4.0 and is deliberately **not** copied. Expected
results are generated independently by ``tools/regen_expectations.py`` running
the queries against the Kusto Emulator.

Usage:
    python tools/harvest_docs.py <path-to-dataexplorer-docs> -o tests/cases/docs
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from duckdb_kql import fixtures, validate  # noqa: E402

FENCE_RE = re.compile(r"^```[kK]usto\s*$(.*?)^```\s*$", re.MULTILINE | re.DOTALL)
LINE_COMMENT_RE = re.compile(r"//[^\n]*")
STRING_RE = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"")
PIPED_RE = re.compile(r"\|\s*([a-zA-Z][a-zA-Z0-9]*(?:-[a-zA-Z][a-zA-Z0-9]*)*)")
CALL_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
INLINES_INPUT_RE = re.compile(r"\b(datatable|print|range)\b")

# A query that reads a fixture table is NOT self-contained, however much inline
# input it also builds. Four cases used `range` *and* `StormEvents`; the old
# `inline`-means-`range` rule put them in the fixture-free sweep, where they were
# frozen against whatever the emulator happened to hold — a draft of the fixture
# — and were never re-frozen when it settled (docs/test-plan.md §5.3).
FIXTURE_TABLE_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t, _, _ in fixtures.TABLES) + r")\b"
)


def is_self_contained(code: str) -> bool:
    """True when the case needs no fixture to reproduce its own result."""
    return bool(INLINES_INPUT_RE.search(code)) and not FIXTURE_TABLE_RE.search(code)

# ---------------------------------------------------------------------------
# Block filtering
#
# The docs tag some fenced blocks ```kusto that are not queries at all — output
# tables, JSON config, SQL, prose (docs/m0-grammar-spike.md §3). Carrying those
# into the corpus would mean permanently-failing junk cases.
#
# Filtering happens in two stages: unambiguous *shapes* are rejected here, and
# everything else is decided by the parser in harvest(). Do not add heuristics
# to this function — an earlier version guessed "bare expression" by shape and
# silently discarded ~27 valid scalar-function cases such as `asin(0.5)`, which
# are legal KQL queries.
# ---------------------------------------------------------------------------

def reject_reason(block: str) -> str | None:
    """Return why *block* is definitely not a KQL query, or None.

    Only *unambiguous* shapes are rejected here. Anything else is handed to the
    parser, which is a far better arbiter than a heuristic — a bare scalar
    expression like ``asin(0.5)`` looks like prose but is a perfectly legal KQL
    query, and rejecting those by shape silently threw away useful
    scalar-function cases.
    """
    s = block.strip()
    if not s:
        return "empty"
    # Rendered output tables:  "result\n-------\n100"
    if re.match(r"^[A-Za-z_][\w ]*\s*\n\s*-{3,}", s):
        return "output-table"
    # JSON config blobs
    if s[0] in "[{" and not s.startswith("[]"):
        return "json"
    # SQL, not KQL
    if s.startswith("--") or re.match(r"^\s*SELECT\b", s, re.IGNORECASE):
        return "sql"
    # A fragment that starts mid-pipeline
    if s.startswith("|"):
        return "fragment"
    if s.startswith("Kusto:"):
        return "prose"
    return None


def strip_noise(code: str) -> str:
    return STRING_RE.sub(" '' ", LINE_COMMENT_RE.sub(" ", code))


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def harvest(repo: Path, out_dir: Path, commit: str) -> dict:
    query_dir = repo / "data-explorer" / "kusto" / "query"
    out_dir.mkdir(parents=True, exist_ok=True)

    stats: Counter[str] = Counter()
    cases: list[dict] = []
    unparsed: list[dict] = []

    for md_path in sorted(query_dir.glob("*.md")):
        page = md_path.name
        text = md_path.read_text(encoding="utf-8", errors="replace")
        for i, m in enumerate(FENCE_RE.finditer(text)):
            block = m.group(1).strip()
            stats["blocks_seen"] += 1

            reason = reject_reason(block)
            if reason:
                stats[f"rejected:{reason}"] += 1
                continue

            # The parser is the arbiter for everything else. A block that does
            # not parse is not emitted as a case — it would be permanently red
            # for reasons unrelated to translation. Counted, not hidden.
            if validate(block):
                stats["rejected:unparsed"] += 1
                unparsed.append({"page": page, "kql": block})
                continue

            code = strip_noise(block)
            inline = is_self_contained(code)
            operators = sorted({mm.group(1) for mm in PIPED_RE.finditer(code)})
            functions = sorted({mm.group(1).lower() for mm in CALL_RE.finditer(code)})

            stats["harvested"] += 1
            stats["self_contained" if inline else "needs_fixture"] += 1

            cases.append(
                {
                    "id": f"{slugify(page[:-3])}-{i:02d}",
                    "source": (
                        "https://github.com/MicrosoftDocs/dataexplorer-docs/blob/"
                        f"{commit}/data-explorer/kusto/query/{page}"
                    ),
                    "source_commit": commit,
                    "source_license": "MIT (LICENSE-CODE)",
                    "kql": block,
                    "inline_input": inline,
                    "tags": {"operators": operators, "functions": functions},
                    # Expectations come from the emulator, never from the docs.
                    "expected": None,
                    "status": "xfail",
                    "oracle": None,
                }
            )

    shard = out_dir / "docs-corpus.json"
    shard.write_text(
        json.dumps(
            {
                "schema": 1,
                "source": {
                    "repo": "MicrosoftDocs/dataexplorer-docs",
                    "commit": commit,
                    "license": "code samples MIT; prose CC-BY-4.0 (not harvested)",
                },
                "cases": cases,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    if unparsed:
        (out_dir / "unparsed.json").write_text(
            json.dumps({"commit": commit, "blocks": unparsed}, indent=1), encoding="utf-8"
        )

    return {"stats": stats, "path": shard, "count": len(cases), "unparsed": len(unparsed)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=Path("tests/cases/docs"))
    args = ap.parse_args()

    if not (args.repo / "data-explorer" / "kusto" / "query").is_dir():
        print(f"error: no query dir under {args.repo}", file=sys.stderr)
        return 1

    try:
        commit = subprocess.check_output(
            ["git", "-C", str(args.repo), "rev-parse", "HEAD"], text=True
        ).strip()
    except Exception:
        commit = "unknown"

    r = harvest(args.repo, args.out, commit)
    s = r["stats"]
    print(f"docs @ {commit[:12]}")
    print(f"  blocks seen      {s['blocks_seen']}")
    print(f"  harvested        {s['harvested']}")
    print(f"    self-contained {s['self_contained']}")
    print(f"    needs fixture  {s['needs_fixture']}")
    rejected = {k: v for k, v in s.items() if k.startswith("rejected:")}
    print(f"  rejected         {sum(rejected.values())}")
    for k, v in sorted(rejected.items(), key=lambda kv: -kv[1]):
        print(f"    {k.split(':', 1)[1]:<16} {v}")
    print(f"\nwrote {r['count']} cases to {r['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
