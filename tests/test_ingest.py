"""Ingestion commands — `.set`, `.append`, `.set-or-append`, `.set-or-replace`.

Every expectation here was measured on the Kusto Emulator. Two of them would
have been wrong if assumed:

* **`.set-or-replace` replaces rows, not the table.** A source whose schema
  differs is rejected, so mapping it to DuckDB's `CREATE OR REPLACE TABLE` — the
  obvious reading of the name — would silently redefine the columns.
* **A write that ingests nothing returns zero rows**, not one row saying zero.
  The result is one row per extent, and no data means no extent.

The write gate is the other half. `duckdb_kql.kql()` allows writes by default
(the caller wrote the query and owns the connection); `duckdb-kql serve` refuses
them by default (it answers unauthenticated loopback requests). The trust
boundary is the socket, not the library.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlUnsupportedError
from duckdb_kql.ingest import (
    INGESTION_COLUMNS,
    INGESTION_SCHEMA,
    is_ingestion_command,
    parse_ingestion,
)

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    return c


def _rows(con, kql):
    return duckdb_kql.kql(con, kql).fetchall()


def _table(con, name):
    return con.execute(f'SELECT * FROM "{name}" ORDER BY 1').fetchall()


# ---------------------------------------------------------------------------
# The four verbs, and what each does when the table is missing or present
# ---------------------------------------------------------------------------


def test_set_creates_the_table(con) -> None:
    assert _rows(con, '.set T <| datatable(a:long, b:string) [1,"x", 2,"y"]')[0][-1] == 2
    assert _table(con, "T") == [(1, "x"), (2, "y")]


def test_set_fails_when_the_table_exists(con) -> None:
    """Measured: `Entity 'T' of kind 'Table' already exists.`"""
    _rows(con, ".set T <| datatable(a:long)[1]")
    with pytest.raises(Exception, match="(?i)already exists"):
        _rows(con, ".set T <| datatable(a:long)[2]")


def test_append_fails_when_the_table_is_missing(con) -> None:
    """Measured: `Entity ID 'T' of kind 'Table' was not found.`"""
    with pytest.raises(Exception, match="(?i)does not exist|not found"):
        _rows(con, ".append T <| datatable(a:long)[1]")


def test_append_adds_rows(con) -> None:
    _rows(con, ".set T <| datatable(a:long)[1]")
    assert _rows(con, ".append T <| datatable(a:long)[2,3]")[0][-1] == 2
    assert _table(con, "T") == [(1,), (2,), (3,)]


def test_set_or_append_creates_then_appends(con) -> None:
    assert _rows(con, ".set-or-append T <| datatable(a:long)[1]")[0][-1] == 1
    assert _rows(con, ".set-or-append T <| datatable(a:long)[2]")[0][-1] == 1
    assert _table(con, "T") == [(1,), (2,)]


def test_set_or_replace_creates_then_replaces(con) -> None:
    _rows(con, ".set-or-replace T <| datatable(a:long)[1,2,3]")
    assert _table(con, "T") == [(1,), (2,), (3,)]
    assert _rows(con, ".set-or-replace T <| datatable(a:long)[9]")[0][-1] == 1
    assert _table(con, "T") == [(9,)]


def test_set_or_replace_keeps_the_table_schema(con) -> None:
    """The finding that rules out `CREATE OR REPLACE TABLE`.

    Kusto answers `Query schema does not match table schema`. What matters is
    that the columns are *not* silently redefined — so a mismatched source must
    fail rather than reshape the table.
    """
    _rows(con, '.set-or-replace T <| datatable(a:long, b:string)[1,"x"]')
    with pytest.raises(duckdb.Error):
        _rows(con, '.set-or-replace T <| datatable(z:string)["only"]')
    names = [r[0] for r in con.execute('DESCRIBE "T"').fetchall()]
    assert names == ["a", "b"]


# ---------------------------------------------------------------------------
# The result table
# ---------------------------------------------------------------------------


def test_the_result_schema_is_the_measured_one(con) -> None:
    rel = duckdb_kql.kql(con, ".set T <| datatable(a:long)[1]")
    assert list(rel.columns) == list(INGESTION_COLUMNS)
    assert INGESTION_COLUMNS == (
        "ExtentId", "OriginalSize", "ExtentSize",
        "CompressedSize", "IndexSize", "RowCount",
    )


def test_sizes_are_null_rather_than_invented(con) -> None:
    """A DuckDB table has no extents. Following `control.py`: present, empty."""
    row = _rows(con, ".set T <| datatable(a:long)[1,2]")[0]
    assert row[1:5] == (None, None, None, None)
    assert row[-1] == 2
    assert row[0] is not None  # ExtentId identifies this write


def test_ingesting_nothing_returns_no_rows(con) -> None:
    """Measured: zero rows, not one row saying zero."""
    assert _rows(con, ".set-or-replace T <| datatable(a:long)[1] | where a > 99") == []
    assert con.execute('SELECT count(*) FROM "T"').fetchone() == (0,)


def test_row_count_is_real(con) -> None:
    assert _rows(con, ".set T <| range i from 1 to 5 step 1 | project v = i")[0][-1] == 5


# ---------------------------------------------------------------------------
# The source is a whole KQL query
# ---------------------------------------------------------------------------


def test_the_source_may_be_a_query_over_a_table(con) -> None:
    _rows(con, ".set Src <| datatable(n:long)[1,2,3,4]")
    _rows(con, ".set-or-replace Derived <| Src | where n > 2 | project doubled = n * 2")
    assert _table(con, "Derived") == [(6,), (8,)]


def test_the_source_is_translated_as_kql_not_sql(con) -> None:
    """`has` is term-based here; a SQL reading would make it a substring."""
    _rows(con, '.set Logs <| datatable(s:string)["error one","errors"]')
    _rows(con, '.set-or-replace Hits <| Logs | where s has "error"')
    assert _table(con, "Hits") == [("error one",)]


# ---------------------------------------------------------------------------
# database= applies to the target and the source
# ---------------------------------------------------------------------------


def test_ingestion_targets_the_named_database(con, tmp_path) -> None:
    path = tmp_path / "sales.db"
    seed = duckdb.connect(str(path))
    seed.execute("CREATE TABLE Seed(v INTEGER)")
    seed.close()
    con.execute(f"ATTACH '{path}' AS sales")

    duckdb_kql.kql(
        con, ".set-or-replace Events <| datatable(a:long)[1,2]", database="sales"
    ).fetchall()
    assert con.execute("SELECT count(*) FROM sales.Events").fetchone() == (2,)
    # And not in the connection's own database.
    assert con.execute(
        "SELECT count(*) FROM duckdb_tables() "
        "WHERE table_name = 'Events' AND database_name = 'memory'"
    ).fetchone() == (0,)


# ---------------------------------------------------------------------------
# Forms that are refused rather than mistranslated
# ---------------------------------------------------------------------------


def test_async_is_refused_because_its_result_is_different(con) -> None:
    """Measured: `async` returns `OperationId`, an operation to poll for."""
    with pytest.raises(KqlUnsupportedError, match="async"):
        _rows(con, ".set-or-replace async T <| datatable(a:long)[1]")


def test_with_properties_are_refused_rather_than_ignored(con) -> None:
    """`extend_schema` / `recreate_schema` change what the command does."""
    with pytest.raises(KqlUnsupportedError, match="with"):
        _rows(con, '.set-or-replace T with (folder="F") <| datatable(a:long)[1]')


@pytest.mark.parametrize(
    "text",
    [
        ".set-or-replace T",                       # no <|
        ".set-or-replace <| datatable(a:long)[1]",  # no table
        ".set-or-replace T <|",                     # no query
        ".set-or-replace T junk <| datatable(a:long)[1]",
    ],
)
def test_malformed_commands_name_ingestion(text: str) -> None:
    with pytest.raises(KqlUnsupportedError, match="(?i)ingestion"):
        parse_ingestion(text)


def test_the_longest_verb_wins(con) -> None:
    """`.set-or-replace` must never be read as `.set` followed by junk."""
    assert parse_ingestion(".set-or-replace T <| X").verb == ".set-or-replace"
    assert parse_ingestion(".set-or-append T <| X").verb == ".set-or-append"
    assert parse_ingestion(".set T <| X").verb == ".set"


def test_a_bracketed_table_name_is_unwrapped() -> None:
    assert parse_ingestion(".set ['my table'] <| X").table == "my table"


def test_is_ingestion_command_does_not_claim_other_commands() -> None:
    assert is_ingestion_command(".set T <| X")
    assert not is_ingestion_command(".show tables")
    assert not is_ingestion_command("T | count")
    assert not is_ingestion_command(".settings something")


# ---------------------------------------------------------------------------
# The write gate
# ---------------------------------------------------------------------------


def test_writes_are_allowed_by_default_in_the_library(con) -> None:
    """The caller wrote the query and owns the connection."""
    assert _rows(con, ".set T <| datatable(a:long)[1]")[0][-1] == 1


def test_allow_write_false_refuses_before_touching_the_database(con) -> None:
    with pytest.raises(KqlUnsupportedError, match="writes are disabled"):
        duckdb_kql.kql(con, ".set T <| datatable(a:long)[1]", allow_write=False)
    assert con.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'T'"
    ).fetchone() == (0,)


def test_to_sql_can_refuse_too() -> None:
    with pytest.raises(KqlUnsupportedError, match="writes are disabled"):
        duckdb_kql.to_sql(".set T <| datatable(a:long)[1]", allow_write=False)


def test_reads_are_unaffected_by_the_gate(con) -> None:
    _rows(con, ".set T <| datatable(a:long)[1]")
    assert duckdb_kql.kql(con, "T | count", allow_write=False).fetchall() == [(1,)]
    assert duckdb_kql.kql(con, ".show tables", allow_write=False).fetchall()


# ---------------------------------------------------------------------------
# The server refuses writes unless told otherwise
# ---------------------------------------------------------------------------


def _ask(server, csl):
    url = f"http://127.0.0.1:{server.server_address[1]}/v1/rest/query"
    body = json.dumps({"db": "default", "csl": csl}).encode()
    request = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request) as response:
            return 200, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # `with exc:` as in test_server.py. An HTTPError holds the response
        # body open; letting it be collected implicitly raises ResourceWarning,
        # which `filterwarnings = ["error"]` turns into a failure — attributed,
        # confusingly, to whichever test was running when the GC ran.
        with exc:
            return exc.code, exc.read().decode()


@pytest.fixture
def serving(con):
    from duckdb_kql.server import KustoRestServer

    started: list[tuple] = []

    def start(allow_write: bool):
        # `quiet=True` as in test_server.py, and not merely for tidiness: the
        # request log is written from the serving *thread*, into whatever pytest
        # has swapped stdout for. On 3.14 that raced capture teardown and
        # surfaced as an unraisable `_TemporaryFileCloser.__del__`, which
        # `filterwarnings = ["error"]` turned into a failure of whichever test
        # happened to be running.
        server = KustoRestServer(con, port=0, allow_write=allow_write, quiet=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        started.append((server, thread))
        return server

    yield start
    for server, thread in started:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_the_server_refuses_writes_by_default(serving, con) -> None:
    """It answers unauthenticated loopback requests, so writes are opt-in."""
    server = serving(False)
    status, body = _ask(server, ".set T <| datatable(a:long)[1]")
    assert status == 403
    assert "writes are disabled" in str(body)
    assert con.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'T'"
    ).fetchone() == (0,)


def test_the_server_allows_writes_when_enabled(serving, con) -> None:
    server = serving(True)
    status, body = _ask(server, ".set T <| datatable(a:long)[1,2]")
    assert status == 200
    assert body["Tables"][0]["Rows"][0][-1] == 2
    assert con.execute('SELECT count(*) FROM "T"').fetchone() == (2,)


def test_the_server_still_answers_reads_when_writes_are_off(serving, con) -> None:
    """A refused write must not read as a broken connection."""
    server = serving(False)
    status, body = _ask(server, "print x = 1")
    assert status == 200
    assert body["Tables"][0]["Rows"] == [[1]]


def test_the_declared_schema_reaches_the_wire(serving, con) -> None:
    server = serving(True)
    _, body = _ask(server, ".set T <| datatable(a:long)[1]")
    columns = [c["ColumnName"] for c in body["Tables"][0]["Columns"]]
    assert columns == list(INGESTION_COLUMNS)
    kinds = [c["DataType"] for c in body["Tables"][0]["Columns"]]
    assert kinds == [c.data_type for c in INGESTION_SCHEMA]
