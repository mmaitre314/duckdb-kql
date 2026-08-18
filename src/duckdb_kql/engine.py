"""Layer 1 — running translated KQL on DuckDB.

Layer 0 turns KQL text into SQL and stops there; this is where a database
appears. It is a thin layer on purpose — DuckDB's own connection object is the
API, and these functions only do the things a caller would otherwise have to
remember:

* set ``TimeZone='UTC'``, because KQL datetimes are UTC (R8) and DuckDB reads
  the *session* zone when casting offset-less text. Forgetting it does not
  fail — it silently shifts every datetime, which is the failure mode this
  project exists to avoid;
* read the connection's schema, which ``join`` needs to reproduce KQL's column
  renaming;
* pass declared query parameters through DuckDB's binding API rather than into
  the SQL text.

Importing ``duckdb_kql`` does not import ``duckdb``. Importing *this* module
does not either — only :func:`connect` needs it, so a caller who already has a
connection can use every other function with ``duckdb`` absent from the
resolver's view.

The DuckDB types below are imported **only for type checkers**. Combined with
``from __future__ import annotations`` that costs nothing at runtime — the
annotations are never evaluated — while giving callers real types rather than
``Any``. Typing an optional dependency does not make it a required one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd
    import pyarrow as pa
    from duckdb import DuckDBPyConnection, DuckDBPyRelation

    from .translate import TranslationResult

#: Values a caller may supply for a query's declared parameters. Deliberately
#: ``Any``: what is acceptable depends on the *declared KQL type*, which is
#: checked at bind time (``duckdb_kql.params.coerce``) rather than by the
#: signature. See the table in docs/api.md.
Parameters = dict[str, Any]

#: Table name -> column names, as :func:`schema` returns it.
Schema = dict[str, list[str]]

__all__ = [
    "connect", "kql", "execute", "df", "arrow", "schema", "databases",
    "Parameters", "Schema",
]


def connect(database: str = ":memory:", **kwargs: Any) -> DuckDBPyConnection:
    """Open a DuckDB connection configured for KQL semantics.

    Equivalent to ``duckdb.connect(...)`` followed by ``SET TimeZone='UTC'``.
    Use it instead of ``duckdb.connect`` unless you are setting the zone
    yourself: on a machine in a non-UTC zone the difference is wrong answers,
    not errors.

    ``**kwargs`` is forwarded to ``duckdb.connect`` and is genuinely ``Any`` —
    DuckDB's own configuration surface is a string-keyed dict of mixed types.
    """
    duckdb = _require_duckdb()
    con: DuckDBPyConnection = duckdb.connect(database, **kwargs)
    con.execute("SET TimeZone='UTC'")
    return con


def _require_duckdb() -> Any:
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError(
            "duckdb_kql.connect() needs DuckDB, which is an optional dependency: "
            "pip install 'duckdb-kql[duckdb]'. Translation (duckdb_kql.to_sql) "
            "works without it."
        ) from exc
    return duckdb


def kql(
    con: DuckDBPyConnection,
    query: str,
    parameters: Parameters | None = None,
    database: str | None = None,
    allow_write: bool = True,
) -> DuckDBPyRelation:
    """Execute the KQL *query* against a DuckDB connection, returning a relation.

    Named for what it takes. It was ``sql()``, which read as though the argument
    were SQL — in a package whose entire job is that the two are not the same.
    The argument is KQL; :func:`duckdb_kql.to_sql` is the one that deals in SQL.

    Sets ``TimeZone='UTC'`` on *con* first. This changes connection state
    deliberately: leaving it to the caller means a machine in a non-UTC zone
    silently returns wrong datetimes.

    The connection also supplies the schema that ``join`` needs, so joins work
    here without the caller passing one.

    *parameters* supplies values for the query's ``declare query_parameters``
    declarations, by KQL name. They are bound by DuckDB, never substituted into
    the SQL, so a caller-supplied string cannot become part of the query::

        duckdb_kql.kql(
            con,
            "declare query_parameters(state:string);"
            " StormEvents | where State == state",
            {"state": user_input},   # safe whatever user_input contains
        )
    """
    translated, bound = _prepare(con, query, parameters, database, allow_write)
    return con.sql(translated, params=bound) if bound else con.sql(translated)


def execute(
    con: DuckDBPyConnection,
    query: str,
    parameters: Parameters | None = None,
    database: str | None = None,
    allow_write: bool = True,
) -> DuckDBPyConnection:
    """Execute the KQL *query* and return the connection, mirroring ``con.execute``.

    Use this for its side effect or its cursor; use :func:`kql` when you want a
    relation to keep composing.
    """
    translated, bound = _prepare(con, query, parameters, database, allow_write)
    return con.execute(translated, bound) if bound else con.execute(translated)


def _prepare(
    con: DuckDBPyConnection,
    query: str,
    parameters: Parameters | None,
    database: str | None = None,
    allow_write: bool = True,
) -> tuple[str, Parameters]:
    """Translate the KQL *query* and get *con* into the state the SQL assumes."""
    from . import to_sql
    from .errors import KqlSchemaError

    con.execute("SET TimeZone='UTC'")
    if database is not None:
        _check_database(con, database)
    translated: TranslationResult = to_sql(
        query,
        schema=schema(con),
        parameters=parameters,
        database=database,
        allow_write=allow_write,
    )

    if translated.unbound:
        # DuckDB would raise too, but naming a generated slot rather than the
        # parameter the caller declared.
        raise KqlSchemaError(
            ", ".join(translated.unbound),
            hint="declared query parameter with no value and no default",
        )
    return str(translated), translated.parameters


def databases(con: DuckDBPyConnection) -> list[str]:
    """Every database reachable on *con*, in name order.

    These are the names ``database=`` and ``database("X").T`` accept: an
    attached DuckDB file is a Kusto database here.
    """
    try:
        rows = con.execute(
            "SELECT database_name FROM duckdb_databases() "
            "WHERE NOT internal ORDER BY database_name"
        ).fetchall()
    except Exception:  # noqa: BLE001 - report it as "none reachable", not a crash
        return []
    return [str(row[0]) for row in rows]


def _check_database(con: DuckDBPyConnection, database: str) -> None:
    """Fail with a KQL error naming the database, not a raw catalog error.

    Without this, `database="typo"` surfaces as DuckDB's ``Catalog Error: Table
    with name "typo.T" does not exist because schema "typo" does not exist`` —
    which blames the table for a mistake in the database name, and only when the
    query happens to reference a table at all.
    """
    from .errors import KqlSchemaError

    known = databases(con)
    if database not in known:
        raise KqlSchemaError(
            database,
            hint=f"database not attached to this connection; reachable: {known}",
        )


def df(
    con: DuckDBPyConnection,
    query: str,
    parameters: Parameters | None = None,
    database: str | None = None,
    allow_write: bool = True,
) -> pd.DataFrame:
    """Execute the KQL *query* and return a pandas DataFrame."""
    return kql(con, query, parameters, database, allow_write).df()


def arrow(
    con: DuckDBPyConnection,
    query: str,
    parameters: Parameters | None = None,
    database: str | None = None,
    allow_write: bool = True,
) -> pa.Table:
    """Execute the KQL *query* and return a pyarrow Table."""
    return kql(con, query, parameters, database, allow_write).arrow()


def schema(con: DuckDBPyConnection) -> Schema:
    """Read table -> column names from a DuckDB connection.

    Only ``join`` consults this, but it is cheap enough to gather
    unconditionally and keeps the public API free of a schema argument.
    """
    try:
        rows = con.execute(
            "SELECT table_catalog, table_name, column_name, current_database() "
            "FROM information_schema.columns "
            "ORDER BY table_catalog, table_name, ordinal_position"
        ).fetchall()
    except Exception:  # noqa: BLE001 - a schema-less connection is not an error
        return {}
    out: Schema = {}
    for catalog, table, column, current in rows:
        # Every table is reachable as `Database.Table`, which is what
        # `database("Sales").Orders` lowers to.
        out.setdefault(f"{catalog}.{table}", []).append(column)
        # A bare name means the current database, in KQL as in DuckDB. Keying
        # every catalog's tables by their bare name too would let an attached
        # file silently shadow a table of the same name in the database the
        # caller actually connected to.
        if catalog == current:
            out.setdefault(table, []).append(column)
    return out
