"""Ingestion control commands — `.set`, `.append`, `.set-or-append`, `.set-or-replace`.

These are the commands that put rows into a table::

    .set-or-replace Events <| datatable(t:datetime, v:long) [ ... ]
    .append Events <| SomeTable | where Level == "Error"

They live apart from :mod:`duckdb_kql.control` because they are a different kind
of thing. The commands there read the catalog and translate to one `SELECT`;
these have a **side effect**, take a whole KQL query as their source, and their
result describes what the write did.

Everything below was measured on the Kusto Emulator, and two of the findings
would have produced quietly wrong SQL if assumed instead:

**`.set-or-replace` replaces rows, not the table.** Feeding it a query whose
schema differs is *rejected* — ``Query schema does not match table schema`` —
so the obvious mapping to DuckDB's ``CREATE OR REPLACE TABLE`` is wrong: that
would silently redefine the table's columns. The right shape is create-if-absent,
then delete, then insert.

**All four share one result schema**, and it is per-extent, not per-command:

===============  =========  ====================================================
column           type       here
===============  =========  ====================================================
`ExtentId`       `Guid`     generated per call — DuckDB has no extents, but the
                            column identifies *this* write and a fresh id is
                            true rather than invented
`OriginalSize`   `Double`   **NULL** — storage figures a DuckDB table does not
`ExtentSize`     `Double`   have. Following `control.py`: a column Kusto has and
`CompressedSize` `Double`   DuckDB does not is present (callers index by name)
`IndexSize`      `Double`   and empty (nothing false is claimed)
`RowCount`       `Int64`    real
===============  =========  ====================================================

A write that ingests nothing returns **zero rows**, not one row saying zero —
measured, and it follows from the result being one row per extent created.

**Known divergence: Kusto's type check is stricter than ours.** Appending an
`int` source to a `long` column is rejected there (``QuerySchema=('int'),
TableSchema=('long')``) and accepted here. It is not fixable at this layer: KQL
`int` and `long` both map to DuckDB `BIGINT`, so the two are the same type by
the time any check could run. The failure direction is loud — a script that
works here is refused by a real cluster — rather than silent, but it is a
divergence and is recorded rather than left to be discovered.

What each verb does when the table is missing or present is also measured:

==================  =================  ==========================
verb                table missing      table present
==================  =================  ==========================
`.set`              creates            **fails**, already exists
`.append`           **fails**, no such appends
`.set-or-append`    creates            appends
`.set-or-replace`   creates            replaces the rows
==================  =================  ==========================
"""

from __future__ import annotations

import re
from typing import NamedTuple

from .control import CommandColumn
from .errors import KqlUnsupportedError

__all__ = [
    "INGESTION_SCHEMA",
    "INGESTION_COLUMNS",
    "INGESTION_VERBS",
    "IngestionCommand",
    "parse_ingestion",
    "is_ingestion_command",
    "render_ingestion",
]

#: The result every ingestion command declares. Measured; see the module
#: docstring for why four of the six are NULL here.
INGESTION_SCHEMA: tuple[CommandColumn, ...] = (
    CommandColumn("ExtentId", "Guid", "guid"),
    CommandColumn("OriginalSize", "Double", "real"),
    CommandColumn("ExtentSize", "Double", "real"),
    CommandColumn("CompressedSize", "Double", "real"),
    CommandColumn("IndexSize", "Double", "real"),
    CommandColumn("RowCount", "Int64", "long"),
)

INGESTION_COLUMNS: tuple[str, ...] = tuple(c.name for c in INGESTION_SCHEMA)

#: Verb -> (create when missing, fail when missing, replace existing rows).
#: Longest first, so `.set-or-replace` is never matched as `.set`.
INGESTION_VERBS: dict[str, tuple[bool, bool, bool]] = {
    ".set-or-replace": (True, False, True),
    ".set-or-append": (True, False, False),
    ".append": (False, True, False),
    ".set": (True, False, False),
}


class IngestionCommand(NamedTuple):
    """A parsed ingestion command."""

    verb: str
    table: str
    #: The KQL after ``<|``, to be translated by the normal query path.
    source: str


_HEAD = re.compile(
    r"""^\s*
    (?P<verb>\.set-or-replace|\.set-or-append|\.append|\.set)
    (?P<async>\s+async\b)?
    \s+(?P<table>\[\s*'[^']*'\s*\]|\[\s*"[^"]*"\s*\]|[A-Za-z_][A-Za-z0-9_]*)
    (?P<props>\s+with\s*\()?
    """,
    re.IGNORECASE | re.VERBOSE,
)


def is_ingestion_command(text: str) -> bool:
    """Whether *text* starts with an ingestion verb.

    Only the verb is checked. Anything malformed after it is a *parse* failure
    with a message about ingestion, which is more use than the generic
    "no such control command".
    """
    return _HEAD.match(text) is not None


def parse_ingestion(text: str) -> IngestionCommand:
    """Parse an ingestion command, or raise explaining what is wrong.

    Refuses two forms Kusto accepts, rather than mistranslating them:

    * ``async`` — the emulator answers it with a **different** result schema
      (`OperationId`, an operation to poll), and there is nothing here to poll;
    * ``with (...)`` — the properties are cluster concepts (`folder`,
      `docstring`, `extend_schema`, `creationTime`, …). `extend_schema` and
      `recreate_schema` genuinely change what the command does, so accepting
      the clause and ignoring it would change results silently.
    """
    match = _HEAD.match(text)
    if match is None:
        raise KqlUnsupportedError(
            f"ingestion command {text.strip()[:60]!r}",
            hint="expected `.set` / `.append` / `.set-or-append` / "
            "`.set-or-replace` <table> <| <query>",
        )
    if match.group("async"):
        raise KqlUnsupportedError(
            "ingestion command:async",
            hint="`async` returns an OperationId to poll for, and there is no "
            "operation queue here; run it synchronously",
        )
    if match.group("props"):
        raise KqlUnsupportedError(
            "ingestion command:with(...)",
            hint="ingestion properties are cluster concepts; `extend_schema` "
            "and `recreate_schema` would change the result, so the clause is "
            "refused rather than ignored",
        )

    body = text[match.end():]
    head, sep, source = body.partition("<|")
    if not sep:
        raise KqlUnsupportedError(
            "ingestion command",
            hint="expected `<|` followed by the query supplying the rows",
        )
    if head.strip():
        raise KqlUnsupportedError(
            f"ingestion command, unexpected {head.strip()[:40]!r}",
            hint="expected `<|` directly after the table name",
        )
    if not source.strip():
        raise KqlUnsupportedError(
            "ingestion command", hint="`<|` is not followed by a query"
        )

    table = match.group("table")
    if table.startswith("["):
        # `['my table']` — the bracketed form, for names a bare identifier
        # cannot spell. The name is the string's value, not its source text.
        table = table.strip()[1:-1].strip()[1:-1]

    return IngestionCommand(match.group("verb").lower(), table, source.strip())


def render_ingestion(command: IngestionCommand, source_sql: str, database: str | None) -> str:
    """The DuckDB statements for *command*, as one multi-statement string.

    DuckDB executes several `;`-separated statements and returns the last
    result, so the whole command still translates to a single piece of SQL —
    `to_sql()` keeps working with no connection, and the CLI can still write a
    `.sql` file.

    Deliberately **not** ``CREATE OR REPLACE TABLE`` for `.set-or-replace`: that
    redefines the columns, and Kusto keeps the table's schema and rejects a
    source that disagrees. Create-if-absent + delete + insert keeps the existing
    definition, so a mismatched source fails on the insert rather than silently
    reshaping the table.

    The source query is evaluated **twice** — once to insert, once to count the
    rows for `RowCount`. These commands exist to load small sample data, and one
    extra pass over a datatable is cheaper than the temporary table it would
    take to avoid it.
    """
    create_if_missing, fail_if_missing, replace = INGESTION_VERBS[command.verb]
    target = _qualified(command.table, database)
    body = f"({source_sql})"

    statements: list[str] = []
    if fail_if_missing:
        # `.append` must fail when the table is absent, which a plain INSERT
        # already does — DuckDB raises a catalog error naming the table.
        pass
    elif create_if_missing and not replace and command.verb == ".set":
        # `.set` must fail when the table is *present*, which CREATE TABLE does.
        statements.append(f"CREATE TABLE {target} AS SELECT * FROM {body} WHERE false")
    else:
        statements.append(
            f"CREATE TABLE IF NOT EXISTS {target} AS SELECT * FROM {body} WHERE false"
        )
    if replace:
        statements.append(f"DELETE FROM {target}")
    statements.append(f"INSERT INTO {target} SELECT * FROM {body}")

    # One row per extent written, so nothing ingested means no rows at all.
    statements.append(
        "SELECT uuid() AS \"ExtentId\", "
        'CAST(NULL AS DOUBLE) AS "OriginalSize", '
        'CAST(NULL AS DOUBLE) AS "ExtentSize", '
        'CAST(NULL AS DOUBLE) AS "CompressedSize", '
        'CAST(NULL AS DOUBLE) AS "IndexSize", '
        f'CAST((SELECT count(*) FROM {body}) AS BIGINT) AS "RowCount" '
        f"WHERE (SELECT count(*) FROM {body}) > 0"
    )
    return ";\n".join(statements)


def _qualified(table: str, database: str | None) -> str:
    from .translate import quote_ident

    name = quote_ident(table)
    return f"{quote_ident(database)}.{name}" if database else name
