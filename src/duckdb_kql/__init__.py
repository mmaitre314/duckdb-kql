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
    duckdb_kql.kql(con, "Logs | where Level == 'Error'").fetchall()

Layer 2 — the ``azure-kusto-data`` shape, for code already written against it::

    from duckdb_kql.kusto import KustoClient, ClientRequestProperties
    from duckdb_kql.kusto.helpers import dataframe_from_result_table

    client = KustoClient("mydata.duckdb")
    response = client.execute("db", "Logs | take 10")
    df = dataframe_from_result_table(response.primary_results[0])
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .clusters import ClusterMap
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
    from .engine import Parameters, arrow, connect, df, execute, kql
    from .schema import Schema
    from .translate import TranslationResult

try:
    # Written by hatch-vcs at build time from the git tag (pyproject.toml).
    from ._version import __version__
except ImportError:  # pragma: no cover - a source tree that was never built
    # Running straight out of `src/` with nothing installed — several of the
    # dev tools do exactly that. Ask the installed distribution, and if there
    # isn't one, say so with a version that could not be mistaken for a release
    # rather than inventing a plausible number.
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _installed_version

    try:
        __version__ = _installed_version("duckdb-kql")
    except PackageNotFoundError:
        __version__ = "0.0.0.dev0+unbuilt"

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
    "kql",
    "execute",
    "df",
    "arrow",
    # type aliases used in the public signatures above, so callers can annotate
    "TranslationResult",
    "Schema",
    "Parameters",
    "__version__",
]

#: Layer 1 names, re-exported here for convenience but defined in ``engine``.
#: Resolved on first access so that importing this package never imports duckdb.
_LAYER1 = frozenset({"connect", "kql", "execute", "df", "arrow"})


#: Types named in the public signatures. Callers need them to annotate their own
#: code, and a name that only exists under TYPE_CHECKING cannot be imported.
#: Resolved from the Layer 0 modules that define them, so this stays duckdb-free.
_LAYER0_TYPES = {"TranslationResult": "translate", "Parameters": "translate",
                 "Schema": "schema"}


def __getattr__(name: str) -> Any:
    if name in _LAYER1:
        from . import engine

        return getattr(engine, name)
    if name in _LAYER0_TYPES:
        import importlib

        module = importlib.import_module(f".{_LAYER0_TYPES[name]}", __name__)
        return getattr(module, name)
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
    database: str | None = None,
    allow_write: bool = True,
    clusters: ClusterMap | None = None,
) -> TranslationResult:
    """Translate *kql* to DuckDB SQL. Requires no connection and no database.

    Returns a ``str`` subclass that also carries ``.parameters`` — the values
    for the placeholders a ``declare query_parameters`` query renders. Pass
    those to DuckDB alongside the SQL; :func:`duckdb_kql.kql` does it for you.

    Args:
        kql: the query text.
        schema: table name -> column names. Only ``join`` consults it.
        parameters: values for the query's declared parameters, by KQL name.
            They are bound as values, never spliced into the SQL, so a value may
            contain any text at all without changing what the query does.
        clusters: what local database stands in for each Kusto cluster, as
            ``{("cluster", "kusto_db"): "duckdb_db"}`` or the nested
            ``{"cluster": {"kusto_db": "duckdb_db"}}``. Omitted, `cluster(...)`
            is refused — treating it as local would answer a question about
            production with local data. Cluster spellings are normalized, so one
            entry covers ``mycluster.example.net``, ``https://mycluster.example.net``
            and a trailing slash.
        database: the database unqualified table names belong to. Rendered into
            the SQL as ``"db"."T"``, so nothing about the connection is
            changed and the answer cannot drift between translating and
            executing. An explicit ``database("other").T`` in the query wins,
            and a name bound by a tabular ``let`` is left alone — it is a CTE,
            not a table. See ``docs/session-state-proposal.md``.

    .. important::
       KQL datetimes are UTC (``docs/TRANSLATION.md`` R8), and DuckDB reads the
       **session** ``TimeZone`` when casting a string that carries no offset. Run
       the returned SQL on a connection with ``SET TimeZone='UTC'`` or datetime
       values will be silently shifted. :func:`duckdb_kql.kql` and
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
    from . import ir
    from .clusters import parse_cluster_map
    from .control import COLUMNS, is_control_command, split_command
    from .control import translate_control_command as _command_sql
    from .ingest import is_ingestion_command, parse_ingestion, render_ingestion
    from .lower import lower, qualify
    from .params import bind
    from .translate import TranslationResult as _Result
    from .translate import to_sql as _emit

    if is_ingestion_command(kql):
        # Ingestion is a control command that *writes*. It is handled before the
        # read-only command table because its source is a whole KQL query, which
        # only this function knows how to translate.
        if not allow_write:
            raise KqlUnsupportedError(
                f"ingestion command {kql.strip().split()[0]}",
                hint="writes are disabled here; see allow_write",
            )
        ingestion = parse_ingestion(kql)
        resolved = parse_cluster_map(clusters)
        rows_sql = _emit(
            qualify(lower(ingestion.source), database, resolved), schema
        )
        return _Result(render_ingestion(ingestion, str(rows_sql), database))

    if is_control_command(kql):
        # A different dialect (see duckdb_kql.control), and one that composes
        # with this one: Kusto pipes a command's tabular result through ordinary
        # query operators, so `.show tables | limit 3` is a command followed by
        # a pipeline. The command half is a closed set of literals; the half
        # after the first `|` is plain KQL and goes through the normal path with
        # the command standing in as its source.
        command, pipeline = split_command(kql)
        head = _command_sql(command)  # raises, naming the ones that work
        if not pipeline:
            return _Result(head)

        # Lowered against a placeholder table, whose source is then replaced.
        # `lower` wants a whole query and the pipeline alone is not one.
        tail = lower(f"__command__ {pipeline}")
        source = ir.CommandSource(head, COLUMNS[command], command)
        return _emit(
            qualify(ir.Query(source, tail.operators), database, parse_cluster_map(clusters)),
            schema,
        )

    query = qualify(lower(kql), database, parse_cluster_map(clusters))
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
