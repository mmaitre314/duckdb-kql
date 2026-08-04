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

__all__ = ["connect", "sql", "execute", "df", "arrow", "schema", "Parameters", "Schema"]


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


def sql(
    con: DuckDBPyConnection,
    kql: str,
    parameters: Parameters | None = None,
) -> DuckDBPyRelation:
    """Execute *kql* against a DuckDB connection, returning a relation.

    Sets ``TimeZone='UTC'`` on *con* first. This changes connection state
    deliberately: leaving it to the caller means a machine in a non-UTC zone
    silently returns wrong datetimes.

    The connection also supplies the schema that ``join`` needs, so joins work
    here without the caller passing one.

    *parameters* supplies values for the query's ``declare query_parameters``
    declarations, by KQL name. They are bound by DuckDB, never substituted into
    the SQL, so a caller-supplied string cannot become part of the query::

        duckdb_kql.sql(
            con,
            "declare query_parameters(state:string);"
            " StormEvents | where State == state",
            {"state": user_input},   # safe whatever user_input contains
        )
    """
    translated, bound = _prepare(con, kql, parameters)
    return con.sql(translated, params=bound) if bound else con.sql(translated)


def execute(
    con: DuckDBPyConnection,
    kql: str,
    parameters: Parameters | None = None,
) -> DuckDBPyConnection:
    """Execute *kql* and return the connection, mirroring ``con.execute``.

    Use this for its side effect or its cursor; use :func:`sql` when you want a
    relation to keep composing.
    """
    translated, bound = _prepare(con, kql, parameters)
    return con.execute(translated, bound) if bound else con.execute(translated)


def _prepare(
    con: DuckDBPyConnection, kql: str, parameters: Parameters | None
) -> tuple[str, Parameters]:
    """Translate *kql* and get *con* into the state the SQL assumes."""
    from . import to_sql
    from .errors import KqlSchemaError

    con.execute("SET TimeZone='UTC'")
    translated: TranslationResult = to_sql(kql, schema=schema(con), parameters=parameters)

    if translated.unbound:
        # DuckDB would raise too, but naming a generated slot rather than the
        # parameter the caller declared.
        raise KqlSchemaError(
            ", ".join(translated.unbound),
            hint="declared query parameter with no value and no default",
        )
    return str(translated), translated.parameters


def df(
    con: DuckDBPyConnection,
    kql: str,
    parameters: Parameters | None = None,
) -> pd.DataFrame:
    """Execute *kql* and return a pandas DataFrame."""
    return sql(con, kql, parameters).df()


def arrow(
    con: DuckDBPyConnection,
    kql: str,
    parameters: Parameters | None = None,
) -> pa.Table:
    """Execute *kql* and return a pyarrow Table."""
    return sql(con, kql, parameters).arrow()


def schema(con: DuckDBPyConnection) -> Schema:
    """Read table -> column names from a DuckDB connection.

    Only ``join`` consults this, but it is cheap enough to gather
    unconditionally and keeps the public API free of a schema argument.
    """
    try:
        rows = con.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "ORDER BY table_name, ordinal_position"
        ).fetchall()
    except Exception:  # noqa: BLE001 - a schema-less connection is not an error
        return {}
    out: Schema = {}
    for table, column in rows:
        out.setdefault(table, []).append(column)
    return out
