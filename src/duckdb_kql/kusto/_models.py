"""Result objects shaped like ``azure.kusto.data._models``.

Code written against the Kusto SDK reaches into these — ``response
.primary_results[0]``, ``row["Column"]``, ``table.raw_rows`` — so the shapes are
reproduced attribute for attribute rather than approximated. Where the SDK's
behaviour is load-bearing it is copied deliberately; the notes below say where
and why.

The most consequential of those is ``raw_rows``. In the SDK it holds the *wire*
values Kusto sent — a datetime is an ISO-8601 string, a timespan is
``d.hh:mm:ss.fffffff`` — and ``dataframe_from_result_table`` parses them from
that form. DuckDB hands us Python objects instead, so :func:`to_wire` converts
them back. Storing the Python objects would be more natural and would quietly
break every converter downstream.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import math
import re
import uuid
from collections.abc import Callable, Iterator, Sequence
from decimal import Decimal
from enum import Enum
from typing import Any

__all__ = [
    "WellKnownDataSet",
    "KustoResultColumn",
    "KustoResultRow",
    "KustoResultTable",
    "JsonColumn",
    "JsonTable",
    "kusto_type",
    "to_wire",
]

#: One column of Kusto's JSON response shape: ``{"ColumnName": …, "ColumnType": …}``.
JsonColumn = dict[str, Any]

#: One table of it — ``TableName``, ``TableKind``, ``Columns``, ``Rows``. The
#: values are heterogeneous by construction (a table name is a string, ``Rows``
#: is a list of lists of anything), so ``Any`` here is the honest type rather
#: than a placeholder.
JsonTable = dict[str, Any]

#: A row as it arrives on the wire — see the module docstring on ``raw_rows``.
WireRow = list[Any]


class WellKnownDataSet(str, Enum):
    """Categorizes data tables according to the role they play in a result."""

    PrimaryResult = "PrimaryResult"
    QueryCompletionInformation = "QueryCompletionInformation"
    TableOfContents = "TableOfContents"
    QueryProperties = "QueryProperties"


# ---------------------------------------------------------------------------
# DuckDB types and values -> Kusto's
# ---------------------------------------------------------------------------

#: DuckDB type name -> Kusto scalar type name. DuckDB has more integer widths
#: than Kusto does; anything wider than 32 bits reports as ``long`` because that
#: is the only 64-bit integer Kusto has.
_TYPE_MAP = {
    "BOOLEAN": "bool",
    "TINYINT": "int",
    "UTINYINT": "int",
    "SMALLINT": "int",
    "USMALLINT": "int",
    "INTEGER": "int",
    "UINTEGER": "long",
    "BIGINT": "long",
    "UBIGINT": "long",
    "HUGEINT": "long",
    "UHUGEINT": "long",
    "FLOAT": "real",
    "DOUBLE": "real",
    "VARCHAR": "string",
    "UUID": "guid",
    "DATE": "datetime",
    "TIMESTAMP": "datetime",
    "TIMESTAMP WITH TIME ZONE": "datetime",
    "TIMESTAMP_S": "datetime",
    "TIMESTAMP_MS": "datetime",
    "TIMESTAMP_NS": "datetime",
    "TIME": "timespan",
    "INTERVAL": "timespan",
    "JSON": "dynamic",
    "BLOB": "string",
    "BIT": "string",
}

_DECIMAL = re.compile(r"^DECIMAL\(", re.IGNORECASE)
#: Composite types have no Kusto counterpart other than dynamic, which is
#: exactly what they are: a nested document.
_COMPOSITE = re.compile(r"(\[\]$|^STRUCT|^MAP|^UNION|^LIST)", re.IGNORECASE)


def kusto_type(duckdb_type: Any) -> str:
    """Name the Kusto scalar type a DuckDB column corresponds to."""
    name = str(duckdb_type).upper()
    if name in _TYPE_MAP:
        return _TYPE_MAP[name]
    if _DECIMAL.match(name):
        return "decimal"
    if _COMPOSITE.search(name):
        return "dynamic"
    # An unmapped type still has a faithful string form, and `string` is the one
    # answer that cannot make a value look like something it is not.
    return "string"


def to_wire(value: Any, column_type: str) -> Any:
    """Render a DuckDB value the way Kusto puts it on the wire.

    ``dataframe_from_result_table`` and ``KustoResultRow`` both parse *from* the
    wire form, so handing them a live Python object skips their conversion and
    lands a different dtype in the DataFrame than real Kusto would.
    """
    if value is None:
        return None

    if column_type == "datetime":
        if isinstance(value, dt.datetime):
            return _iso(value)
        if isinstance(value, dt.date):
            return f"{value.isoformat()}T00:00:00Z"
        return str(value)

    if column_type == "timespan":
        if isinstance(value, dt.timedelta):
            return _timespan(value)
        if isinstance(value, dt.time):
            return _timespan(
                dt.timedelta(
                    hours=value.hour,
                    minutes=value.minute,
                    seconds=value.second,
                    microseconds=value.microsecond,
                )
            )
        return str(value)

    if column_type == "decimal":
        return str(value)

    if column_type == "guid":
        return str(value)

    if column_type == "dynamic":
        # DuckDB returns JSON columns as text and composite columns as Python
        # containers. Kusto sends parsed JSON, and that is what the SDK's
        # consumers expect to index into.
        if isinstance(value, (dict, list)):
            return _jsonable(value)
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return value
        return _jsonable(value)

    if column_type == "real":
        # Kusto spells the non-finite doubles out; parse_float in the SDK's
        # helpers looks for exactly these three strings.
        if isinstance(value, float):
            if math.isnan(value):
                return "NaN"
            if math.isinf(value):
                return "Infinity" if value > 0 else "-Infinity"
        return value

    if column_type == "string" and isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")

    return value


def _jsonable(value: Any) -> Any:
    """Make a DuckDB composite value safe to hand to json/pandas."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dt.datetime):
        return _iso(value)
    if isinstance(value, dt.timedelta):
        return _timespan(value)
    if isinstance(value, (Decimal, uuid.UUID)):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")
    return value


def _iso(value: dt.datetime) -> str:
    """ISO-8601 in UTC with a ``Z``, which is how Kusto writes a datetime."""
    if value.tzinfo is not None:
        value = value.astimezone(dt.timezone.utc).replace(tzinfo=None)
    text = value.isoformat(timespec="microseconds")
    return f"{text}Z"


def _timespan(value: dt.timedelta) -> str:
    """``[-][d.]hh:mm:ss[.fffffff]`` — .NET's TimeSpan format, which Kusto uses."""
    total = value.total_seconds()
    sign = "-" if total < 0 else ""
    rest = abs(value)

    days = rest.days
    seconds = rest.seconds
    micros = rest.microseconds
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    text = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    if days:
        text = f"{days}.{text}"
    if micros:
        # Kusto reports 7 fractional digits (100ns ticks); DuckDB stores 6, so
        # the last digit is always 0 rather than invented.
        text = f"{text}.{micros:06d}0"
    return sign + text


# The wire -> Python direction, used by KustoResultRow's conversion table.
# Defined above the class because that table is built at class-creation time.
def _parse_datetime(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value
    text = str(value)
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return value


def _parse_timespan(value: Any) -> Any:
    if isinstance(value, dt.timedelta):
        return value
    if isinstance(value, (int, float)):
        # .NET ticks: 100ns each.
        return dt.timedelta(microseconds=value / 10)
    from ..params import parse_timespan

    parsed = parse_timespan(str(value))
    return value if parsed is None else parsed


# ---------------------------------------------------------------------------
# The SDK-shaped objects
# ---------------------------------------------------------------------------


class KustoResultColumn:
    def __init__(self, json_column: JsonColumn, ordinal: int) -> None:
        self.column_name = json_column["ColumnName"]
        self.column_type = json_column.get("ColumnType") or json_column["DataType"]
        self.ordinal = ordinal

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            "KustoResultColumn("
            + json.dumps({"ColumnName": self.column_name, "ColumnType": self.column_type})
            + f",{self.ordinal})"
        )


class KustoResultRow:
    """Iterator over a result row, addressable by index or by column name."""

    #: Wire form -> Python, matching the SDK's own conversion table. Note that
    #: `dynamic` is absent from it there too: JSON arrives parsed.
    conversion_funcs: dict[str, Callable[[Any], Any]] = {
        "datetime": _parse_datetime,
        "timespan": _parse_timespan,
        "decimal": Decimal,
    }

    def __init__(
        self, columns: Sequence[KustoResultColumn | str], row: WireRow
    ) -> None:
        self._value_by_name: dict[str, Any] = {}
        self._value_by_index: list[Any] = []

        for i, value in enumerate(row):
            column = columns[i]
            try:
                # The SDK tolerates being handed bare column *names* rather than
                # KustoResultColumn objects, and code that builds a row by hand
                # relies on that. Such a column has no type, so its value is
                # taken as-is.
                column_type = column.column_type.lower()  # type: ignore[union-attr]
            except AttributeError:
                self._value_by_index.append(value)
                self._value_by_name[str(column)] = value
                continue
            typed = self.get_typed_value(column_type, value)
            self._value_by_index.append(typed)
            self._value_by_name[column.column_name] = typed  # type: ignore[union-attr]

    @staticmethod
    def get_typed_value(column_type: str, value: Any) -> Any:
        if value is None or column_type not in KustoResultRow.conversion_funcs:
            return value
        return KustoResultRow.conversion_funcs[column_type](value)

    @property
    def columns_count(self) -> int:
        return len(self._value_by_name)

    def __iter__(self) -> Iterator[Any]:
        for i in range(self.columns_count):
            yield self[i]

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._value_by_index[key]
        return self._value_by_name[key]

    def __len__(self) -> int:
        return self.columns_count

    def to_dict(self) -> dict[str, Any]:
        return self._value_by_name

    def to_list(self) -> list[Any]:
        return self._value_by_index

    def __str__(self) -> str:
        return "['{}']".format("', '".join(str(v) for v in self._value_by_index))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        values = ", ".join(repr(v) for v in self._value_by_name.values())
        return "KustoResultRow(['{}'], [{}])".format(
            "', '".join(self._value_by_name), values
        )

    def __eq__(self, other: Any) -> bool:
        if len(self) != len(other):
            return False
        return all(value == other[i] for i, value in enumerate(self))


class KustoResultTable:
    """Iterator over a result table. Built from the SDK's JSON table shape."""

    def __init__(self, json_table: JsonTable) -> None:
        self.table_name: str | None = json_table.get("TableName")
        self.table_id: int | None = json_table.get("TableId")
        kind = json_table.get("TableKind")
        self.table_kind: WellKnownDataSet | None = (
            WellKnownDataSet[kind] if kind else None
        )
        self.raw_columns: list[JsonColumn] = json_table["Columns"]
        self.columns = [
            KustoResultColumn(c, i) for i, c in enumerate(json_table["Columns"])
        ]
        self.raw_rows: list[WireRow] = json_table["Rows"]
        self.kusto_result_rows: list[KustoResultRow] | None = None

    def __bool__(self) -> bool:
        return any(self.columns)

    __nonzero__ = __bool__

    @property
    def columns_count(self) -> int:
        return len(self.columns)

    @property
    def rows_count(self) -> int:
        return len(self.raw_rows)

    @property
    def rows(self) -> list[KustoResultRow]:
        if not self.kusto_result_rows:
            self.kusto_result_rows = [
                KustoResultRow(self.columns, row) for row in self.raw_rows
            ]
        return self.kusto_result_rows

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.table_name,
            "kind": self.table_kind,
            "data": [r.to_dict() for r in self],
        }

    def __len__(self) -> int:
        return self.rows_count

    def __iter__(self) -> Iterator[KustoResultRow]:
        for index, row in enumerate(self.raw_rows):
            if self.kusto_result_rows:
                yield self.kusto_result_rows[index]
            else:
                yield KustoResultRow(self.columns, row)

    def __getitem__(self, key: int) -> KustoResultRow:
        return self.rows[key]

    def __str__(self) -> str:
        d = self.to_dict()
        if d["kind"] is not None:
            d["kind"] = d["kind"].value
        return json.dumps(d, default=str)


def _register_with_sdk() -> None:
    """Make ``isinstance(our_table, azure...KustoResultTable)`` true, if present.

    The SDK's own ``dataframe_from_result_table`` type-checks its argument, so
    without this a caller who has both packages installed and imports the helper
    from ``azure.kusto.data.helpers`` — the import their existing code already
    has — gets a TypeError. ``KustoResultTable`` inherits ABCMeta, so registering
    is enough; nothing of the SDK's behaviour is inherited or relied on.

    ``BaseException`` rather than ``Exception`` is deliberate. Importing the SDK
    drags in ``cryptography``, whose compiled extension can raise a pyo3
    ``PanicException`` — a ``BaseException`` — when the wheel does not match the
    interpreter. That is a broken install of a package we do not depend on, and
    it must not take our import down with it. The registration is an
    optimisation; failing it costs nothing.
    """
    try:  # pragma: no cover - depends on what is installed
        from azure.kusto.data import _models as sdk

        sdk.KustoResultTable.register(KustoResultTable)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001 - see above
        pass


_register_with_sdk()
