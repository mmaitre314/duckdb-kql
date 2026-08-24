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
`cluster('https://mycluster')` and a trailing slash are one host. A **short name
and its `.kusto.windows.net` domain are one cluster**, which one entry covers —
see `test_a_short_name_and_its_domain_are_one_cluster` for what that costs and
why the completion happens at lookup rather than in the normalizer.
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


def test_a_short_name_and_its_domain_are_one_cluster(con) -> None:
    """Trap. This refused, and the refusal was the bug.

    **The wrong answer.** A map keyed on the full domain and a query written
    `cluster('mycluster.eastus')` — the spelling the ADX UI and a hand-written
    query both use — raised `KqlSchemaError`, listing the entry that was, to the
    reader, plainly the same cluster.

    **What was measured.** The emulator resolves `cluster('help')` to
    `https://help/` and fails with "Name or service not known" — and that is
    exactly why it is not the oracle here. A single-node emulator has no cluster
    federation and no DNS suffix to complete a name with, so what that error
    shows is its URI builder, not a resolution rule. Microsoft's `cluster()`
    reference is unambiguous that the argument may be "the name of the cluster
    without the .kusto.windows.net suffix", and gives `cluster('help')` and
    `cluster('help.kusto.windows.net')` as the same cluster.

    **Why the obvious fix was wrong.** Completing the name inside
    `normalize_cluster` — one canonical key, no lookup change — puts a guessed
    domain into every host the module merely *echoes*: an entity group written
    `cluster('prod').database('X')` would be reported by `.show entity_groups`
    as `cluster('prod.kusto.windows.net')`, a domain nobody wrote. And the guess
    is only right for the public cloud: `.kusto.chinacloudapi.cn` and a custom
    domain complete differently, so canonicalizing rewrites hosts it has no
    business rewriting.

    Completing at *lookup* keeps the blast radius at one question. A wrong guess
    can only fail to find an entry — a refusal, never a query answered from the
    wrong database — which is the trade the charter asks for.
    """
    # The normalizer still only normalizes spelling: it does not invent a domain.
    assert normalize_cluster("mycluster.eastus") == "mycluster.eastus"

    rows = duckdb_kql.kql(
        con, "cluster('mycluster.eastus').database('mydb').table1 | count", clusters=MAP
    ).fetchall()
    assert rows == [(2,)]


def test_the_map_may_be_written_short_and_the_query_long(con) -> None:
    """The other direction: completion is about the pair, not about the map."""
    rows = duckdb_kql.kql(
        con,
        f"cluster('{CLUSTER}').database('mydb').table1 | count",
        clusters={("mycluster.eastus", "mydb"): "database1"},
    ).fetchall()
    assert rows == [(2,)]


def test_the_repro_that_prompted_this(con) -> None:
    """The reported failure, in shape: two clusters, both written short."""
    rows = duckdb_kql.kql(
        con,
        "cluster('mycluster.eastus').database('mydb').table1"
        "| join kind=leftouter (cluster('othercluster.westus').database('otherdb').table2)"
        " on AlertId"
        "| count",
        clusters={(CLUSTER, "mydb"): "database1", (OTHER, "otherdb"): "database2"},
    ).fetchall()
    assert rows == [(2,)]


def test_an_address_is_not_completed() -> None:
    """A host that is already an address gets no second key.

    Not a correctness rule — an extra candidate that matches nothing is
    harmless — but `localhost.kusto.windows.net` in a suggested map entry reads
    like a bug, and the suggestion is the whole point of the error message.
    """
    from duckdb_kql.clusters import cluster_fqdn

    assert cluster_fqdn("mycluster.eastus") == CLUSTER
    assert cluster_fqdn("help") == "help.kusto.windows.net"
    assert cluster_fqdn(CLUSTER) == CLUSTER
    for already_an_address in (
        "localhost",
        "kusto.localhost",
        "127.0.0.1",
        "localhost:8080",
        "mycluster.eastus.kusto.chinacloudapi.cn",
        "abc.z5.kusto.fabric.microsoft.com",
    ):
        assert cluster_fqdn(already_an_address) == already_an_address


def test_two_spellings_of_one_cluster_may_not_disagree() -> None:
    """The one way completion could answer from the wrong database — refused.

    Both keys name the cluster a query calls `mycluster.eastus`, so which local
    database it read would depend on how it happened to spell the host.
    """
    with pytest.raises(KqlSchemaError, match="two spellings"):
        parse_cluster_map(
            {("mycluster.eastus", "mydb"): "database1", (CLUSTER, "mydb"): "database2"}
        )


def test_the_conflict_check_sees_every_pair_a_lookup_could_confuse() -> None:
    """The invariant the refusal rests on, checked rather than assumed.

    `_refuse_aliased_conflicts` groups keys by `cluster_fqdn`; a lookup matches
    by `_spellings`. If those two ever disagreed, a conflicting pair could slip
    past the check and be resolved two ways — which is the exact failure the
    check exists to prevent. They are inverses, so they cannot.
    """
    from duckdb_kql.clusters import _spellings, cluster_fqdn

    for host in (
        "mycluster.eastus",
        CLUSTER,
        "help",
        "help.kusto.windows.net",
        "localhost",
        "localhost.kusto.windows.net",
        "127.0.0.1",
        "mycluster.eastus.kusto.chinacloudapi.cn",
        "",
    ):
        for spelling in _spellings(host):
            assert cluster_fqdn(spelling) == cluster_fqdn(host), host


def test_two_spellings_agreeing_are_allowed() -> None:
    """Redundant, not contradictory — there is an answer, so it is not refused."""
    assert parse_cluster_map(
        {("mycluster.eastus", "mydb"): "database1", (CLUSTER, "mydb"): "database1"}
    ) == {("mycluster.eastus", "mydb"): "database1", (CLUSTER, "mydb"): "database1"}


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


def test_an_unmapped_cluster_names_the_entry_to_add(con) -> None:
    """The error is a copy/paste, not a puzzle.

    "not in the map" leaves the reader to work out *which* of the two halves
    they got wrong, and to guess the tuple syntax. So the entry that is missing
    is spelled out as Python, keyed on the fully qualified host — that is the
    key that names the cluster on its own, and the completion at lookup means it
    matches the short spelling the query used.
    """
    with pytest.raises(KqlSchemaError) as caught:
        duckdb_kql.kql(
            con, f"cluster('{OTHER}').database('mydb').table1", clusters=MAP
        )
    message = str(caught.value)
    assert OTHER in message
    assert f"add ('{OTHER}', 'mydb')" in message
    # and it still lists what *is* mapped, in the same pasteable syntax
    assert f"('{CLUSTER}', 'mydb'): 'database1'" in message


def test_the_suggested_entry_is_the_one_that_works(con) -> None:
    """Whatever the error suggests has to resolve when pasted — including for a
    query that wrote the short name, which is where the suggested key and the
    written host differ."""
    with pytest.raises(KqlSchemaError) as caught:
        duckdb_kql.kql(
            con, "cluster('othercluster.westus').database('mydb').table1", clusters=MAP
        )
    assert f"add ('{OTHER}', 'mydb')" in str(caught.value)
    rows = duckdb_kql.kql(
        con,
        "cluster('othercluster.westus').database('mydb').table1 | count",
        clusters={(OTHER, "mydb"): "database1"},
    ).fetchall()
    assert rows == [(2,)]


def test_with_no_map_at_all_the_entry_is_named_too(con) -> None:
    """The first error a caller sees is the one that should teach the shape."""
    with pytest.raises(KqlSchemaError) as caught:
        duckdb_kql.kql(con, "cluster('mycluster.eastus').database('mydb').table1")
    assert f"clusters={{('{CLUSTER}', 'mydb')" in str(caught.value)


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


# ---------------------------------------------------------------------------
# The process-wide default
# ---------------------------------------------------------------------------


@pytest.fixture
def no_global_leak():
    """Save and restore the default, so these tests cannot infect the suite.

    The hazard a process-wide setting introduces, handled where it is created.
    """
    from duckdb_kql.clusters import get_clusters, set_clusters

    before = get_clusters()
    yield
    set_clusters(before)


def test_the_default_applies_when_a_call_passes_nothing(con, no_global_leak) -> None:
    duckdb_kql.set_clusters(MAP)
    rows = duckdb_kql.kql(
        con, f"cluster('{CLUSTER}').database('mydb').table1 | count"
    ).fetchall()
    assert rows == [(2,)]


def test_a_call_argument_replaces_the_default(con, no_global_leak) -> None:
    """Replaces rather than merges: one query's resolution comes from one place."""
    duckdb_kql.set_clusters({(CLUSTER, "mydb"): "database1"})
    rows = duckdb_kql.kql(
        con,
        f"cluster('{CLUSTER}').database('otherdb').table2 | count",
        clusters={(CLUSTER, "otherdb"): "database2"},
    ).fetchall()
    assert rows == [(2,)]

    # And the default's entry is NOT visible to that call, because it replaced it.
    with pytest.raises(KqlSchemaError):
        duckdb_kql.kql(
            con,
            f"cluster('{CLUSTER}').database('mydb').table1",
            clusters={(CLUSTER, "otherdb"): "database2"},
        )


def test_an_empty_map_opts_out_of_the_default(con, no_global_leak) -> None:
    """`{}` is how a call says "no mapping", distinct from omitting the argument."""
    duckdb_kql.set_clusters(MAP)
    with pytest.raises(KqlSchemaError):
        duckdb_kql.kql(
            con, f"cluster('{CLUSTER}').database('mydb').table1", clusters={}
        )


def test_setting_none_clears_it(con, no_global_leak) -> None:
    duckdb_kql.set_clusters(MAP)
    duckdb_kql.set_clusters(None)
    assert duckdb_kql.get_clusters() is None
    with pytest.raises(KqlSchemaError):
        duckdb_kql.kql(con, f"cluster('{CLUSTER}').database('mydb').table1")


def test_get_clusters_returns_the_normalized_form(no_global_leak) -> None:
    """What actually matches, not what was typed — the host is normalized."""
    duckdb_kql.set_clusters({f"HTTPS://{CLUSTER.upper()}/": {"mydb": "database1"}})
    assert duckdb_kql.get_clusters() == {(CLUSTER, "mydb"): "database1"}


def test_get_clusters_returns_a_copy(no_global_leak) -> None:
    """Mutating the result must not change resolution behind the setter's back."""
    duckdb_kql.set_clusters(MAP)
    got = duckdb_kql.get_clusters()
    got.clear()
    assert duckdb_kql.get_clusters() == parse_cluster_map(MAP)


def test_a_malformed_default_fails_at_configuration_time(no_global_leak) -> None:
    """Not at whichever query runs first — the fixture is where the mistake is."""
    with pytest.raises(KqlSchemaError):
        duckdb_kql.set_clusters({"host": "should-be-a-dict"})


def test_the_default_reaches_layer_0_and_the_server(con, no_global_leak) -> None:
    duckdb_kql.set_clusters(MAP)
    sql = str(duckdb_kql.to_sql(f"cluster('{CLUSTER}').database('mydb').table1"))
    assert '"database1"."table1"' in sql

    from duckdb_kql.server import KustoRestServer

    server = KustoRestServer(con, port=0, quiet=True)  # no per-server map
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _ask(
            server, f"cluster('{CLUSTER}').database('mydb').table1 | count"
        )
        assert status == 200, body
        assert body["Tables"][0]["Rows"] == [[2]]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
