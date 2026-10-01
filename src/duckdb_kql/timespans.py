"""KQL timespan literals as tick counts (100 ns), measured on the emulator.

One parser for the two places a literal's value is needed before DuckDB sees
it: inside a ``dynamic(...)`` literal, where Kusto stores a timespan as its
tick count (`dynamic([1d])` is `[864000000000]`), and a scalar literal DuckDB's
``INTERVAL '…'`` cannot parse (`1tick`, `time(1.02:03:04)`, `time(2)`).

Layer 0: standard library only.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

__all__ = ["ticks", "TICKS_PER_DAY"]

TICKS_PER_DAY = 864_000_000_000

#: Ticks per unit, keyed by the start of the unit's spelling, longest first so
#: `ms`, `milli` and `micro` are not read as minutes.
_UNITS = (
    ("tick", Decimal(1)),
    ("nano", Decimal("0.01")),
    ("micro", Decimal(10)),
    ("milli", Decimal(10_000)),
    ("ms", Decimal(10_000)),
    ("hr", Decimal(36_000_000_000)),
    ("h", Decimal(36_000_000_000)),
    ("d", Decimal(TICKS_PER_DAY)),
    ("m", Decimal(600_000_000)),
    ("s", Decimal(10_000_000)),
)
_SECOND = Decimal(10_000_000)

_UNIT = re.compile(r"^(\d+(?:\.\d+)?)([a-z]+)$")
_CLOCK = re.compile(r"^(-)?(?:(\d+)\.)?(\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d{1,7}))?)?$")


def ticks(text: str) -> int:
    """The tick count of a timespan literal, written with or without its
    ``time(…)``/``timespan(…)`` wrapper.

    Raises ValueError for a form this cannot spell for certain — including
    `null`, which has no tick count.
    """
    text = text.strip()
    lowered = text.lower()
    if lowered.startswith(("time(", "timespan(")) and text.endswith(")"):
        text = text.partition("(")[2][:-1].strip()
        lowered = text.lower()
    if lowered == "null":
        raise ValueError("a null timespan has no tick count")
    if text.isdigit():
        # Measured: `time(2)` is two days; `time(1.5)` is a syntax error.
        return int(text) * TICKS_PER_DAY
    clock = _CLOCK.match(text)
    if clock is not None:
        negative, days, hours, minutes, seconds, fraction = clock.groups()
        total = (
            (int(days or 0) * 86_400 + int(hours) * 3_600 + int(minutes) * 60
             + int(seconds or 0)) * 10_000_000
            + int((fraction or "").ljust(7, "0"))
        )
        return -total if negative else total
    found = _UNIT.match(lowered)
    if found is None:
        raise ValueError(f"timespan {text!r}")
    number, unit = found.groups()
    for prefix, per in _UNITS:
        if unit.startswith(prefix):
            try:
                value = Decimal(number)
            except InvalidOperation:
                break
            if per < _SECOND and value != value.to_integral_value():
                # Measured, and no rule fits: `1.5ticks` is 1, `0.5microseconds`
                # 0 and `1.25microseconds` 10 — the fraction is lost before the
                # unit applies, unlike `1.5h`. Refused rather than guessed.
                raise ValueError(f"timespan {text!r}: a fraction of a sub-second unit")
            # Measured: whole nanoseconds round down — 150 and 199 are one tick.
            return int(value * per // 1)
    raise ValueError(f"timespan {text!r}")


def needs_ticks(text: str) -> bool:
    """Whether DuckDB's ``INTERVAL '…'`` cannot read this literal itself.

    Measured against every unit spelling: DuckDB agrees with Kusto wherever it
    parses one, and refuses `tick(s)`, `nano…`, every `milli…`/`micro…` short
    of the full word (`1millisec`, `1micros`), a bare day count and the
    `d.hh:mm:ss` clock form. Only those are rendered from ticks, so every other
    literal keeps the SQL it had.
    """
    lowered = text.strip().lower()
    if lowered.isdigit():
        return True
    clock = _CLOCK.match(lowered)
    if clock is not None:
        return clock.group(2) is not None
    found = _UNIT.match(lowered)
    if found is None:
        return False
    unit = found.group(2)
    if unit.startswith(("milli", "micro")):
        return unit not in _DUCKDB_SPELLINGS
    return unit.startswith(("tick", "nano"))


#: The `milli…`/`micro…` spellings DuckDB's INTERVAL parses itself.
_DUCKDB_SPELLINGS = frozenset(
    {"millisecond", "milliseconds", "microsecond", "microseconds"}
)
