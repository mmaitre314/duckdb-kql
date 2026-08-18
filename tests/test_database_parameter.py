"""``database=`` — targeting a database without touching the connection.

KQL names one database at a time; DuckDB can have several attached at once. The
obvious bridge is to switch the connection and switch it back, and it cannot be
made correct here — a relation from :func:`duckdb_kql.kql` is **lazy**, and
DuckDB resolves an unqualified table name when the relation is *fetched*. A
restore that runs before the caller fetches makes the query read the wrong
database, silently. Two threads sharing a connection make it worse.

So the database is baked into the SQL at translate time instead:
``T`` becomes ``"sales"."T"``. Nothing mutates, so there is nothing to restore
and nothing to race, and the answer cannot drift between translating and
fetching. Full reasoning and measurements: ``docs/session-state-proposal.md``.

The tests below are mostly about what is *not* qualified, because that is where
this goes wrong quietly: an explicit ``database("X").T`` must win, and a
``let``-bound name is a CTE rather than a table.
"""

from __future__ import annotations

import threading

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlSchemaError

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def con(tmp_path):
    """One in-memory database plus two attached files, each with its own `T`."""
    for name, value in (("sales", "from_sales"), ("hr", "from_hr")):
        path = tmp_path / f"{name}.db"
        seed = duckdb.connect(str(path))
        seed.execute("CREATE TABLE T(v VARCHAR)")
        seed.execute("INSERT INTO T VALUES (?)", [value])
        seed.close()
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    c.execute(f"ATTACH '{tmp_path / 'sales.db'}' AS sales")
    c.execute(f"ATTACH '{tmp_path / 'hr.db'}' AS hr")
    c.execute("CREATE TABLE T(v VARCHAR)")
    c.execute("INSERT INTO T VALUES ('from_memory')")
    return c


# ---------------------------------------------------------------------------
# It targets the database
# ---------------------------------------------------------------------------


def test_database_selects_the_table(con) -> None:
    assert duckdb_kql.kql(con, "T | project v", database="sales").fetchall() == [
        ("from_sales",)
    ]
    assert duckdb_kql.kql(con, "T | project v", database="hr").fetchall() == [
        ("from_hr",)
    ]


def test_omitting_it_keeps_the_connection_default(con) -> None:
    assert duckdb_kql.kql(con, "T | project v").fetchall() == [("from_memory",)]


def test_the_connection_is_not_modified(con) -> None:
    """The whole point: no session state changes, so nothing needs restoring."""
    before = con.execute("SELECT current_database()").fetchone()[0]
    duckdb_kql.kql(con, "T | project v", database="sales").fetchall()
    assert con.execute("SELECT current_database()").fetchone()[0] == before


def test_lazy_relations_do_not_drift(con) -> None:
    """The failure that killed the switch-and-restore design.

    Both relations are built first and fetched afterwards. Under `USE`, whichever
    database was current at *fetch* time would answer both.
    """
    a = duckdb_kql.kql(con, "T | project v", database="sales")
    b = duckdb_kql.kql(con, "T | project v", database="hr")
    c = duckdb_kql.kql(con, "T | project v")
    assert a.fetchall() == [("from_sales",)]
    assert b.fetchall() == [("from_hr",)]
    assert c.fetchall() == [("from_memory",)]


def test_concurrent_calls_never_cross_databases(con) -> None:
    """Under switch-and-restore this crossed over: ~1 answer in 144 came from
    the other database, silently. Qualification cannot cross, because the name
    is in the SQL text.

    Serialized the way `server.py` serializes, because a shared DuckDB
    connection is **not** safe for concurrent use in the first place — see
    `test_a_shared_connection_still_needs_external_serialization`. This isolates
    the property that actually changed.
    """
    lock = threading.Lock()
    seen: dict[str, set[str]] = {"sales": set(), "hr": set()}

    def run(target: str) -> None:
        for _ in range(40):
            with lock:
                rows = duckdb_kql.kql(con, "T | project v", database=target).fetchall()
            seen[target].add(rows[0][0])

    threads = [threading.Thread(target=run, args=(t,)) for t in ("sales", "hr")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert seen["sales"] == {"from_sales"}
    assert seen["hr"] == {"from_hr"}


def test_a_shared_connection_still_needs_external_serialization(con) -> None:
    """A pre-existing limitation, asserted so it is not mistaken for a new one.

    `_prepare` reads the schema with `con.execute(...).fetchall()`, and that
    pair is not atomic: a second thread's `execute` invalidates the first
    thread's pending result. Measured at 108 errors in 120 unsynchronized calls
    on a build predating `database=`, so qualification neither caused it nor
    fixes it — callers sharing a connection across threads must serialize, as
    `KustoRestServer` does with its own lock.

    Recorded as a test rather than a comment because the day it stops being
    true, this should fail and the documentation should change.
    """
    errors: list[str] = []

    def run() -> None:
        for _ in range(40):
            try:
                duckdb_kql.kql(con, "T | project v").fetchall()
            except Exception as exc:  # noqa: BLE001 - collecting is the point
                errors.append(type(exc).__name__)

    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Not asserting that it *does* fail — that would be a flaky test of a race.
    # Asserting the shape: any failure here is DuckDB's concurrency complaint,
    # never a wrong answer.
    assert all(name == "InvalidInputException" for name in errors), set(errors)


# ---------------------------------------------------------------------------
# What must NOT be qualified
# ---------------------------------------------------------------------------


def test_an_explicit_database_wins(con) -> None:
    """`database("hr").T` means hr even when the parameter says sales."""
    rows = duckdb_kql.kql(
        con, 'database("hr").T | project v', database="sales"
    ).fetchall()
    assert rows == [("from_hr",)]


def test_a_tabular_let_is_a_cte_not_a_table(con) -> None:
    """Qualifying a `let` name would emit SQL naming a table that does not exist."""
    rows = duckdb_kql.kql(
        con,
        "let Rows = range x from 1 to 3 step 1; Rows | count",
        database="sales",
    ).fetchall()
    assert rows == [(3,)]


def test_a_let_shadowing_a_real_table_still_wins(con) -> None:
    """`let T = ...` shadows the table, in KQL as in the generated SQL."""
    rows = duckdb_kql.kql(
        con,
        'let T = datatable(v:string)["shadowed"]; T | project v',
        database="sales",
    ).fetchall()
    assert rows == [("shadowed",)]


def test_a_let_defined_after_another_let_still_resolves(con) -> None:
    rows = duckdb_kql.kql(
        con,
        "let A = range x from 1 to 2 step 1; let B = A | count; B",
        database="sales",
    ).fetchall()
    assert rows == [(2,)]


# ---------------------------------------------------------------------------
# It reaches every table position, not just the source
# ---------------------------------------------------------------------------


def test_the_join_right_side_is_qualified_too(con) -> None:
    sql = str(
        duckdb_kql.to_sql(
            "T | join kind=inner (T) on v",
            schema={"sales.T": ["v"]},
            database="sales",
        )
    )
    assert sql.count('"sales"."T"') == 2


def test_the_lookup_right_side_is_qualified_too(con) -> None:
    sql = str(
        duckdb_kql.to_sql(
            "T | lookup (T) on v", schema={"sales.T": ["v"]}, database="sales"
        )
    )
    assert sql.count('"sales"."T"') == 2


def test_an_in_subquery_is_qualified(con) -> None:
    rows = duckdb_kql.kql(
        con, "T | where v in (T | project v) | count", database="hr"
    ).fetchall()
    assert rows == [(1,)]


def test_a_command_pipeline_still_translates(con) -> None:
    """`.show tables | count` goes down a different path in to_sql()."""
    rows = duckdb_kql.kql(con, ".show tables | count", database="sales").fetchall()
    assert rows and isinstance(rows[0][0], int)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_an_unattached_database_is_named_in_the_error(con) -> None:
    """Otherwise DuckDB blames the *table* for a mistake in the database name."""
    with pytest.raises(KqlSchemaError) as caught:
        duckdb_kql.kql(con, "T | count", database="nope")
    message = str(caught.value)
    assert "nope" in message
    assert "sales" in message and "hr" in message


def test_databases_lists_what_is_reachable(con) -> None:
    from duckdb_kql.engine import databases

    assert set(databases(con)) >= {"hr", "memory", "sales"}


# ---------------------------------------------------------------------------
# Layer 0 keeps working with no connection at all
# ---------------------------------------------------------------------------


def test_to_sql_takes_database_without_a_connection() -> None:
    """A `USE`-based design could never offer this."""
    sql = str(duckdb_kql.to_sql("T | count", database="sales"))
    assert '"sales"."T"' in sql


def test_df_and_arrow_pass_it_through(con) -> None:
    pytest.importorskip("pandas")
    assert duckdb_kql.df(con, "T | project v", database="hr")["v"].tolist() == [
        "from_hr"
    ]


def test_execute_passes_it_through(con) -> None:
    cur = duckdb_kql.execute(con, "T | project v", database="sales")
    assert cur.fetchall() == [("from_sales",)]
