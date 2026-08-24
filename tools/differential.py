#!/usr/bin/env python3
"""Ask the Kusto Emulator and this translator the same question, side by side.

Every semantic fact in ``docs/TRANSLATION.md`` was established this way, and the
harness got rewritten from scratch a dozen times because it lived in scratch
files. It lives here now.

    python tools/differential.py 'print x = countof("aaaa", "aa")'
    python tools/differential.py -f probes.txt

Or from a probe script::

    from tools.differential import Differential

    d = Differential()
    d.pair('print x = tostring(true)')
    d.pair('datatable(b:bool)[true] | project s = tostring(b)')
    raise SystemExit(d.report())

Lines that agree print with a leading blank; divergences print ``>>``. The exit
code is the number of divergences, so a probe script is usable in a loop.

**Three ways this harness has produced false results**, each of which cost real
time and each of which is defended against below:

1. **Comparing rendered forms.** The emulator returns a parsed `datetime`; we
   return an ISO string. They are the same value and compare unequal. Anything
   comparing raw rows must go through :func:`duckdb_kql.comparison.compare`, or
   at least know that it is comparing spellings — hence `raw=True` being opt-in.

   `compare` does not close the gap for **null strings**, and this one still
   bites: the emulator renders a null `string` as `''`, indistinguishable in the
   JSON from a genuine empty string, so a `('',)`-vs-`(None,)` pair is reported
   as a divergence whether or not one exists. Settle those by asking for
   `isnull(x)` and `strlen(x)` instead of `x` — a real null answers
   ``(True, null)`` and a real empty string ``(False, 0)``.
2. **Asking the wrong evaluator.** Kusto's constant folder and its row engine
   **disagree** — `print x = substring('abcdefg', long(null))` is `'abcdefg'`
   while the same expression over a `datatable` is null. A `print`-only sweep
   concluded the opposite of the truth and nearly shipped it. :meth:`both` asks
   each question both ways for exactly this reason.
3. **Self-inflicted syntax.** A needle containing `'` or `|` breaks the *query*,
   both engines refuse, and the pair scores as "agreeing on a rejection". Use
   :meth:`pair` with parameters, or read the printed text before believing a
   mutual refusal.

Needs a running emulator (``docker compose up -d kusto``); dev/CI only, never a
runtime dependency, and what may be published about it is constrained by its
EULA §2(d) — read ``docs/licensing.md`` before writing up anything you measure
with this.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from typing import Any

sys.path.insert(0, "src")

import duckdb_kql
from duckdb_kql.errors import KqlError
from duckdb_kql.oracle import EmulatorError, KustoEmulator

#: A Kusto semantic-error code, which is the useful part of a 400 body.
_SEM = re.compile(r"(SEM\d+)")


@dataclass
class Differential:
    """One emulator connection and one DuckDB connection, asked in parallel."""

    emulator: KustoEmulator = field(default_factory=KustoEmulator)
    #: Compare raw values rather than KQL-canonical ones. Off by default: the
    #: emulator and DuckDB spell datetimes and dynamics differently, and a raw
    #: comparison reports that spelling difference as a divergence.
    raw: bool = False
    #: Print every pair, not only the divergences.
    verbose: bool = True
    divergences: int = 0
    checked: int = 0

    # -- the two sides -----------------------------------------------------

    def kusto(self, kql: str) -> Any:
        """Ground truth, or a short refusal naming the SEM code.

        A *transport* failure raises rather than returning a refusal. It has to:
        a stopped container otherwise reads as "Kusto rejected this", every pair
        scores as a divergence or a mutual refusal, and a whole sweep looks like
        a finding. That is not hypothetical — it is what this function did the
        first time it ran here.
        """
        try:
            result = self.emulator.query(kql)
        except EmulatorError as exc:
            found = _SEM.search(str(exc))
            if found is None and "cannot reach" in str(exc):
                raise RuntimeError(
                    f"emulator unreachable — start it with "
                    f"`docker compose up -d kusto` and wait for healthy: {exc}"
                ) from exc
            return f"REJECTED {found.group(1) if found else str(exc)[:60]}"
        return {"columns": list(result.columns), "rows": [list(r) for r in result.rows]}

    def ours(self, kql: str) -> Any:
        """Our answer, distinguishing a KQL refusal from a leaked engine error.

        The distinction is the point: a `KqlError` is this project working as
        designed, while anything else is an internal exception reaching a
        caller, which principle 5 forbids regardless of the answer.
        """
        try:
            cursor = duckdb_kql.kql(self._con(), kql)
            return {
                "columns": [d[0] for d in cursor.description],
                "rows": [list(r) for r in cursor.fetchall()],
            }
        except KqlError as exc:
            return f"REFUSED {type(exc).__name__}: {str(exc).splitlines()[0][:60]}"
        except Exception as exc:  # noqa: BLE001 - a leak is the finding
            return f"LEAK {type(exc).__name__}: {str(exc).splitlines()[0][:60]}"

    def _con(self) -> Any:
        if not hasattr(self, "_connection"):
            self._connection = duckdb_kql.connect()
        return self._connection

    # -- asking ------------------------------------------------------------

    def pair(self, kql: str, label: str | None = None) -> bool:
        """Ask one question of both engines. True when they agree."""
        expected, actual = self.kusto(kql), self.ours(kql)
        agree = self._agree(expected, actual)
        self.checked += 1
        if not agree:
            self.divergences += 1
        if self.verbose or not agree:
            print(f"{'   ' if agree else '>> '}{label or kql}")
            print(f"     kusto {_render(expected)}")
            print(f"     ours  {_render(actual)}")
        return agree

    def both(
        self,
        expression: str,
        column: str = "c",
        declared: str = "c:string",
        value: str = "'abcdefg'",
    ) -> bool:
        """Ask one scalar expression of the **folder** and the **row engine**.

        Kusto answers some questions differently depending on which evaluator
        runs — `substring(s, long(null))` is `'abcdefg'` folded and null over
        rows. Anything about null handling or index clamping must be asked both
        ways, because the two answers are the two halves of the real rule.

        *expression* *must* reference *column*, and the assertion below enforces
        it. Wrapping a constant expression in a `datatable` does **not** reach
        the row engine — Kusto folds it just the same, so both halves ask the
        folder and agree, and the disagreement that matters stays invisible.
        That is not a hypothetical either: the first version of this method did
        exactly that, and reported four confident results from two questions.
        """
        assert re.search(rf"\b{re.escape(column)}\b", expression), (
            f"{expression!r} does not reference {column!r}, so the `datatable` "
            "form is constant-folded too and both halves ask the same evaluator"
        )
        constant = expression.replace(column, value)
        folded = self.pair(f"print x = {constant}", f"{expression}   [folded]")
        tabular = self.pair(
            f"datatable({declared})[{value}] | project x = {expression}",
            f"{expression}   [rows]",
        )
        return folded and tabular

    # -- comparing ---------------------------------------------------------

    def _agree(self, expected: Any, actual: Any) -> bool:
        # A refusal on both sides counts as agreement on *refusing*, which is
        # weaker than it looks — see hazard 3 in the module docstring.
        if isinstance(expected, str) or isinstance(actual, str):
            return isinstance(expected, str) and isinstance(actual, str) and (
                expected.startswith("REJECTED") and actual.startswith("REFUSED")
            )
        if self.raw:
            return expected == actual
        from duckdb_kql.comparison import compare

        return compare(expected, actual).equal

    def report(self) -> int:
        """Print the tally and return it as an exit code."""
        agreed = self.checked - self.divergences
        print(f"\n{agreed}/{self.checked} agree, {self.divergences} divergent")
        return self.divergences


def _render(value: Any) -> str:
    if isinstance(value, str):
        return value
    rows = value["rows"]
    shown = rows[:6]
    tail = f" … +{len(rows) - 6} rows" if len(rows) > 6 else ""
    return f"{value['columns']} {[tuple(r) for r in shown]}{tail}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("query", nargs="*", help="KQL to ask both engines")
    parser.add_argument("-f", "--file", help="file of queries, one per line")
    parser.add_argument(
        "--raw", action="store_true",
        help="compare raw values, not KQL-canonical ones (see the docstring)",
    )
    args = parser.parse_args(argv)

    queries = list(args.query)
    if args.file:
        with open(args.file, encoding="utf-8") as handle:
            queries += [ln.strip() for ln in handle if ln.strip() and ln[0] != "#"]
    if not queries:
        parser.error("give a query, or -f FILE")

    differential = Differential(raw=args.raw)
    for query in queries:
        differential.pair(query)
    return differential.report()


if __name__ == "__main__":
    raise SystemExit(main())
