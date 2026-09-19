"""L5 trap test — ``df()`` must not build a relation on its way to a DataFrame.

A performance report measured a query whose translated SQL reached 1.4 MB.
``duckdb_kql.df()`` took roughly twice as long as translating the same KQL and
handing the SQL to ``con.execute(...).df()``, for byte-identical data. The cause
is not in the translator at all: ``df()`` was ``kql(...).df()``, and ``kql()``
returns a **relation**. Building a relation binds the statement, because a
relation has to be able to report its schema; fetching it binds again. The cost
of a bind is proportional to the size of the statement, so on a statement small
enough to read the second bind is free and on a large one it is half the wall
clock.

Timing cannot be the test — it is the symptom, and asserting on it buys a flaky
build. The defect is structural: the translated SQL reached ``con.sql``. So the
test spies on the connection and pins *which* entry point the query text goes
to. ``kql()`` must keep using ``con.sql``; that is its contract — it returns a
relation to keep composing. ``df()`` must not.

The obvious "tidy-up" — routing ``df`` back through ``kql`` so the two share a
line — restores the fault, and it shows up only as slowness, on inputs nobody
puts in a unit test.

The write case is here because the rewrite could plausibly have dropped it:
``.set-or-replace`` translates to a ``CREATE TABLE ... AS`` wrapped so that it
still answers Kusto's extents frame, and both entry points run it. So does the
bound-parameter branch, which is the other thing a one-line rewrite loses.
"""

from __future__ import annotations

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")


class SpyConnection:
    """Forwards everything to a real connection, recording the SQL it is asked.

    Deliberately not a mock: the query still has to run and answer correctly,
    or the test would pass just as well against a `df()` that returns nothing.
    """

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self._con = con
        self.sql_calls: list[str] = []
        self.execute_calls: list[str] = []

    def sql(self, query: str, **kwargs: object) -> object:
        self.sql_calls.append(query)
        return self._con.sql(query, **kwargs)  # type: ignore[arg-type]

    def execute(self, query: str, *args: object, **kwargs: object) -> object:
        self.execute_calls.append(query)
        return self._con.execute(query, *args, **kwargs)  # type: ignore[arg-type]

    def __getattr__(self, name: str) -> object:
        return getattr(self._con, name)


@pytest.fixture
def con():
    with duckdb_kql.connect() as connection:
        connection.execute("CREATE TABLE T(a BIGINT)")
        connection.execute("INSERT INTO T VALUES (1), (2)")
        yield connection


def _query_calls(calls: list[str]) -> list[str]:
    """The recorded SQL that is the translated query, not connection setup.

    ``_prepare`` sets the time zone and reads the schema through the same
    connection, so an unfiltered count would be pinned to how many statements
    that happens to take today.
    """
    return [c for c in calls if '"T"' in c]


def test_df_does_not_go_through_a_relation(con) -> None:
    spy = SpyConnection(con)
    frame = duckdb_kql.df(spy, "T | project b = a * 2 | sort by b asc")

    assert frame["b"].tolist() == [2, 4]
    assert _query_calls(spy.sql_calls) == [], (
        "df() built a relation: the query binds once for the relation's schema "
        "and again to fetch it, and the second bind costs the same as the first"
    )
    assert len(_query_calls(spy.execute_calls)) == 1


def test_kql_still_returns_a_relation(con) -> None:
    """The other half. `kql()` is the composable one and must stay lazy."""
    spy = SpyConnection(con)
    relation = duckdb_kql.kql(spy, "T | project b = a * 2")

    assert isinstance(relation, duckdb.DuckDBPyRelation)
    assert len(_query_calls(spy.sql_calls)) == 1
    assert relation.order("b").fetchall() == [(2,), (4,)]


def test_df_of_a_write_still_writes(con) -> None:
    """A command goes through `execute` as happily as a query does."""
    frame = duckdb_kql.df(con, ".set-or-replace U <| T | project a")

    assert "RowCount" in frame.columns
    assert con.execute('SELECT count(*) FROM "U"').fetchone() == (2,)


def test_parameters_still_reach_duckdb_bound(con) -> None:
    """The bound-parameter branch is the one the rewrite could have dropped."""
    frame = duckdb_kql.df(
        con,
        "declare query_parameters(floor:long); T | where a >= floor",
        {"floor": 2},
    )

    assert frame["a"].tolist() == [2]
