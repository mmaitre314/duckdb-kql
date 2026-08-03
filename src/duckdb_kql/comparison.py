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

import datetime as _dt
import math
import re
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "compare", "ComparisonResult", "ComparisonOptions",
    "is_order_significant", "is_nondeterministic",
]

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

#: Functions whose value changes between runs. A frozen expectation can never
#: be meaningfully compared against these — the emulator's answer was true at
#: freeze time and nothing can reproduce it (R10).
NONDETERMINISTIC_FUNCTIONS = frozenset(
    {
        "now", "rand", "new_guid", "ingestion_time", "current_principal",
        # Cursors encode the engine's current commit position — a clock in
        # disguise. Caught by the drift lane, which saw cursor_current() return
        # a different value on every run.
        "cursor_current", "current_cursor", "cursor_after",
        "current_database", "current_cluster_endpoint", "current_principal_details",
    }
)

# Aggregates whose results are estimates, not exact values (R11).
APPROXIMATE_FUNCTIONS = frozenset(
    {"dcount", "dcountif", "percentile", "percentiles", "percentilew", "tdigest"}
)

# Operators that pick rows arbitrarily. `sample` re-rolls on every execution, so
# its output is not reproducible even on the same engine and the same data —
# unlike `take`, whose *order* is undefined but whose row set is stable enough to
# compare unordered.
_NONDETERMINISTIC_OPERATOR_RE = re.compile(
    r"\|\s*(sample|sample-distinct)\b", re.IGNORECASE
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


def is_nondeterministic(kql: str) -> bool:
    """True if the query's result cannot be reproduced across runs.

    Such a query has no stable ground truth, so comparing it against a frozen
    expectation tests nothing. Callers should skip rather than fail.
    """
    lowered = kql.lower()
    if any(f"{fn}(" in lowered for fn in NONDETERMINISTIC_FUNCTIONS):
        return True
    return _NONDETERMINISTIC_OPERATOR_RE.search(kql) is not None


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

    # A GUID arrives as a string from the emulator and as a uuid.UUID from
    # DuckDB; compare the identifiers, not their spellings.
    if isinstance(a, _uuid.UUID) or isinstance(b, _uuid.UUID):
        ua, ub = _as_uuid(a), _as_uuid(b)
        if ua is not None and ub is not None:
            return ua == ub

    # Temporal values may arrive as an ISO/KQL string from the emulator and as a
    # Python object from DuckDB; compare the instants, not the spellings.
    if type(a) is not type(b):
        ta, tb = _as_timedelta(a), _as_timedelta(b)
        if ta is not None and tb is not None:
            return ta == tb
        da, db = _as_datetime(a), _as_datetime(b)
        if da is not None and db is not None:
            return da == db

    return _scalar_key(a) == _scalar_key(b)


_TIMESPAN_RE = re.compile(
    r"^(?P<sign>-)?(?:(?P<days>\d+)\.)?(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})"
    r"(?:\.(?P<frac>\d+))?$"
)


def _as_timedelta(v: Any) -> _dt.timedelta | None:
    """Coerce a KQL timespan string or a Python timedelta to a timedelta.

    The emulator renders a timespan as ``[-][d.]hh:mm:ss[.fffffff]`` while DuckDB
    returns an INTERVAL as ``datetime.timedelta``. They denote the same value, so
    comparing their *representations* would report a difference that isn't one.
    """
    if isinstance(v, _dt.timedelta):
        return v
    if isinstance(v, str):
        m = _TIMESPAN_RE.match(v.strip())
        if not m:
            return None
        frac = m.group("frac") or ""
        micros = int((frac + "000000")[:6]) if frac else 0
        td = _dt.timedelta(
            days=int(m.group("days") or 0),
            hours=int(m.group("h")),
            minutes=int(m.group("m")),
            seconds=int(m.group("s")),
            microseconds=micros,
        )
        return -td if m.group("sign") else td
    return None


_FRACTION_RE = re.compile(r"(?<=:\d\d)\.(\d+)")


def _iso_for_fromisoformat(text: str) -> str:
    """Rewrite an ISO-8601 instant into the narrow dialect old Pythons accept.

    ``datetime.fromisoformat`` only became a full ISO-8601 parser in 3.11.
    Before that it rejects a trailing ``Z`` and accepts *exactly* 3 or 6
    fractional digits — so the emulator's ``23:59:59.9Z`` and its 7-digit
    ``.1234567`` ticks both raise on 3.9/3.10.

    That failure is invisible in the worst way: ``_as_datetime`` returns None,
    the comparison falls back to comparing a string against a datetime, and the
    case is reported as a mismatch. The suite would silently under-report
    matches on the oldest Python we claim to support.
    """
    text = text.replace("Z", "+00:00").replace("z", "+00:00")
    # Pad or truncate the fractional second to exactly 6 digits (microseconds,
    # the most a datetime can hold — KQL ticks are 100ns so the 7th digit is
    # dropped by any Python version).
    return _FRACTION_RE.sub(lambda m: "." + (m.group(1) + "000000")[:6], text, count=1)


def _as_datetime(v: Any) -> _dt.datetime | None:
    """Coerce an ISO-8601 string or a Python datetime to a naive UTC datetime.

    KQL datetimes are always UTC (R8), so a trailing ``Z`` and an explicit
    +00:00 offset denote the same instant as DuckDB's naive TIMESTAMP.
    """
    if isinstance(v, _dt.datetime):
        dt = v
    elif isinstance(v, str):
        text = v.strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}[T ]", text):
            return None
        try:
            dt = _dt.datetime.fromisoformat(_iso_for_fromisoformat(text))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    return dt


def _as_uuid(v: Any) -> _uuid.UUID | None:
    if isinstance(v, _uuid.UUID):
        return v
    if isinstance(v, str):
        try:
            return _uuid.UUID(v)
        except ValueError:
            return None
    return None


def _scalar_key(v: Any) -> Any:
    """A comparable, hashable key for a scalar."""
    if v is None:
        return None
    if isinstance(v, (_dt.datetime, _dt.timedelta)):
        return v
    if isinstance(v, _uuid.UUID):
        return str(v)
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
