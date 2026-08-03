"""Query parameters — the safe way to put a caller's value into a KQL query.

KQL's answer to string interpolation is a declaration::

    declare query_parameters(state:string, since:datetime);
    StormEvents | where State == state and StartTime > since

The names are *values*, not text. Real Kusto sends them beside the query and
binds them server-side, so a value containing ``' | project secret`` is a string
that happens to contain punctuation — never a new clause. We reproduce that
property rather than approximate it: a declared parameter becomes an
:class:`~duckdb_kql.ir.Parameter`, which renders as a DuckDB prepared-statement
placeholder, and the value reaches DuckDB through its binding API. The generated
SQL contains no caller-controlled bytes, so there is no escaping to get wrong.

This module is Layer 0 — it does the *typing* half: what a declaration says, and
whether a supplied value can honestly be called that type.
"""

from __future__ import annotations

import base64
import datetime as _dt
import json
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .errors import KqlSchemaError, KqlUnsupportedError

__all__ = ["ParameterDeclaration", "bind"]

#: KQL scalar types a parameter may be declared as, and their aliases.
#: Anything outside this is refused rather than guessed at. ``int8`` is the
#: notable omission: the grammar accepts it, but it is a legacy spelling whose
#: meaning (bool, in the storage layer) does not match what anyone writing it
#: today would expect, and guessing between the two is exactly the kind of
#: silent wrongness this project refuses.
_TYPE_ALIASES = {
    "bool": "bool",
    "boolean": "bool",
    "int": "int",
    "long": "long",
    "int64": "long",
    "real": "real",
    "double": "real",
    "decimal": "decimal",
    "string": "string",
    "datetime": "datetime",
    "date": "datetime",
    "timespan": "timespan",
    "time": "timespan",
    "guid": "guid",
    "uuid": "guid",
    "uniqueid": "guid",
    "dynamic": "dynamic",
}


@dataclass(frozen=True)
class ParameterDeclaration:
    """One entry of a ``declare query_parameters(...)`` list.

    ``slot`` is the generated DuckDB placeholder name. It is deliberately *not*
    derived from ``name``: a KQL identifier can be an escaped name containing
    arbitrary text, and the whole point is that nothing the caller writes ends
    up in the SQL string.
    """

    name: str
    type: str
    slot: str
    #: Default expression text as written in the query, or ``None``.
    default: Any = None

    @property
    def required(self) -> bool:
        return self.default is None


def normalize_type(text: str) -> str:
    """Map a declared type name to its canonical KQL spelling."""
    key = text.strip().lower()
    if key not in _TYPE_ALIASES:
        raise KqlUnsupportedError(
            f"query_parameters type:{text}",
            hint="not a KQL scalar type",
        )
    return _TYPE_ALIASES[key]


def bind(
    declarations: list[ParameterDeclaration],
    values: dict[str, Any] | None,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Coerce caller *values* to the declared types.

    Returns ``(bound, unbound)`` — values keyed by placeholder slot, and the
    names of declarations that got neither a value nor a default. Translating a
    query without supplying every value is legitimate (the SQL is worth looking
    at on its own), so the missing ones are *reported* rather than raised on;
    execution is where they become an error.

    A value supplied for a name that was never declared **is** refused here. It
    is far more likely a typo — and a filter that silently does nothing — than
    an intentional no-op.
    """
    supplied = dict(values or {})
    declared = {d.name for d in declarations}

    unknown = sorted(set(supplied) - declared)
    if unknown:
        known = sorted(declared)
        raise KqlSchemaError(
            ", ".join(unknown),
            hint=(
                f"not declared by this query; declared parameters are {known}"
                if known
                else "the query has no `declare query_parameters` statement"
            ),
        )

    bound: dict[str, Any] = {}
    unbound: list[str] = []
    for decl in declarations:
        if decl.name in supplied:
            raw = supplied[decl.name]
        elif decl.default is not None:
            raw = decl.default
        else:
            unbound.append(decl.name)
            continue
        bound[decl.slot] = coerce(raw, decl.type, decl.name)
    return bound, tuple(unbound)


def coerce(value: Any, kind: str, name: str) -> Any:
    """Convert *value* to the Python type DuckDB expects for a KQL *kind*.

    Conversions here are the ones that cannot lose information — parsing an
    ISO-8601 string into a ``datetime``, say. A float handed to a ``long``
    parameter is refused rather than truncated: the caller and the query
    disagree about the type, and picking one silently is how wrong numbers get
    into reports.
    """
    if value is None:
        return None

    if kind == "string":
        if not isinstance(value, str):
            raise _mismatch(name, kind, value)
        return value

    if kind == "bool":
        if not isinstance(value, bool):
            raise _mismatch(name, kind, value)
        return value

    if kind in ("int", "long"):
        # bool is an int subclass in Python; a `true` where a number belongs is
        # a mistake worth surfacing.
        if isinstance(value, bool) or not isinstance(value, int):
            raise _mismatch(name, kind, value)
        return value

    if kind == "real":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _mismatch(name, kind, value)
        return float(value)

    if kind == "decimal":
        if isinstance(value, Decimal):
            return value
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise _mismatch(name, kind, value)
        try:
            return Decimal(value)
        except Exception as exc:  # noqa: BLE001
            raise _mismatch(name, kind, value) from exc

    if kind == "datetime":
        return _to_datetime(value, name)

    if kind == "timespan":
        return _to_timespan(value, name)

    if kind == "guid":
        if isinstance(value, uuid.UUID):
            return value
        if not isinstance(value, str):
            raise _mismatch(name, kind, value)
        try:
            return uuid.UUID(value)
        except ValueError as exc:
            raise _mismatch(name, kind, value) from exc

    if kind == "dynamic":
        # DuckDB has no dict/list parameter type, so a dynamic value crosses as
        # JSON text and is cast back on the other side. json.dumps is a total
        # function on JSON-shaped input and produces no SQL syntax, so this stays
        # a value the whole way.
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, default=_json_default)
        except (TypeError, ValueError) as exc:
            raise _mismatch(name, kind, value) from exc

    raise KqlUnsupportedError(f"query_parameters type:{kind}")


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (_dt.datetime, _dt.date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, (bytes, bytearray)):
        return base64.b64encode(bytes(obj)).decode("ascii")
    raise TypeError(f"not JSON-serializable: {type(obj).__name__}")


def _mismatch(name: str, kind: str, value: Any) -> KqlSchemaError:
    return KqlSchemaError(
        name,
        hint=(
            f"declared {kind}, got {type(value).__name__} "
            f"({value!r:.60}) — pass a value of the declared type"
        ),
    )


def _to_datetime(value: Any, name: str) -> _dt.datetime:
    if isinstance(value, _dt.datetime):
        return _as_naive_utc(value)
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day)
    if not isinstance(value, str):
        raise _mismatch(name, "datetime", value)
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return _as_naive_utc(_dt.datetime.fromisoformat(text))
    except ValueError as exc:
        raise _mismatch(name, "datetime", value) from exc


def _as_naive_utc(value: _dt.datetime) -> _dt.datetime:
    """KQL datetimes are UTC and carry no zone (R8).

    An aware value is converted; a naive one is taken as already-UTC, matching
    how the rest of the translator reads offset-less text.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(_dt.timezone.utc).replace(tzinfo=None)


#: KQL's timespan literal spelling: ``1d``, ``2.5h``, ``100ms``, ``1.02:03:04``.
_TIMESPAN_UNITS = {
    "d": "days",
    "day": "days",
    "days": "days",
    "h": "hours",
    "hr": "hours",
    "hrs": "hours",
    "hour": "hours",
    "hours": "hours",
    "m": "minutes",
    "min": "minutes",
    "minute": "minutes",
    "minutes": "minutes",
    "s": "seconds",
    "sec": "seconds",
    "second": "seconds",
    "seconds": "seconds",
    "ms": "milliseconds",
    "milli": "milliseconds",
    "millisecond": "milliseconds",
    "milliseconds": "milliseconds",
    "micro": "microseconds",
    "microsecond": "microseconds",
    "microseconds": "microseconds",
    "tick": "ticks",
    "ticks": "ticks",
}

_TIMESPAN_SUFFIXED = re.compile(r"^([0-9]*\.?[0-9]+)\s*([a-z]+)$", re.IGNORECASE)
_TIMESPAN_CLOCK = re.compile(
    r"^(?:(?P<days>\d+)\.)?(?P<hours>\d{1,2}):(?P<minutes>\d{2})"
    r"(?::(?P<seconds>\d{2}(?:\.\d+)?))?$"
)


def _to_timespan(value: Any, name: str) -> _dt.timedelta:
    if isinstance(value, _dt.timedelta):
        return value
    if isinstance(value, str):
        parsed = parse_timespan(value)
        if parsed is None:
            raise _mismatch(name, "timespan", value)
        return parsed
    raise _mismatch(name, "timespan", value)


def parse_timespan(text: str) -> _dt.timedelta | None:
    """Parse a KQL timespan literal. ``None`` when it is not one."""
    text = text.strip()
    negative = text.startswith("-")
    if negative or text.startswith("+"):
        text = text[1:]

    span = _parse_timespan_body(text)
    if span is None:
        return None
    return -span if negative else span


def _parse_timespan_body(text: str) -> _dt.timedelta | None:
    suffixed = _TIMESPAN_SUFFIXED.match(text)
    if suffixed:
        unit = _TIMESPAN_UNITS.get(suffixed.group(2).lower())
        if unit is None:
            return None
        amount = float(suffixed.group(1))
        if unit == "ticks":
            # A .NET tick is 100ns; timedelta's finest unit is a microsecond.
            return _dt.timedelta(microseconds=amount / 10)
        return _dt.timedelta(**{unit: amount})

    clock = _TIMESPAN_CLOCK.match(text)
    if clock:
        return _dt.timedelta(
            days=int(clock.group("days") or 0),
            hours=int(clock.group("hours")),
            minutes=int(clock.group("minutes")),
            seconds=float(clock.group("seconds") or 0),
        )

    try:
        # A bare number is a count of days, as in `timespan(3)`.
        return _dt.timedelta(days=float(text))
    except ValueError:
        return None
