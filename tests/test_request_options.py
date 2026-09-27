"""Request options: every documented one classified, and each class kept honest.

Read against the product's request-properties page for the first time on
2026-09-27 (it had been unreachable from here), which turned up four things the
option table got wrong. Each one is pinned below with what was measured.

1. **Three names were invented.** The table said ``query_datetime_scope_*``;
   the documentation, and every caller, says ``query_datetimescope_*``. The
   real names still got refused, but by the generic "not one of the options"
   fallback, and the reasons written for them were keyed on spellings nobody
   sends.
2. **``request_readonly`` was a no-op** on the grounds that translated KQL only
   reads, which stopped being true when ingestion landed. Measured: Kusto
   refuses ``.set-or-replace`` under it, or under ``request_readonly_hardline``
   ("Cannot invoke control command … the readonly flag is set"). Here the write
   went through, on the client and on ``serve --allow-write``.
3. **The quiet sibling of 2.** ``KustoClient.execute_query`` handed a write to
   `to_sql` with its default ``allow_write=True``, so a client constructed with
   ``allow_write=False`` wrote anyway — and so did ``execute``, when a ``//``
   comment above the command hid the leading dot it dispatches on. Kusto
   refuses a control command on the query endpoint unless it is a ``.show``.
4. **``serve`` accepted ``query_now`` and ``servertimeout`` and never used
   them**: `check_options` classified them as implemented and `run` was never
   handed the options, so a pinned clock was answered from the wall clock.

The documentation was not trusted either. It lists thirteen options that
"can't be set with a set statement"; measured, Kusto accepts all thirteen as
statements and honours two of them (``set servertimeout = 10ms`` times out,
``set truncationmaxsize = 10`` refuses a large result). Classifying by that list
would have been classifying by a claim the oracle contradicts.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlUnsupportedError
from duckdb_kql.options import OPTION_SUPPORT, OptionSupport, read_only_requested

#: Every property on the request-properties page, as fetched 2026-09-27.
DOCUMENTED = frozenset(
    {
        "best_effort",
        "client_max_redirect_count",
        "client_results_reader_allow_varying_row_widths",
        "deferpartialqueryfailures",
        "materialized_view_shuffle_query",
        "max_memory_consumption_per_query_per_node",
        "maxmemoryconsumptionperiterator",
        "maxoutputcolumns",
        "norequesttimeout",
        "notruncation",
        "push_selection_through_aggregation",
        "query_bin_auto_at",
        "query_bin_auto_size",
        "query_cursor_after_default",
        "query_cursor_before_or_at_default",
        "query_cursor_current",
        "query_cursor_disabled",
        "query_cursor_scoped_tables",
        "query_datascope",
        "query_datetimescope_column",
        "query_datetimescope_from",
        "query_datetimescope_to",
        "query_distribution_nodes_span",
        "query_fanout_nodes_percent",
        "query_fanout_threads_percent",
        "query_force_row_level_security",
        "query_language",
        "query_log_query_parameters",
        "query_max_entities_in_union",
        "query_now",
        "query_optimize_fts_at_relop",
        "query_python_debug",
        "query_results_apply_getschema",
        "query_results_cache_force_refresh",
        "query_results_cache_max_age",
        "query_results_cache_per_shard",
        "query_results_progressive_row_count",
        "query_results_progressive_update_period",
        "query_take_max_records",
        "query_weakconsistency_session_id",
        "queryconsistency",
        "request_app_name",
        "request_block_row_level_security",
        "request_callout_disabled",
        "request_description",
        "request_external_data_disabled",
        "request_external_table_disabled",
        "request_impersonation_disabled",
        "request_readonly",
        "request_readonly_hardline",
        "request_remote_entities_disabled",
        "request_sandboxed_execution_disabled",
        "request_user",
        "results_error_reporting_placement",
        "results_progressive_enabled",
        "results_v2_fragment_primary_tables",
        "results_v2_newlines_between_frames",
        "servertimeout",
        "truncationmaxrecords",
        "truncationmaxsize",
        "validatepermissions",
    }
)


def test_every_documented_option_is_classified_by_name() -> None:
    """None of them reaches the generic fallback, which explains nothing."""
    assert sorted(DOCUMENTED - set(OPTION_SUPPORT)) == []


def test_every_classified_option_is_a_documented_one() -> None:
    """The converse is what catches an invented spelling: a reason keyed on a
    name no caller sends is a reason no caller ever reads."""
    assert sorted(set(OPTION_SUPPORT) - DOCUMENTED) == []


@pytest.mark.parametrize("wrong", ["column", "from", "to"])
def test_the_datetime_scope_spelling_is_kustos(wrong) -> None:
    assert f"query_datetime_scope_{wrong}" not in OPTION_SUPPORT
    support, reason = OPTION_SUPPORT[f"query_datetimescope_{wrong}"]
    assert support == OptionSupport.REFUSED and "widen" in reason


# ---------------------------------------------------------------------------
# A restriction that already holds — and the refusal that makes it hold
# ---------------------------------------------------------------------------

#: Each "restriction already holds" no-op, and constructs it forbids. They are a
#: no-op only *because* every one of these is refused. If one starts translating,
#: this fails naming the option whose classification just became a lie.
GUARDED = {
    "request_callout_disabled": [
        "evaluate http_request('https://example.com')",
        "evaluate sql_request('Server=x', 'select 1')",
        "print x = dynamic({}) | evaluate bag_unpack(x)",
    ],
    "request_sandboxed_execution_disabled": [
        "print x = 1 | evaluate python(typeof(*), 'result = df')",
    ],
    "request_external_data_disabled": [
        "externaldata(a:string)['https://example.com/a.csv']",
        "external_table('T')",
    ],
    "request_external_table_disabled": ["external_table('T')"],
    "query_cursor_disabled": ["print cursor_current()", "T | where cursor_after('')"],
}


@pytest.mark.parametrize(
    ("option", "query"),
    [(option, query) for option, queries in GUARDED.items() for query in queries],
)
def test_what_a_restriction_forbids_is_still_refused(option, query) -> None:
    assert OPTION_SUPPORT[option][0] == OptionSupport.NO_OP
    with pytest.raises(KqlUnsupportedError):
        duckdb_kql.to_sql(query)


def test_every_restriction_no_op_is_guarded() -> None:
    restrictions = {
        name
        for name, (support, reason) in OPTION_SUPPORT.items()
        if support == OptionSupport.NO_OP and "already holds" in reason
    }
    assert restrictions == set(GUARDED)


# ---------------------------------------------------------------------------
# The set-statement form
# ---------------------------------------------------------------------------


@pytest.fixture
def con():
    duckdb = pytest.importorskip("duckdb")
    c = duckdb.connect()
    yield c
    c.close()


@pytest.mark.parametrize(
    "statement",
    [
        "set request_readonly = true",
        "set request_readonly_hardline = true",
        "set request_app_name = 'tests'",
        "set request_user = 'u'",
        "set results_progressive_enabled = true",
        "set push_selection_through_aggregation = true",
    ],
)
def test_a_statement_kusto_accepts_and_that_changes_nothing_runs(con, statement) -> None:
    """Measured: Kusto runs each of these, several of them on the documentation's
    "can't be set with a set statement" list. None changes what a query returns
    here, and `request_readonly` is implemented as a *request* option — a
    statement form refused because of that would reject a query that cannot
    write in the first place."""
    assert duckdb_kql.kql(con, f"{statement}; print x = 1").fetchall() == [(1,)]


@pytest.mark.parametrize(
    ("statement", "because"),
    [
        # Measured: SEM0004 once the result has more columns than this.
        ("maxoutputcolumns = 1", "refusing the query"),
        # Measured: the result becomes a ColumnName/ColumnOrdinal/... table.
        ("query_results_apply_getschema = true", "getschema"),
        ("query_datetimescope_column = 'T'", "widen"),
    ],
)
def test_a_statement_that_changes_the_answer_in_kusto_is_refused(statement, because) -> None:
    with pytest.raises(KqlUnsupportedError, match=because):
        duckdb_kql.to_sql(f"set {statement}; print a = 1, b = 2")


# ---------------------------------------------------------------------------
# Read-only, and writes on the query path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"request_readonly": True}, True),
        ({"request_readonly_hardline": True}, True),
        ({"Request_ReadOnly": "true"}, True),  # a hand-written wire request
        ({"request_readonly": False, "request_readonly_hardline": False}, False),
        ({"request_readonly": "false"}, False),
        ({}, False),
    ],
)
def test_read_only_requested(options, expected) -> None:
    assert read_only_requested(options) is expected


def _tables(con) -> list[str]:
    return sorted(
        row[0] for row in con.sql("SELECT table_name FROM information_schema.tables").fetchall()
    )


@pytest.fixture
def client():
    from duckdb_kql.kusto import KustoClient

    c = KustoClient(":memory:")
    yield c
    c.close()


@pytest.mark.parametrize("option", ["request_readonly", "request_readonly_hardline"])
def test_a_read_only_request_does_not_write(client, option) -> None:
    from duckdb_kql.kusto import ClientRequestProperties, KustoServiceError

    props = ClientRequestProperties()
    props.set_option(option, True)
    with pytest.raises(KustoServiceError, match="read-only"):
        client.execute_mgmt(None, ".set-or-replace T <| print a = 1", props)
    assert _tables(client._connection) == []

    # And the same client writes when the request does not decline it.
    props.set_option(option, False)
    client.execute_mgmt(None, ".set-or-replace T <| print a = 1", props)
    assert _tables(client._connection) == ["T"]


@pytest.mark.parametrize(
    ("method", "command"),
    [
        ("execute_query", ".set-or-replace T <| print a = 1"),
        ("execute_query", "// a note\n.set-or-replace T <| print a = 1"),
        ("execute", "// a note\n.set-or-replace T <| print a = 1"),
        ("execute_query", ".create database D"),
    ],
)
@pytest.mark.parametrize("allow_write", [False, True])
def test_the_query_path_does_not_write(method, command, allow_write) -> None:
    """Measured: Kusto's query endpoints refuse every control command but
    ``.show``, and its management endpoint refuses one preceded by a comment
    (SYN0100). With ``allow_write=False`` this wrote ``T`` before the fix."""
    from duckdb_kql.kusto import KustoClient, KustoServiceError

    with KustoClient(":memory:", allow_write=allow_write) as client:
        with pytest.raises(KustoServiceError, match="query endpoint"):
            getattr(client, method)(None, command)
        assert _tables(client._connection) == []
        # `.show` is still served there, as Kusto serves it.
        assert client.execute_query(None, ".show tables").primary_results[0] is not None


# ---------------------------------------------------------------------------
# The server hands the options it accepts to the query
# ---------------------------------------------------------------------------


@pytest.fixture
def serve():
    from duckdb_kql import fixtures
    from duckdb_kql.server import build_server

    started = []

    def start(*, allow_write: bool = False):
        server = build_server(quiet=True, port=0, allow_write=allow_write)
        fixtures.load_duckdb(server._con)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        started.append((server, thread))
        return server

    yield start
    for server, thread in started:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _post(server, route: str, csl: str, options: dict) -> tuple[int, object]:
    body = json.dumps({"csl": csl, "properties": {"Options": options}}).encode()
    request = urllib.request.Request(
        server.url + route, body, {"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.status, json.loads(exc.read())


def test_the_server_pins_the_clock_it_was_asked_to(serve) -> None:
    server = serve()
    status, body = _post(
        server, "/v1/rest/query", "print t = now()", {"query_now": "2020-01-02T03:04:05Z"}
    )
    assert status == 200, body
    ((value,),) = body["Tables"][0]["Rows"]
    assert value.startswith("2020-01-02T03:04:05"), value


def test_the_server_enforces_servertimeout(serve) -> None:
    server = serve()
    slow = (
        "StormEvents | join kind=inner (StormEvents) on State "
        "| join kind=inner (StormEvents) on State | summarize c = count()"
    )
    status, body = _post(server, "/v1/rest/query", slow, {"servertimeout": "00:00:00.050"})
    assert status == 400 and "timed out" in json.dumps(body)
    # The connection survives the interrupt.
    status, _ = _post(server, "/v1/rest/query", "print x = 1", {})
    assert status == 200


@pytest.mark.parametrize("option", ["request_readonly", "request_readonly_hardline"])
def test_the_server_refuses_a_read_only_write_even_with_allow_write(serve, option) -> None:
    server = serve(allow_write=True)
    status, body = _post(
        server, "/v1/rest/mgmt", ".set-or-replace RoProbe <| print a = 1", {option: True}
    )
    assert status == 400 and "read-only" in json.dumps(body)
    assert "RoProbe" not in _tables(server._con)

    status, body = _post(
        server, "/v1/rest/mgmt", ".set-or-replace RoProbe <| print a = 1", {option: False}
    )
    assert status == 200, body
    assert "RoProbe" in _tables(server._con)


def test_the_web_uis_options_still_let_a_query_through(serve) -> None:
    """Sent verbatim on every query; `request_readonly_hardline` among them."""
    from test_server import ADX_QUERY_OPTIONS

    status, body = _post(serve(), "/v2/rest/query", "print x = 1", ADX_QUERY_OPTIONS)
    assert status == 200, body

