"""L5 traps — the ``azure-kusto-data`` drop-in.

Two things are being tested, and they pull in opposite directions.

The first is *fidelity*: code written against the SDK has to keep working, which
means the response shape, the wire format inside ``raw_rows``, and the dtypes
``dataframe_from_result_table`` produces all have to match what the real client
returns. Getting these subtly wrong is worse than not supporting them, because
nothing fails — a column just arrives as ``object`` and comparisons stop working.

The second is *refusal*: everything this client cannot honour has to say so.
The tests below walk the whole option table and assert that nothing is merely
stored and ignored, because an ignored ``servertimeout`` or
``truncationmaxrecords`` is a promise the caller thinks is being kept.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

import pytest

from duckdb_kql import fixtures
from duckdb_kql.kusto import (
    OPTION_SUPPORT,
    ClientRequestProperties,
    KustoClient,
    KustoClosedError,
    KustoConnectionStringBuilder,
    KustoServiceError,
    KustoUnsupportedError,
    OptionSupport,
    WellKnownDataSet,
)

duckdb = pytest.importorskip("duckdb")


@pytest.fixture(scope="module")
def client():
    c = KustoClient(":memory:")
    fixtures.load_duckdb(c._connection)
    yield c
    c.close()


PARAMETERIZED = (
    "declare query_parameters(state:string);\n"
    "StormEvents | where State == state | count"
)


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


def test_response_carries_the_three_tables_a_query_returns(client) -> None:
    response = client.execute("db", "StormEvents | take 2")
    assert len(response) == 3
    assert [t.table_kind for t in response] == [
        WellKnownDataSet.PrimaryResult,
        WellKnownDataSet.QueryProperties,
        WellKnownDataSet.QueryCompletionInformation,
    ]


def test_primary_results_selects_only_the_query_output(client) -> None:
    response = client.execute("db", "StormEvents | take 2")
    assert len(response.primary_results) == 1
    assert len(response.primary_results[0]) == 2


def test_response_is_indexable_by_position_and_by_name(client) -> None:
    response = client.execute("db", "print x = 1")
    assert response[0].table_name == "PrimaryResult"
    assert response["QueryCompletionInformation"].table_kind is (
        WellKnownDataSet.QueryCompletionInformation
    )
    with pytest.raises(LookupError):
        response["NoSuchTable"]


def test_rows_are_addressable_by_index_and_by_column_name(client) -> None:
    table = client.execute(
        "db", "StormEvents | project State, EventType | take 1"
    ).primary_results[0]
    row = table[0]
    assert row[0] == row["State"]
    assert list(row) == [row["State"], row["EventType"]]
    assert row.to_dict() == {"State": row["State"], "EventType": row["EventType"]}
    assert len(row) == 2


def test_column_types_are_kusto_names_not_duckdb_ones(client) -> None:
    table = client.execute(
        "db",
        "print s='a', n=1, r=1.5, b=true, t=datetime(2020-01-01), "
        "sp=2h, d=dynamic({'k':1}), g=toguid('12345678-1234-5678-1234-567812345678')",
    ).primary_results[0]
    assert [c.column_type for c in table.columns] == [
        "string", "long", "real", "bool", "datetime", "timespan", "dynamic", "guid",
    ]


def test_errors_count_is_zero_because_failures_raise(client) -> None:
    response = client.execute("db", "print x = 1")
    assert response.errors_count == 0
    assert response.get_exceptions() == []


def test_table_is_json_serializable(client) -> None:
    table = client.execute("db", "print x = 1").primary_results[0]
    assert json.loads(str(table))["data"] == [{"x": 1}]


# ---------------------------------------------------------------------------
# The wire format inside raw_rows
# ---------------------------------------------------------------------------


def test_datetime_is_stored_as_an_iso_string(client) -> None:
    """``raw_rows`` holds what Kusto *sends*, which the converters parse.

    Storing the Python datetime instead would skip every downstream converter
    and land a different dtype in the DataFrame than the real client produces.
    """
    table = client.execute("db", "print t = datetime(2020-01-02 03:04:05)").primary_results[0]
    assert table.raw_rows[0][0] == "2020-01-02T03:04:05.000000Z"
    assert table[0]["t"] == dt.datetime(2020, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc)


def test_timespan_is_stored_in_dotnet_format(client) -> None:
    table = client.execute("db", "print s = 1d + 2h + 3m + 4s").primary_results[0]
    assert table.raw_rows[0][0] == "1.02:03:04"
    assert table[0]["s"] == dt.timedelta(days=1, hours=2, minutes=3, seconds=4)


def test_sub_second_timespan_keeps_seven_digits(client) -> None:
    """Kusto reports 100ns ticks; DuckDB stores microseconds.

    The seventh digit is written as 0 rather than invented, so the format is
    right without the precision being overstated.
    """
    table = client.execute("db", "print s = 1500ms").primary_results[0]
    assert table.raw_rows[0][0] == "00:00:01.5000000"


def test_negative_timespan_keeps_its_sign(client) -> None:
    table = client.execute("db", "print s = -2h").primary_results[0]
    assert table.raw_rows[0][0] == "-02:00:00"
    assert table[0]["s"] == dt.timedelta(hours=-2)


def test_dynamic_is_stored_parsed_not_as_text(client) -> None:
    table = client.execute("db", "print d = dynamic({'a':[1,2]})").primary_results[0]
    assert table.raw_rows[0][0] == {"a": [1, 2]}


def test_guid_is_stored_as_a_string(client) -> None:
    g = "12345678-1234-5678-1234-567812345678"
    table = client.execute("db", f"print g = toguid('{g}')").primary_results[0]
    assert table.raw_rows[0][0] == g


def test_null_stays_null(client) -> None:
    table = client.execute("db", "print x = tostring(dynamic(null))").primary_results[0]
    assert table.raw_rows[0][0] is None


# ---------------------------------------------------------------------------
# dataframe_from_result_table
# ---------------------------------------------------------------------------


def test_dataframe_dtypes_match_the_sdks() -> None:
    pd = pytest.importorskip("pandas")
    from duckdb_kql.kusto.helpers import dataframe_from_result_table

    with KustoClient(":memory:") as c:
        table = c.execute(
            "db",
            "print s='a', n=1, r=1.5, b=true, t=datetime(2020-01-01), sp=2h",
        ).primary_results[0]
        frame = dataframe_from_result_table(table)

    assert frame["n"].dtype == pd.Int64Dtype()
    assert frame["r"].dtype == pd.Float64Dtype()
    assert frame["b"].dtype == bool
    assert str(frame["t"].dtype).startswith("datetime64")
    assert str(frame["sp"].dtype).startswith("timedelta64")
    assert frame["t"][0] == pd.Timestamp("2020-01-01", tz="UTC")
    assert frame["sp"][0] == pd.Timedelta(hours=2)


def test_dataframe_rejects_something_that_is_not_a_result_table() -> None:
    pytest.importorskip("pandas")
    from duckdb_kql.kusto.helpers import dataframe_from_result_table

    with pytest.raises(TypeError):
        dataframe_from_result_table(object())


def test_dataframe_converters_can_be_overridden() -> None:
    pytest.importorskip("pandas")
    from duckdb_kql.kusto.helpers import dataframe_from_result_table

    with KustoClient(":memory:") as c:
        table = c.execute("db", "print n = 1").primary_results[0]
        frame = dataframe_from_result_table(
            table, converters_by_column_name={"n": lambda col, df: df[col].astype(str)}
        )
    assert frame["n"][0] == "1"


# ---------------------------------------------------------------------------
# Query parameters — the reason this layer exists
# ---------------------------------------------------------------------------


def test_set_parameter_binds_a_value_not_query_text(client) -> None:
    props = ClientRequestProperties()
    props.set_parameter("state", "TEXAS")
    count = client.execute("db", PARAMETERIZED, props).primary_results[0][0]["Count"]
    assert count > 0


@pytest.mark.parametrize(
    "payload",
    [
        "' OR 1=1 --",
        "'; DROP TABLE StormEvents; --",
        "TEXAS' OR State != '",
        "%",
    ],
)
def test_injection_payload_matches_nothing(client, payload: str) -> None:
    props = ClientRequestProperties()
    props.set_parameter("state", payload)
    count = client.execute("db", PARAMETERIZED, props).primary_results[0][0]["Count"]
    assert count == 0


def test_injection_payload_does_not_drop_the_table(client) -> None:
    props = ClientRequestProperties()
    props.set_parameter("state", "'; DROP TABLE StormEvents; --")
    client.execute("db", PARAMETERIZED, props)
    assert client.execute("db", "StormEvents | count").primary_results[0][0][0] > 0


def test_missing_parameter_is_a_semantic_error(client) -> None:
    """Not a DuckDB complaint about a generated slot the caller never saw."""
    with pytest.raises(KustoServiceError) as exc:
        client.execute("db", PARAMETERIZED)
    assert "state" in str(exc.value)
    assert exc.value.is_semantic_error()


def test_parameter_for_an_undeclared_name_is_refused(client) -> None:
    props = ClientRequestProperties()
    props.set_parameter("stat", "TEXAS")
    with pytest.raises(KustoServiceError):
        client.execute("db", PARAMETERIZED, props)


def test_parameter_accessors_match_the_sdk() -> None:
    props = ClientRequestProperties()
    assert not props.has_parameter("a")
    props.set_parameter("a", "x")
    assert props.has_parameter("a")
    assert props.get_parameter("a", "default") == "x"
    assert props.get_parameter("b", "default") == "default"
    assert json.loads(props.to_json())["Parameters"] == {"a": "x"}


# ---------------------------------------------------------------------------
# Request options: implemented or refused, never silently ignored
# ---------------------------------------------------------------------------


def test_every_classified_option_has_a_real_reason() -> None:
    for name, (support, reason) in OPTION_SUPPORT.items():
        assert support in (
            OptionSupport.IMPLEMENTED,
            OptionSupport.NO_OP,
            OptionSupport.REFUSED,
        ), name
        assert len(reason) > 20, f"{name} needs an explanation, not a placeholder"


@pytest.mark.parametrize(
    "name",
    [n for n, (s, _) in OPTION_SUPPORT.items() if s == OptionSupport.REFUSED],
)
def test_refused_options_raise_at_the_line_that_sets_them(name: str) -> None:
    with pytest.raises(KustoUnsupportedError):
        ClientRequestProperties().set_option(name, 1)


@pytest.mark.parametrize(
    "name",
    [n for n, (s, _) in OPTION_SUPPORT.items() if s != OptionSupport.REFUSED],
)
def test_accepted_options_are_accepted(name: str) -> None:
    props = ClientRequestProperties()
    props.set_option(name, 1)
    assert props.has_option(name)


def test_an_unrecognised_option_is_refused_rather_than_stored() -> None:
    """An option nobody classified cannot be assumed harmless."""
    with pytest.raises(KustoUnsupportedError):
        ClientRequestProperties().set_option("some_future_option", True)


def test_server_timeout_actually_interrupts(client) -> None:
    """The whole point: a timeout that is set must be a timeout that fires."""
    props = ClientRequestProperties()
    props.set_option(props.request_timeout_option_name, dt.timedelta(milliseconds=50))
    slow = (
        "StormEvents | join kind=inner (StormEvents) on State "
        "| join kind=inner (StormEvents) on State | summarize c = count()"
    )
    with pytest.raises(KustoServiceError) as exc:
        client.execute("db", slow, props)
    assert "timed out" in str(exc.value)
    # The connection has to survive the interrupt, or the timeout costs the client.
    assert client.execute("db", "print x = 1").primary_results[0][0][0] == 1


def test_no_request_timeout_disables_the_deadline(client) -> None:
    props = ClientRequestProperties()
    props.set_option(props.request_timeout_option_name, dt.timedelta(milliseconds=1))
    props.set_option(props.no_request_timeout_option_name, True)
    assert client.execute("db", "print x = 1", props).primary_results[0][0][0] == 1


def test_server_timeout_accepts_a_kql_timespan(client) -> None:
    props = ClientRequestProperties()
    props.set_option(props.request_timeout_option_name, "5m")
    assert client.execute("db", "print x = 1", props).primary_results[0][0][0] == 1


def test_unparseable_timeout_is_refused(client) -> None:
    props = ClientRequestProperties()
    props.set_option(props.request_timeout_option_name, "whenever")
    with pytest.raises(KustoUnsupportedError):
        client.execute("db", "print x = 1", props)


def test_client_request_id_is_carried_into_the_response(client) -> None:
    props = ClientRequestProperties()
    props.client_request_id = "MyApp;42"
    response = client.execute("db", "print x = 1", props)
    completion = response["QueryCompletionInformation"]
    assert completion[0]["ClientRequestId"] == "MyApp;42"
    assert props.get_tracing_attributes() == {"client_request_id": "MyApp;42"}


def test_option_name_must_not_be_empty() -> None:
    with pytest.raises(ValueError):
        ClientRequestProperties().set_option("  ", 1)
    with pytest.raises(ValueError):
        ClientRequestProperties().set_parameter("", 1)


# ---------------------------------------------------------------------------
# Connection strings
# ---------------------------------------------------------------------------


def test_a_bare_path_is_a_data_source() -> None:
    kcsb = KustoConnectionStringBuilder(":memory:")
    assert kcsb.data_source == ":memory:"
    assert kcsb.database_name is None


def test_a_keyword_connection_string_is_parsed() -> None:
    kcsb = KustoConnectionStringBuilder(
        "Data Source=analytics.duckdb;Initial Catalog=Logs"
    )
    assert kcsb.data_source == "analytics.duckdb"
    assert kcsb.database_name == "Logs"


def test_a_cluster_url_is_refused_rather_than_reinterpreted() -> None:
    """This refusal is what makes discarding credentials defensible.

    If a cluster URL silently became a local file, a caller would get answers
    from data that has nothing to do with the cluster they named.
    """
    for url in (
        "https://help.kusto.windows.net",
        "http://localhost:8080",
        "net.tcp://cluster",
    ):
        with pytest.raises(KustoUnsupportedError):
            KustoConnectionStringBuilder(url)
        with pytest.raises(KustoUnsupportedError):
            KustoClient(url)


def test_auth_constructors_exist_and_discard_their_credentials() -> None:
    kcsb = KustoConnectionStringBuilder.with_aad_application_key_authentication(
        ":memory:", "app-id", "app-secret", "tenant"
    )
    assert kcsb.data_source == ":memory:"
    assert "app-secret" not in json.dumps(kcsb.ignored_credentials)


def test_auth_constructor_still_refuses_a_cluster_url() -> None:
    with pytest.raises(KustoUnsupportedError):
        KustoConnectionStringBuilder.with_az_cli_authentication(
            "https://help.kusto.windows.net"
        )


def test_credentials_in_a_keyword_string_are_recorded_as_dropped() -> None:
    kcsb = KustoConnectionStringBuilder(
        "Data Source=x.duckdb;AAD Federated Security=True;Application Key=hunter2"
    )
    assert set(kcsb.ignored_credentials) == {
        "AAD Federated Security",
        "Application Key",
    }


# ---------------------------------------------------------------------------
# Lifecycle and databases
# ---------------------------------------------------------------------------


def test_an_existing_connection_is_borrowed_not_adopted() -> None:
    con = duckdb.connect()
    with KustoClient(con) as c:
        assert c.execute("db", "print x = 1").primary_results[0][0][0] == 1
    # Closing the client must not close a connection it did not open.
    assert con.execute("SELECT 1").fetchone() == (1,)


def test_a_closed_client_refuses_to_run() -> None:
    c = KustoClient(":memory:")
    c.close()
    with pytest.raises(KustoClosedError):
        c.execute("db", "print x = 1")


def test_close_is_idempotent() -> None:
    c = KustoClient(":memory:")
    c.close()
    c.close()


def test_a_database_that_is_not_attached_is_refused(tmp_path) -> None:
    """Silently answering from the wrong database is the failure to avoid."""
    c = KustoClient(f"Data Source={tmp_path / 'a.duckdb'};Initial Catalog=Main")
    with pytest.raises(KustoUnsupportedError) as exc:
        c.execute("Other", "print x = 1")
    assert "ATTACH" in str(exc.value)
    c.close()


def test_an_attached_database_is_selected(tmp_path) -> None:
    other = tmp_path / "other.duckdb"
    c = KustoClient(":memory:")
    c._connection.execute(f"ATTACH '{other}' AS Other")
    c._connection.execute("CREATE TABLE Other.T AS SELECT 1 AS a")
    assert c.execute("Other", "T | count").primary_results[0][0][0] == 1
    c.close()


def test_a_file_backed_database_round_trips(tmp_path) -> None:
    path = tmp_path / "analytics.duckdb"
    with KustoClient(str(path)) as c:
        c._connection.execute("CREATE TABLE T AS SELECT 42 AS a")
    with KustoClient(str(path)) as c:
        assert c.execute(None, "T | project a").primary_results[0][0]["a"] == 42


# ---------------------------------------------------------------------------
# Control commands
# ---------------------------------------------------------------------------


def test_execute_dispatches_a_dot_command_to_mgmt(client) -> None:
    table = client.execute("db", ".show version").primary_results[0]
    assert [c.column_name for c in table.columns][:2] == ["BuildVersion", "BuildTime"]


def test_show_tables_lists_what_is_there(client) -> None:
    names = {
        row["TableName"]
        for row in client.execute("db", ".show tables").primary_results[0]
    }
    assert {"StormEvents", "PopulationData"} <= names


def test_show_databases_lists_the_catalog(client) -> None:
    table = client.execute("db", ".show databases").primary_results[0]
    assert len(table) >= 1


@pytest.mark.parametrize(
    "command",
    [
        ".create table Foo (a:int)",
        ".ingest inline into table Foo <| 1",
        ".show table StormEvents policy retention",
        ".drop table StormEvents",
    ],
)
def test_unimplemented_control_commands_are_refused(client, command: str) -> None:
    """A stub returning an empty table would look like a command that worked."""
    with pytest.raises(KustoUnsupportedError):
        client.execute("db", command)


def test_a_refused_command_did_not_run(client) -> None:
    with pytest.raises(KustoUnsupportedError):
        client.execute("db", ".drop table StormEvents")
    assert client.execute("db", "StormEvents | count").primary_results[0][0][0] > 0


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_a_syntax_error_is_a_semantic_kusto_error(client) -> None:
    with pytest.raises(KustoServiceError) as exc:
        client.execute("db", "StormEvents | wherex Level == 1")
    assert exc.value.is_semantic_error()
    assert not exc.value.has_partial_results()
    assert exc.value.get_partial_results() == []


def test_an_unsupported_construct_is_a_semantic_error(client) -> None:
    with pytest.raises(KustoServiceError) as exc:
        client.execute("db", "StormEvents | parse State with * 'X' *")
    assert exc.value.is_semantic_error()


def test_an_unknown_column_is_a_service_error(client) -> None:
    with pytest.raises(KustoServiceError):
        client.execute("db", "StormEvents | where NoSuchColumn == 1")


# ---------------------------------------------------------------------------
# Drop-in fidelity against the real SDK, when it happens to be installed
# ---------------------------------------------------------------------------


def test_sdk_helper_accepts_our_table_when_the_sdk_is_installed(client) -> None:
    """The SDK's helper type-checks its argument; we register so it passes.

    Skipped when ``azure-kusto-data`` is absent, which is the normal case — the
    point of this package is not needing it.
    """
    sdk_helpers = pytest.importorskip("azure.kusto.data.helpers")
    pytest.importorskip("pandas")

    table = client.execute("db", "print n = 1").primary_results[0]
    frame = sdk_helpers.dataframe_from_result_table(table)
    assert frame["n"][0] == 1


def test_uuid_columns_survive_the_round_trip(client) -> None:
    g = uuid.UUID("12345678-1234-5678-1234-567812345678")
    table = client.execute("db", f"print g = toguid('{g}')").primary_results[0]
    assert table[0]["g"] == str(g)
