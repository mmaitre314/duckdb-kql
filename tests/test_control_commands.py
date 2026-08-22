"""`.show tables` and friends — a different dialect, reached the same way.

Control commands are not KQL. They have their own grammar, their own Kusto
endpoint, and their own result shapes, and the vendored `Kql.g4` describes only
queries — so `duckdb_kql.kql(con, ".show tables")` used to fail with a syntax
error listing every keyword in the language.

Every column name and type below was **measured on the Kusto Emulator**, not
designed:

    .show version    BuildVersion, BuildTime, ServiceType, ProductVersion,
                     ServiceOffering
    .show databases  DatabaseName, PersistentStorage, Version, IsCurrent,
                     DatabaseAccessMode, PrettyName, ReservedSlot1, DatabaseId,
                     InTransitionTo, SuspensionState
    .show tables     TableName, DatabaseName, Folder, DocString

That matters more here than for a query. Callers index these by name — the SDK's
own samples do — so a plausible subset breaks them at the point of use rather
than at the point of translation, and the hand-rolled version this replaces had
drifted to three columns for `.show databases` where Kusto has ten.

Reproduce with:

    docker compose up -d kusto
    python -c "
    from duckdb_kql.oracle import KustoEmulator
    print(KustoEmulator().command('.show tables').to_dict())"
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.control import SUPPORTED, is_control_command

duckdb = pytest.importorskip("duckdb")

#: The full schema Kusto reports for each command — `(name, ordinal, DataType,
#: ColumnType)` — captured verbatim from `.show X | getschema` on the emulator.
#:
#: This is the stronger form of the name check below: a column can carry the
#: right name and the wrong type, and a caller reading `IsCurrent` as a bool or
#: `DatabaseId` as a GUID would find out at the point of use. `getschema` turns
#: "what type is this column" into rows, so the whole shape is one assertion.
KUSTO_SCHEMA: dict[str, list[tuple[str, int, str, str]]] = {
    ".show version": [
        ("BuildVersion", 0, "System.String", "string"),
        ("BuildTime", 1, "System.DateTime", "datetime"),
        ("ServiceType", 2, "System.String", "string"),
        ("ProductVersion", 3, "System.String", "string"),
        ("ServiceOffering", 4, "System.String", "string"),
    ],
    ".show entity_groups": [
        ("Name", 0, "System.String", "string"),
        ("Entities", 1, "System.String", "string"),
    ],
    ".show databases": [
        ("DatabaseName", 0, "System.String", "string"),
        ("PersistentStorage", 1, "System.String", "string"),
        ("Version", 2, "System.String", "string"),
        ("IsCurrent", 3, "System.SByte", "bool"),
        ("DatabaseAccessMode", 4, "System.String", "string"),
        ("PrettyName", 5, "System.String", "string"),
        ("ReservedSlot1", 6, "System.SByte", "bool"),
        ("DatabaseId", 7, "System.Guid", "guid"),
        ("InTransitionTo", 8, "System.String", "string"),
        ("SuspensionState", 9, "System.String", "string"),
    ],
    ".show databases entities": [
        ("DatabaseName", 0, "System.String", "string"),
        ("EntityType", 1, "System.String", "string"),
        ("EntityName", 2, "System.String", "string"),
        ("DocString", 3, "System.String", "string"),
        ("Folder", 4, "System.String", "string"),
        ("CslInputSchema", 5, "System.String", "string"),
        ("Content", 6, "System.String", "string"),
        ("CslOutputSchema", 7, "System.String", "string"),
        ("Properties", 8, "System.Object", "dynamic"),
    ],
    ".show tables": [
        ("TableName", 0, "System.String", "string"),
        ("DatabaseName", 1, "System.String", "string"),
        ("Folder", 2, "System.String", "string"),
        ("DocString", 3, "System.String", "string"),
    ],
    # Worth reading against `control.SCHEMA`, which records the same command's
    # columns as its *REST envelope* declares them. They disagree: here
    # `IsHealthy` is `System.SByte` and `Lookback` is `timespan`; there they are
    # `Boolean` and `time`. Both are Kusto's answers — one is what a query
    # operator sees, the other what the management endpoint announces.
    ".show materialized-views": [
        ("Name", 0, "System.String", "string"),
        ("SourceTable", 1, "System.String", "string"),
        ("Query", 2, "System.String", "string"),
        ("MaterializedTo", 3, "System.DateTime", "datetime"),
        ("LastRun", 4, "System.DateTime", "datetime"),
        ("LastRunResult", 5, "System.String", "string"),
        ("IsHealthy", 6, "System.SByte", "bool"),
        ("IsEnabled", 7, "System.SByte", "bool"),
        ("Status", 8, "System.String", "string"),
        ("Folder", 9, "System.String", "string"),
        ("DocString", 10, "System.String", "string"),
        ("AutoUpdateSchema", 11, "System.SByte", "bool"),
        ("EffectiveDateTime", 12, "System.DateTime", "datetime"),
        ("LastDefinitionUpdate", 13, "System.DateTime", "datetime"),
        ("Lookback", 14, "System.TimeSpan", "timespan"),
        ("LookbackColumn", 15, "System.String", "string"),
    ],
}

#: Measured on the emulator. Order is part of the shape. Derived from the schema
#: above so the two cannot describe different tables.
KUSTO_COLUMNS = {
    command: [name for name, _, _, _ in columns]
    for command, columns in KUSTO_SCHEMA.items()
}

#: Commands whose result is always empty here, and legitimately so. A
#: materialized view is a cluster-side incremental aggregation with its own
#: scheduler; there is nothing in a DuckDB file to schedule. The command still
#: has to *answer* — the web UI asks for it while opening a database, and a
#: refusal reads as a broken connection — so it returns its sixteen columns and
#: no rows.
#
#: `.show entity_groups` joins it for a different reason: a named entity group
#: is cluster-side state, so what this reports is the caller's
#: `entity_groups=` mapping — and this fixture passes none.
ALWAYS_EMPTY = {".show materialized-views", ".show entity_groups"}


@pytest.fixture(scope="module")
def con():
    c = duckdb_kql.connect()
    c.execute("CREATE TABLE Requests(n INTEGER); INSERT INTO Requests VALUES (1)")
    # A view, because that is how the docs tell people to expose parquet/CSV.
    c.execute("CREATE VIEW Logs AS SELECT 'Error' AS Level")
    return c


# ---------------------------------------------------------------------------
# They run at all — the reported bug
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", SUPPORTED)
def test_the_command_runs_through_the_public_api(con, command: str) -> None:
    """`duckdb_kql.kql(con, ".show tables")` raised KqlSyntaxError."""
    result = duckdb_kql.kql(con, command)
    # A table, not an exception, is what the bug was about — and a command with
    # nothing to report still produced one, so the shape is what to assert.
    assert list(result.columns) == KUSTO_COLUMNS[command]
    rows = result.fetchall()
    if command not in ALWAYS_EMPTY:
        assert rows, f"{command} returned nothing"


@pytest.mark.parametrize("command", sorted(ALWAYS_EMPTY))
def test_an_always_empty_command_answers_rather_than_refusing(con, command: str) -> None:
    """Zero rows and the full column list — not an error.

    The distinction matters to a client: an empty table says "there are none of
    these", and a refusal says "this connection is broken".
    """
    result = duckdb_kql.kql(con, command)
    assert result.fetchall() == []
    assert list(result.columns) == KUSTO_COLUMNS[command]


@pytest.mark.parametrize("command", SUPPORTED)
def test_the_columns_are_the_ones_kusto_returns(con, command: str) -> None:
    assert list(duckdb_kql.kql(con, command).columns) == KUSTO_COLUMNS[command]


@pytest.mark.parametrize("command", SUPPORTED)
def test_the_whole_schema_is_the_one_kusto_returns(con, command: str) -> None:
    """Names, order, ordinals and types — checked with `getschema` itself.

    The names alone were already asserted above, and names alone are not enough:
    `IsCurrent` typed as a string, or `DatabaseId` as text rather than a guid,
    would pass that check and break a caller that reads them. Running the same
    `| getschema` we run against Kusto makes the comparison total.
    """
    rows = [tuple(r) for r in duckdb_kql.kql(con, f"{command} | getschema").fetchall()]
    assert rows == [tuple(r) for r in KUSTO_SCHEMA[command]]


@pytest.mark.parametrize(
    "spelling", [".show tables", ".SHOW TABLES", "  .Show   Tables  ", ".show tables;"]
)
def test_spelling_and_spacing_do_not_matter(con, spelling: str) -> None:
    """Kusto is case-insensitive here, and a stray trailing `;` is common."""
    assert list(duckdb_kql.kql(con, spelling).columns) == KUSTO_COLUMNS[".show tables"]


# ---------------------------------------------------------------------------
# What they actually report
# ---------------------------------------------------------------------------


def test_show_tables_lists_tables_and_views(con) -> None:
    """A view is a queryable table as far as KQL is concerned.

    Omitting views would answer "no tables" to someone whose queries work, which
    is the more misleading of the two options.
    """
    names = {row[0] for row in duckdb_kql.kql(con, ".show tables").fetchall()}
    assert {"Requests", "Logs"} <= names

    # ...and the listing is true: everything in it can actually be queried.
    for name in ("Requests", "Logs"):
        duckdb_kql.kql(con, f"{name} | count").fetchall()


def test_show_databases_marks_the_current_one(con) -> None:
    rows = duckdb_kql.kql(con, ".show databases").fetchall()
    current = [r for r in rows if r[3]]  # IsCurrent
    assert len(current) == 1, f"expected exactly one current database, got {rows}"


def test_show_version_reports_this_package_and_duckdb(con) -> None:
    row = duckdb_kql.kql(con, ".show version").fetchone()
    assert row[0] == duckdb_kql.__version__
    assert "duckdb-kql" in row[3] and "DuckDB" in row[3]


def test_nothing_is_invented_where_there_is_no_answer(con) -> None:
    """The columns a cluster has and a DuckDB file does not are NULL.

    An earlier version returned `datetime(2026, 1, 1)` for BuildTime — a
    real-looking timestamp that was never true of any build. A plausible wrong
    value is the failure mode this project exists to avoid; a null is not.
    """
    version = duckdb_kql.kql(con, ".show version").fetchone()
    assert version[1] is None, "BuildTime should be null, not a fabricated date"

    databases = duckdb_kql.kql(con, ".show databases").fetchone()
    assert databases[7] is None, "DatabaseId should be null — there is no cluster GUID"


# ---------------------------------------------------------------------------
# Refusing the rest
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        ".drop table Requests",
        ".ingest inline into table T [1]",
        ".show cluster",
        ".create table T (a:int)",
    ],
)
def test_an_unimplemented_command_is_refused_by_name(con, command: str) -> None:
    """Loudly, and saying what does work — these administer a cluster."""
    with pytest.raises(duckdb_kql.KqlUnsupportedError) as caught:
        duckdb_kql.to_sql(command)
    message = str(caught.value)
    assert ".show tables" in message, f"the refusal does not say what works: {message}"


def test_a_control_command_is_not_a_syntax_error() -> None:
    """It used to be, and the message named every keyword in the language.

    `validate` is a *syntax* check and says nothing about translatability, so it
    must accept a well-formed command whether or not we implement it — exactly
    as it accepts a query using an operator we do not support yet.
    """
    assert duckdb_kql.validate(".show tables") == []
    assert duckdb_kql.validate(".drop table T") == []
    assert duckdb_kql.parse(".show tables").ok


def test_a_control_command_has_no_query_syntax_tree() -> None:
    """Documented rather than incidental: it is a different grammar."""
    assert duckdb_kql.parse(".show tables").tree is None
    assert duckdb_kql.parse("Requests | count").tree is not None


def test_a_query_is_still_a_query() -> None:
    """The counterweight: the dot rule must not swallow ordinary queries."""
    assert not is_control_command("Requests | count")
    assert not is_control_command("  Requests | where a == 1")
    assert is_control_command(".show tables")
    assert is_control_command("  .show tables")


# ---------------------------------------------------------------------------
# Layer 0 and Layer 2 agree
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", SUPPORTED)
def test_the_kusto_client_returns_the_same_shape(con, command: str) -> None:
    """One implementation. The two used to be written separately, and drifted."""
    from duckdb_kql.kusto import KustoClient  # noqa: PLC0415

    with KustoClient(con) as client:
        table = client.execute_mgmt("db", command).primary_results[0]

    assert [c.column_name for c in table.columns] == KUSTO_COLUMNS[command]


def test_translating_a_control_command_needs_no_connection() -> None:
    """Layer 0 stays Layer 0 — it is only SQL text."""
    sql = duckdb_kql.to_sql(".show tables")
    assert "information_schema.tables" in sql
    assert sql.parameters == {}


def test_layer_2_refuses_with_its_own_error_type() -> None:
    """`execute_mgmt` raises KustoUnsupportedError, not a service error.

    The two layers use different taxonomies on purpose — Layer 0 speaks
    `KqlError`, the client speaks the SDK's — and "this client does not
    implement that command" is a statement about the client. Routing both
    through one translation quietly changed this to `KustoServiceError` until
    the client's own tests caught it.
    """
    from duckdb_kql.kusto import KustoClient  # noqa: PLC0415
    from duckdb_kql.kusto.exceptions import KustoUnsupportedError  # noqa: PLC0415

    with KustoClient(":memory:") as client, pytest.raises(KustoUnsupportedError) as caught:
        client.execute_mgmt("db", ".drop table T")

    # ...and it still names the commands that work, from the one place they are listed.
    assert ".show tables" in str(caught.value)


# ---------------------------------------------------------------------------
# Piping a command into query operators
# ---------------------------------------------------------------------------
#
# Kusto composes the two dialects, and the emulator confirms it: `| limit`,
# `| count`, `| where`, `| project`, `| summarize` and `| extend` all work on
# `.show tables`. The command half is a closed set of literals; everything after
# the first `|` is plain KQL and goes through the ordinary translator with the
# command standing in as the source.


PIPED_COLUMNS = [
    (".show tables | count", ["Count"]),
    (".show tables | project TableName", ["TableName"]),
    (".show databases | project DatabaseName, IsCurrent", ["DatabaseName", "IsCurrent"]),
    (".show version | project ServiceType", ["ServiceType"]),
    (
        ".show version | extend Tag = 'x' | project Tag, ServiceType",
        ["Tag", "ServiceType"],
    ),
]


@pytest.mark.parametrize("query,columns", PIPED_COLUMNS, ids=[q for q, _ in PIPED_COLUMNS])
def test_a_command_pipes_into_query_operators(con, query: str, columns: list) -> None:
    """Each of these was checked against the emulator, which returns the same."""
    assert list(duckdb_kql.kql(con, query).columns) == columns


def test_the_pipeline_half_keeps_its_case(con) -> None:
    """KQL identifiers are case-sensitive (R7); only the command head is not.

    Normalizing the whole string for matching lowercased the pipeline too, so
    `| project TableName` produced a column called `tablename` — a wrong answer
    that runs cleanly, which is the shape of bug this project is built around.
    """
    assert list(duckdb_kql.kql(con, ".show tables | project TableName").columns) == [
        "TableName"
    ]
    # ...and the command half stays case-insensitive, as Kusto has it.
    assert list(duckdb_kql.kql(con, ".SHOW TABLES | project TableName").columns) == [
        "TableName"
    ]


def test_the_pipeline_filters_real_rows(con) -> None:
    names = {r[0] for r in duckdb_kql.kql(con, ".show tables").fetchall()}
    filtered = {
        r[0]
        for r in duckdb_kql.kql(
            con, '.show tables | where TableName startswith "Req"'
        ).fetchall()
    }
    assert filtered == {n for n in names if n.startswith("Req")}
    assert filtered < names, "the filter matched everything, so it proves nothing"


def test_an_unsupported_operator_after_a_command_still_refuses(con) -> None:
    """Composing must not paper over an operator we do not implement.

    This used to assert `getschema` raises, which it did until it was
    implemented — so the case is now made with one that still does not.
    """
    with pytest.raises(duckdb_kql.KqlUnsupportedError):
        duckdb_kql.to_sql(".show tables | evaluate bag_unpack(Folder)")


def test_the_split_matches_the_command_before_looking_for_a_pipe() -> None:
    """`|` is not only a pipe in this dialect, so cutting at the first one is wrong.

    `.ingest inline into table T <| 1` contains `<|`. Matching a known command
    head first means only commands whose syntax we know are ever divided, and an
    unsupported one is reported whole rather than mis-split.
    """
    from duckdb_kql.control import split_command  # noqa: PLC0415

    assert split_command(".show tables") == (".show tables", "")
    assert split_command(".show tables | limit 3") == (".show tables", "| limit 3")
    assert split_command(".ingest inline into table T <| 1") == (
        ".ingest inline into table t <| 1",
        "",
    )
