#!/usr/bin/env python3
"""Measure the things `docs/maintenance/` says to keep an eye on.

Report-only, by design. Nothing here fails a build: the numbers are a *compass*
for deciding what to maintain next, and a metric that gates CI stops being a
measurement and becomes a target to satisfy (Goodhart). The two ratchets that
*are* enforced live where they belong — in the tests
(``BASELINE_PASSING`` and friends), which this script only reads back.

    python tools/maintenance_metrics.py            # human-readable report
    python tools/maintenance_metrics.py --json     # same numbers, machine-readable
    python tools/maintenance_metrics.py --top 15   # longer hotspot/offender lists

Stdlib only, plus `git`, so it runs in the minimal CI image. The mapping-surface
section additionally imports the package's registries; it is skipped, not fatal,
when the ANTLR runtime is absent (Layer 0 is still an install).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

#: Hand-written source only. The vendored parser is regenerated, never edited
#: (grammar/UPSTREAM.md), so its size and shape say nothing about our
#: maintenance burden — counting it would swamp every structural metric.
GENERATED = ("_antlr", "_version.py")

#: A module past this is hard to hold in one head, and hard to review as a diff.
MODULE_LOC_BUDGET = 800
#: SmartBear/Cisco: defect discovery falls off past 200-400 changed lines.
DIFF_LOC_BUDGET = 400
#: Past this a function stops fitting on a screen and starts hiding branches.
FUNCTION_LOC_BUDGET = 60
#: Branch count (if/for/while/except/and/or) inside one function body.
FUNCTION_BRANCH_BUDGET = 12

#: Paths whose churn is mechanical: generated artifacts and frozen corpora.
#: Counting them as "changed lines" makes every corpus refresh look like a
#: 20,000-line commit nobody could have reviewed.
MECHANICAL = (
    "src/duckdb_kql/_antlr/",
    "tests/cases/",
    "tests/fixtures/",
    "docs/kql-support.md",
    "demo/",
)


def _run(*args: str) -> str:
    return subprocess.run(
        args, cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout


def _python_files(*roots: str) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        for path in sorted((ROOT / root).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if any(marker in rel for marker in GENERATED):
                continue
            out.append(path)
    return out


def _loc(path: Path) -> int:
    """Lines that are neither blank nor a whole-line comment."""
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            count += 1
    return count


# ---------------------------------------------------------------------------
# Correctness surface — what the project actually promises
# ---------------------------------------------------------------------------


def ratchets() -> dict[str, int | None]:
    """The baselines the test suite refuses to let regress.

    These are the only numbers here that are *enforced*; a refactor that moves
    one down has changed behaviour, whatever its commit message says.
    """
    wanted = {
        "behaviour_cases_passing": ("tests/test_behavior.py", "BASELINE_PASSING"),
        "azure_monitor_probes": ("tests/test_profile_azure_monitor.py", "BASELINE_SUPPORTED"),
        "corpus_queries_parsed": ("tests/test_corpus.py", "BASELINE_PARSED"),
        "frozen_expectations": ("tests/test_corpus.py", "BASELINE_FROZEN"),
    }
    found: dict[str, int | None] = {}
    for label, (rel, name) in wanted.items():
        path = ROOT / rel
        match = (
            re.search(rf"^{name} = (\d+)$", path.read_text(encoding="utf-8"), re.MULTILINE)
            if path.is_file()
            else None
        )
        found[label] = int(match.group(1)) if match else None
    return found


def mapping_surface() -> dict[str, Any]:
    """Registry rows by kind — the coverage surface, and how it is expressed.

    Support is meant to grow by *adding data*, not by adding branches: rows in
    `translate/functions.py` rather than special forms in `translate/`. The
    ratio of hand-written translate lines to registry rows is the cheapest
    early warning that the architecture is drifting back into code.
    """
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from duckdb_kql.translate.functions import (  # noqa: PLC0415 - optional import
            AGGREGATE_FUNCTIONS,
            BINARY_OPERATORS,
            SCALAR_FUNCTIONS,
        )
    except ImportError as exc:  # pragma: no cover - depends on the environment
        return {"available": False, "reason": str(exc)}

    kinds = Counter(spec.kind for spec in SCALAR_FUNCTIONS.values())
    rows = len(SCALAR_FUNCTIONS) + len(AGGREGATE_FUNCTIONS) + len(BINARY_OPERATORS)
    translate_loc = sum(_loc(p) for p in _python_files("src/duckdb_kql/translate"))
    registry_loc = _loc(ROOT / "src/duckdb_kql/translate/functions.py")

    return {
        "available": True,
        "registry_rows": rows,
        "scalar_functions": len(SCALAR_FUNCTIONS),
        "aggregate_functions": len(AGGREGATE_FUNCTIONS),
        "binary_operators": len(BINARY_OPERATORS),
        "scalar_by_kind": dict(sorted(kinds.items())),
        # A UDF is the last resort (TRANSLATION.md §7): it leaves DuckDB's
        # engine, so it is slow and it is ours to keep correct. The count going
        # up is a design signal, not a coverage win.
        "udf_mappings": kinds.get("udf", 0),
        "rows_citing_an_r_rule": sum(1 for s in SCALAR_FUNCTIONS.values() if s.rules),
        "rows_with_a_gotcha_note": sum(1 for s in SCALAR_FUNCTIONS.values() if s.note),
        "translate_loc": translate_loc,
        "hand_written_loc_per_row": round((translate_loc - registry_loc) / rows, 1)
        if rows
        else None,
        "deliberate_refusals": _refusal_count(),
    }


def _refusal_count() -> int | None:
    """Constructs listed as refused in the support-matrix generator."""
    section = ROOT / "docs/kql-support.md"
    if not section.is_file():
        return None
    body = section.read_text(encoding="utf-8")
    block = re.search(
        r"^## Deliberate refusals$(.*?)(^## |\Z)", body, re.MULTILINE | re.DOTALL
    )
    return len(re.findall(r"^\| `", block.group(1), re.MULTILINE)) if block else None


# ---------------------------------------------------------------------------
# Structure — how hard the code is to change
# ---------------------------------------------------------------------------


@dataclass
class Function:
    qualname: str
    file: str
    line: int
    loc: int
    branches: int


def _branches(node: ast.AST) -> int:
    kinds = (ast.If, ast.For, ast.While, ast.ExceptHandler, ast.IfExp, ast.Match)
    total = 0
    for child in ast.walk(node):
        if isinstance(child, kinds):
            total += 1
        elif isinstance(child, ast.BoolOp):
            total += len(child.values) - 1
    return total


def functions(paths: list[Path]) -> list[Function]:
    out: list[Function] = []
    for path in paths:
        rel = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken tree is not our report
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            end = node.end_lineno or node.lineno
            out.append(
                Function(node.name, rel, node.lineno, end - node.lineno + 1, _branches(node))
            )
    return out


def structure(top: int) -> dict[str, Any]:
    src = _python_files("src")
    sizes = sorted(((_loc(p), p.relative_to(ROOT).as_posix()) for p in src), reverse=True)
    funcs = functions(src)
    long_funcs = sorted(funcs, key=lambda f: f.loc, reverse=True)
    branchy = sorted(funcs, key=lambda f: f.branches, reverse=True)
    return {
        "source_loc": sum(loc for loc, _ in sizes),
        "modules": len(sizes),
        "modules_over_budget": [
            f"{name} ({loc})" for loc, name in sizes if loc > MODULE_LOC_BUDGET
        ],
        "largest_modules": [f"{name} ({loc})" for loc, name in sizes[:top]],
        "functions": len(funcs),
        "functions_over_loc_budget": sum(1 for f in funcs if f.loc > FUNCTION_LOC_BUDGET),
        "functions_over_branch_budget": sum(
            1 for f in funcs if f.branches > FUNCTION_BRANCH_BUDGET
        ),
        "longest_functions": [
            f"{f.file}:{f.line} {f.qualname} ({f.loc} lines)" for f in long_funcs[:top]
        ],
        "branchiest_functions": [
            f"{f.file}:{f.line} {f.qualname} ({f.branches} branches)" for f in branchy[:top]
        ],
    }


def hotspots(top: int) -> list[str]:
    """Churn x size: where a refactor buys the most (Tornhill's hotspots).

    A big file nobody touches is not a problem worth solving. A big file that
    changes every week is where the next defect will be.
    """
    log = _run("git", "log", "--format=", "--name-only", "--", "src")
    churn = Counter(line for line in log.splitlines() if line.strip())
    scored = []
    for rel, commits in churn.items():
        path = ROOT / rel
        if not path.is_file() or any(marker in rel for marker in GENERATED):
            continue
        loc = _loc(path)
        scored.append((commits * loc, rel, commits, loc))
    scored.sort(reverse=True)
    return [
        f"{rel} — {commits} commits x {loc} lines = {score}"
        for score, rel, commits, loc in scored[:top]
    ]


# ---------------------------------------------------------------------------
# Suppressions — the debt that hides in plain sight
# ---------------------------------------------------------------------------


def suppressions() -> dict[str, Any]:
    """Every place a check was told to look away.

    None of these are wrong on their own; the repo has good reasons for most of
    them, written next to each. What matters is the *trend*: a suppression added
    without a reason is a checker that has quietly stopped checking.
    """
    text = {
        p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8")
        for p in _python_files("src", "tools", "tests", "demo")
    }
    noqa: Counter[str] = Counter()
    unexplained: list[str] = []
    for rel, body in text.items():
        lines = body.splitlines()
        for i, line in enumerate(lines, 1):
            match = re.search(r"#\s*noqa:\s*([A-Z]+[0-9]+)(.*)$", line)
            if not match:
                continue
            noqa[match.group(1)] += 1
            # A reason counts whether it sits after the code or on the line
            # above -- both are used here, and both are readable.
            above = lines[i - 2].strip() if i >= 2 else ""
            if not match.group(2).strip(" -") and not above.startswith("#"):
                unexplained.append(f"{rel}:{i}")

    # Test-suppression counts come from tests/ alone: a tool that *mentions*
    # `xfail` (this one does) is not a skipped test.
    suite = "\n".join(
        body for rel, body in text.items() if rel.startswith("tests/")
    )
    joined = "\n".join(text.values())
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return {
        "noqa_total": sum(noqa.values()),
        "noqa_by_rule": dict(noqa.most_common()),
        "noqa_without_a_reason": len(unexplained),
        # Ruff's own answer to "is this suppression load-bearing?". A directive
        # it calls unused names a rule the enabled `select` set never runs, so
        # the reason written next to it is enforced by nothing.
        "noqa_suppressing_an_unselected_rule": _unused_noqa(),
        "type_ignore": len(re.findall(r"#\s*type:\s*ignore", joined)),
        "skipped_tests": len(re.findall(r"@pytest\.mark\.skip\b", suite)),
        "conditionally_skipped_tests": len(re.findall(r"skipif|importorskip", suite)),
        "xfail_tests": len(re.findall(r"\bxfail\b", suite)),
        "mypy_module_overrides": pyproject.count("[[tool.mypy.overrides]]"),
        "ruff_per_file_ignores": len(
            re.findall(r'^"[^"]+"\s*=\s*\[', pyproject, re.MULTILINE)
        ),
    }


def _unused_noqa() -> int | None:
    """RUF100 count, or None when ruff is not installed."""
    # `--extend-select`, never `--select`: the latter REPLACES the configured
    # rule set, so every rule a directive names would look unselected and all
    # of them would be reported unused. That overcounts (125 vs 113 here)
    # and, worse, hides which directives are load-bearing.
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--extend-select", "RUF100",
         "--statistics", "src", "tools", "tests", "demo"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    match = re.search(r"^(\d+)\s+RUF100", proc.stdout, re.MULTILINE)
    if match:
        return int(match.group(1))
    return 0 if proc.returncode == 0 else None


def tests() -> dict[str, Any]:
    src_loc = sum(_loc(p) for p in _python_files("src"))
    test_files = _python_files("tests")
    test_loc = sum(_loc(p) for p in test_files)
    count = sum(
        1
        for f in functions(test_files)
        if f.qualname.startswith("test_")
    )
    return {
        "test_files": len(test_files),
        "test_functions": count,
        "test_loc": test_loc,
        "test_to_source_loc": round(test_loc / src_loc, 2) if src_loc else None,
    }


# ---------------------------------------------------------------------------
# Flow — is change arriving in reviewable pieces?
# ---------------------------------------------------------------------------


def diff_discipline(commits: int) -> dict[str, Any]:
    """Changed lines per commit, generated artifacts excluded.

    The review literature's 200-400 line ceiling is not advice about tidiness:
    past it, defect discovery drops and the diff gets approved rather than read.
    """
    raw = _run("git", "log", f"-{commits}", "--numstat", "--format=%H")
    sizes: list[int] = []
    current: int | None = None
    for line in raw.splitlines():
        if re.fullmatch(r"[0-9a-f]{40}", line.strip()):
            if current is not None:
                sizes.append(current)
            current = 0
            continue
        parts = line.split("\t")
        if len(parts) != 3 or current is None:
            continue
        added, deleted, path = parts
        if added == "-" or any(path.startswith(m) for m in MECHANICAL):
            continue
        current += int(added) + int(deleted)
    if current is not None:
        sizes.append(current)
    if not sizes:
        return {"commits": 0}
    ordered = sorted(sizes)
    return {
        "commits": len(sizes),
        "median_changed_lines": int(median(ordered)),
        "p90_changed_lines": ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))],
        "max_changed_lines": ordered[-1],
        "over_review_budget": sum(1 for s in sizes if s > DIFF_LOC_BUDGET),
        "review_budget": DIFF_LOC_BUDGET,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass
class Section:
    title: str
    body: dict[str, Any] = field(default_factory=dict)


def collect(top: int, commits: int) -> dict[str, Any]:
    return {
        "correctness_ratchets": ratchets(),
        "mapping_surface": mapping_surface(),
        "structure": structure(top),
        "hotspots": hotspots(top),
        "suppressions": suppressions(),
        "tests": tests(),
        "diff_discipline": diff_discipline(commits),
    }


def render(data: dict[str, Any]) -> str:
    lines: list[str] = ["duckdb-kql maintenance metrics", ""]
    for section, body in data.items():
        lines.append(section.replace("_", " ").upper())
        if isinstance(body, list):
            lines.extend(f"  - {item}" for item in body)
            lines.append("")
            continue
        for key, value in body.items():
            label = key.replace("_", " ")
            if isinstance(value, list):
                lines.append(f"  {label}: {len(value)}")
                lines.extend(f"      - {item}" for item in value)
            elif isinstance(value, dict):
                lines.append(f"  {label}:")
                lines.extend(f"      {k}: {v}" for k, v in value.items())
            else:
                lines.append(f"  {label}: {value}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a report")
    parser.add_argument("--top", type=int, default=8, help="entries per ranked list")
    parser.add_argument(
        "--commits", type=int, default=50, help="commits to measure diff size over"
    )
    args = parser.parse_args()

    data = collect(args.top, args.commits)
    print(json.dumps(data, indent=2) if args.json else render(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
