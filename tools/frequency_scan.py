#!/usr/bin/env python3
"""Frequency-scan the Kusto docs to rank KQL constructs by real-world usage.

Counts how often each tabular operator and scalar/aggregate function appears
across the ``kusto`` code samples in MicrosoftDocs/dataexplorer-docs, and reports
how many of those samples are *self-contained* (input inlined via ``datatable``,
``print`` or ``range``) and therefore directly runnable as test cases.

Produces statistics only — no doc prose or output tables are copied, so nothing
here is a redistribution of CC-BY content (see docs/licensing.md §1).

Usage:
    python tools/frequency_scan.py <path-to-dataexplorer-docs> [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------------------
# Lexical helpers
# ---------------------------------------------------------------------------

FENCE_RE = re.compile(r"^```[kK]usto\s*$(.*?)^```\s*$", re.MULTILINE | re.DOTALL)
LINE_COMMENT_RE = re.compile(r"//[^\n]*")
STRING_RE = re.compile(r"""'[^'\n]*'|"[^"\n]*\"""", re.VERBOSE)

# A tabular operator is what follows a pipe.
PIPED_RE = re.compile(r"\|\s*([a-zA-Z][a-zA-Z0-9]*(?:-[a-zA-Z][a-zA-Z0-9]*)*)")
# A function call is an identifier immediately followed by "(".
CALL_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")

# Sources that inline their own data — these samples are runnable as-is.
SELF_CONTAINED_RE = re.compile(r"\b(datatable|print|range)\b")

# Identifiers that look like calls but aren't KQL library functions.
NOT_FUNCTIONS = {
    "by", "on", "and", "or", "not", "in", "has", "contains", "where", "let",
    "if", "case", "typeof", "kind", "step", "from", "to", "hint", "with",
    "union", "join", "summarize", "extend", "project", "sort", "order", "top",
    "take", "limit", "distinct", "count", "mv", "expand", "apply", "parse",
    "declare", "pattern", "set", "alias", "database", "cluster", "table",
    "view", "materialize", "toscalar", "totable",
}

# Operators worth reporting even though they are also spelled like functions.
KNOWN_OPERATORS = {
    "where", "project", "project-away", "project-keep", "project-rename",
    "project-reorder", "project-by-names", "extend", "summarize", "join",
    "lookup", "union", "sort", "order", "top", "top-nested", "top-hitters",
    "take", "limit", "distinct", "count", "mv-expand", "mv-apply", "parse",
    "parse-where", "parse-kv", "make-series", "render", "sample",
    "sample-distinct", "search", "serialize", "getschema", "evaluate",
    "invoke", "facet", "find", "fork", "partition", "reduce", "scan",
    "consume", "as", "datatable", "print", "range", "externaldata",
    "materialize", "assert",
}

# kql-to-sql's declared support (from its operator checklist) — a cross-library
# "worth implementing" signal. See docs/kql-on-duckdb-landscape.md §3.1.
KQL_TO_SQL_UNSUPPORTED = {
    "facet", "find", "fork", "invoke", "macro-expand", "partition", "reduce",
    "project-by-names",
}


def strip_noise(code: str) -> str:
    """Remove comments and string literals so their contents don't get counted."""
    code = LINE_COMMENT_RE.sub(" ", code)
    return STRING_RE.sub(" '' ", code)


def extract_blocks(md: str) -> list[str]:
    return [m.group(1) for m in FENCE_RE.finditer(md)]


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

def scan(query_dir: Path) -> dict:
    op_counts: Counter[str] = Counter()
    fn_counts: Counter[str] = Counter()
    op_selfcontained: Counter[str] = Counter()
    fn_selfcontained: Counter[str] = Counter()
    op_pages: dict[str, set[str]] = {}
    fn_pages: dict[str, set[str]] = {}

    total_blocks = 0
    self_contained_blocks = 0
    pages_with_blocks = 0

    for md_path in sorted(query_dir.glob("*.md")):
        blocks = extract_blocks(md_path.read_text(encoding="utf-8", errors="replace"))
        if blocks:
            pages_with_blocks += 1
        page = md_path.name

        for raw in blocks:
            total_blocks += 1
            code = strip_noise(raw)
            inline = bool(SELF_CONTAINED_RE.search(code))
            if inline:
                self_contained_blocks += 1

            ops = {m.group(1) for m in PIPED_RE.finditer(code)}
            for op in ops:
                op_counts[op] += 1
                op_pages.setdefault(op, set()).add(page)
                if inline:
                    op_selfcontained[op] += 1

            fns = {
                m.group(1).lower()
                for m in CALL_RE.finditer(code)
                if m.group(1).lower() not in NOT_FUNCTIONS
            }
            for fn in fns:
                fn_counts[fn] += 1
                fn_pages.setdefault(fn, set()).add(page)
                if inline:
                    fn_selfcontained[fn] += 1

    return {
        "totals": {
            "pages_scanned": len(list(query_dir.glob("*.md"))),
            "pages_with_kusto_blocks": pages_with_blocks,
            "kusto_blocks": total_blocks,
            "self_contained_blocks": self_contained_blocks,
        },
        "operators": {
            name: {
                "blocks": n,
                "self_contained": op_selfcontained[name],
                "pages": len(op_pages[name]),
                "kql_to_sql_supported": name not in KQL_TO_SQL_UNSUPPORTED,
            }
            for name, n in op_counts.most_common()
        },
        "functions": {
            name: {
                "blocks": n,
                "self_contained": fn_selfcontained[name],
                "pages": len(fn_pages[name]),
            }
            for name, n in fn_counts.most_common()
        },
    }


# Candidate implementation waves, for closure-coverage analysis. A block is
# "covered" by a wave only if *every* operator it uses is in that wave — which is
# what actually makes a doc example runnable end to end.
WAVES: dict[str, set[str]] = {
    "wave1": {
        "where", "project", "project-away", "project-keep", "project-rename",
        "project-reorder", "extend", "summarize", "count", "join", "union",
        "sort", "order", "top", "take", "limit", "distinct", "as",
        "datatable", "print", "range",
    },
    "wave2": {
        "mv-expand", "mv-apply", "parse", "parse-where", "parse-kv",
        "make-series", "search", "getschema", "serialize", "sample",
        "sample-distinct", "lookup", "top-nested", "top-hitters",
    },
    "wave3": {"evaluate", "scan", "externaldata", "materialize", "consume", "assert"},
}


def coverage(query_dir: Path, waves: dict[str, set[str]]) -> list[tuple[str, int, int]]:
    """Cumulative share of blocks whose operators are fully inside each wave."""
    blocks: list[tuple[set[str], bool]] = []
    for md_path in sorted(query_dir.glob("*.md")):
        for raw in extract_blocks(
            md_path.read_text(encoding="utf-8", errors="replace")
        ):
            code = strip_noise(raw)
            ops = {m.group(1) for m in PIPED_RE.finditer(code)}
            blocks.append((ops, bool(SELF_CONTAINED_RE.search(code))))

    rows: list[tuple[str, int, int]] = []
    cumulative: set[str] = set()
    for name, ops in waves.items():
        cumulative |= ops
        full = sum(1 for b_ops, _ in blocks if b_ops <= cumulative)
        full_sc = sum(1 for b_ops, sc in blocks if sc and b_ops <= cumulative)
        rows.append((name, full, full_sc))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", type=Path, help="path to a dataexplorer-docs checkout")
    ap.add_argument("--json", type=Path, help="write full results as JSON")
    ap.add_argument("--top", type=int, default=40)
    args = ap.parse_args()

    query_dir = args.repo / "data-explorer" / "kusto" / "query"
    if not query_dir.is_dir():
        print(f"error: {query_dir} not found", file=sys.stderr)
        return 1

    try:
        commit = subprocess.check_output(  # noqa: S603 - literal argv, no shell
            ["git", "-C", str(args.repo), "rev-parse", "HEAD"], text=True
        ).strip()
    except Exception:  # noqa: BLE001 - no git, no repo, no HEAD: all mean "unknown"
        commit = "unknown"

    result = scan(query_dir)
    result["source"] = {
        "repo": "MicrosoftDocs/dataexplorer-docs",
        "commit": commit,
        "path": "data-explorer/kusto/query",
    }

    t = result["totals"]
    print(f"# KQL usage frequency — dataexplorer-docs @ {commit[:12]}\n")
    print(
        f"{t['pages_scanned']} pages, {t['pages_with_kusto_blocks']} with kusto blocks, "
        f"{t['kusto_blocks']} blocks total, "
        f"{t['self_contained_blocks']} self-contained "
        f"({100 * t['self_contained_blocks'] / max(t['kusto_blocks'], 1):.0f}%)\n"
    )

    print(f"## Top {args.top} tabular operators\n")
    print(f"{'operator':<22}{'blocks':>8}{'self-cont':>11}{'pages':>7}  kql-to-sql")
    for name, d in list(result["operators"].items())[: args.top]:
        flag = "yes" if d["kql_to_sql_supported"] else "NO (defer)"
        print(
            f"{name:<22}{d['blocks']:>8}{d['self_contained']:>11}{d['pages']:>7}  {flag}"
        )

    print(f"\n## Top {args.top} functions\n")
    print(f"{'function':<26}{'blocks':>8}{'self-cont':>11}{'pages':>7}")
    for name, d in list(result["functions"].items())[: args.top]:
        print(f"{name:<26}{d['blocks']:>8}{d['self_contained']:>11}{d['pages']:>7}")

    print("\n## Cumulative closure coverage by wave\n")
    print("(a block counts only if EVERY operator it uses is implemented)\n")
    print(f"{'through':<12}{'blocks':>9}{'% all':>8}{'self-cont':>11}{'% s-c':>8}")
    rows = coverage(query_dir, WAVES)
    result["coverage"] = {}
    for name, full, full_sc in rows:
        pct = 100 * full / max(t["kusto_blocks"], 1)
        pct_sc = 100 * full_sc / max(t["self_contained_blocks"], 1)
        print(f"{name:<12}{full:>9}{pct:>7.0f}%{full_sc:>11}{pct_sc:>7.0f}%")
        result["coverage"][name] = {
            "blocks_fully_covered": full,
            "self_contained_fully_covered": full_sc,
        }

    if args.json:
        args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
