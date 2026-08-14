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

#: Measured on the emulator. Order is part of the shape.
KUSTO_COLUMNS = {
    ".show version": [
        "BuildVersion",
        "BuildTime",
        "ServiceType",
        "ProductVersion",
        "ServiceOffering",
    ],
    ".show databases": [
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
    ],
    ".show tables": ["TableName", "DatabaseName", "Folder", "DocString"],
}


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
    rows = duckdb_kql.kql(con, command).fetchall()
    assert rows, f"{command} returned nothing"


@pytest.mark.parametrize("command", SUPPORTED)
def test_the_columns_are_the_ones_kusto_returns(con, command: str) -> None:
    assert list(duckdb_kql.kql(con, command).columns) == KUSTO_COLUMNS[command]


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
