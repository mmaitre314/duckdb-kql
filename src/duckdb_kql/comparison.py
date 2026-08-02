"""Result comparison — the compare half of freeze-and-compare.

Implements the comparison semantics specified in ``docs/test-plan.md`` §4.2.
Naive equality produces false failures, because KQL itself does not promise as
much as a strict comparison would assume:

* **Row order is undefined** unless the query ends in a terminal ``sort``/``top``.
* **Types differ by name** across engines (``long`` vs ``BIGINT``) while meaning
  the same thing.
* **Some aggregates are approximate** — ``dcount`` is HLL-based and
  ``percentile`` is an estimate (``docs/TRANSLATION.md`` R11), so exact equality
  is simply the wrong assertion.
* **Doc examples are often truncated** ("the first 5 rows").

Getting these wrong in either direction is costly: too strict floods the suite
with false failures, too loose hides real divergence.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

__all__ = ["compare", "ComparisonResult", "ComparisonOptions", "is_order_significant"]

# KQL type name -> canonical bucket. DuckDB names map to the same buckets so
# that "long" and "BIGINT" compare equal.
_TYPE_BUCKETS = {
    # integers
    "int": "int", "long": "int", "int32": "int", "int64": "int",
    "integer": "int", "bigint": "int", "smallint": "int", "tinyint": "int",
    "hugeint": "int", "ubigint": "int",
    # floats
    "real": "float", "double": "float", "float": "float", "decimal": "float",
    "numeric": "float",
    # strings
    "string": "string", "varchar": "string", "text": "string", "char": "string",
    # booleans
    "bool": "bool", "boolean": "bool",
    # temporal
    "datetime": "datetime", "timestamp": "datetime", "date": "datetime",
    "timespan": "timespan", "interval": "timespan", "time": "timespan",
    # structured
    "dynamic": "dynamic", "json": "dynamic", "struct": "dynamic",
    "list": "dynamic", "map": "dynamic", "array": "dynamic",
    "guid": "guid", "uuid": "guid",
}

# Aggregates whose results are estimates, not exact values (R11).
APPROXIMATE_FUNCTIONS = frozenset(
    {"dcount", "dcountif", "percentile", "percentiles", "percentilew", "tdigest"}
)

_TERMINAL_ORDERING_RE = re.compile(
    r"\|\s*(sort|order|top|top-nested|top-hitters)\b", re.IGNORECASE
)


def normalize_type(name: str) -> str:
    """Map an engine-specific type name onto a canonical bucket."""
    base = re.sub(r"\(.*\)$", "", (name or "").strip()).lower()
    base = base.removeprefix("system.")
    base = base.removesuffix("[]")
    return _TYPE_BUCKETS.get(base, base)


def is_order_significant(kql: str) -> bool:
    """True when the query's own text makes row order meaningful.

    A KQL result is only ordered if the query says so. Absent a terminal
    ``sort``/``top``, both engines may return rows in any order and a
    position-sensitive comparison would be asserting something KQL never
    promised (R10).
    """
    matches = list(_TERMINAL_ORDERING_RE.finditer(kql))
    if not matches:
        return False
    # Only ordering in the *final* segment survives; a sort followed by
    # summarize, join, or union has its order discarded.
    tail = kql[matches[-1].end():]
    return not re.search(
        r"\|\s*(summarize|join|union|distinct|make-series|count|lookup)\b",
        tail,
        re.IGNORECASE,
    )


def uses_approximate_function(kql: str) -> bool:
    lowered = kql.lower()
    return any(f"{fn}(" in lowered for fn in APPROXIMATE_FUNCTIONS)


@dataclass
class ComparisonOptions:
    """Knobs for one comparison. Defaults follow ``docs/test-plan.md`` §4.2."""

    ordered: bool = False
    rel_tolerance: float = 1e-9
    abs_tolerance: float = 1e-12
    #: Allow *actual* to contain the documented prefix and more (docs truncate).
    allow_prefix: bool = False
    #: Compare column names, not just values.
    check_column_names: bool = True
    #: Compare canonical column types.
    check_column_types: bool = False

    @classmethod
    def for_query(cls, kql: str, **overrides: Any) -> ComparisonOptions:
        """Derive sensible options from the query text itself."""
        opts = cls(ordered=is_order_significant(kql))
        if uses_approximate_function(kql):
            # An HLL estimate can legitimately differ by a few percent.
            opts.rel_tolerance = 0.05
        for k, v in overrides.items():
            setattr(opts, k, v)
        return opts


@dataclass
class ComparisonResult:
    equal: bool
    differences: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:  # pragma: no cover - trivial
        return self.equal

    def __str__(self) -> str:  # pragma: no cover - trivial
        return "equal" if self.equal else "; ".join(self.differences[:5])


def _values_equal(a: Any, b: Any, opts: ComparisonOptions) -> bool:
    if a is None or b is None:
        return a is None and b is None
    # bool is a subclass of int in Python, so this must be checked before the
    # numeric branch — and a bool must never compare equal to a non-bool, or
    # True would equal 2 (bool(2) is True). KQL keeps bool and int distinct.
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if math.isnan(a) and math.isnan(b):
            return True
        return math.isclose(a, b, rel_tol=opts.rel_tolerance, abs_tol=opts.abs_tolerance)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_values_equal(x, y, opts) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_values_equal(a[k], b[k], opts) for k in a)
    return _scalar_key(a) == _scalar_key(b)


def _scalar_key(v: Any) -> Any:
    """A comparable, hashable key for a scalar."""
    if v is None:
        return None
    if isinstance(v, str):
        # Trailing whitespace is not semantically meaningful in these results.
        return v.strip()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, (list, dict, tuple)):
        import json

        return json.dumps(v, sort_keys=True, default=str)
    return v


def _row_key(row: list[Any]) -> tuple:
    return tuple(_scalar_key(v) for v in row)


def compare(
    expected: dict | None,
    actual: dict | None,
    opts: ComparisonOptions | None = None,
) -> ComparisonResult:
    """Compare two result tables under KQL-appropriate semantics.

    Each table is ``{"columns": [...], "rows": [[...], ...]}``; ``column_types``
    is optional and only consulted when ``check_column_types`` is set.
    """
    opts = opts or ComparisonOptions()
    diffs: list[str] = []

    if expected is None or actual is None:
        if expected is None and actual is None:
            return ComparisonResult(True)
        return ComparisonResult(False, ["one side is missing a result table"])

    exp_cols = list(expected.get("columns", []))
    act_cols = list(actual.get("columns", []))
    exp_rows = [list(r) for r in expected.get("rows", [])]
    act_rows = [list(r) for r in actual.get("rows", [])]

    if len(exp_cols) != len(act_cols):
        diffs.append(f"column count {len(exp_cols)} != {len(act_cols)}")
        return ComparisonResult(False, diffs)

    if opts.check_column_names and exp_cols != act_cols:
        diffs.append(f"column names {exp_cols} != {act_cols}")

    if opts.check_column_types:
        e = [normalize_type(t) for t in expected.get("column_types", [])]
        a = [normalize_type(t) for t in actual.get("column_types", [])]
        if e and a and e != a:
            diffs.append(f"column types {e} != {a}")

    if opts.allow_prefix:
        if len(act_rows) < len(exp_rows):
            diffs.append(f"expected at least {len(exp_rows)} rows, got {len(act_rows)}")
        act_rows = act_rows[: len(exp_rows)]
    elif len(exp_rows) != len(act_rows):
        diffs.append(f"row count {len(exp_rows)} != {len(act_rows)}")
        return ComparisonResult(False, diffs)

    if opts.ordered:
        for i, (er, ar) in enumerate(zip(exp_rows, act_rows)):
            if not all(_values_equal(x, y, opts) for x, y in zip(er, ar)):
                diffs.append(f"row {i}: {er!r} != {ar!r}")
                if len(diffs) > 8:
                    diffs.append("... (further differences suppressed)")
                    break
    else:
        # Multiset comparison: same rows, any order.
        from collections import Counter

        try:
            exp_c = Counter(_row_key(r) for r in exp_rows)
            act_c = Counter(_row_key(r) for r in act_rows)
        except TypeError:  # pragma: no cover - unhashable payload
            diffs.append("rows are not hashable; use ordered comparison")
            return ComparisonResult(False, diffs)

        # Exact multiset match handles the common case cheaply. Anything left
        # over may still match *within tolerance* — hashing is exact, so
        # without this second pass every approximate aggregate (dcount,
        # percentile — R11) would falsely fail in the default unordered mode.
        missing = list((exp_c - act_c).elements())
        unexpected = list((act_c - exp_c).elements())

        if missing and unexpected:
            leftover_expected: list[tuple] = []
            remaining = list(unexpected)
            for key in missing:
                for i, other in enumerate(remaining):
                    if len(key) == len(other) and all(
                        _values_equal(x, y, opts) for x, y in zip(key, other)
                    ):
                        remaining.pop(i)
                        break
                else:
                    leftover_expected.append(key)
            missing, unexpected = leftover_expected, remaining

        for key in missing:
            diffs.append(f"missing row {list(key)!r}")
        for key in unexpected:
            diffs.append(f"unexpected row {list(key)!r}")
        if len(diffs) > 8:
            diffs = diffs[:8] + ["... (further differences suppressed)"]

    return ComparisonResult(not diffs, diffs)
