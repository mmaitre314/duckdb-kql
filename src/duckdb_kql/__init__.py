"""duckdb-kql — run Kusto KQL queries on DuckDB, from Python.

**Status: pre-alpha.** Parsing works today; translation to DuckDB SQL is being
built wave by wave (``docs/implementation-plan.md``). The execution entry points
below exist so the intended API is visible and stable, but they raise
``NotImplementedError`` until Wave 1 lands — deliberately, rather than returning
something plausible and wrong.

Working today::

    >>> import duckdb_kql
    >>> result = duckdb_kql.parse('Logs | where Level == "Error" | take 10')
    >>> result.ok
    True
    >>> duckdb_kql.validate("Logs | wherex")     # doctest: +ELLIPSIS
    [...]
"""

from __future__ import annotations

from typing import Any

from .errors import (
    Diagnostic,
    KqlError,
    KqlSchemaError,
    KqlSyntaxError,
    KqlUnsupportedError,
    SourceSpan,
)
from .parser import ParseResult, parse, validate

__version__ = "0.0.1.dev0"

__all__ = [
    # working today
    "parse",
    "validate",
    "ParseResult",
    "Diagnostic",
    "SourceSpan",
    # errors
    "KqlError",
    "KqlSyntaxError",
    "KqlUnsupportedError",
    "KqlSchemaError",
    # planned execution API (see below)
    "to_sql",
    "sql",
    "df",
    "arrow",
    "__version__",
]

_NOT_YET = (
    "translation is not implemented yet — this is a pre-alpha release in which "
    "only parsing works. Track progress in docs/implementation-plan.md; the "
    "Wave 1 operator set is listed in docs/frequency-scan-results.md."
)


def to_sql(kql: str, schema: Any | None = None) -> str:
    """Translate *kql* to DuckDB SQL. Requires no connection.

    Raises:
        NotImplementedError: until Wave 1 lands.
    """
    parse(kql)  # fail fast and precisely on syntax errors, which do work today
    raise NotImplementedError(_NOT_YET)


def sql(con: Any, kql: str) -> Any:
    """Execute *kql* against a DuckDB connection, returning a relation.

    Raises:
        NotImplementedError: until Wave 1 lands.
    """
    parse(kql)
    raise NotImplementedError(_NOT_YET)


def df(con: Any, kql: str) -> Any:
    """Execute *kql* and return a pandas DataFrame.

    Raises:
        NotImplementedError: until Wave 1 lands.
    """
    parse(kql)
    raise NotImplementedError(_NOT_YET)


def arrow(con: Any, kql: str) -> Any:
    """Execute *kql* and return a pyarrow Table.

    Raises:
        NotImplementedError: until Wave 1 lands.
    """
    parse(kql)
    raise NotImplementedError(_NOT_YET)
