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

from .errors import KqlUnsupportedError

__all__ = [
    "is_control_command",
    "translate_control_command",
    "split_command",
    "COLUMNS",
    "SUPPORTED",
    "UNSUPPORTED_HINT",
]

#: Commands this package implements, normalized. Anything else `.`-prefixed
#: describes administering a cluster — ingestion, policies, schema management —
#: and there is no cluster for it to act on.
SUPPORTED = (".show version", ".show databases", ".show tables")

#: What each command produces, measured on the emulator. Needed once a command
#: can be piped: the operators that resolve names before the query runs — `join`
#: renaming, `extend`'s in-place ordering — have to know them.
COLUMNS: dict[str, tuple[str, ...]] = {
    ".show version": (
        "BuildVersion",
        "BuildTime",
        "ServiceType",
        "ProductVersion",
        "ServiceOffering",
    ),
    ".show databases": (
        "DatabaseName",
        "PersistentStorage",
        "Version",
        "IsCurrent",
        "DatabaseAccessMode",
        "PrettyName",
        "ReservedSlot1",
        "DatabaseId",
        "InTransitionTo",
        "SuspensionState",
    ),
    ".show tables": ("TableName", "DatabaseName", "Folder", "DocString"),
}

#: Said once, so Layer 0's KqlUnsupportedError and Layer 2's
#: KustoUnsupportedError cannot drift into describing different sets.
UNSUPPORTED_HINT = (
    "supported control commands are "
    + ", ".join(SUPPORTED)
    + "; the rest administer a cluster, and there is no cluster here"
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


def translate_control_command(text: str) -> str:
    """Translate a supported control command to DuckDB SQL.

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
    build = _COMMANDS.get(command)
    if build is None:
        raise KqlUnsupportedError(
            f"control command {text.strip()!r}", hint=UNSUPPORTED_HINT
        )
    return build()


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

#: Normalized command -> the function that produces its SQL.
_COMMANDS: dict[str, Callable[[], str]] = {
    ".show version": _version_sql,
    ".show databases": _databases_sql,
    ".show tables": _tables_sql,
}
