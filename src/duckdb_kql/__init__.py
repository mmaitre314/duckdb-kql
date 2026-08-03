"""duckdb-kql — run Kusto KQL queries on DuckDB, from Python.

**Status: pre-alpha.** Translation is being built wave by wave
(``docs/implementation-plan.md``). Wave 1 covers ``where`` / ``project`` /
``extend`` / ``take`` / ``sort`` / ``count`` / ``distinct`` over ``print``,
``datatable``, and table sources. Anything outside it raises
``KqlUnsupportedError`` — deliberately, rather than returning something
plausible and wrong.

Working today::

    >>> import duckdb_kql
    >>> result = duckdb_kql.parse('Logs | where Level == "Error" | take 10')
    >>> result.ok
    True
    >>> duckdb_kql.to_sql("print x = 1 + 1")
    'SELECT (CAST(1 AS BIGINT) + CAST(1 AS BIGINT)) AS "x"'
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

def to_sql(kql: str, schema: Any | None = None) -> str:
    """Translate *kql* to DuckDB SQL. Requires no connection.

    .. important::
       KQL datetimes are UTC (``docs/TRANSLATION.md`` R8), and DuckDB reads the
       **session** ``TimeZone`` when casting a string that carries no offset. Run
       the returned SQL on a connection with ``SET TimeZone='UTC'`` or datetime
       values will be silently shifted. :func:`sql` does this for you; the
       requirement only falls to callers who execute the SQL themselves.

    Raises:
        KqlSyntaxError: the query does not parse.
        KqlUnsupportedError: it parses but uses a construct outside this wave.
    """
    from .lower import lower
    from .translate import to_sql as _emit

    return str(_emit(lower(kql), schema))


def sql(con: Any, kql: str) -> Any:
    """Execute *kql* against a DuckDB connection, returning a relation.

    Sets ``TimeZone='UTC'`` on *con* first — see :func:`to_sql`. This changes
    connection state deliberately: leaving it to the caller means a machine in a
    non-UTC zone silently returns wrong datetimes.

    The connection also supplies the schema that ``join`` needs, so joins work
    here without the caller passing one.
    """
    con.execute("SET TimeZone='UTC'")
    return con.sql(to_sql(kql, schema=_connection_schema(con)))


def _connection_schema(con: Any) -> dict[str, list[str]]:
    """Read table -> column names from a DuckDB connection.

    Only ``join`` consults this, but it is cheap enough to gather unconditionally
    and keeps the public API free of a schema argument.
    """
    try:
        rows = con.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "ORDER BY table_name, ordinal_position"
        ).fetchall()
    except Exception:  # noqa: BLE001 - a schema-less connection is not an error
        return {}
    schema: dict[str, list[str]] = {}
    for table, column in rows:
        schema.setdefault(table, []).append(column)
    return schema


def df(con: Any, kql: str) -> Any:
    """Execute *kql* and return a pandas DataFrame."""
    return sql(con, kql).df()


def arrow(con: Any, kql: str) -> Any:
    """Execute *kql* and return a pyarrow Table."""
    return sql(con, kql).arrow()
