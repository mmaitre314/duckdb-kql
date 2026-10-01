"""Lowering for ``dynamic(...)`` literals whose payload is more than JSON.

KQL's JSON dialect inside ``dynamic(...)`` admits values JSON has no spelling
for — ``datetime(…)``, a timespan, ``guid(…)``, ``long(…)``/``real(…)``, a hex
number, adjacent strings (``"x" "y"``) and a nested ``dynamic(…)``. They used
to reach DuckDB verbatim, as text no JSON parser accepts. Each is now spelled
the way Kusto stores it — measured on the emulator, element by element:

    datetime(2020-01-01)          "2020-01-01T00:00:00.0000000Z"  (a string)
    1d, 90m, time(1.02:03:04)     864000000000, …                 (ticks, a long)
    guid(ABCDEF00-…)              "abcdef00-…"                    (lower-case)
    long(5), real(1.5), 0x10      5, 1.5, 16
    long(null), real(null)        null
    real(nan), real(+inf)         "NaN", "Infinity"
    "x" "y", 'it''s'              "xy", "its"
    dynamic([1,2]), dynamic(null) [1,2], null

A datetime needs SQL — its literal accepts every format `todatetime` does,
and only DuckDB's conversion parses them — so a payload holding one becomes
``parse_json(strcat(<json text>, tostring(datetime(…)), <json text>))``. Every
other element converts here, and a payload with none of these elements keeps
its old path untouched.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from . import ir
from . import lower as L

#: Ticks per unit, keyed by the start of the unit's spelling, longest first so
#: `ms`, `milli` and `micro` are not read as minutes.
_TICKS = (
    ("tick", Decimal(1)),
    ("nano", Decimal("0.01")),
    ("micro", Decimal(10)),
    ("milli", Decimal(10_000)),
    ("ms", Decimal(10_000)),
    ("hr", Decimal(36_000_000_000)),
    ("h", Decimal(36_000_000_000)),
    ("d", Decimal(864_000_000_000)),
    ("m", Decimal(600_000_000)),
    ("s", Decimal(10_000_000)),
)

_SECOND = Decimal(10_000_000)

_UNIT = re.compile(r"^(\d+(?:\.\d+)?)([a-z]+)$")
_CLOCK = re.compile(r"^(-)?(?:(\d+)\.)?(\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d{1,7}))?)?$")
_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class _Unsupported(Exception):
    """An element Kusto refuses, or one this cannot spell for certain."""


def lower(value: Any) -> ir.Expr | None:
    """The literal for the `JsonValue` *value*, or None to keep the plain path.

    None means the payload is plain JSON, which the caller already handles —
    so every such literal keeps exactly the SQL it had.
    """
    parts: list[str | ir.Expr] = []
    try:
        special = _walk(value, parts)
    except _Unsupported as exc:
        raise L._unsupported(value, f"dynamic literal: {exc}") from None
    if not special:
        return None
    merged: list[str | ir.Expr] = []
    for part in parts:
        if isinstance(part, str) and merged and isinstance(merged[-1], str):
            merged[-1] += part
        else:
            merged.append(part)
    if len(merged) == 1 and isinstance(merged[0], str):
        return ir.Literal(merged[0], "dynamic")
    args = tuple(
        ir.Literal(p, "string") if isinstance(p, str) else ir.FunctionCall("tostring", (p,))
        for p in merged
    )
    return ir.FunctionCall("parse_json", (ir.FunctionCall("strcat", args),))


def _walk(node: Any, out: list[str | ir.Expr]) -> bool:
    """Append *node*'s JSON to *out*; True if it held anything but JSON."""
    kind = L._cls(node)
    if kind == "JsonValue":
        (child,) = L._rule_children(node)
        return _walk(child, out)
    if kind == "JsonArray":
        out.append("[")
        special = False
        for i, item in enumerate(node.Values):
            if i:
                out.append(",")
            special |= _walk(item, out)
        out.append("]")
        return special
    if kind == "JsonObject":
        out.append("{")
        special = False
        for i, pair in enumerate(node.Pairs):
            if i:
                out.append(",")
            special |= _string([pair.Name.text], out)
            out.append(":")
            special |= _walk(pair.Value, out)
        out.append("}")
        return special
    if kind == "JsonString":
        return _string([t.text for t in node.Tokens], out)
    if kind == "JsonNull":
        out.append("null")
        return False
    if kind == "JsonBoolean":
        text = node.Token.text
        if "(" in text:
            # Measured: "String 'bool(true)' was not recognized as a valid Boolean".
            raise _Unsupported("bool(...) inside dynamic (Kusto: SYN0002)")
        out.append(text.lower())
        return False
    if kind in ("JsonLong", "JsonReal"):
        return _number(node, kind == "JsonReal", out)
    if kind == "JsonTimeSpan":
        out.append(_ticks(node.Token.text))
        return True
    if kind == "JsonGuid":
        inner = _unwrap(node.Token.text).lower()
        if not _GUID.match(inner):
            raise _Unsupported(f"guid {inner!r}")
        out.append(json.dumps(inner))
        return True
    if kind == "JsonDateTime":
        literal = L._typed_literal(node, "datetime", str)
        if literal.value is None:
            # Measured: "Null literal of type 'datetime' cannot appear in this context".
            raise _Unsupported("datetime(null) inside dynamic (Kusto: SYN0002)")
        # Its text never holds `"` or `\`, so it can sit between quotes as is.
        out.extend(['"', literal, '"'])
        return True
    if kind == "DynamicLiteralExpression":
        payload = [c for c in L._rule_children(node) if L._cls(c) == "JsonValue"]
        if not payload or payload[0].getText().strip().lower() == "null":
            out.append("null")
        else:
            _walk(payload[0], out)
        return True
    raise _Unsupported(kind)


def _string(tokens: list[str], out: list[str | ir.Expr]) -> bool:
    """A string, its adjacent literals concatenated — measured, `'it''s'` is
    `"its"`. Special unless the old quote-swapping spelled it the same: it
    did not for `"x" "y"`, an escape JSON lacks (`\\'`) or a `"` inside `'…'`."""
    text = json.dumps("".join(L._string_value(t) for t in tokens), ensure_ascii=False)
    out.append(text)
    return len(tokens) > 1 or L._normalize_json(tokens[0]) != text


#: A number JSON can spell as written.
_JSON_NUMBER = re.compile(r"-?(0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?")


def _unwrap(text: str) -> str:
    """``long(5)`` -> ``5``; text without a ``name(...)`` wrapper is unchanged."""
    if text.endswith(")") and "(" in text:
        return text.partition("(")[2][:-1].strip()
    return text


def _number(node: Any, real: bool, out: list[str | ir.Expr]) -> bool:
    sign = "-" if getattr(node, "SignToken", None) is not None else ""
    raw = node.LiteralToken.text
    text = _unwrap(raw)
    special = text != raw
    lowered = text.lower()
    if lowered == "null":
        if sign:
            raise _Unsupported(raw)
        out.append("null")
        return True
    if real and lowered.lstrip("+-") in ("nan", "inf", "infinity"):
        value = "NaN" if "nan" in lowered else (
            "-Infinity" if lowered.startswith("-") else "Infinity"
        )
        out.append(json.dumps(value))
        return True
    if lowered.startswith(("0x", "+0x", "-0x")):
        if sign:
            # Measured: "'-0x1' could not be parsed as a literal of type 'long'".
            raise _Unsupported(f"{sign}{raw}")
        out.append(str(int(lowered, 16)))
        return True
    if real:
        try:
            number = float(text)
        except ValueError:
            raise _Unsupported(raw) from None
        if not special and _JSON_NUMBER.fullmatch(text):
            out.append(sign + text)
            return False
        out.append(sign + repr(number))
        return True
    if not special and _JSON_NUMBER.fullmatch(text):
        out.append(sign + text)
        return False
    try:
        out.append(sign + str(int(text)))
    except ValueError:
        raise _Unsupported(raw) from None
    return True


def _ticks(text: str) -> str:
    """A KQL timespan literal as a tick count, as Kusto stores it in a dynamic."""
    lowered = text.lower()
    if lowered.startswith(("time(", "timespan(")):
        inner = _unwrap(text).strip()
        if inner.lower() == "null":
            raise _Unsupported("timespan(null) inside dynamic")
        if inner.isdigit():
            # Measured: `time(2)` is two days; `time(1.5)` is a syntax error.
            return str(int(inner) * 864_000_000_000)
        clock = _CLOCK.match(inner)
        if clock is None:
            return _ticks(inner)
        negative, days, hours, minutes, seconds, fraction = clock.groups()
        total = (
            (int(days or 0) * 86_400 + int(hours) * 3_600 + int(minutes) * 60
             + int(seconds or 0)) * 10_000_000
            + int((fraction or "").ljust(7, "0"))
        )
        return str(-total if negative else total)
    found = _UNIT.match(lowered)
    if found is None:
        raise _Unsupported(f"timespan {text!r}")
    number, unit = found.groups()
    for prefix, ticks in _TICKS:
        if unit.startswith(prefix):
            try:
                value = Decimal(number)
            except InvalidOperation:
                break
            if ticks < _SECOND and value != value.to_integral_value():
                # Measured, and no rule fits: `1.5ticks` is 1, `0.5microseconds`
                # 0 and `1.25microseconds` 10 — the fraction is lost before the
                # unit applies, unlike `1.5h`. Refused rather than guessed.
                raise _Unsupported(f"timespan {text!r}: a fraction of a sub-second unit")
            # Measured: whole nanoseconds round down — 150 and 199 are one tick.
            return str(int(value * ticks // 1))
    raise _Unsupported(f"timespan {text!r}")
