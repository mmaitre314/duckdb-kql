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
"""

from __future__ import annotations

from typing import Any

__all__ = ["connect", "sql", "execute", "df", "arrow", "schema"]


def connect(database: str = ":memory:", **kwargs: Any) -> Any:
    """Open a DuckDB connection configured for KQL semantics.

    Equivalent to ``duckdb.connect(...)`` followed by ``SET TimeZone='UTC'``.
    Use it instead of ``duckdb.connect`` unless you are setting the zone
    yourself: on a machine in a non-UTC zone the difference is wrong answers,
    not errors.
    """
    duckdb = _require_duckdb()
    con = duckdb.connect(database, **kwargs)
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
    con: Any,
    kql: str,
    parameters: dict[str, Any] | None = None,
) -> Any:
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


def execute(con: Any, kql: str, parameters: dict[str, Any] | None = None) -> Any:
    """Execute *kql* and return the connection, mirroring ``con.execute``.

    Use this for its side effect or its cursor; use :func:`sql` when you want a
    relation to keep composing.
    """
    translated, bound = _prepare(con, kql, parameters)
    return con.execute(translated, bound) if bound else con.execute(translated)


def _prepare(
    con: Any, kql: str, parameters: dict[str, Any] | None
) -> tuple[str, dict[str, Any]]:
    """Translate *kql* and get *con* into the state the SQL assumes."""
    from . import to_sql
    from .errors import KqlSchemaError

    con.execute("SET TimeZone='UTC'")
    translated = to_sql(kql, schema=schema(con), parameters=parameters)

    unbound = getattr(translated, "unbound", ())
    if unbound:
        # DuckDB would raise too, but naming a generated slot rather than the
        # parameter the caller declared.
        raise KqlSchemaError(
            ", ".join(unbound),
            hint="declared query parameter with no value and no default",
        )
    return str(translated), getattr(translated, "parameters", {})


def df(con: Any, kql: str, parameters: dict[str, Any] | None = None) -> Any:
    """Execute *kql* and return a pandas DataFrame."""
    return sql(con, kql, parameters).df()


def arrow(con: Any, kql: str, parameters: dict[str, Any] | None = None) -> Any:
    """Execute *kql* and return a pyarrow Table."""
    return sql(con, kql, parameters).arrow()


def schema(con: Any) -> dict[str, list[str]]:
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
    out: dict[str, list[str]] = {}
    for table, column in rows:
        out.setdefault(table, []).append(column)
    return out
