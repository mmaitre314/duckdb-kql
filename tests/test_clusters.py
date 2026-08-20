"""`cluster()` — running queries written against a real service on local data.

Test suites hold queries written for production:

    cluster('mycluster.eastus.kusto.windows.net').database('mydb').MyTable

There is no cluster here, so the reference is **mapped**, and the mapping is
supplied rather than guessed. Unmapped is refused, because treating a cluster
reference as local would answer a question about production with local data —
a wrong answer that looks exactly like a right one.

The fixture mirrors the layout a caller actually uses: `ATTACH ':memory:' AS
database1` makes a DuckDB **catalog**, and `CREATE TABLE database1.table1` puts
the table in that catalog's `main` schema. So the mapping target is a catalog
name, which is the same thing `database=` renders.

Cluster spellings are normalized because Kusto itself resolves the argument to
`https://<host>/`, which was measured on the emulator: `cluster('mycluster')`,
`cluster('https://mycluster')` and a trailing slash are one host. The **short
name is not expanded** to a domain — the emulator does not expand it either, and
inventing that rule would resolve a name the engine resolves differently.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

import duckdb_kql
from duckdb_kql.clusters import normalize_cluster, parse_cluster_map
from duckdb_kql.errors import KqlSchemaError

duckdb = pytest.importorskip("duckdb")

CLUSTER = "mycluster.eastus.kusto.windows.net"
OTHER = "othercluster.westus.kusto.windows.net"


@pytest.fixture
def con():
    """The multi-database layout a caller builds for tests."""
    c = duckdb_kql.connect()
    c.sql("ATTACH ':memory:' AS database1")
    c.sql("ATTACH ':memory:' AS database2")
    c.sql(
        "CREATE OR REPLACE TABLE database1.table1 AS SELECT * FROM (VALUES "
        "('authentication', 'alert-1', 'tenant-a'), "
        "('authentication', 'alert-2', 'tenant-b')) "
        "AS rows(GeneratingRuleName, AlertId, TenantId)"
    )
    c.sql(
        "CREATE OR REPLACE TABLE database2.table2 AS SELECT * FROM (VALUES "
        "('alert-1'), ('alert-2')) AS rows(AlertId)"
    )
    return c


MAP = {(CLUSTER, "mydb"): "database1", (CLUSTER, "otherdb"): "database2"}


# ---------------------------------------------------------------------------
# It resolves
# ---------------------------------------------------------------------------


def test_a_mapped_cluster_reads_the_local_table(con) -> None:
    rows = duckdb_kql.kql(
        con,
        f"cluster('{CLUSTER}').database('mydb').table1 | project AlertId",
        clusters=MAP,
    ).fetchall()
    assert rows == [("alert-1",), ("alert-2",)]


def test_two_databases_on_one_cluster_stay_distinct(con) -> None:
    a = duckdb_kql.kql(
        con, f"cluster('{CLUSTER}').database('mydb').table1 | count", clusters=MAP
    ).fetchall()
    b = duckdb_kql.kql(
        con, f"cluster('{CLUSTER}').database('otherdb').table2 | count", clusters=MAP
    ).fetchall()
    assert a == [(2,)] and b == [(2,)]


def test_a_join_across_two_cluster_references(con) -> None:
    """The shape the real queries take."""
    rows = duckdb_kql.kql(
        con,
        f"cluster('{CLUSTER}').database('mydb').table1"
        f"| join kind=inner (cluster('{CLUSTER}').database('otherdb').table2)"
        " on AlertId"
        "| count",
        clusters=MAP,
    ).fetchall()
    assert rows == [(2,)]


def test_the_nested_map_shape_works_too(con) -> None:
    """What a JSON config file looks like."""
    rows = duckdb_kql.kql(
        con,
        f"cluster('{CLUSTER}').database('mydb').table1 | count",
        clusters={CLUSTER: {"mydb": "database1"}},
    ).fetchall()
    assert rows == [(2,)]


def test_it_works_without_a_connection() -> None:
    """Layer 0 keeps working: the mapping is a translate-time rewrite."""
    sql = str(
        duckdb_kql.to_sql(
            f"cluster('{CLUSTER}').database('mydb').table1 | count", clusters=MAP
        )
    )
    assert '"database1"."table1"' in sql


# ---------------------------------------------------------------------------
# Spellings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "written",
    [
        CLUSTER,
        f"https://{CLUSTER}",
        f"https://{CLUSTER}/",
        f"HTTPS://{CLUSTER.upper()}/",
        f"http://{CLUSTER}",
    ],
)
def test_one_entry_covers_every_spelling_of_a_host(con, written: str) -> None:
    rows = duckdb_kql.kql(
        con, f"cluster('{written}').database('mydb').table1 | count", clusters=MAP
    ).fetchall()
    assert rows == [(2,)]


def test_a_short_name_is_not_expanded_to_a_domain(con) -> None:
    """Measured: the emulator resolves `cluster('mycluster')` to `https://mycluster/`.

    So the short name is a *different host*, not shorthand. Expanding it here
    would resolve a name differently from the engine we translate for.
    """
    assert normalize_cluster("mycluster") == "mycluster"
    with pytest.raises(KqlSchemaError):
        duckdb_kql.kql(
            con, "cluster('mycluster').database('mydb').table1", clusters=MAP
        )


def test_the_database_name_is_matched_exactly(con) -> None:
    """Identifiers are case-sensitive here (R7), unlike the hostname."""
    with pytest.raises(KqlSchemaError):
        duckdb_kql.kql(
            con, f"cluster('{CLUSTER}').database('MYDB').table1", clusters=MAP
        )


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_without_a_map_it_is_refused(con) -> None:
    with pytest.raises(KqlSchemaError) as caught:
        duckdb_kql.kql(con, f"cluster('{CLUSTER}').database('mydb').table1")
    assert CLUSTER in str(caught.value)
    assert "clusters=" in str(caught.value)


def test_an_unmapped_cluster_lists_what_is_mapped(con) -> None:
    """The usual mistake is a spelling, so the error shows the alternatives."""
    with pytest.raises(KqlSchemaError) as caught:
        duckdb_kql.kql(
            con, f"cluster('{OTHER}').database('mydb').table1", clusters=MAP
        )
    message = str(caught.value)
    assert OTHER in message
    assert f"{CLUSTER}/mydb" in message


def test_an_unmapped_cluster_reads_nothing(con) -> None:
    """The refusal must happen before any query runs, not after."""
    with pytest.raises(KqlSchemaError):
        duckdb_kql.kql(
            con, f"cluster('{OTHER}').database('mydb').table1", clusters=MAP
        )


def test_a_cluster_without_a_database_is_refused(con) -> None:
    """Kusto rejects it too — SEM0048, "database name must be explicit"."""
    with pytest.raises(Exception, match="(?i)cluster"):
        duckdb_kql.kql(con, f"cluster('{CLUSTER}').table1", clusters=MAP)


@pytest.mark.parametrize(
    "bad",
    [
        {("only-one",): "x"},
        {("a", "b"): {"nested": "wrong"}},
        {"host": "should-be-a-dict"},
        {"host": {"db": 5}},
    ],
)
def test_a_malformed_map_is_rejected_not_ignored(bad) -> None:
    """A dropped entry would send the query somewhere else, silently."""
    with pytest.raises(KqlSchemaError):
        parse_cluster_map(bad)


# ---------------------------------------------------------------------------
# `database()` alone is unchanged
# ---------------------------------------------------------------------------


def test_a_bare_database_reference_still_means_local(con) -> None:
    """Passing a cluster map must not change what `database("X")` means."""
    rows = duckdb_kql.kql(
        con, "database('database1').table1 | count", clusters=MAP
    ).fetchall()
    assert rows == [(2,)]


def test_the_database_parameter_still_works_alongside(con) -> None:
    rows = duckdb_kql.kql(
        con, "table1 | count", database="database1", clusters=MAP
    ).fetchall()
    assert rows == [(2,)]


# ---------------------------------------------------------------------------
# The server, because the ADX UI sends cluster()
# ---------------------------------------------------------------------------


def _ask(server, csl):
    url = f"http://127.0.0.1:{server.server_address[1]}/v1/rest/query"
    body = json.dumps({"db": "default", "csl": csl}).encode()
    request = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, exc.read().decode()


@pytest.fixture
def serving(con):
    from duckdb_kql.server import KustoRestServer

    started: list = []

    def start(clusters):
        server = KustoRestServer(con, port=0, quiet=True, clusters=clusters)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        started.append((server, thread))
        return server

    yield start
    for server, thread in started:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_the_server_resolves_a_mapped_cluster(serving) -> None:
    server = serving(MAP)
    status, body = _ask(server, f"cluster('{CLUSTER}').database('mydb').table1 | count")
    assert status == 200, body
    assert body["Tables"][0]["Rows"] == [[2]]


def test_the_server_refuses_an_unmapped_cluster(serving) -> None:
    server = serving(None)
    status, body = _ask(server, f"cluster('{CLUSTER}').database('mydb').table1 | count")
    assert status == 400
    assert CLUSTER in str(body)
