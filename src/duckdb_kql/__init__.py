"""duckdb-kql — run Kusto KQL queries on DuckDB, from Python.

**Status: pre-alpha.** Translation is being built wave by wave
(``docs/implementation-plan.md``). Anything outside the covered surface raises
``KqlUnsupportedError`` — deliberately, rather than returning something
plausible and wrong.

The API comes in three layers, each adding one dependency. Import only the layer
you need and you pay only for that layer::

    Layer 0  duckdb_kql            KQL text in, DuckDB SQL out.  antlr4 only.
    Layer 1  duckdb_kql.engine     Run it.                        + duckdb
    Layer 2  duckdb_kql.kusto      KustoClient drop-in.           + pandas

Layer 0 — translate and inspect, no database anywhere::

    >>> import duckdb_kql
    >>> duckdb_kql.to_sql("print x = 1 + 1")
    'SELECT (CAST(1 AS BIGINT) + CAST(1 AS BIGINT)) AS "x"'
    >>> duckdb_kql.parse('Logs | where Level == "Error" | take 10').ok
    True
    >>> duckdb_kql.validate("Logs | wherex")     # doctest: +ELLIPSIS
    [...]

Layer 1 — execute against a DuckDB connection::

    import duckdb, duckdb_kql
    con = duckdb_kql.connect()               # or duckdb.connect(), TimeZone=UTC
    con.sql("CREATE TABLE Logs AS SELECT 'Error' AS Level")
    duckdb_kql.sql(con, "Logs | where Level == 'Error'").fetchall()

Layer 2 — the ``azure-kusto-data`` shape, for code already written against it::

    from duckdb_kql.kusto import KustoClient, ClientRequestProperties
    from duckdb_kql.kusto.helpers import dataframe_from_result_table

    client = KustoClient("mydata.duckdb")
    response = client.execute("db", "Logs | take 10")
    df = dataframe_from_result_table(response.primary_results[0])
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .errors import (
    Diagnostic,
    KqlError,
    KqlSchemaError,
    KqlSyntaxError,
    KqlUnsupportedError,
    SourceSpan,
)
from .params import ParameterDeclaration
from .parser import ParseResult, parse, validate

if TYPE_CHECKING:
    # Layer 1 is resolved lazily at runtime (see ``__getattr__``), which leaves
    # a type checker with nothing to go on — every re-export would be ``Any``,
    # and a caller would be told their code is fine when it is not. Importing
    # the real names here, for the checker only, keeps the laziness without
    # paying for it in the signatures.
    from .engine import Parameters, arrow, connect, df, execute, sql
    from .schema import Schema
    from .translate import TranslationResult

__version__ = "0.0.1.dev0"

__all__ = [
    # Layer 0 — KQL text, no database
    "parse",
    "validate",
    "to_sql",
    "query_parameters",
    "ParseResult",
    "ParameterDeclaration",
    "Diagnostic",
    "SourceSpan",
    # errors (Layer 0)
    "KqlError",
    "KqlSyntaxError",
    "KqlUnsupportedError",
    "KqlSchemaError",
    # Layer 1 — requires duckdb
    "connect",
    "sql",
    "execute",
    "df",
    "arrow",
    "__version__",
]

#: Layer 1 names, re-exported here for convenience but defined in ``engine``.
#: Resolved on first access so that importing this package never imports duckdb.
_LAYER1 = frozenset({"connect", "sql", "execute", "df", "arrow"})


def __getattr__(name: str) -> Any:
    if name in _LAYER1:
        from . import engine

        return getattr(engine, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)


# ---------------------------------------------------------------------------
# Layer 0
# ---------------------------------------------------------------------------


def to_sql(
    kql: str,
    schema: Schema | None = None,
    parameters: Parameters | None = None,
) -> TranslationResult:
    """Translate *kql* to DuckDB SQL. Requires no connection and no database.

    Returns a ``str`` subclass that also carries ``.parameters`` — the values
    for the placeholders a ``declare query_parameters`` query renders. Pass
    those to DuckDB alongside the SQL; :func:`duckdb_kql.sql` does it for you.

    Args:
        kql: the query text.
        schema: table name -> column names. Only ``join`` consults it.
        parameters: values for the query's declared parameters, by KQL name.
            They are bound as values, never spliced into the SQL, so a value may
            contain any text at all without changing what the query does.

    .. important::
       KQL datetimes are UTC (``docs/TRANSLATION.md`` R8), and DuckDB reads the
       **session** ``TimeZone`` when casting a string that carries no offset. Run
       the returned SQL on a connection with ``SET TimeZone='UTC'`` or datetime
       values will be silently shifted. :func:`duckdb_kql.sql` and
       :func:`duckdb_kql.connect` do this for you; the requirement only falls to
       callers who execute the SQL themselves.

    Raises:
        KqlSyntaxError: the query does not parse.
        KqlUnsupportedError: it parses but uses a construct outside this wave.
        KqlSchemaError: a supplied value does not match its declared type, or
            names a parameter the query does not declare.

    Translating without supplying every declared value is allowed — the SQL is
    worth reading on its own. The names still missing are listed in
    ``.unbound``, and executing is what turns them into an error.
    """
    from .lower import lower
    from .params import bind
    from .translate import to_sql as _emit

    query = lower(kql)
    result = _emit(query, schema)
    if query.parameters or parameters:
        bound, unbound = bind(query.parameters, parameters)
        return result.with_parameters(bound, unbound, tuple(query.parameters))
    return result


def query_parameters(kql: str) -> list[ParameterDeclaration]:
    """The parameters *kql* declares, in declaration order.

    Lets a caller discover what a query expects before deciding what to supply::

        >>> [(p.name, p.type) for p in query_parameters(
        ...     "declare query_parameters(state:string);"
        ...     " StormEvents | where State == state")]
        [('state', 'string')]
    """
    from .lower import query_parameters as _declared

    return _declared(kql)
