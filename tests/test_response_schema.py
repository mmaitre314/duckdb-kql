"""Pinning tests — the tables a v2 response carries around its result.

`duckdb_kql.server` and `KustoClient` both emit a `QueryProperties` and a
`QueryCompletionInformation` table, and each used to spell the column list
itself. The lists below were read off the emulator's own v2 frames
(2026-11-08, `/v2/rest/query`, `print x = 1`); renaming a column in the shared
copy passed every server and client test before this file existed.

The completion *row* had drifted where the schema had not: the client said
`S_OK` where the service says `S_OK (0)`, and the server's payload was
`json.dumps` with its default separators, `{"Count": 1, ...}`, where the
service sends compact JSON. Both now emit the measured tail.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql import server
from duckdb_kql.kusto._models import COMPLETION_COLUMNS, QUERY_PROPERTIES_COLUMNS

pytest.importorskip("duckdb")

MEASURED_PROPERTIES = [("TableId", "int"), ("Key", "string"), ("Value", "dynamic")]
MEASURED_COMPLETION = [
    ("Timestamp", "datetime"),
    ("ClientRequestId", "string"),
    ("ActivityId", "guid"),
    ("SubActivityId", "guid"),
    ("ParentActivityId", "guid"),
    ("Level", "int"),
    ("LevelName", "string"),
    ("StatusCode", "int"),
    ("StatusCodeName", "string"),
    ("EventType", "int"),
    ("EventTypeName", "string"),
    ("Payload", "string"),
]


#: A successful query's completion row after its timestamp and ids, measured.
MEASURED_SUCCESS = [
    4, "Info", 0, "S_OK (0)", 4, "QueryInfo",
    '{"Count":1,"Text":"Query completed successfully"}',
]


def test_the_shared_schemas_are_the_services() -> None:
    assert list(QUERY_PROPERTIES_COLUMNS) == MEASURED_PROPERTIES
    assert list(COMPLETION_COLUMNS) == MEASURED_COMPLETION


def test_the_rest_server_emits_them() -> None:
    frames = server.v2_response(server.Result(columns=[], rows=[]), "crid")
    tables = {f["TableKind"]: f for f in frames if f.get("FrameType") == "DataTable"}
    for kind, expected in (
        ("QueryProperties", MEASURED_PROPERTIES),
        ("QueryCompletionInformation", MEASURED_COMPLETION),
    ):
        columns = [(c["ColumnName"], c["ColumnType"]) for c in tables[kind]["Columns"]]
        assert columns == expected
    # Was a spaced payload, `{"Count": 1, ...}`, from json.dumps' defaults.
    assert tables["QueryCompletionInformation"]["Rows"][0][5:] == MEASURED_SUCCESS


def test_the_client_emits_them() -> None:
    from duckdb_kql.kusto import KustoClient

    response = KustoClient(duckdb_kql.connect()).execute("db", "print x = 1")
    tables = {t.table_kind.value: t for t in response.tables}
    for kind, expected in (
        ("QueryProperties", MEASURED_PROPERTIES),
        ("QueryCompletionInformation", MEASURED_COMPLETION),
    ):
        columns = [(c.column_name, c.column_type) for c in tables[kind].columns]
        assert columns == expected
    # Was `S_OK`, where the service says `S_OK (0)`.
    assert list(tables["QueryCompletionInformation"].rows[0])[5:] == MEASURED_SUCCESS
