"""Trap tests — a connection shared by threads fails loudly, and a shared client does not fail.

Two silent wrong answers, both measured with four threads on one connection:

* **An empty schema stood in for a failed read.** `engine.schema` answered
  `{}` for *any* error, on the theory that a schema-less connection is not an
  error. Every connection has an `information_schema`, so what reached it was a
  busy, closed or interrupted connection — and an empty schema is not "no
  tables": it sends translation down its schema-free path, which moves a
  replaced column to the end. `T1 | extend a = a + 100` came back as columns
  `b, a`. The read now raises, and the caller sees DuckDB's own complaint —
  and it is one call: `execute` then `fetchall` is two, and another thread's
  statement in between left the read an empty result, with no error at all.
* **A `KustoClient` switched databases outside its lock.** It held the lock
  around running the query, but the `USE` that picks the database, and the
  schema read, came before it — so a second thread's request in between ran in
  the first one's database. Three to six of 600 requests answered from the
  wrong database, alongside hundreds of "connection is busy" errors.

The obvious fix for the second, taking the lock around the schema read as well,
made it *worse*: with fewer loud failures, 15 to 30 of 600 answered from the
wrong database. The lock now covers the whole request.
"""

from __future__ import annotations

import collections
import sys
import threading
from collections.abc import Iterator

import pytest

duckdb = pytest.importorskip("duckdb")

from duckdb_kql.engine import schema  # noqa: E402 - after the importorskip
from duckdb_kql.kusto import KustoClient  # noqa: E402


def test_a_schema_read_that_fails_is_an_error_not_an_empty_schema() -> None:
    con = duckdb.connect()
    con.execute("CREATE TABLE T(a BIGINT)")
    assert schema(con) == {"memory.T": ["a"], "T": ["a"]}
    con.close()
    with pytest.raises(duckdb.Error):
        schema(con)


def test_rows_left_unread_by_the_caller_do_not_block_a_query() -> None:
    """The trap in reading in one call. DuckDB 2.0 refuses a relation while an
    earlier `execute` has rows left unread, and `execute(...).fetchall()` never
    minded — a new `execute` drops them. Reading the schema as a relation alone
    broke every caller that had looked at one row of something first."""
    import duckdb_kql

    con = duckdb_kql.connect()
    con.execute("CREATE TABLE T(a BIGINT, b VARCHAR)")
    con.execute("INSERT INTO T VALUES (1, 'x'), (2, 'y')")
    con.execute("SELECT * FROM range(10)").fetchone()  # nine rows left unread
    assert schema(con) == {"memory.T": ["a", "b"], "T": ["a", "b"]}
    con.execute("SELECT * FROM range(10)")
    rows = duckdb_kql.kql(con, "T | extend a = a + 100 | order by a asc").fetchall()
    assert rows == [(101, "x"), (102, "y")]


@pytest.fixture
def frequent_switches() -> Iterator[None]:
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(interval)


def test_a_schema_read_on_a_busy_connection_is_refused_never_empty(
    frequent_switches: None,
) -> None:
    """Measured with `execute(...).fetchall()`: 21 of 600 reads answered `{}`."""
    con = duckdb.connect()
    con.execute("CREATE TABLE T1(a BIGINT, b VARCHAR)")
    want = schema(con)
    wrong: list[object] = []

    def read() -> None:
        for _ in range(150):
            try:
                got = schema(con)
            except duckdb.Error:
                continue  # refused as busy: loud, which is the point
            if got != want:
                wrong.append(got)

    def other_statements() -> None:
        for _ in range(150):
            try:
                con.execute("SET TimeZone='UTC'")
            except duckdb.Error:
                pass

    threads = [threading.Thread(target=read) for _ in range(2)]
    threads += [threading.Thread(target=other_statements) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not wrong, f"{len(wrong)} schema reads answered wrong, e.g. {wrong[0]!r}"


def test_one_client_shared_by_threads_answers_from_each_database_asked_for(
    frequent_switches: None,
) -> None:
    con = duckdb.connect()
    for database, value in (("dba", "from_a"), ("dbb", "from_b")):
        con.execute(f"ATTACH ':memory:' AS {database}")
        con.execute(f"CREATE TABLE {database}.T AS SELECT '{value}' AS v")
    client = KustoClient(con)
    want = {"dba": "from_a", "dbb": "from_b"}
    answers: collections.Counter[tuple[str, str]] = collections.Counter()

    def work(database: str) -> None:
        for _ in range(100):
            try:
                rows = client.execute(database, "T | project v").primary_results[0]
                answers[(database, rows[0]["v"])] += 1
            except Exception as exc:  # noqa: BLE001 - counted, and the assertion names it
                answers[(database, f"{type(exc).__name__}: {exc}")] += 1

    threads = [threading.Thread(target=work, args=(d,)) for d in ("dba", "dbb") * 2]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert answers == {("dba", want["dba"]): 200, ("dbb", want["dbb"]): 200}
