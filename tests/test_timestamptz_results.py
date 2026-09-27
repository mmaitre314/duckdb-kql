"""L5 trap tests — a caller's `TIMESTAMP WITH TIME ZONE` column must come back.

A reported failure: with `duckdb-kql[duckdb]` installed and nothing else,
fetching a TIMESTAMPTZ result raised ``Invalid Input Error: Required module
'pytz' failed to import``.

**The cause is not in this package.** Reproduced on a bare `duckdb.connect()`,
with no `SET TimeZone`, no translation and nothing of ours in the process:
DuckDB's Python package declares *no* dependencies at all, yet converting a
TIMESTAMPTZ value to a Python datetime imports pytz. Naive TIMESTAMP is
unaffected, and so is every zone, so it is neither our `SET TimeZone='UTC'` nor
UTC in particular.

It reached users anyway, because the `[duckdb]` extra promises "now you can run
queries and read the answers" and that was untrue for any table holding such a
column — so pytz is listed there, with the reasoning and the conditions for
removing it again in `pyproject.toml`.

**Why nothing caught it.** duckdb-kql never *emits* TIMESTAMPTZ: R8 lands every
datetime it builds — literals, `now()`, `ago()`, `todatetime()`, `datatable`
columns — on a naive TIMESTAMP via `AT TIME ZONE 'UTC'`. The type only ever
arrives from the caller's own schema, which no corpus case and no test had. And
it stayed invisible for as long as it did because pandas <3 required pytz and
dragged it in; pandas 3 dropped it.

The first test below is the one that would have failed. The second is the half
that must keep holding: what we generate ourselves stays naive, because a
translator that started emitting TIMESTAMPTZ would put every user back in front
of this.
"""

from __future__ import annotations

import datetime as dt

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")

MOMENT = dt.datetime(2020, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc)


@pytest.fixture
def con():
    with duckdb_kql.connect() as connection:
        connection.execute(
            "CREATE TABLE Events (Occurred TIMESTAMPTZ, Naive TIMESTAMP, N BIGINT)"
        )
        connection.execute(
            "INSERT INTO Events VALUES "
            "('2020-01-02T03:04:05Z', '2020-01-02T03:04:05', 7)"
        )
        yield connection


def test_a_timestamptz_column_survives_every_result_path(con) -> None:
    """Each of these fetches for itself, so each could fail on its own."""
    assert duckdb_kql.kql(con, "Events | project Occurred").fetchall() == [(MOMENT,)]

    frame = duckdb_kql.df(con, "Events | project Occurred")
    assert frame["Occurred"].tolist() == [MOMENT]

    from duckdb_kql.kusto import KustoClient

    with KustoClient(con) as client:
        rows = client.execute(None, "Events | project Occurred").primary_results[0]
        assert rows[0]["Occurred"] == MOMENT


def test_the_instant_is_preserved_not_just_the_wall_clock(con) -> None:
    """A fetch that dropped the zone would still return *a* datetime.

    Asserting only "it did not raise" would pass for a conversion that read the
    value in the session's zone. The session is UTC here, so the wall clock and
    the instant agree — which is why this also checks a query whose answer
    depends on the offset being right.
    """
    rows = duckdb_kql.kql(
        con, "Events | extend Later = Occurred + 2h | project Occurred, Later"
    ).fetchall()

    occurred, later = rows[0]
    assert occurred == MOMENT
    assert later - occurred == dt.timedelta(hours=2)
    assert occurred.utcoffset() == dt.timedelta(0)


def test_aggregates_and_comparisons_over_the_column(con) -> None:
    """`max()` and a `where` both return the column's type, so both fetch it."""
    assert duckdb_kql.kql(con, "Events | summarize max(Occurred)").fetchall() == [
        (MOMENT,)
    ]
    kept = duckdb_kql.kql(
        con, "Events | where Occurred > datetime(2020-01-01) | project N"
    ).fetchall()
    assert kept == [(7,)]


@pytest.mark.parametrize(
    "kql",
    [
        "print t = datetime(2020-01-02 03:04:05)",
        "print t = now()",
        "print t = ago(1d)",
        "print t = todatetime('2020-01-02')",
        "datatable(t:datetime)[datetime(2020-01-02)] | project t",
    ],
)
def test_what_we_generate_ourselves_stays_naive(con, kql: str) -> None:
    """R8's `AT TIME ZONE 'UTC'` is what keeps this off everyone else's path.

    If a mapping ever starts handing back TIMESTAMPTZ, every caller inherits
    DuckDB's pytz requirement for queries that touch no such column of their
    own — so this is a property worth pinning, not an implementation detail.
    """
    types = [str(t) for t in duckdb_kql.kql(con, kql).types]

    assert types == ["TIMESTAMP"], f"{kql} now yields {types}"
