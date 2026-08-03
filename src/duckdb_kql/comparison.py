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
import json as _json
import math
import re
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "compare", "ComparisonResult", "ComparisonOptions",
    "is_order_significant", "is_nondeterministic", "is_arbitrary_selection",
    "sort_key_names",
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

# `take`/`limit` return an ARBITRARY subset — KQL documents it that way, and the
# emulator's five rows are not the five DuckDB's LIMIT picks. Asserting specific
# rows there asserts something KQL never promised, exactly as with row order.
_ARBITRARY_SELECTION_RE = re.compile(r"\|\s*(take|limit)\b", re.IGNORECASE)


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


def is_arbitrary_selection(kql: str) -> bool:
    """True when the query ends in ``take``/``limit`` with nothing ordering it.

    Which rows come back is then the engine's choice, so only the *shape* of the
    result — row count and columns — is meaningfully comparable. A preceding
    terminal ``sort`` makes the selection deterministic again.
    """
    m = list(_ARBITRARY_SELECTION_RE.finditer(kql))
    if not m:
        return False
    if is_order_significant(kql):
        return False
    # Only a trailing take matters; `take 100 | summarize count()` is fine.
    tail = kql[m[-1].end():]
    return not re.search(
        r"\|\s*(summarize|count|distinct|join|union|make-series)\b", tail, re.IGNORECASE
    )


_SORT_KEYS_RE = re.compile(
    r"\|\s*(?:sort|order)\s+by\s+(.*?)(?=\||$)", re.IGNORECASE | re.DOTALL
)


def sort_key_names(kql: str) -> list[str]:
    """Column names in the final ``sort by`` / ``order by`` clause.

    KQL guarantees rows come back ordered by these keys — and nothing more.
    Rows sharing a key value may appear in any order, because neither engine
    promises a stable sort. Knowing the keys lets the comparison assert the part
    that is promised without asserting the part that is not.
    """
    matches = list(_SORT_KEYS_RE.finditer(kql))
    if not matches:
        return []
    names = []
    for part in matches[-1].group(1).split(","):
        token = part.strip().split()
        if token:
            name = token[0].strip("[]`\"'")
            if name.isidentifier():
                names.append(name)
    return names


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
    #: Compare row count and columns but not row values. For results whose row
    #: *selection* is the engine's choice (`take` without a sort).
    shape_only: bool = False
    #: Columns the query sorted by. When ordered comparison is on, only these
    #: are checked positionally; rows tied on them compare as a multiset,
    #: because KQL does not promise a stable sort.
    sort_keys: tuple[str, ...] = ()
    #: Compare list values as multisets. `make_set` produces a SET, whose
    #: element order carries no meaning in either engine.
    unordered_lists: bool = False

    @classmethod
    def for_query(cls, kql: str, **overrides: Any) -> ComparisonOptions:
        """Derive sensible options from the query text itself."""
        opts = cls(
            ordered=is_order_significant(kql),
            shape_only=is_arbitrary_selection(kql),
            sort_keys=tuple(sort_key_names(kql)),
            # make_list order IS meaningful, so this is scoped to make_set. A
            # query using both loses order-checking on its make_list column —
            # rare, and preferable to failing every make_set case on a
            # difference that means nothing.
            unordered_lists="make_set(" in kql.lower(),
        )
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


def _values_equal(a: Any, b: Any, opts: ComparisonOptions, in_dynamic: bool = False) -> bool:
    """Compare two cell values.

    ``in_dynamic`` marks values reached *inside* a ``dynamic``/JSON value. There
    the temporal normalisation is applied even when both sides are strings,
    because ADX rewrites a datetime it finds inside a dynamic value into its own
    7-digit-tick spelling at ingestion time — a storage artifact, not a
    formatting choice either engine is making.

    That relaxation is deliberately **not** applied to top-level strings: there,
    two different spellings of the same instant is exactly the ``tostring()``
    divergence this suite exists to catch.
    """
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
    # A `dynamic` column comes back parsed from the emulator and as a JSON
    # *string* from DuckDB, so one side must be decoded before they can be
    # compared at all. Comparing the raw forms reports every dynamic value as a
    # difference.
    # Either side may be structured, or *both* may be JSON text: the unordered
    # fast path canonicalises lists to JSON strings before hashing, so the
    # leftover pairing sees string-vs-string and would otherwise skip decoding
    # entirely — which made every make_set row fail on element order alone.
    if _is_structured(a) or _is_structured(b) or (_looks_json(a) and _looks_json(b)):
        pa, pb = _as_structured(a), _as_structured(b)
        if pa is not None and pb is not None:
            a, b, in_dynamic = pa, pb, True

    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            return False
        if opts.unordered_lists:
            remaining = list(b)
            for x in a:
                for i, y in enumerate(remaining):
                    if _values_equal(x, y, opts, in_dynamic):
                        remaining.pop(i)
                        break
                else:
                    return False
            return True
        return all(_values_equal(x, y, opts, in_dynamic) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        # Recursive, not ==, because leaves need the same tolerance and temporal
        # normalization as top-level cells: ADX canonicalises a datetime *inside*
        # a dynamic value, rendering "…T00:41:00Z" as "…T00:41:00.0000000Z".
        return a.keys() == b.keys() and all(
            _values_equal(a[k], b[k], opts, in_dynamic) for k in a
        )

    # A GUID arrives as a string from the emulator and as a uuid.UUID from
    # DuckDB; compare the identifiers, not their spellings.
    if isinstance(a, _uuid.UUID) or isinstance(b, _uuid.UUID):
        ua, ub = _as_uuid(a), _as_uuid(b)
        if ua is not None and ub is not None:
            return ua == ub

    # Temporal values may arrive as an ISO/KQL string from the emulator and as a
    # Python object from DuckDB; compare the instants, not the spellings.
    if in_dynamic or type(a) is not type(b):
        ta, tb = _as_timedelta(a), _as_timedelta(b)
        if ta is not None and tb is not None:
            return ta == tb
        da, db = _as_datetime(a), _as_datetime(b)
        if da is not None and db is not None:
            return da == db

    return _scalar_key(a) == _scalar_key(b)


def _is_structured(v: Any) -> bool:
    return isinstance(v, (dict, list, tuple))


def _looks_json(v: Any) -> bool:
    return isinstance(v, str) and v.strip()[:1] in ("{", "[")


def _as_structured(v: Any) -> Any | None:
    """Decode a JSON string into dict/list, or pass a structure through.

    Returns None when *v* is neither — the caller then falls through to the
    scalar path rather than forcing a structural comparison.
    """
    if _is_structured(v):
        return list(v) if isinstance(v, tuple) else v
    if isinstance(v, str):
        text = v.strip()
        if not text.startswith(("{", "[")):
            return None
        try:
            return _json.loads(text)
        except ValueError:
            return None
    return None


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
    """A comparable, hashable key for a scalar.

    This is the fast path for unordered comparison, so it must agree with
    :func:`_values_equal`: anything the two engines spell differently but mean
    identically has to hash the same, or the multiset match fails and every row
    falls into the O(n²) pairing fallback — which on a 5000-row result is 25
    million comparisons and reports false "missing row" differences besides.

    Two representations therefore get canonicalised here:

    * **dynamic values** — a dict from the emulator, a JSON string from DuckDB;
    * **datetimes** — an ISO string from the emulator, an object from DuckDB.

    Canonicalising datetime-*looking strings* does mean that, in unordered mode,
    two different string spellings of one instant hash alike. That is the same
    rule :func:`_values_equal` already applies across types, and datetime
    formatting divergence is asserted directly in ``tests/test_datetime_traps.py``
    where the comparison is ordered.
    """
    if v is None:
        return None
    if isinstance(v, _dt.timedelta):
        return v
    if isinstance(v, _dt.datetime):
        return v.astimezone(_dt.timezone.utc).replace(tzinfo=None) if v.tzinfo else v
    if isinstance(v, _uuid.UUID):
        return str(v)
    if isinstance(v, (list, dict, tuple)):
        return _canonical_dynamic(v)
    if isinstance(v, str):
        text = v.strip()
        structured = _as_structured(text)
        if structured is not None:
            return _canonical_dynamic(structured)
        dt_value = _as_datetime(text)
        if dt_value is not None:
            return dt_value
        return text
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _canonical_dynamic(v: Any) -> str:
    """Canonical JSON for a dynamic value, with nested datetimes normalised.

    ADX rewrites a datetime inside a dynamic value into its own 7-digit-tick
    spelling at ingestion, so the raw text differs even when the value does not.
    """

    def norm(x: Any) -> Any:
        if isinstance(x, dict):
            return {k: norm(x[k]) for k in sorted(x)}
        if isinstance(x, (list, tuple)):
            return [norm(i) for i in x]
        if isinstance(x, str):
            d = _as_datetime(x)
            return d.isoformat() if d is not None else x
        if isinstance(x, _dt.datetime):
            return x.isoformat()
        return x

    return _json.dumps(norm(v), sort_keys=True, default=str)


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

    # KQL has no null string distinct from the empty string: `isnull('')` is
    # false and `string(null)` round-trips as ''. So an outer join's unmatched
    # string column comes back as '' from the emulator and as NULL from DuckDB —
    # the same value, spelled differently. Only applied where the *expected*
    # column type says string, so a genuine null/'' difference in another type
    # still fails.
    string_idx = [
        i
        for i, t in enumerate(expected.get("column_types") or [])
        if normalize_type(t) == "string"
    ]
    if string_idx:
        exp_rows = _blank_nulls(exp_rows, string_idx)
        act_rows = _blank_nulls(act_rows, string_idx)

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

    if opts.shape_only:
        # Row count and columns already checked above; which rows the engine
        # picked is not ours to assert.
        return ComparisonResult(not diffs, diffs)

    if opts.ordered:
        # KQL's sort is not stable, so rows tied on the sort key may come back
        # in any order. Asserting their relative positions asserts something
        # neither engine promises — and with a low-cardinality key (`order by
        # dcount(...)`, where dozens of groups tie) that is most of the result.
        # Check the promised part positionally, the rest as a set.
        key_idx = [i for i, c in enumerate(exp_cols) if c in opts.sort_keys]
        if key_idx and len(exp_rows) == len(act_rows):
            for i, (er, ar) in enumerate(zip(exp_rows, act_rows)):
                if not all(_values_equal(er[j], ar[j], opts) for j in key_idx):
                    diffs.append(
                        f"row {i}: sort keys differ "
                        f"{[er[j] for j in key_idx]!r} != {[ar[j] for j in key_idx]!r}"
                    )
                    if len(diffs) > 8:
                        diffs.append("... (further differences suppressed)")
                        break
            if diffs:
                return ComparisonResult(False, diffs)
            # Ordering holds; now the rows themselves, order-insensitively.
            return _compare_unordered(exp_rows, act_rows, opts, diffs)

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


def _compare_unordered(
    exp_rows: list, act_rows: list, opts: ComparisonOptions, diffs: list
) -> ComparisonResult:
    """Compare two row sets as multisets, tolerance-aware."""
    from collections import Counter

    try:
        exp_c = Counter(_row_key(r) for r in exp_rows)
        act_c = Counter(_row_key(r) for r in act_rows)
    except TypeError:  # pragma: no cover - unhashable payload
        diffs.append("rows are not hashable; use ordered comparison")
        return ComparisonResult(False, diffs)

    missing = list((exp_c - act_c).elements())
    unexpected = list((act_c - exp_c).elements())

    if missing and unexpected:
        leftover: list[tuple] = []
        remaining = list(unexpected)
        for key in missing:
            for i, other in enumerate(remaining):
                if len(key) == len(other) and all(
                    _values_equal(x, y, opts) for x, y in zip(key, other)
                ):
                    remaining.pop(i)
                    break
            else:
                leftover.append(key)
        missing, unexpected = leftover, remaining

    for key in missing:
        diffs.append(f"missing row {list(key)!r}")
    for key in unexpected:
        diffs.append(f"unexpected row {list(key)!r}")
    if len(diffs) > 8:
        diffs = diffs[:8] + ["... (further differences suppressed)"]
    return ComparisonResult(not diffs, diffs)


def _blank_nulls(rows: list[list[Any]], indexes: list[int]) -> list[list[Any]]:
    """Map None -> '' in the given string columns (see :func:`compare`)."""
    out = []
    for row in rows:
        new = list(row)
        for i in indexes:
            if i < len(new) and new[i] is None:
                new[i] = ""
        out.append(new)
    return out
