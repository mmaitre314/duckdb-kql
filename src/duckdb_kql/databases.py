"""Database lifecycle commands — `.create`, `.attach`, `.detach`.

These exist only in the standalone Kusto engine, which ships publicly as the
emulator; on a real cluster a database is an ARM resource. They are barely
documented, so `docs/create-database.md` records the grammar and the measured
behaviour, and this module implements the part that maps onto DuckDB::

    .create database Logs volatile
    .create database Logs persist (@"/data/logs.duckdb")
    .attach database Logs from @"/data/logs.duckdb"
    .detach database Logs

They live apart from :mod:`duckdb_kql.control` for the same reason ingestion
does: the commands there read the catalog and translate to one `SELECT`, while
these have a **side effect** and take arguments.

**What a Kusto path is, and what a DuckDB one is.** Kusto persists a database
into *folders* — conventionally two, one for metadata and one for data, which is
what the result's `StoresMetadata` and `StoresData` flags describe. A DuckDB
database is a **single file** holding both. So a path here names the file, and a
command giving several paths uses the first. That is a real difference, and it
is not hidden: the result row reports the `PersistentPath` actually used, so the
answer says which of the paths it took.

**Remote paths are refused.** A blob URI (`https://…`, `abfss://…`) is a valid
Kusto persist target and there is nothing local behind it. Attaching one as
though it were a file would either fail obscurely or, worse, create a local file
named after the URL.

Measured on the emulator, and the details are not guessable:

* `.create database X volatile` returns `DatabaseName, PersistentPath, Created,
  StoresMetadata, StoresData` — with `PersistentPath` **null** for a volatile
  database and both `Stores*` flags **true**.
* The three flags are `System.SByte`/`bool` on the wire — the *query* spelling,
  not the `Boolean`/`bool` that `.show databases` uses for `IsCurrent`.
* Creating a database that exists is an **error**; adding `ifnotexists` makes it
  succeed with `Created` = **false**, which is the only way to tell the two
  apart.
* A bare `.create database X` with neither `persist` nor `volatile` is a
  **syntax error**, so one of the two is required.
* `.detach database X` answers a single `Result` column,
  `'Metadata detach successful.'`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .control import CommandColumn
from .errors import KqlUnsupportedError
from .translate import quote_ident, quote_string

__all__ = [
    "ATTACH_SCHEMA",
    "CREATE_SCHEMA",
    "DETACH_SCHEMA",
    "DatabaseCommand",
    "database_command_schema",
    "is_database_command",
    "parse_database_command",
    "render_database_command",
]


#: `.create database` / `.attach database`. Measured; note `System.SByte` for
#: the booleans, which is the query path's spelling and not `.show databases`'s.
CREATE_SCHEMA: tuple[CommandColumn, ...] = (
    CommandColumn("DatabaseName", "String", "string"),
    CommandColumn("PersistentPath", "String", "string"),
    CommandColumn("Created", "SByte", "bool"),
    CommandColumn("StoresMetadata", "SByte", "bool"),
    CommandColumn("StoresData", "SByte", "bool"),
)

#: `.attach database` reports the same shape a create does.
ATTACH_SCHEMA: tuple[CommandColumn, ...] = CREATE_SCHEMA

DETACH_SCHEMA: tuple[CommandColumn, ...] = (
    CommandColumn("Result", "String", "string"),
)


@dataclass(frozen=True)
class DatabaseCommand:
    """One parsed lifecycle command."""

    verb: str
    name: str
    #: Storage paths, in the order written. Empty for `volatile` and `.detach`.
    paths: tuple[str, ...] = ()
    volatile: bool = False
    if_not_exists: bool = False
    read_only: bool = False

    @property
    def target(self) -> str:
        """The DuckDB database to open: a file, or in-memory when volatile."""
        return self.paths[0] if self.paths else ":memory:"


# Both are interpolated into larger patterns, so the alternation has to be
# grouped: bare, the `|` reaches to the end of whatever it is pasted into, and
# `persist ('a', 'b')` silently stopped matching at all.
_NAME = r"(?:\[\s*'[^']*'\s*\]|\[\s*\"[^\"]*\"\s*\]|[A-Za-z_][A-Za-z0-9_]*)"
_STRING = r"(?:@?'[^']*'|@?\"[^\"]*\")"

_HEAD = re.compile(
    rf"^\s*\.(?P<verb>create|attach|detach)\s+database\s+(?P<name>{_NAME})\s*",
    re.IGNORECASE,
)
_CREATE = re.compile(
    rf"""^\s*\.create\s+database\s+(?P<name>{_NAME})\s*
    (?:
        persist\s*\(\s*(?P<paths>{_STRING}(?:\s*,\s*{_STRING})*)\s*\)
      | (?P<volatile>volatile)
    )
    (?P<ifnotexists>\s+ifnotexists)?
    (?P<props>\s+with\s*\(.*)?
    \s*$""",
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)
_ATTACH = re.compile(
    rf"""^\s*\.attach\s+database\s+(?P<name>{_NAME})\s+
    from\s+(?P<path>{_STRING})
    (?P<readonly>\s+readonly)?
    (?P<version>\s+version\s*=\s*{_STRING})?
    (?P<props>\s+with\s*\(.*)?
    \s*$""",
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)
_DETACH = re.compile(
    rf"""^\s*\.detach\s+database\s+(?P<name>{_NAME})
    (?P<ifexists>\s+ifexists)?
    (?P<skipseal>\s+skip-seal)?
    \s*$""",
    re.IGNORECASE | re.VERBOSE,
)

#: A path we will not open. Everything with a scheme is remote — a blob
#: container, a data lake — and there is nothing local behind it.
_REMOTE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def is_database_command(text: str) -> bool:
    """Whether *text* is a database lifecycle command.

    Only the verb and the word `database` are checked, so anything malformed
    after that is a *parse* failure naming this family — more use than the
    generic "no such control command".
    """
    return _HEAD.match(text) is not None


def database_command_schema(text: str) -> tuple[CommandColumn, ...]:
    """The columns *text* will produce, for callers that declare a shape."""
    match = _HEAD.match(text)
    verb = match.group("verb").lower() if match else ""
    return DETACH_SCHEMA if verb == "detach" else CREATE_SCHEMA


def parse_database_command(text: str) -> DatabaseCommand:
    """Parse a lifecycle command, or raise explaining what is wrong."""
    head = _HEAD.match(text)
    if head is None:  # pragma: no cover - callers check is_database_command
        raise KqlUnsupportedError(f"database command {text.strip()[:60]!r}")
    verb = head.group("verb").lower()

    if _has_pipeline(text):
        # Kusto pipes a command's result through query operators, as
        # `.show tables | limit 3` does and `duckdb_kql.control` supports. It
        # cannot work here: a lifecycle command renders to SEVERAL statements
        # (the ATTACH, then the row describing it), and a statement list is not
        # a subquery. Refused with the reason rather than reported as a
        # malformed command, which is what the grammar check would have said.
        raise KqlUnsupportedError(
            f"database command {text.strip()[:60]!r} with a pipeline",
            hint="a lifecycle command runs statements rather than producing a "
            "subquery, so its result cannot be piped; run it and query after",
        )

    if verb == "create":
        return _parse_create(text)
    if verb == "attach":
        return _parse_attach(text)
    return _parse_detach(text)


def _has_pipeline(text: str) -> bool:
    """Whether a `|` follows the command, ignoring any inside a quoted path."""
    quote = ""
    for char in text:
        if quote:
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == "|":
            return True
    return False


def _parse_create(text: str) -> DatabaseCommand:
    match = _CREATE.match(text)
    if match is None:
        raise KqlUnsupportedError(
            f"database command {text.strip()[:60]!r}",
            hint="expected `.create database <name> (volatile | persist (<path>)) "
            "[ifnotexists]`; one of volatile or persist is required, as it is in "
            "Kusto",
        )
    _refuse_properties(match.group("props"), ".create database")
    paths = tuple(_path(p) for p in re.findall(_STRING, match.group("paths") or ""))
    for path in paths:
        _refuse_remote(path)
    return DatabaseCommand(
        verb="create",
        name=_name(match.group("name")),
        paths=paths,
        volatile=bool(match.group("volatile")),
        if_not_exists=bool(match.group("ifnotexists")),
    )


def _parse_attach(text: str) -> DatabaseCommand:
    match = _ATTACH.match(text)
    if match is None:
        raise KqlUnsupportedError(
            f"database command {text.strip()[:60]!r}",
            hint="expected `.attach database <name> from <path> [readonly]`",
        )
    _refuse_properties(match.group("props"), ".attach database")
    if match.group("version"):
        # A pinned version selects a historical snapshot from Kusto's metadata,
        # which a DuckDB file does not keep. Attaching the current state and
        # calling it the pinned one would answer with the wrong data.
        raise KqlUnsupportedError(
            "attach database version=",
            hint="a DuckDB file keeps no version history to pin to",
        )
    path = _path(match.group("path"))
    _refuse_remote(path)
    return DatabaseCommand(
        verb="attach",
        name=_name(match.group("name")),
        paths=(path,),
        read_only=bool(match.group("readonly")),
    )


def _parse_detach(text: str) -> DatabaseCommand:
    match = _DETACH.match(text)
    if match is None:
        raise KqlUnsupportedError(
            f"database command {text.strip()[:60]!r}",
            hint="expected `.detach database <name>`",
        )
    if match.group("ifexists"):
        # DuckDB has no conditional DETACH — `DETACH IF EXISTS` is a parser
        # error — and a pre-check cannot be expressed in the single statement
        # this translates to. Refused rather than silently detaching
        # unconditionally, which would turn "leave it alone if absent" into an
        # error on the case it exists to avoid.
        raise KqlUnsupportedError(
            "detach database ifexists",
            hint="DuckDB has no conditional DETACH; check with `.show databases` first",
        )
    if match.group("skipseal"):
        raise KqlUnsupportedError(
            "detach database skip-seal",
            hint="sealing is a cluster-side flush; there is nothing here to seal",
        )
    return DatabaseCommand(verb="detach", name=_name(match.group("name")))


def _refuse_properties(props: str | None, command: str) -> None:
    if props:
        # The grammar does not enumerate them and the engine decides which are
        # valid, so there is no set to implement against. Accepting and dropping
        # them would silently discard whatever the caller asked for.
        raise KqlUnsupportedError(
            f"{command} with (...)",
            hint="the property set is engine-defined; none is implemented here",
        )


def _refuse_remote(path: str) -> None:
    if _REMOTE.match(path):
        raise KqlUnsupportedError(
            f"database path {path!r}",
            hint="only local paths are supported; a blob or data-lake URI has "
            "nothing local behind it, and opening it as a file would create one "
            "named after the URL",
        )


def _name(text: str) -> str:
    """`Logs` or `['my db']` -> the name itself."""
    text = text.strip()
    if text.startswith("["):
        return text[1:-1].strip()[1:-1]
    return text


def _path(text: str) -> str:
    """`@"/a/b"` / `'/a/b'` -> the path itself.

    `@` marks a verbatim string in KQL, where a backslash is not an escape —
    which is exactly what a Windows path needs, and why the prefix exists.
    """
    text = text.strip().removeprefix("@")
    return text[1:-1]


def render_database_command(command: DatabaseCommand) -> str:
    """The DuckDB statements for *command*, as one multi-statement string.

    DuckDB runs several `;`-separated statements and returns the last result,
    so the whole command is still one piece of SQL and `to_sql()` keeps working
    with no connection.
    """
    if command.verb == "detach":
        return (
            f"DETACH {quote_ident(command.name)};\n"
            f"SELECT 'Metadata detach successful.' AS \"Result\""
        )

    name = quote_ident(command.name)
    target = quote_string(command.target)
    persistent = "CAST(NULL AS VARCHAR)" if command.volatile else quote_string(
        command.target
    )
    options = " (READ_ONLY)" if command.read_only else ""

    if not command.if_not_exists:
        # Kusto errors when the database exists, and so does a plain ATTACH.
        return (
            f"ATTACH {target} AS {name}{options};\n"
            f"SELECT {quote_string(command.name)} AS \"DatabaseName\", "
            f'{persistent} AS "PersistentPath", '
            'TRUE AS "Created", TRUE AS "StoresMetadata", TRUE AS "StoresData"'
        )

    # `ifnotexists` has to report whether it actually created anything, and the
    # answer stops being knowable the moment the ATTACH runs. So the pre-state
    # is captured into a temp table first — the one place this needs more than
    # a statement and a SELECT.
    return (
        "CREATE OR REPLACE TEMP TABLE _duckdb_kql_created AS SELECT NOT EXISTS ("
        "SELECT 1 FROM duckdb_databases() WHERE database_name = "
        f"{quote_string(command.name)}) AS created;\n"
        f"ATTACH IF NOT EXISTS {target} AS {name}{options};\n"
        f"SELECT {quote_string(command.name)} AS \"DatabaseName\", "
        f'{persistent} AS "PersistentPath", '
        'created AS "Created", TRUE AS "StoresMetadata", TRUE AS "StoresData" '
        "FROM _duckdb_kql_created"
    )
