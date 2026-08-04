"""``dataframe_from_result_table`` — result table to pandas DataFrame.

The conversion table is the SDK's, kept the same on purpose: the dtypes a caller
gets — ``Int64Dtype`` for a long, a UTC-aware ``datetime64`` for a datetime, a
``Timedelta`` for a timespan — are what their downstream code was written
against, and quietly handing back ``object`` columns would break arithmetic and
comparisons rather than error.

Because the SDK's own helper type-checks its argument, ``_models`` registers our
table with it when ``azure-kusto-data`` is installed. A caller can then keep
their existing ``from azure.kusto.data.helpers import dataframe_from_result_table``
import and pass one of our responses to it.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from typing import Any

__all__ = ["dataframe_from_result_table", "default_dict", "Converter"]

#: Column name or Kusto type -> the pandas dtype to convert that column to,
#: either as a dtype name or as ``(column_name, frame) -> Series``.
Converter = dict[str, str | Callable[[str, Any], Any]]


@lru_cache(maxsize=1)
def default_dict() -> Converter:
    """Kusto scalar type -> the pandas dtype the SDK produces for it."""
    import pandas as pd

    return {
        "string": lambda col, df: (
            df[col].astype(pd.StringDtype()) if hasattr(pd, "StringDtype") else df[col]
        ),
        "guid": lambda col, df: df[col],
        "uuid": lambda col, df: df[col],
        "uniqueid": lambda col, df: df[col],
        "dynamic": lambda col, df: df[col],
        "bool": lambda col, df: df[col].astype(bool),
        "boolean": lambda col, df: df[col].astype(bool),
        "int": lambda col, df: df[col].astype(pd.Int32Dtype()),
        "int32": lambda col, df: df[col].astype(pd.Int32Dtype()),
        "int64": lambda col, df: df[col].astype(pd.Int64Dtype()),
        "long": lambda col, df: df[col].astype(pd.Int64Dtype()),
        "real": lambda col, df: parse_float(df, col),
        "double": lambda col, df: parse_float(df, col),
        "decimal": lambda col, df: parse_float(df, col),
        "datetime": lambda col, df: parse_datetime(df, col),
        "date": lambda col, df: parse_datetime(df, col),
        "timespan": lambda col, df: df[col].apply(parse_timedelta),
        "time": lambda col, df: df[col].apply(parse_timedelta),
    }


def dataframe_from_result_table(
    table: Any,
    nullable_bools: bool = False,
    converters_by_type: Converter | None = None,
    converters_by_column_name: Converter | None = None,
) -> Any:
    """Convert a result table into a pandas DataFrame.

    Args:
        table: a table from ``response.primary_results``.
        nullable_bools: convert nulls in a bool column to ``pandas.NA`` rather
            than to ``False``. Off by default, matching the SDK.
        converters_by_type: override the dtype chosen for a Kusto type.
        converters_by_column_name: override the dtype for a named column. Takes
            precedence over ``converters_by_type``.
    """
    import pandas as pd

    if not table:
        raise ValueError("table is empty")
    if not hasattr(table, "columns") or not hasattr(table, "raw_rows"):
        raise TypeError(
            "expected a Kusto result table (response.primary_results[0]), got "
            + type(table).__name__
        )

    columns = [col.column_name for col in table.columns]
    frame = pd.DataFrame(table.raw_rows, columns=columns)
    default = default_dict()

    for col in table.columns:
        name = col.column_name
        kind = col.column_type
        if converters_by_column_name and name in converters_by_column_name:
            converter = converters_by_column_name.get(name)
        elif converters_by_type and kind in converters_by_type:
            converter = converters_by_type.get(kind)
        elif nullable_bools and kind == "bool":
            converter = lambda col, df: df[col].astype(pd.BooleanDtype())  # noqa: E731
        else:
            converter = default.get(kind)
        if converter is None:
            raise Exception("Unexpected type " + kind)
        if isinstance(converter, str):
            frame[name] = frame[name].astype(converter)
        else:
            frame[name] = converter(name, frame)

    return frame


def parse_float(frame: Any, col: str) -> Any:
    import numpy as np
    import pandas as pd

    # The SDK passes copy=False here; pandas 3 deprecated the keyword, and
    # copy-on-write makes it a no-op anyway.
    frame[col] = frame[col].infer_objects().replace(
        {"NaN": np.nan, "Infinity": np.inf, "-Infinity": -np.inf}
    )
    frame[col] = pd.to_numeric(frame[col], errors="coerce").astype(pd.Float64Dtype())
    return frame[col]


def parse_datetime(frame: Any, col: str) -> Any:
    import pandas as pd

    frame[col] = pd.to_datetime(frame[col], format="ISO8601", utc=True, errors="coerce")
    return frame[col]


def parse_timedelta(raw_value: Any) -> Any:
    """Wire timespan -> ``pandas.Timedelta``.

    Kusto writes ``d.hh:mm:ss.fffffff``; pandas wants ``d days hh:mm:ss``. The
    ambiguity worth knowing about is that the day separator is a ``.``, the same
    character as the fractional-second separator, so the first one only counts
    when it appears before the first ``:``.
    """
    import pandas as pd

    if raw_value is None:
        return pd.NaT
    if isinstance(raw_value, pd.Timedelta):
        return raw_value
    if isinstance(raw_value, (int, float)):
        # .NET ticks: 100ns each.
        return pd.to_timedelta(raw_value * 100, unit="ns")
    if isinstance(raw_value, str):
        parts = raw_value.split(":")
        if "." not in parts[0]:
            return pd.to_timedelta(raw_value)
        return pd.to_timedelta(raw_value.replace(".", " days ", 1))
    return pd.to_timedelta(raw_value)
