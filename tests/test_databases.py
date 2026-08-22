"""L5 trap tests — `.create` / `.attach` / `.detach database`.

These exist only in the standalone Kusto engine (the emulator); on a real
cluster a database is an ARM resource. `docs/create-database.md` records the
grammar, reconstructed from the parser because there is no reference page.

The mapping is short — DuckDB's `ATTACH` and `DETACH` do the work — but the
result shape and the edge behaviour are not guessable, so all of it was
measured:

* `.create database X volatile` answers `DatabaseName, PersistentPath, Created,
  StoresMetadata, StoresData`, with `PersistentPath` **null** for a volatile
  database and both `Stores*` flags **true**;
* creating one that exists is an **error**, and `ifnotexists` turns that into
  `Created` = **false** — the only way to tell "made it" from "it was there";
* a bare `.create database X`, with neither `persist` nor `volatile`, is a
  **syntax error**, so one of the two is required;
* `.detach database X` answers one `Result` column,
  `'Metadata detach successful.'`.

The one deliberate difference: Kusto persists into *folders*, conventionally
two (metadata and data), while a DuckDB database is a single **file** holding
both. A path here names that file, and several paths use the first — reported
back in `PersistentPath`, so the answer says which one it took.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlUnsupportedError

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    return c


def _rows(con, kql, **kw):
    return duckdb_kql.kql(con, kql, **kw).fetchall()


# ---------------------------------------------------------------------------
# .create database
# ---------------------------------------------------------------------------


def test_volatile_creates_an_in_memory_database(con) -> None:
    assert _rows(con, ".create database VolA volatile") == [
        ("VolA", None, True, True, True)
    ]
    assert ("VolA",) in _rows(con, ".show databases | project DatabaseName")


def test_the_result_columns_are_the_ones_kusto_returns(con) -> None:
    rel = duckdb_kql.kql(con, ".create database VolB volatile")
    assert list(rel.columns) == [
        "DatabaseName", "PersistentPath", "Created", "StoresMetadata", "StoresData"
    ]


def test_creating_one_that_exists_is_an_error(con) -> None:
    _rows(con, ".create database VolC volatile")
    with pytest.raises(Exception):  # noqa: B017 - DuckDB's own catalog error
        _rows(con, ".create database VolC volatile")


def test_ifnotexists_reports_created_false(con) -> None:
    """The only way to tell "I made it" from "it was already there"."""
    assert _rows(con, ".create database VolD volatile ifnotexists")[0][2] is True
    assert _rows(con, ".create database VolD volatile ifnotexists")[0][2] is False


def test_persist_names_the_file_and_reports_it(con, tmp_path) -> None:
    path = tmp_path / "logs.duckdb"
    assert _rows(con, f".create database P1 persist (@'{path}')") == [
        ("P1", str(path), True, True, True)
    ]
    assert path.exists()


def test_several_paths_use_the_first_and_say_so(con, tmp_path) -> None:
    """Kusto's two-path form is (metadata, data); DuckDB has one file for both.

    Not silent: `PersistentPath` in the answer is the path actually opened.
    """
    md, data = tmp_path / "md.duckdb", tmp_path / "data"
    rows = _rows(con, f".create database P2 persist (@'{md}', @'{data}')")
    assert rows == [("P2", str(md), True, True, True)]
    assert md.exists()
    assert not data.exists()


def test_a_bare_create_is_refused(con) -> None:
    """Kusto makes it a syntax error too — one of persist/volatile is required."""
    with pytest.raises(KqlUnsupportedError):
        _rows(con, ".create database Bare")


def test_a_bracketed_name(con) -> None:
    assert _rows(con, ".create database ['my db'] volatile")[0][0] == "my db"


def test_a_remote_path_is_refused(con) -> None:
    """A blob URI is a valid Kusto target with nothing local behind it."""
    with pytest.raises(KqlUnsupportedError) as exc:
        _rows(con, ".create database R persist (@'https://x.blob.core.windows.net/md')")
    assert "local" in str(exc.value)


def test_a_property_list_is_refused(con) -> None:
    """The valid set is engine-defined, so there is nothing to implement."""
    with pytest.raises(KqlUnsupportedError):
        _rows(con, ".create database W volatile with (a=1)")


# ---------------------------------------------------------------------------
# .attach / .detach, and the round trip
# ---------------------------------------------------------------------------


def test_a_database_survives_detach_and_reattach(con, tmp_path) -> None:
    """The point of persisting: the rows are still there afterwards."""
    path = tmp_path / "round.duckdb"
    _rows(con, f".create database R1 persist (@'{path}')")
    _rows(con, ".set-or-replace T <| datatable(x:long)[1,2]", database="R1")
    assert _rows(con, "database('R1').T | count") == [(2,)]

    assert _rows(con, ".detach database R1") == [("Metadata detach successful.",)]
    assert ("R1",) not in _rows(con, ".show databases | project DatabaseName")

    assert _rows(con, f".attach database R2 from @'{path}'")[0][0] == "R2"
    assert _rows(con, "database('R2').T | count") == [(2,)]


def test_attach_readonly_refuses_a_write(con, tmp_path) -> None:
    path = tmp_path / "ro.duckdb"
    _rows(con, f".create database W1 persist (@'{path}')")
    _rows(con, ".set-or-replace T <| datatable(x:long)[1]", database="W1")
    _rows(con, ".detach database W1")

    _rows(con, f".attach database RO from @'{path}' readonly")
    assert _rows(con, "database('RO').T | count") == [(1,)]
    with pytest.raises(Exception):  # noqa: B017 - DuckDB's own read-only error
        _rows(con, ".set-or-replace T2 <| datatable(x:long)[1]", database="RO")


def test_attach_with_a_pinned_version_is_refused(con, tmp_path) -> None:
    """A DuckDB file keeps no history, so there is no version to pin to."""
    with pytest.raises(KqlUnsupportedError):
        _rows(con, f".attach database V from @'{tmp_path / 'x.duckdb'}' version='1'")


def test_detach_ifexists_is_refused(con) -> None:
    """DuckDB has no conditional DETACH, and detaching unconditionally would
    turn "leave it alone if absent" into an error on the case it exists for."""
    with pytest.raises(KqlUnsupportedError):
        _rows(con, ".detach database Nope ifexists")


def test_detaching_something_absent_is_an_error(con) -> None:
    with pytest.raises(Exception):  # noqa: B017 - DuckDB's own catalog error
        _rows(con, ".detach database NeverExisted")


# ---------------------------------------------------------------------------
# It is a write
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        ".create database X volatile",
        ".attach database X from @'/tmp/x.duckdb'",
        ".detach database X",
    ],
)
def test_allow_write_false_refuses_every_lifecycle_command(command: str) -> None:
    """Creating or opening a database writes, so it sits behind the same gate
    ingestion does rather than with the read-only `.show` family."""
    with pytest.raises(KqlUnsupportedError) as exc:
        duckdb_kql.to_sql(command, allow_write=False)
    assert "allow_write" in str(exc.value)


def test_a_pipeline_after_the_command_is_refused(con) -> None:
    """Kusto pipes a command's result — `.show tables | limit 3` works, and
    this package supports that. A lifecycle command cannot: it renders to
    several statements, and a statement list is not a subquery. The refusal
    says so rather than reporting a malformed command."""
    with pytest.raises(KqlUnsupportedError) as exc:
        _rows(con, ".create database X volatile | getschema")
    assert "pipeline" in str(exc.value)


def test_a_pipe_inside_a_quoted_path_is_not_a_pipeline(con, tmp_path) -> None:
    path = tmp_path / "a|b.duckdb"
    assert _rows(con, f".create database Piped persist (@'{path}')")[0][1] == str(path)


def test_they_translate_without_a_connection() -> None:
    """Layer 0 still works: the command is text in and SQL out."""
    sql = duckdb_kql.to_sql(".create database X volatile")
    assert "ATTACH ':memory:' AS \"X\"" in sql


# ---------------------------------------------------------------------------
# Through the SDK client
# ---------------------------------------------------------------------------


def test_the_kusto_client_runs_them(con) -> None:
    from duckdb_kql.kusto import KustoClient  # noqa: PLC0415

    client = KustoClient(con)
    table = client.execute("db", ".create database ViaClient volatile").primary_results[0]
    assert [c.column_name for c in table.columns] == [
        "DatabaseName", "PersistentPath", "Created", "StoresMetadata", "StoresData"
    ]
    # Measured: the flags are `bool` on the wire, spelled `System.SByte`.
    assert [c.column_type for c in table.columns] == [
        "string", "string", "bool", "bool", "bool"
    ]
    assert table[0]["Created"] is True


def test_the_kusto_client_honours_allow_write(con) -> None:
    from duckdb_kql.kusto import KustoClient  # noqa: PLC0415
    from duckdb_kql.kusto.exceptions import KustoUnsupportedError  # noqa: PLC0415

    read_only = KustoClient(con, allow_write=False)
    with pytest.raises(KustoUnsupportedError):
        read_only.execute("db", ".create database Nope volatile")
