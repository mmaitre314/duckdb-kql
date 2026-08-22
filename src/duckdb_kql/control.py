"""Control commands — the `.`-prefixed dialect, which is not KQL.

Kusto has two languages. Queries (`Requests | count`) are one; **control
commands** (`.show tables`) are the other, with their own grammar, their own
endpoint (`/v1/rest/mgmt`, not `/v1/rest/query`) and their own result shapes.
Microsoft's `Kql.g4` — the grammar this package vendors — describes only the
first, so a control command reaching the ANTLR parser produced a wall of
expected-token noise that named every keyword in the language except the one
thing that would have helped::

    KqlSyntaxError: could not parse KQL at 1:0: extraneous input '.' expecting
    {'*', '-', '[', '(', '+', 'access', 'aggregations', 'alias', …

They are handled here instead, ahead of the parser, and translated straight to
SQL over DuckDB's catalog.

**The column shapes are measured, not designed.** Each was taken from the Kusto
Emulator (`docs/oracle-harness.md`), because code written against the real SDK
indexes into these by name and a plausible-looking subset is the usual way that
breaks — the previous hand-rolled version in the Kusto client reported three
columns for `.show databases` where Kusto reports ten::

    .show version    BuildVersion, BuildTime, ServiceType, ProductVersion,
                     ServiceOffering
    .show databases  DatabaseName, PersistentStorage, Version, IsCurrent,
                     DatabaseAccessMode, PrettyName, ReservedSlot1, DatabaseId,
                     InTransitionTo, SuspensionState
    .show tables     TableName, DatabaseName, Folder, DocString

Where a column describes something a cluster has and a DuckDB file does not — a
`DatabaseId` GUID, a `SuspensionState` — it is NULL. Inventing a plausible value
would be the same failure as a wrong answer, so the column is present (callers
index by name) and empty (nothing false is claimed).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import NamedTuple

from .entity_groups import ResolvedGroups
from .errors import KqlUnsupportedError
from .translate import quote_string

__all__ = [
    "is_control_command",
    "translate_control_command",
    "split_command",
    "COLUMNS",
    "SCHEMA",
    "SUPPORTED",
    "UNSUPPORTED_HINT",
]

class CommandColumn(NamedTuple):
    """One column of a command's result, as Kusto *declares* it on the wire.

    Both labels are recorded because neither is derivable from the other, and
    neither is derivable from the KQL type. See :data:`SCHEMA`.
    """

    #: The name a caller indexes by.
    name: str
    #: The v1 REST ``DataType``: a .NET type name without its namespace.
    data_type: str
    #: The ``ColumnType``: a CSL type name. ``None`` for the commands that omit
    #: the field entirely.
    column_type: str | None


#: What each command produces, measured on the emulator. Needed once a command
#: can be piped: the operators that resolve names before the query runs — `join`
#: renaming, `extend`'s in-place ordering — have to know the names. The two type
#: labels are needed by the REST server, which reports a bare command's schema
#: as Kusto declares it rather than as DuckDB happens to compute it.
#:
#: **The labels are per-command data, not a rule.** A control command's result
#: schema is declared inside Kusto, and the declarations disagree with each
#: other and with the query path. Measured on the emulator:
#:
#: * `.show databases`.IsCurrent is `Boolean`/`bool`, but a `bool` column in a
#:   *query* result is `SByte`/`bool` — and `.show operations`.ShouldRetry, also
#:   a command, is `SByte` too.
#: * `.show materialized-views`.Lookback is `TimeSpan`/**`time`**, the legacy CSL
#:   spelling, while `.show operations`.Duration is `TimeSpan`/`timespan`.
#: * `.show version` and `.show database schema` omit `ColumnType` altogether.
#:
#: So these are transcribed, not generated. Deriving them from
#: :func:`duckdb_kql.types.rest_datatype` would produce a self-consistent table
#: that disagreed with Kusto on two of the five commands here.
SCHEMA: dict[str, tuple[CommandColumn, ...]] = {
    ".show version": (
        CommandColumn("BuildVersion", "String", None),
        CommandColumn("BuildTime", "DateTime", None),
        CommandColumn("ServiceType", "String", None),
        CommandColumn("ProductVersion", "String", None),
        CommandColumn("ServiceOffering", "String", None),
    ),
    ".show databases": (
        CommandColumn("DatabaseName", "String", "string"),
        CommandColumn("PersistentStorage", "String", "string"),
        CommandColumn("Version", "String", "string"),
        CommandColumn("IsCurrent", "Boolean", "bool"),
        CommandColumn("DatabaseAccessMode", "String", "string"),
        CommandColumn("PrettyName", "String", "string"),
        CommandColumn("ReservedSlot1", "Boolean", "bool"),
        CommandColumn("DatabaseId", "Guid", "guid"),
        CommandColumn("InTransitionTo", "String", "string"),
        CommandColumn("SuspensionState", "String", "string"),
    ),
    ".show tables": (
        CommandColumn("TableName", "String", "string"),
        CommandColumn("DatabaseName", "String", "string"),
        CommandColumn("Folder", "String", "string"),
        CommandColumn("DocString", "String", "string"),
    ),
    # Measured: `Entities` is a **string** holding a JSON array of the entity
    # references, not a dynamic. Reporting it as dynamic would look tidier and
    # disagree with every client that reads it back.
    ".show entity_groups": (
        CommandColumn("Name", "String", "string"),
        CommandColumn("Entities", "String", "string"),
    ),
    # The web UI reads this one to build its schema tree: `CslOutputSchema`
    # carries each table's columns as `name:kqltype`, which is why the type
    # mapping has to be right here and not merely plausible.
    #
    # `Properties` is `Object` on Azure Data Explorer today; the pinned emulator
    # image still says `JObject`. The service's own answer wins — the web UI is
    # the consumer, and it is the service the UI is written against.
    ".show databases entities": (
        CommandColumn("DatabaseName", "String", "string"),
        CommandColumn("EntityType", "String", "string"),
        CommandColumn("EntityName", "String", "string"),
        CommandColumn("DocString", "String", "string"),
        CommandColumn("Folder", "String", "string"),
        CommandColumn("CslInputSchema", "String", "string"),
        CommandColumn("Content", "String", "string"),
        CommandColumn("CslOutputSchema", "String", "string"),
        CommandColumn("Properties", "Object", "dynamic"),
    ),
    # Always empty: a materialized view is a cluster-side incremental
    # aggregation with its own scheduler, and there is nothing here to schedule.
    # Reported as an empty table rather than refused, because the web UI asks
    # for it while opening a database and a refusal reads as a broken connection.
    ".show materialized-views": (
        CommandColumn("Name", "String", "string"),
        CommandColumn("SourceTable", "String", "string"),
        CommandColumn("Query", "String", "string"),
        CommandColumn("MaterializedTo", "DateTime", "datetime"),
        CommandColumn("LastRun", "DateTime", "datetime"),
        CommandColumn("LastRunResult", "String", "string"),
        CommandColumn("IsHealthy", "Boolean", "bool"),
        CommandColumn("IsEnabled", "Boolean", "bool"),
        CommandColumn("Status", "String", "string"),
        CommandColumn("Folder", "String", "string"),
        CommandColumn("DocString", "String", "string"),
        CommandColumn("AutoUpdateSchema", "Boolean", "bool"),
        CommandColumn("EffectiveDateTime", "DateTime", "datetime"),
        CommandColumn("LastDefinitionUpdate", "DateTime", "datetime"),
        CommandColumn("Lookback", "TimeSpan", "time"),
        CommandColumn("LookbackColumn", "String", "string"),
    ),
}

#: Just the names, which is all the pipe machinery needs. Derived so the two
#: cannot disagree about a command's shape.
COLUMNS: dict[str, tuple[str, ...]] = {
    command: tuple(column.name for column in columns) for command, columns in SCHEMA.items()
}

#: Commands this package implements, normalized. Anything else `.`-prefixed
#: describes administering a cluster — ingestion, policies, schema management —
#: and there is no cluster for it to act on.
#:
#: Derived, because a command listed here without a schema would be refused by
#: the translator and announced as supported by the error message.
SUPPORTED: tuple[str, ...] = tuple(SCHEMA)

#: Said once, so Layer 0's KqlUnsupportedError and Layer 2's
#: KustoUnsupportedError cannot drift into describing different sets.
UNSUPPORTED_HINT = (
    "supported control commands are "
    + ", ".join(SUPPORTED)
    + "; ingestion (.set / .append / .set-or-append / .set-or-replace) is "
    "handled separately, by duckdb_kql.kql(), by KustoClient.execute(), and by "
    "`duckdb-kql serve --allow-write`; the rest administer a cluster, and there "
    "is no cluster here"
)

_WHITESPACE = re.compile(r"\s+")


def is_control_command(text: str) -> bool:
    """Whether *text* is a control command rather than a query.

    A leading dot is the whole rule, and it is unambiguous: no KQL query can
    begin with one.
    """
    return text.lstrip().startswith(".")


def _normalize(text: str) -> str:
    """`  .SHOW   Tables ` -> `.show tables`. Kusto is case-insensitive here."""
    return _WHITESPACE.sub(" ", text.strip().rstrip(";").strip()).lower()


def split_command(text: str) -> tuple[str, str]:
    """Split `.show tables | limit 3` into its command and its pipeline.

    Kusto composes the two dialects: a control command produces a table, and
    ordinary query operators can be piped onto it. Verified on the emulator —
    `| limit`, `| count`, `| where`, `| project`, `| summarize` and `| extend`
    all work on `.show tables`.

    The split matches a **known command as a prefix** rather than cutting at the
    first `|`. That ordering matters: `|` is not only a pipe in this dialect
    (`.ingest inline into table T <| …`), so cutting first would mis-split a
    command we do not support and report the wrong half as the problem. Matching
    the head first means only commands whose syntax we know are ever divided.

    Returns ``(command, pipeline)`` with *pipeline* empty when there is none, and
    the whole input as *command* when it matches nothing — leaving the refusal,
    and the error message, to the caller.
    """
    collapsed = _WHITESPACE.sub(" ", text.strip().rstrip(";").strip())
    lowered = collapsed.lower()
    for command in sorted(SUPPORTED, key=len, reverse=True):
        if lowered == command:
            return command, ""
        if lowered.startswith(command):
            # Sliced from `collapsed`, not `lowered`: the command half is
            # case-insensitive but the pipeline is KQL, where identifiers are
            # case-sensitive (R7). Lowercasing it turned `| project TableName`
            # into a column called `tablename`. The indices line up because the
            # commands are ASCII, so lowering the matched prefix cannot change
            # its length.
            rest = collapsed[len(command) :].lstrip()
            if rest.startswith("|"):
                return command, rest
    return lowered, ""


def translate_control_command(
    text: str, entity_groups: ResolvedGroups | None = None
) -> str:
    """Translate a supported control command to DuckDB SQL.

    *entity_groups* is only read by `.show entity_groups`, which reports the
    caller's mapping — there is no cluster holding the real thing.

    Raises:
        KqlUnsupportedError: for every other `.`-command, naming the ones that
            do work rather than leaving the caller to guess.
    """
    command, pipeline = split_command(text)
    if pipeline:
        raise KqlUnsupportedError(
            f"control command {text.strip()!r}",
            hint="a piped command is translated by duckdb_kql.to_sql, not here",
        )
    if command == ".show entity_groups":
        return _entity_groups_sql(entity_groups)
    build = _COMMANDS.get(command)
    if build is None:
        raise KqlUnsupportedError(f"control command {text.strip()!r}", hint=UNSUPPORTED_HINT)
    return build()


def _entity_groups_sql(groups: ResolvedGroups | None) -> str:
    """`.show entity_groups`, over the caller-supplied mapping.

    A named entity group is cluster-side state and there is no cluster, so what
    this reports is the `entity_groups=` mapping — which is exactly the thing a
    `macro-expand MyGroup` will resolve against. With no mapping the answer is
    no rows, which is what a cluster with no groups also returns.

    Measured shape: `Name`, then `Entities` as a **string** holding a JSON array
    of the entity references, separators and all —
    ``["database('a')","database('b')"]``, with no space after the comma.
    """
    import json  # noqa: PLC0415

    if not groups:
        return (
            "SELECT CAST(NULL AS VARCHAR) AS \"Name\", "
            "CAST(NULL AS VARCHAR) AS \"Entities\" WHERE FALSE"
        )
    rows = []
    for name, entities in sorted(groups.items()):
        listed = json.dumps([e.as_kql() for e in entities], separators=(",", ":"))
        rows.append(
            f"SELECT {quote_string(name)} AS \"Name\", "
            f"{quote_string(listed)} AS \"Entities\""
        )
    return " UNION ALL ".join(rows)


def _version_sql() -> str:
    """`.show version`, five columns.

    ``BuildTime`` is NULL on purpose. This package has no build timestamp, and
    the hand-rolled version this replaces returned a hard-coded
    ``datetime(2026, 1, 1)`` — a real-looking date that was never true of any
    build. ``ProductVersion`` reports DuckDB's version too, resolved by the
    engine at execution, which is the fact a reader of this command wants.

    Built per call rather than stored: the version is not a constant, and a
    module-level string would freeze whatever it was at import time.
    """
    from . import __version__

    return (
        f"SELECT '{__version__}' AS \"BuildVersion\", "
        'CAST(NULL AS TIMESTAMP) AS "BuildTime", '
        "'Engine' AS \"ServiceType\", "
        f"'duckdb-kql {__version__} on DuckDB ' || version() AS \"ProductVersion\", "
        "'' AS \"ServiceOffering\""
    )


def _databases_sql() -> str:
    """`.show databases`, ten columns.

    ``Version`` is DuckDB's, the closest true statement — Kusto's is a database
    *schema* version (``v7.0``) with no analogue here. ``IsCurrent`` is real: a
    DuckDB connection can have several databases attached.
    """
    return _DATABASES_SQL


def _tables_sql() -> str:
    """`.show tables`, four columns.

    Views are included deliberately: ``CREATE VIEW Logs AS SELECT * FROM
    'logs/*.parquet'`` is how this package's own docs tell people to expose files
    to KQL, and a listing that omitted them would answer "no tables" to someone
    whose queries work.
    """
    return _TABLES_SQL


_DATABASES_SQL = """\
SELECT database_name AS "DatabaseName",
       coalesce(path, '') AS "PersistentStorage",
       version() AS "Version",
       database_name = current_database() AS "IsCurrent",
       CASE WHEN readonly THEN 'ReadOnly' ELSE 'ReadWrite' END AS "DatabaseAccessMode",
       CAST(NULL AS VARCHAR) AS "PrettyName",
       CAST(NULL AS BOOLEAN) AS "ReservedSlot1",
       CAST(NULL AS UUID) AS "DatabaseId",
       '' AS "InTransitionTo",
       CAST(NULL AS VARCHAR) AS "SuspensionState"
FROM duckdb_databases()
WHERE NOT internal
ORDER BY "DatabaseName\""""

_TABLES_SQL = """\
SELECT table_name AS "TableName",
       table_catalog AS "DatabaseName",
       CAST(NULL AS VARCHAR) AS "Folder",
       CAST(NULL AS VARCHAR) AS "DocString"
FROM information_schema.tables
WHERE table_catalog = current_database()
ORDER BY "TableName\""""


def _entities_sql() -> str:
    """`.show databases entities` — one row per table, with its column list.

    **Every** database, not the current one. That is measured, not assumed: on
    the emulator with a second database attached, running it from `NetDefaultDB`
    returns `NetDefaultDB.Users` *and* `Sales.Orders`, and running it from
    `Sales` returns the same rows in the same order. `.show tables` is the one
    that is current-database-only — also measured, and left that way.

    The distinction is what makes an attached database usable: the Azure Data
    Explorer web UI draws its schema tree from this command, so filtering to the
    current database would hide everything `duckdb-kql serve --init` attached.

    `CslOutputSchema` is the load-bearing column: `C0:long, C1:datetime`, in KQL
    type names. The UI reads it for each table's columns, so a wrong type here is
    visible in the product rather than buried.
    """
    from .types import kusto_type_sql

    column_list = (
        "SELECT string_agg(c.column_name || ':' || "
        f"{kusto_type_sql('c.data_type')}, ', ' ORDER BY c.ordinal_position) "
        "FROM information_schema.columns c "
        "WHERE c.table_catalog = t.table_catalog AND c.table_schema = t.table_schema "
        "AND c.table_name = t.table_name"
    )
    return f"""\
SELECT t.table_catalog AS "DatabaseName",
       'Table' AS "EntityType",
       t.table_name AS "EntityName",
       '' AS "DocString",
       '' AS "Folder",
       '' AS "CslInputSchema",
       '' AS "Content",
       coalesce(({column_list}), '') AS "CslOutputSchema",
       CAST('{{"column_docs":{{}}}}' AS JSON) AS "Properties"
FROM information_schema.tables t
ORDER BY "DatabaseName", "EntityName"
"""


def _materialized_views_sql() -> str:
    """`.show materialized-views` — the sixteen columns, and never a row.

    Typed by casting rather than left to inference: an empty result still has to
    report `IsHealthy` as a bool and `Lookback` as a timespan, or a client that
    reads the schema of an empty table gets a different answer from Kusto's.
    """
    return """\
SELECT CAST(NULL AS VARCHAR) AS "Name",
       CAST(NULL AS VARCHAR) AS "SourceTable",
       CAST(NULL AS VARCHAR) AS "Query",
       CAST(NULL AS TIMESTAMP) AS "MaterializedTo",
       CAST(NULL AS TIMESTAMP) AS "LastRun",
       CAST(NULL AS VARCHAR) AS "LastRunResult",
       CAST(NULL AS BOOLEAN) AS "IsHealthy",
       CAST(NULL AS BOOLEAN) AS "IsEnabled",
       CAST(NULL AS VARCHAR) AS "Status",
       CAST(NULL AS VARCHAR) AS "Folder",
       CAST(NULL AS VARCHAR) AS "DocString",
       CAST(NULL AS BOOLEAN) AS "AutoUpdateSchema",
       CAST(NULL AS TIMESTAMP) AS "EffectiveDateTime",
       CAST(NULL AS TIMESTAMP) AS "LastDefinitionUpdate",
       CAST(NULL AS INTERVAL) AS "Lookback",
       CAST(NULL AS VARCHAR) AS "LookbackColumn"
WHERE FALSE"""


#: Normalized command -> the function that produces its SQL.
_COMMANDS: dict[str, Callable[[], str]] = {
    ".show version": _version_sql,
    ".show databases": _databases_sql,
    ".show databases entities": _entities_sql,
    ".show tables": _tables_sql,
    ".show materialized-views": _materialized_views_sql,
}
