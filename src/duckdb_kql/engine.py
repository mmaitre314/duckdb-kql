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

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd
    import pyarrow as pa
    from duckdb import DuckDBPyConnection, DuckDBPyRelation

    from .clusters import ClusterMap
    from .entity_groups import EntityGroupMap
    from .translate import TranslationResult

#: Values a caller may supply for a query's declared parameters. Deliberately
#: ``Any``: what is acceptable depends on the *declared KQL type*, which is
#: checked at bind time (``duckdb_kql.params.coerce``) rather than by the
#: signature. See the table in docs/api.md.
Parameters = dict[str, Any]

#: Table name -> column names, as :func:`schema` returns it.
Schema = dict[str, list[str]]

__all__ = [
    "connect", "kql", "query", "execute", "script", "split_script",
    "df", "arrow", "schema", "databases",
    "Parameters", "Schema", "ScriptResult",
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
    clusters: ClusterMap | None = None,
    entity_groups: EntityGroupMap | None = None,
) -> DuckDBPyRelation:
    """Execute the KQL *query* against a DuckDB connection, returning a relation.

    Named for what it takes. It was ``sql()``, which read as though the argument
    were SQL — in a package whose entire job is that the two are not the same.
    The argument is KQL; :func:`duckdb_kql.to_sql` is the one that deals in SQL.
    :func:`duckdb_kql.query` is the same function under the name the Kusto APIs
    use, for callers who reach for that one first.

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
    translated, bound = _prepare(
        con, query, parameters, database, allow_write, clusters, entity_groups
    )
    return con.sql(translated, params=bound) if bound else con.sql(translated)


#: ``query`` is :func:`kql` under the name most callers reach for first — it is
#: what `azure-kusto-data` and the Kusto REST API call this, and what a reader
#: coming from `con.sql()` expects. `kql` stays the primary spelling because in
#: *this* package the point of the name is that the argument is not SQL; the
#: alias exists so nobody has to find that out.
#:
#: Deliberately the same object rather than a wrapper: `query is kql` holds, so
#: the two cannot drift, and a patch or a stub applied to one is applied to
#: both.
query = kql


def execute(
    con: DuckDBPyConnection,
    query: str,
    parameters: Parameters | None = None,
    database: str | None = None,
    allow_write: bool = True,
    clusters: ClusterMap | None = None,
    entity_groups: EntityGroupMap | None = None,
) -> DuckDBPyConnection:
    """Execute the KQL *query* and return the connection, mirroring ``con.execute``.

    Use this for its side effect or its cursor; use :func:`kql` when you want a
    relation to keep composing.
    """
    translated, bound = _prepare(
        con, query, parameters, database, allow_write, clusters, entity_groups
    )
    return con.execute(translated, bound) if bound else con.execute(translated)


@dataclass(frozen=True)
class ScriptResult:
    """What one statement of a script did.

    ``rows`` is materialized rather than left as a relation on purpose. A
    DuckDB relation is lazy and bound to the connection, so a later statement
    that replaces the table an earlier relation reads would change what that
    relation answers — and in an *initialization* script that is the normal
    case, not an edge one. Running each statement to completion before starting
    the next is the whole point of a script.

    A dataclass rather than a `NamedTuple` for one small reason worth recording:
    `index` is the natural name for the field and `tuple.index` is a method, so
    a `NamedTuple` cannot have it. Nothing here wants tuple unpacking anyway.
    """

    #: 1-based position in the script — the *n*th statement, not the *n*th line.
    index: int
    #: 1-based line in the script text where the statement starts, for pointing
    #: at the failure in a file the caller wrote.
    line: int
    #: The statement as written, stripped.
    text: str
    columns: list[str]
    rows: list[tuple[Any, ...]]
    #: The failure, when ``continue_on_errors`` let the script carry on past it.
    #: ``None`` otherwise — without that flag a failure raises instead.
    error: Exception | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


#: A blank line ends a statement. `\S` is deliberate rather than `''`: a line of
#: spaces or a stray `\r` reads as blank to a person writing the file.
_BLANK_LINE = re.compile(r"^[^\S\n]*$")


def split_script(text: str) -> list[tuple[int, str]]:
    """Split a script into ``(line, statement)`` pairs, one per statement.

    **Blank lines separate statements**, which is how Azure Data Explorer's
    database scripts are written — its own example puts one between each
    command, and its ARM template parameter joins them with ``\\n\\n``. The docs
    say "at least one line break", but a single newline cannot be the rule
    here: a statement's text routinely spans lines, from a `datatable(...)`
    literal to the query on the right of a `.set-or-replace <|`.

    Splitting on blank lines is *sound* rather than merely conventional, and for
    a reason worth stating: a KQL string literal cannot contain a raw newline —
    measured, ``print x = 'a<newline>b'`` is a lexer error in all three
    spellings, plain, double-quoted and verbatim. So a blank line in the script
    text can never be inside a literal, and this needs no knowledge of KQL to
    cut in the right places.

    Blank statements are dropped, as are ones holding nothing but ``//``
    comments — a comment between two commands is a comment on the script, not a
    statement that should be sent anywhere.
    """
    statements: list[tuple[int, str]] = []
    lines = text.splitlines()
    start = 0
    while start < len(lines):
        if _BLANK_LINE.match(lines[start]):
            start += 1
            continue
        end = start
        while end < len(lines) and not _BLANK_LINE.match(lines[end]):
            end += 1
        chunk = "\n".join(lines[start:end])
        if _has_code(chunk):
            statements.append((start + 1, chunk.strip()))
        start = end
    return statements


def _has_code(chunk: str) -> bool:
    """Whether *chunk* is more than whitespace and ``//`` comments.

    Needs no knowledge of string literals, which is worth saying because the
    obvious worry is ``print x = "http://example"`` — a `//` a textual strip
    would mistake for a comment. It cannot be reached: getting as far as the
    quote means already having passed a character that is neither whitespace
    nor a comment, and this answers ``True`` there. KQL has no block comment
    form — measured, ``/* … */`` is a syntax error — so a line comment is the
    only thing to skip.
    """
    i, n = 0, len(chunk)
    while i < n:
        if chunk.startswith("//", i):
            newline = chunk.find("\n", i)
            if newline == -1:
                return False
            i = newline + 1
            continue
        if not chunk[i].isspace():
            return True
        i += 1
    return False


def script(
    con: DuckDBPyConnection,
    script: str,
    database: str | None = None,
    allow_write: bool = True,
    clusters: ClusterMap | None = None,
    entity_groups: EntityGroupMap | None = None,
    continue_on_errors: bool = False,
) -> list[ScriptResult]:
    """Run a KQL **script** — several statements, in order, against one connection.

    The shape Azure Data Explorer calls a *database script*: a list of
    statements separated by blank lines, run top to bottom, for getting a
    database into a known state. ``continue_on_errors`` is ADX's flag of the
    same name and the same default — stop at the first failure.

    ::

        duckdb_kql.script(con, '''
            .create database Telemetry

            .set-or-replace Events <|
                datatable(ts: datetime, level: string)
                [ datetime(2024-01-01), "Error" ]

            .set Levels <| Events | distinct level
        ''')

    Two deliberate differences from ADX, both widening rather than narrowing:

    * **Every statement this package can run is allowed.** ADX restricts a
      script to database-level commands beginning `.create`, `.create-or-alter`,
      `.create-merge`, `.alter`, `.alter-merge` or `.add`; here `.set-or-replace`
      and a plain query are as welcome as a `.create`. A script is the natural
      way to seed a database, and seeding it is ingestion.
    * **A statement may be a query.** Its rows come back in the result, which
      makes a script usable as a check ("…and now count what we loaded") rather
      than only as a mutation.

    There is no ``parameters`` argument, and its absence is the point: values
    would have to apply to *every* statement, and a statement that declares none
    would then fail for being handed one it never asked for. Parameterize with
    :func:`kql` per statement.

    Args:
        con: the connection. Statements run against it in order and their
            effects are visible to the ones that follow — a `.create database`
            early in the script is what makes a later `.set` into it work.
        script: the script text — several statements separated by blank lines.
        database: the database unqualified table names belong to, as
            :func:`kql` takes it. Applied to every statement.
        allow_write: pass ``False`` to run a script through the translator
            without letting it write. Ingestion and lifecycle commands then
            refuse, which is a way to check a script before running it.
        clusters: cluster mapping, as :func:`duckdb_kql.to_sql` takes it.
        entity_groups: named entity groups, as :func:`duckdb_kql.to_sql` takes it.
        continue_on_errors: run the remaining statements after one fails, and
            report the failures in the results instead of raising.

    Returns:
        One :class:`ScriptResult` per statement, in script order.

    Raises:
        KqlScriptError: a statement failed and ``continue_on_errors`` is false.
            It names which statement and which line; the underlying error is
            chained on ``__cause__``.
    """
    from .errors import KqlScriptError

    results: list[ScriptResult] = []
    for index, (line, statement) in enumerate(split_script(script), start=1):
        try:
            columns, rows = _run_statement(
                con, statement, database, allow_write, clusters, entity_groups
            )
        except Exception as exc:
            if not continue_on_errors:
                raise KqlScriptError(str(exc), index, line, statement) from exc
            results.append(ScriptResult(index, line, statement, [], [], exc))
            continue
        results.append(ScriptResult(index, line, statement, columns, rows))
    return results


def _run_statement(
    con: DuckDBPyConnection,
    statement: str,
    database: str | None,
    allow_write: bool,
    clusters: ClusterMap | None,
    entity_groups: EntityGroupMap | None,
) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Execute one statement eagerly and read back what it produced."""
    translated, bound = _prepare(
        con, statement, None, database, allow_write, clusters, entity_groups
    )
    cursor = con.execute(translated, bound) if bound else con.execute(translated)
    # `description` is None for a statement that returns no result set. Every
    # KQL statement here does return one — a query its rows, an ingestion or a
    # lifecycle command its summary row — but reading it defensively costs
    # nothing and keeps a future one from crashing this loop.
    if cursor.description is None:
        return [], []
    return [c[0] for c in cursor.description], cursor.fetchall()


def _prepare(
    con: DuckDBPyConnection,
    query: str,
    parameters: Parameters | None,
    database: str | None = None,
    allow_write: bool = True,
    clusters: ClusterMap | None = None,
    entity_groups: EntityGroupMap | None = None,
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
        entity_groups=entity_groups,
        clusters=clusters,
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
    clusters: ClusterMap | None = None,
    entity_groups: EntityGroupMap | None = None,
) -> pd.DataFrame:
    """Execute the KQL *query* and return a pandas DataFrame."""
    return kql(con, query, parameters, database, allow_write, clusters, entity_groups).df()


def arrow(
    con: DuckDBPyConnection,
    query: str,
    parameters: Parameters | None = None,
    database: str | None = None,
    allow_write: bool = True,
    clusters: ClusterMap | None = None,
    entity_groups: EntityGroupMap | None = None,
) -> pa.Table:
    """Execute the KQL *query* and return a pyarrow Table."""
    return kql(con, query, parameters, database, allow_write, clusters, entity_groups).arrow()


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
