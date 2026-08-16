"""The local Kusto REST endpoint, exercised over a real socket.

These go through HTTP rather than calling the handler's methods, because most of
what this module promises is only true on the wire: the bind address, the CORS
headers, the status codes, and the exact JSON shapes the Azure Data Explorer web
UI reads. A test that called `v1_response()` directly would pass while the server
listened on `0.0.0.0` and echoed `Access-Control-Allow-Origin: *`.

The expected envelopes are **captured from Azure Data Explorer**, not designed —
the request/response pairs a browser exchanges with a real cluster while adding a
connection and running one query.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Any

import pytest

from duckdb_kql.control import COLUMNS, SCHEMA, SUPPORTED
from duckdb_kql.server import ADX_ORIGINS, DEFAULT_PORT, build_server, check_options

pytest.importorskip("duckdb")

ALLOWED_ORIGIN = "https://dataexplorer.azure.com"

#: The options the web UI sends on every v2 query, verbatim. If this set ever
#: fails `check_options`, the product stops working — which is the point of
#: asserting it rather than trusting the policy to stay reasonable.
ADX_QUERY_OPTIONS = {
    "servertimeout": "00:04:00",
    "queryconsistency": "strongconsistency",
    "query_language": "kql",
    "request_readonly": False,
    "request_readonly_hardline": True,
}


@pytest.fixture(scope="module")
def server():
    # Port 0: the OS picks a free one, so the suite cannot collide with a
    # `duckdb-kql serve` the developer left running on the real port.
    srv = build_server(quiet=True, port=0)
    srv.run("print x = 1")  # warm the translator before any request is timed
    srv._con.execute(
        "CREATE TABLE Data(C0 BIGINT, C1 TIMESTAMP);"
        "INSERT INTO Data VALUES (1, TIMESTAMP '2020-01-01'), (2, TIMESTAMP '2020-01-02')"
    )
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


def call(
    server: Any,
    route: str,
    body: dict[str, Any] | None = None,
    *,
    method: str = "POST",
    origin: str | None = None,
) -> tuple[int, dict[str, str], Any]:
    """One HTTP request. Returns ``(status, headers, parsed body)``."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(server.url + route, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if origin is not None:
        request.add_header("Origin", origin)
    try:
        # Closed explicitly: the server speaks HTTP/1.1, so an unclosed response
        # holds a keep-alive connection — and a handler thread — open.
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
            return response.status, dict(response.headers), json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        with exc:
            raw = exc.read()
            return exc.status, dict(exc.headers), json.loads(raw) if raw else None


def mgmt(server: Any, csl: str, **body: Any) -> Any:
    status, _, payload = call(server, "/v1/rest/mgmt", {"csl": csl, **body})
    assert status == 200, payload
    return payload["Tables"][0]


# ---------------------------------------------------------------------------
# Reachability
# ---------------------------------------------------------------------------


def test_it_binds_to_loopback_only(server) -> None:
    """The bind *is* the security boundary, so it is asserted directly.

    There is no authentication here — the endpoint answers any query against
    whatever database it was pointed at. On a shared network a `0.0.0.0` bind
    would publish that to everyone on the subnet, and it is one careless edit
    away at all times.
    """
    assert server.server_address[0] == "127.0.0.1"


def test_the_cli_cannot_ask_for_a_different_bind_address() -> None:
    """`--host` must not exist. A flag is all it would take to undo the above."""
    import argparse  # noqa: PLC0415

    from duckdb_kql.cli import _parser  # noqa: PLC0415

    (subparsers,) = [
        action
        for action in _parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    serve = subparsers.choices["serve"]
    flags = {option for action in serve._actions for option in action.option_strings}
    assert not flags & {"--host", "--bind", "--interface", "--address"}


def test_the_default_port_is_the_documented_one() -> None:
    assert DEFAULT_PORT == 31415


def test_the_landing_page_names_the_url_to_connect_with(server) -> None:
    status, _, payload = call(server, "/", method="GET")
    assert status == 200
    assert payload["database"] == "memory"
    assert server.url in payload["connect"]
    assert "/v1/rest/mgmt" in payload["endpoints"]


def test_an_unknown_route_is_a_404_not_a_crash(server) -> None:
    status, _, payload = call(server, "/v1/rest/ingest", {"csl": ".show version"})
    assert status == 404
    assert "no route" in payload["error"]["@message"]


# ---------------------------------------------------------------------------
# CORS — who may read this database from a browser
# ---------------------------------------------------------------------------


#: What Chrome asks for on behalf of the Azure Data Explorer UI, captured
#: verbatim from a `dataexplorer.azure.com` HAR against this server.
ADX_REQUEST_HEADERS = "authorization,content-type,x-ms-app,x-ms-client-request-id,x-ms-user-id"


def preflight(server: Any, requested: str = ADX_REQUEST_HEADERS, *, origin: str = ALLOWED_ORIGIN):
    request = urllib.request.Request(server.url + "/v1/rest/mgmt", method="OPTIONS")
    request.add_header("Origin", origin)
    request.add_header("Access-Control-Request-Method", "POST")
    request.add_header("Access-Control-Request-Headers", requested)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers)
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.status, dict(exc.headers)


def test_a_preflight_from_the_web_ui_is_allowed(server) -> None:
    status, headers = preflight(server)
    assert status == 204
    assert headers["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
    assert "POST" in headers["Access-Control-Allow-Methods"]


def test_the_preflight_covers_every_header_the_web_ui_asks_for(server) -> None:
    """The check a browser actually performs, done the way it performs it.

    A preflight can return 204 and still block the request: the browser compares
    `Access-Control-Request-Headers` against `Access-Control-Allow-Headers` and
    fails the *real* call — as a bare `net::ERR_FAILED`, with nothing in the
    response to point at. That happened, because the allow-list said `x-ms-user`
    where the UI sends `x-ms-user-id`, and asserting "authorization is in the
    string" did not catch it. Set coverage is the assertion that would have.
    """
    _, headers = preflight(server)
    allowed = {h.strip().lower() for h in headers["Access-Control-Allow-Headers"].split(",")}
    asked = [h.strip().lower() for h in ADX_REQUEST_HEADERS.split(",")]
    assert not [h for h in asked if h not in allowed], (
        f"preflight would block the request: {headers['Access-Control-Allow-Headers']}"
    )


def test_a_header_the_ui_adds_later_does_not_break_the_preflight(server) -> None:
    """Echoing is the point: the previous list broke the moment the UI changed."""
    _, headers = preflight(server, "content-type,x-ms-invented-tomorrow")
    allowed = {h.strip().lower() for h in headers["Access-Control-Allow-Headers"].split(",")}
    assert "x-ms-invented-tomorrow" in allowed


def test_the_preflight_never_answers_with_a_wildcard(server) -> None:
    """`*` is invalid next to `Allow-Credentials: true`, so it is not a shortcut.

    Echoing the requested list is not a wildcard in disguise either — what
    decides who may reach this endpoint is the origin allow-list and the
    loopback bind, both checked before a header name is ever read.
    """
    _, headers = preflight(server)
    assert headers["Access-Control-Allow-Headers"] != "*"
    assert headers["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
    assert "Access-Control-Request-Headers" in headers.get("Vary", "")


def test_the_preflight_answers_the_private_network_question_only_when_asked(server) -> None:
    """Chrome gates a public page reaching a private address behind this."""
    request = urllib.request.Request(server.url + "/v1/rest/mgmt", method="OPTIONS")
    request.add_header("Origin", ALLOWED_ORIGIN)
    request.add_header("Access-Control-Request-Method", "POST")
    request.add_header("Access-Control-Request-Private-Network", "true")
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.headers["Access-Control-Allow-Private-Network"] == "true"

    _, headers = preflight(server)
    assert "Access-Control-Allow-Private-Network" not in headers


def test_the_post_the_web_ui_makes_after_the_preflight_succeeds(server) -> None:
    """The request the browser blocked. End to end, with the UI's own headers."""
    request = urllib.request.Request(
        server.url + "/v1/rest/mgmt",
        data=json.dumps({"csl": ".show version", "properties": None}).encode(),
        headers={
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json",
            "Origin": ALLOWED_ORIGIN,
            "x-ms-app": "Kusto.Web.KWE:2.259.0-5|embeddedIn:dataexplorer.azure.com",
            "x-ms-client-request-id": "Kusto.Web.KWE.Query;29e3ba3d;740cd2cb",
            "x-ms-user-id": "someone",
            "Authorization": "Bearer ignored",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200
        assert response.headers["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
        payload = json.loads(response.read())
    assert payload["Tables"][0]["Columns"][0]["ColumnName"] == "BuildVersion"


def test_the_server_header_reports_a_version(server) -> None:
    """It read `duckdb-kql/duckdb-kql` — the cluster name in the version slot."""
    from duckdb_kql import __version__  # noqa: PLC0415

    _, headers, _ = call(server, "/", method="GET")
    assert headers["Server"].startswith(f"duckdb-kql/{__version__} ")


def test_a_preflight_from_anywhere_else_is_refused(server) -> None:
    """The one thing standing between a random tab and this data.

    A browser will let any page you visit POST to `http://localhost:31415`. What
    stops it reading the reply is the absence of an allow-origin header, so an
    over-broad allow-list here is a data leak, not a formatting choice.
    """
    status, headers, _ = call(
        server, "/v1/rest/mgmt", method="OPTIONS", origin="https://evil.example"
    )
    assert status == 403
    assert "Access-Control-Allow-Origin" not in headers


def test_a_response_never_carries_a_wildcard_origin(server) -> None:
    _, headers, _ = call(
        server, "/v1/rest/mgmt", {"csl": ".show version"}, origin=ALLOWED_ORIGIN
    )
    assert headers["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
    assert headers.get("Vary") == "Origin"


def test_a_disallowed_origin_gets_no_allow_header_even_on_success(server) -> None:
    """The request still runs — the browser is what must refuse to hand it over.

    This is the shape CORS actually has, and asserting it stops someone
    "simplifying" the check into something a non-browser client would notice.
    """
    status, headers, _ = call(
        server, "/v1/rest/mgmt", {"csl": ".show version"}, origin="https://evil.example"
    )
    assert status == 200
    assert "Access-Control-Allow-Origin" not in headers


def test_every_national_cloud_is_in_the_default_allow_list() -> None:
    assert ADX_ORIGINS == (
        "https://dataexplorer.azure.com",
        "https://dataexplorer.azure.cn",
        "https://dataexplorer.azure.us",
    )


# ---------------------------------------------------------------------------
# /v1/rest/mgmt — the shapes the UI reads while opening a connection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", SUPPORTED)
def test_a_command_reports_the_schema_kusto_declares(server, command) -> None:
    """Column *labels* come from the table, not from DuckDB's result types.

    Kusto's control commands declare their own schemas and the declarations are
    not self-consistent — `.show databases`.IsCurrent is `Boolean` where a query
    would say `SByte`, and `.show materialized-views`.Lookback is `time` where a
    query would say `timespan`. Deriving these would be tidier and wrong.
    """
    table = mgmt(server, command)
    described = [
        (c["ColumnName"], c["DataType"], c.get("ColumnType")) for c in table["Columns"]
    ]
    assert described == [tuple(c) for c in SCHEMA[command]]


def test_show_version_omits_columntype_entirely(server) -> None:
    """Not `null` — absent. Measured on the emulator and on the service.

    `.show version` is the first request the web UI makes, so this is the shape
    that has to be right before anything else is reachable.
    """
    for column in mgmt(server, ".show version")["Columns"]:
        assert "ColumnType" not in column
        assert set(column) == {"ColumnName", "DataType"}


def test_show_databases_names_the_duckdb_database(server) -> None:
    table = mgmt(server, ".show databases")
    (row,) = table["Rows"]
    assert row[0] == "memory"
    assert row[3] is True  # IsCurrent
    # Nothing invented: a DuckDB database has no cluster-side GUID, so the
    # column is present and empty rather than filled with a plausible one.
    assert row[7] is None  # DatabaseId


def test_show_databases_entities_carries_the_column_list(server) -> None:
    """`CslOutputSchema` is what the UI draws its schema tree from."""
    (row,) = mgmt(server, ".show databases entities")["Rows"]
    assert row[2] == "Data"
    assert row[7] == "C0:long, C1:datetime"
    # `dynamic` goes on the wire parsed, not as a string of JSON.
    assert row[8] == {"column_docs": {}}


def test_show_materialized_views_is_an_empty_table_not_a_refusal(server) -> None:
    """The UI asks for this while opening a database; an error reads as broken."""
    table = mgmt(server, ".show materialized-views")
    assert table["Rows"] == []
    assert len(table["Columns"]) == 16


def test_a_piped_command_is_typed_like_a_query(server) -> None:
    """Kusto composes the dialects, and the composition is a query result.

    So the command's declared labels stop applying the moment an operator is
    piped onto it — `Lookback` is `time` on the bare command and `timespan`
    once anything has processed it.
    """
    table = mgmt(server, ".show materialized-views | project Lookback")
    assert table["Columns"] == [
        {"ColumnName": "Lookback", "DataType": "TimeSpan", "ColumnType": "timespan"}
    ]


def test_a_piped_command_keeps_its_identifier_case(server) -> None:
    """`| project TableName`, not `tablename`. KQL identifiers are case-sensitive."""
    table = mgmt(server, ".show tables | project TableName")
    assert [c["ColumnName"] for c in table["Columns"]] == ["TableName"]
    assert table["Rows"] == [["Data"]]


# ---------------------------------------------------------------------------
# /v2/rest/query — the frame protocol
# ---------------------------------------------------------------------------


def v2(server: Any, csl: str, **body: Any) -> list[dict[str, Any]]:
    status, _, payload = call(server, "/v2/rest/query", {"csl": csl, **body})
    assert status == 200, payload
    return payload


def test_the_frames_arrive_in_the_order_the_ui_expects(server) -> None:
    """A response missing its bookends is reported as a *failed* query.

    Not as an empty one — which is why the empty `QueryProperties` frame and the
    trailing `DataSetCompletion` are not optional decoration.
    """
    frames = v2(server, "Data | count", db="memory", properties={"Options": ADX_QUERY_OPTIONS})
    assert [f["FrameType"] for f in frames] == [
        "DataSetHeader",
        "DataTable",
        "DataTable",
        "DataTable",
        "DataSetCompletion",
    ]
    assert [f.get("TableKind") for f in frames[1:4]] == [
        "QueryProperties",
        "PrimaryResult",
        "QueryCompletionInformation",
    ]
    assert [f["TableId"] for f in frames[1:4]] == [0, 1, 2]
    assert frames[-1] == {
        "FrameType": "DataSetCompletion",
        "HasErrors": False,
        "Cancelled": False,
    }


def test_the_primary_result_carries_the_answer(server) -> None:
    (primary,) = [f for f in v2(server, "Data | count") if f.get("TableKind") == "PrimaryResult"]
    assert primary["Columns"] == [{"ColumnName": "Count", "ColumnType": "long"}]
    assert primary["Rows"] == [[2]]


def test_the_visualization_blob_is_a_json_string_not_an_object(server) -> None:
    """Kusto sends it double-encoded. The UI parses it, so an object breaks it."""
    (properties,) = [
        f for f in v2(server, "Data | count") if f.get("TableKind") == "QueryProperties"
    ]
    (row,) = properties["Rows"]
    assert row[1] == "Visualization"
    assert isinstance(row[2], str)
    assert json.loads(row[2])["Visualization"] is None


def test_the_completion_frame_echoes_the_client_request_id(server) -> None:
    request = urllib.request.Request(
        server.url + "/v2/rest/query",
        data=json.dumps({"csl": "Data | count"}).encode(),
        headers={"Content-Type": "application/json", "x-ms-client-request-id": "KWC;abc"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        frames = json.loads(response.read())
    (completion,) = [
        f for f in frames if f.get("TableKind") == "QueryCompletionInformation"
    ]
    assert completion["Rows"][0][1] == "KWC;abc"


def test_datetimes_go_out_in_kustos_wire_form(server) -> None:
    (primary,) = [
        f
        for f in v2(server, "Data | project C1 | take 1")
        if f.get("TableKind") == "PrimaryResult"
    ]
    assert primary["Rows"] == [["2020-01-01T00:00:00.000000Z"]]


# ---------------------------------------------------------------------------
# Request options — implemented or refused, never quietly dropped
# ---------------------------------------------------------------------------


def test_the_options_the_web_ui_sends_are_all_accepted() -> None:
    """Otherwise the product is unusable, and this is the test that says so."""
    assert check_options(ADX_QUERY_OPTIONS) == []


def test_an_option_is_judged_by_its_value_not_its_name() -> None:
    """`strongconsistency` is what a single local database gives; accept it.

    `weakconsistency` names a choice that does not exist here, so accepting it
    would be the silent-wrong-answer failure this package exists to avoid. The
    Python API refuses both — right for a caller who can change the line — but on
    the wire the caller is a UI that sends it on every request.
    """
    assert check_options({"queryconsistency": "strongconsistency"}) == []
    (refusal,) = check_options({"queryconsistency": "weakconsistency"})
    assert "weakconsistency" in refusal

    assert check_options({"query_language": "kql"}) == []
    (refusal,) = check_options({"query_language": "sql"})
    assert "sql" in refusal


def test_an_unrecognised_option_is_refused(server) -> None:
    """An option nobody has classified cannot be assumed harmless."""
    status, _, payload = call(
        server,
        "/v1/rest/query",
        {"csl": "Data", "properties": {"Options": {"invented_option": 1}}},
    )
    assert status == 400
    assert "invented_option" in payload["error"]["@message"]


def test_a_refused_option_says_why(server) -> None:
    status, _, payload = call(
        server,
        "/v1/rest/query",
        {"csl": "Data", "properties": {"Options": {"truncationmaxrecords": 10}}},
    )
    assert status == 400
    # The reason, not just the name: silently returning fewer rows would look
    # like a complete answer.
    assert "truncat" in payload["error"]["@message"].lower()


def test_a_request_with_no_properties_is_fine(server) -> None:
    status, _, payload = call(server, "/v1/rest/mgmt", {"csl": ".show version", "properties": None})
    assert status == 200


# ---------------------------------------------------------------------------
# Parameters and databases
# ---------------------------------------------------------------------------


def test_parameters_are_bound_as_values(server) -> None:
    status, _, payload = call(
        server,
        "/v1/rest/query",
        {
            "csl": "declare query_parameters(n:long); Data | where C0 >= n | project C0",
            "properties": {"Parameters": {"n": 2}},
        },
    )
    assert status == 200, payload
    assert payload["Tables"][0]["Rows"] == [[2]]


def test_a_parameter_is_never_pasted_into_the_query(server) -> None:
    """The whole point of binding: a value cannot become syntax.

    A string parameter carrying a quote and a second statement has to come back
    as a value that matched nothing, not as a query that ran.
    """
    status, _, payload = call(
        server,
        "/v1/rest/query",
        {
            "csl": "declare query_parameters(s:string); Data | where tostring(C0) == s",
            "properties": {"Parameters": {"s": "1' or '1'='1"}},
        },
    )
    assert status == 200, payload
    assert payload["Tables"][0]["Rows"] == []


def test_a_request_for_another_database_is_refused(server) -> None:
    """Answering out of the one database we have would be a mislabelled answer."""
    status, _, payload = call(server, "/v1/rest/query", {"db": "Production", "csl": "Data"})
    assert status == 404
    assert "Production" in payload["error"]["@message"]


def test_the_database_a_client_reads_from_show_databases_is_accepted(server) -> None:
    """The round trip the UI actually makes: list, then query by name."""
    (row,) = mgmt(server, ".show databases")["Rows"]
    status, _, payload = call(server, "/v1/rest/query", {"db": row[0], "csl": "Data | count"})
    assert status == 200, payload


def test_default_is_accepted_before_a_client_has_asked(server) -> None:
    """`default` is what the UI sends on its very first request."""
    status, _, _ = call(server, "/v1/rest/mgmt", {"db": "default", "csl": ".show version"})
    assert status == 200


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_a_bad_query_is_a_400_in_kustos_error_envelope(server) -> None:
    """A 500 tells the user nothing; the reason belongs in the shape they parse."""
    status, _, payload = call(server, "/v1/rest/query", {"csl": "Data | bogus"})
    assert status == 400
    error = payload["error"]
    assert error["code"] == "General_BadRequest"
    assert error["@type"] == "Kusto.Data.Exceptions.KustoBadRequestException"
    assert "could not parse KQL at 1:5" in error["@message"]


def test_an_execution_failure_reaches_the_client_too(server) -> None:
    """Not only translation failures: a query that parses and then fails to run
    is the same 400, carrying DuckDB's reason rather than a bare 500."""
    status, _, payload = call(server, "/v1/rest/query", {"csl": "Data | project Nope"})
    assert status == 400
    assert "Nope" in payload["error"]["@message"]


def test_an_unsupported_command_names_the_supported_ones(server) -> None:
    status, _, payload = call(server, "/v1/rest/mgmt", {"csl": ".create table T (a:int)"})
    assert status == 400
    assert ".show tables" in payload["error"]["@message"]


def test_an_empty_request_is_refused(server) -> None:
    status, _, payload = call(server, "/v1/rest/query", {"csl": "   "})
    assert status == 400
    assert "csl" in payload["error"]["@message"]


def test_a_malformed_body_is_refused(server) -> None:
    request = urllib.request.Request(
        server.url + "/v1/rest/query",
        data=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request, timeout=10)
    assert caught.value.status == 400
    caught.value.close()


# ---------------------------------------------------------------------------
# The tables the rest of this depends on
# ---------------------------------------------------------------------------


def test_the_schema_table_covers_exactly_the_supported_commands() -> None:
    assert tuple(SCHEMA) == SUPPORTED


def test_the_name_table_is_derived_from_the_schema_table() -> None:
    """One source of truth: names for the pipe machinery, labels for the wire."""
    assert COLUMNS == {
        command: tuple(c.name for c in columns) for command, columns in SCHEMA.items()
    }


def test_no_command_declares_a_duplicate_column_name() -> None:
    for command, columns in SCHEMA.items():
        names = [c.name for c in columns]
        assert len(names) == len(set(names)), command


# ---------------------------------------------------------------------------
# Init scripts and several databases at once
# ---------------------------------------------------------------------------


@pytest.fixture
def two_databases(tmp_path):
    """Two DuckDB files and an init script that attaches both.

    The two tables share a key and share *no* other column, deliberately: a
    join that reaches the second database can then be told apart from one that
    quietly resolved both sides in the first, because `Name` exists in only one
    of the files.
    """
    import duckdb  # noqa: PLC0415

    con = duckdb.connect(str(tmp_path / "sales.duckdb"))
    con.execute("CREATE TABLE Orders(CustomerId BIGINT, Amount DOUBLE)")
    con.execute("INSERT INTO Orders VALUES (1, 10.0), (2, 20.0)")
    con.close()

    con = duckdb.connect(str(tmp_path / "customers.duckdb"))
    con.execute("CREATE TABLE Customers(CustomerId BIGINT, Name VARCHAR)")
    con.execute("INSERT INTO Customers VALUES (1, 'ann'), (2, 'bo')")
    con.close()
    init = tmp_path / "attach.sql"
    init.write_text(
        f"ATTACH '{tmp_path / 'sales.duckdb'}' AS Sales (READ_ONLY);\n"
        f"ATTACH '{tmp_path / 'customers.duckdb'}' AS Customers (READ_ONLY);\n"
    )
    return init


def test_an_init_script_attaches_databases(two_databases) -> None:
    """The point of --init: one server, several DuckDB files."""
    srv = build_server(quiet=True, port=0, init=two_databases)
    try:
        assert srv.databases() == ["Customers", "Sales", "memory"]
    finally:
        srv.server_close()


def test_the_init_script_runs_before_the_socket_is_bound(two_databases) -> None:
    """A client must never see the database halfway through its own setup.

    Ordering is asserted through the observable consequence: by the time a
    server object exists at all, every attach has already happened.
    """
    srv = build_server(quiet=True, port=0, init=two_databases)
    try:
        assert "Sales" in srv.databases()
    finally:
        srv.server_close()


def test_a_failing_init_script_stops_the_server_starting(tmp_path) -> None:
    """Serving a half-attached database answers queries with 'no such table'
    rather than with the reason the attach failed."""
    init = tmp_path / "bad.sql"
    init.write_text("ATTACH '/no/such/file.duckdb' AS Nope;\n")
    with pytest.raises(Exception) as caught:
        build_server(quiet=True, port=0, init=init)
    assert "Nope" in str(caught.value) or "no/such" in str(caught.value)


def test_an_init_script_must_be_sql(tmp_path) -> None:
    from duckdb_kql.server import read_init_script  # noqa: PLC0415

    script = tmp_path / "setup.txt"
    script.write_text("ATTACH 'x' AS y;")
    with pytest.raises(ValueError, match="unsupported init script type"):
        read_init_script(script)


def test_a_kql_init_script_is_refused_by_name_not_by_accident(tmp_path) -> None:
    """`.kql` is a coherent idea that is not built yet, and the message says so.

    Falling through to "unsupported extension" would read as though `.kql` were
    a typo, and handing it to DuckDB would fail as a SQL syntax error partway
    down someone's file.
    """
    from duckdb_kql.server import INIT_SCRIPT_LANGUAGES, read_init_script  # noqa: PLC0415

    assert ".kql" in INIT_SCRIPT_LANGUAGES
    script = tmp_path / "setup.kql"
    script.write_text("let x = 1;")
    with pytest.raises(ValueError, match="not implemented yet"):
        read_init_script(script)


def test_show_databases_lists_every_attached_database(two_databases) -> None:
    srv = build_server(quiet=True, port=0, init=two_databases)
    try:
        names = [row[0] for row in srv.run(".show databases").rows]
        assert names == ["Customers", "Sales", "memory"]
    finally:
        srv.server_close()


def test_show_databases_entities_spans_every_database(two_databases) -> None:
    """Measured on the emulator, not assumed.

    With a second database attached, `.show databases entities` run from
    `NetDefaultDB` returns `NetDefaultDB.Users` *and* `Sales.Orders` — and the
    same rows in the same order when run from `Sales`. `.show tables` is the
    current-database one. This matters in the product: the Azure Data Explorer
    web UI draws its schema tree from this command, so filtering to the current
    database would hide everything `--init` attached.
    """
    srv = build_server(quiet=True, port=0, init=two_databases)
    try:
        rows = srv.run(".show databases entities").rows
        assert [(r[0], r[2]) for r in rows] == [
            ("Customers", "Customers"),
            ("Sales", "Orders"),
        ]
        # CslOutputSchema is what the UI reads for each table's columns.
        assert rows[1][7] == "CustomerId:long, Amount:real"
    finally:
        srv.server_close()


def test_show_tables_stays_current_database_only(two_databases) -> None:
    """The other half of the measurement: `.show tables` did *not* span."""
    srv = build_server(quiet=True, port=0, init=two_databases)
    try:
        assert srv.run(".show tables").rows == []
    finally:
        srv.server_close()


def test_a_query_reaches_into_an_attached_database(two_databases) -> None:
    srv = build_server(quiet=True, port=0, init=two_databases)
    try:
        assert srv.run('database("Sales").Orders | count').rows == [(2,)]
    finally:
        srv.server_close()


def test_a_query_joins_across_two_attached_databases(two_databases) -> None:
    """The thing a single DuckDB file cannot do, over one connection.

    `Name` lives only in `customers.duckdb` and `Amount` only in
    `sales.duckdb`, so a result carrying both is proof the join crossed.
    """
    srv = build_server(quiet=True, port=0, init=two_databases)
    try:
        result = srv.run(
            'database("Sales").Orders'
            ' | join kind=inner (database("Customers").Customers) on CustomerId'
            " | project Name, Amount"
            " | sort by Amount asc"
        )
        assert [c.name for c in result.columns] == ["Name", "Amount"]
        assert [tuple(r) for r in result.rows] == [("ann", 10.0), ("bo", 20.0)]
    finally:
        srv.server_close()


def test_a_client_may_select_an_attached_database_by_name(two_databases) -> None:
    """`db` on the request is checked against what is actually attached."""
    srv = build_server(quiet=True, port=0, init=two_databases)
    try:
        assert srv.serves("Sales")
        assert srv.serves("Customers")
        assert not srv.serves("Production")
    finally:
        srv.server_close()
