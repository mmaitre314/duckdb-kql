"""L5 trap tests — per-client config must reach the translator, not the globals.

A reported bug: `KustoClient(con, entity_groups=…)` stored the mapping and then
translated without it, so a `macro-expand` over a named group failed —
*while the same query, on the same connection, through
`duckdb_kql.query(con, q, entity_groups=…)` answered correctly.* The client's
translation call simply did not forward the argument, so it fell back to
whatever `set_entity_groups` had been given process-wide, which for a per-client
configuration is nothing.

**The failure mode is what makes this worth pinning.** Nothing was stored
wrongly and nothing was ignored loudly; the attribute was right and the
translation was configured from somewhere else. The only signal was one API
working and another not.

Three further findings came out of checking how far it went:

* the same call site also fed the **write** path, so an ingestion command could
  not name a group either;
* `to_sql` itself dropped `entity_groups` on its **ingestion branch** — every
  other branch threads it — so `.set-or-replace T <| macro-expand G …` failed
  through `duckdb_kql.query(entity_groups=…)` too. That one is Layer 0 and has
  nothing to do with the client;
* `KustoClient` had no `clusters` parameter at all, though `serve` and
  `duckdb_kql.query` both take one, so a per-client cluster map was impossible
  in the same way.

`tools/sql_snapshot.py` is **byte-identical** across the corpus for all of it:
no corpus case combines ingestion with an entity group, which is exactly why the
Layer 0 half survived this long. These tests are the whole guard.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.kusto import KustoClient, KustoServiceError

duckdb = pytest.importorskip("duckdb")

QUERY = "macro-expand Sources as S (S.Events) | summarize Total = sum(Value)"
CROSS = "cluster('c1.eastus').database('First').Events | summarize Total = sum(Value)"
INGEST = ".set-or-replace Rollup <| macro-expand Sources as S (S.Events) | project Value"

BOTH = {"Sources": ["database('First')", "database('Second')"]}
ONLY_FIRST = {"Sources": ["database('First')"]}
ONLY_SECOND = {"Sources": ["database('Second')"]}
CLUSTER_MAP = {("c1.eastus.kusto.windows.net", "First"): "First"}


@pytest.fixture
def con():
    """Two attached catalogs holding one row each, so the sum names the source."""
    with duckdb_kql.connect() as connection:
        for name, value in (("First", 1), ("Second", 2)):
            connection.execute(f"ATTACH ':memory:' AS {name}")
            connection.execute(f"CREATE TABLE {name}.Events (Value BIGINT)")
            connection.execute(f"INSERT INTO {name}.Events VALUES ({value})")
        yield connection


@pytest.fixture(autouse=True)
def no_globals():
    """Every test here runs with the process-global mappings cleared.

    Load-bearing: with a global mapping set, a client that forwards nothing
    still answers, and every assertion below passes for the wrong reason.
    """
    groups, clusters = duckdb_kql.get_entity_groups(), duckdb_kql.get_clusters()
    duckdb_kql.set_entity_groups(None)
    duckdb_kql.set_clusters(None)
    try:
        yield
    finally:
        duckdb_kql.set_entity_groups(groups)
        duckdb_kql.set_clusters(clusters)


def totals(client, query=QUERY, database=None):
    rows = client.execute(database, query).primary_results[0]
    return [row.to_dict()["Total"] for row in rows]


def test_the_reported_bug_both_apis_agree(con) -> None:
    """The repro: engine and client, same connection, no global mapping."""
    assert duckdb_kql.query(con, QUERY, entity_groups=BOTH).fetchall() == [(3,)]

    with KustoClient(con, entity_groups=BOTH) as client:
        assert totals(client) == [3]


def test_execute_query_is_covered_too(con) -> None:
    """`execute` dispatches on the leading dot; `execute_query` is the other door."""
    with KustoClient(con, entity_groups=BOTH) as client:
        rows = client.execute_query(None, QUERY).primary_results[0]
        assert [row.to_dict()["Total"] for row in rows] == [3]


def test_two_clients_do_not_share_a_mapping(con) -> None:
    """The point of a per-client mapping, and what a global one cannot do."""
    with KustoClient(con, entity_groups=ONLY_FIRST) as first, \
         KustoClient(con, entity_groups=ONLY_SECOND) as second:
        assert totals(first) == [1]
        assert totals(second) == [2]
        # again, because a cached translation keyed on the query text rather
        # than the client would pass the two assertions above and fail this one
        assert totals(first) == [1]


def test_an_explicit_client_mapping_replaces_the_global_one(con) -> None:
    """The documented policy for `entity_groups=` — replace, never merge.

    `{}` is therefore how a client says "no groups at all", distinct from
    omitting the argument, and that distinction has to survive being parsed at
    construction and forwarded.
    """
    duckdb_kql.set_entity_groups(ONLY_SECOND)

    with KustoClient(con, entity_groups=ONLY_FIRST) as client:
        assert totals(client) == [1], "the global mapping won"

    with KustoClient(con) as client:
        assert totals(client) == [2], "no client mapping, so the global applies"

    with KustoClient(con, entity_groups={}) as client:
        with pytest.raises(KustoServiceError, match="unknown entity group"):
            totals(client)


def test_an_unmapped_group_is_still_refused_by_name(con) -> None:
    """Forwarding must not become 'resolve it to a local table and hope'."""
    with KustoClient(con, entity_groups={"Other": ["database('First')"]}) as client:
        with pytest.raises(KustoServiceError) as caught:
            totals(client)
    message = str(caught.value)
    assert "Sources" in message and "unknown entity group" in message


def test_an_ingestion_source_can_name_a_group(con) -> None:
    """The Layer 0 half: every `to_sql` branch but this one threaded the groups.

    An ingestion command's source is a whole KQL query, so it reaches `lower`
    like any other — and this branch called `lower(ingestion.source)` bare.
    """
    assert duckdb_kql.query(con, INGEST, entity_groups=BOTH).fetchall()
    assert con.execute("SELECT sum(Value) FROM Rollup").fetchone() == (3,)

    con.execute("DROP TABLE Rollup")
    with KustoClient(con, entity_groups=BOTH) as client:
        client.execute("main", INGEST)
    assert con.execute("SELECT sum(Value) FROM Rollup").fetchone() == (3,)


def test_a_client_cluster_map_is_honoured(con) -> None:
    """`clusters=` was missing from the client entirely, not merely dropped."""
    with KustoClient(con, clusters=CLUSTER_MAP) as client:
        assert totals(client, CROSS) == [1]

    with KustoClient(con) as client:
        with pytest.raises(KustoServiceError, match="(?i)no cluster"):
            totals(client, CROSS)


def test_parsing_a_mapping_twice_is_the_identity() -> None:
    """The enabling change, and the reason the bug was easy to write.

    The client keeps its mapping **parsed**, so a malformed entry fails at
    construction where it was written. Forwarding a parsed map then required
    `parse_entity_groups` to accept one — it did not, and raised ``entity must
    be a str … got Entity``. `parse_cluster_map` was already idempotent, which
    is why only the group half needed changing.
    """
    from duckdb_kql.clusters import parse_cluster_map
    from duckdb_kql.entity_groups import parse_entity_groups

    once = parse_entity_groups(BOTH)
    assert parse_entity_groups(once) == once
    assert parse_cluster_map(parse_cluster_map(CLUSTER_MAP)) == parse_cluster_map(CLUSTER_MAP)

    # and the None/{} distinction survives, since `{}` is a meaningful answer
    assert parse_entity_groups(None) is None
    assert parse_entity_groups({}) == {}
