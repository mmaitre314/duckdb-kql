"""`database("Sales").Orders` — Kusto's cross-database reference.

Not a scalar function: `database()` is the first half of a qualified name, and
DuckDB spells the same thing `"Sales"."Orders"` once the file is attached. That
correspondence is what makes this a rename rather than a feature, and it is what
lets one `duckdb-kql serve` present several DuckDB files as several Kusto
databases.

Measured on the Kusto Emulator with a second database created:

    .create database Sales volatile
    database("Sales").Orders | count                      -> works
    database("NetDefaultDB").Users | count                -> works (current db)
    Users | join (database("Sales").Orders) on Id         -> joins across
    database("Nope").T | count                            -> 400
    cluster("kustainer").database("Sales").Orders         -> service error
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql import ir
from duckdb_kql.errors import KqlUnsupportedError

#: Both halves of the join have to be resolvable, and the qualified key is the
#: same string `engine.schema` produces for an attached database.
SCHEMA = {
    "Sales.Orders": ["CustomerId", "Amount"],
    "Customers.Customers": ["CustomerId", "Name"],
    "Local": ["CustomerId"],
}


def test_a_qualified_source_becomes_a_qualified_table() -> None:
    sql = duckdb_kql.to_sql('database("Sales").Orders | count', schema=SCHEMA)
    assert '"Sales"."Orders"' in str(sql)


def test_two_parts_not_three() -> None:
    """`"db"."name"`, never `"db"."main"."name"`.

    DuckDB finds the table wherever the attached database's search path puts it.
    Pinning `main` in the middle would be more explicit and would stop resolving
    the moment someone attaches a file whose tables live in another schema.
    """
    sql = str(duckdb_kql.to_sql('database("Sales").Orders | count', schema=SCHEMA))
    assert '"main"' not in sql


def test_an_unqualified_name_still_means_the_current_database() -> None:
    assert '"Local"' in str(duckdb_kql.to_sql("Local | count", schema=SCHEMA))


def test_single_and_double_quotes_both_name_the_database() -> None:
    for query in ('database("Sales").Orders', "database('Sales').Orders"):
        assert '"Sales"."Orders"' in str(duckdb_kql.to_sql(f"{query} | count", schema=SCHEMA))


def test_a_join_may_cross_databases() -> None:
    sql = str(
        duckdb_kql.to_sql(
            'database("Sales").Orders'
            ' | join kind=inner (database("Customers").Customers) on CustomerId',
            schema=SCHEMA,
        )
    )
    assert '"Sales"."Orders"' in sql
    assert '"Customers"."Customers"' in sql


def test_the_ir_records_the_database_rather_than_flattening_the_name() -> None:
    """`TableRef("Sales.Orders")` would quote as one identifier and never
    resolve; the two parts have to stay apart until they are quoted."""
    from duckdb_kql.lower import lower  # noqa: PLC0415

    source = lower('database("Sales").Orders | count').source
    assert isinstance(source, ir.TableRef)
    assert (source.database, source.name) == ("Sales", "Orders")
    assert source.qualified == "Sales.Orders"


def test_an_unqualified_ref_has_no_database() -> None:
    from duckdb_kql.lower import lower  # noqa: PLC0415

    source = lower("Local | count").source
    assert isinstance(source, ir.TableRef)
    assert source.database is None
    assert source.qualified == "Local"


def test_cluster_is_refused_rather_than_ignored() -> None:
    """The failure this package exists to prevent, in one line.

    Treating `cluster("prod").database("Sales").Orders` as the local `Sales`
    would answer a question about production with local data — a wrong answer
    that looks exactly like a right one.
    """
    with pytest.raises(KqlUnsupportedError) as caught:
        duckdb_kql.to_sql('cluster("prod").database("Sales").Orders | count', schema=SCHEMA)
    assert "cluster" in str(caught.value)
    # And it says what to do instead.
    assert 'database("Name").Table' in str(caught.value)


def test_an_unknown_qualified_table_is_reported_with_its_database() -> None:
    """`Orders` alone in the message would send someone looking in the wrong
    database."""
    with pytest.raises(Exception) as caught:
        duckdb_kql.to_sql(
            'database("Nope").Orders | join (Local) on CustomerId', schema=SCHEMA
        )
    assert "Nope.Orders" in str(caught.value)


def test_a_deeper_path_is_not_mistaken_for_a_table() -> None:
    """`database("X").T.Column` is not a cross-database table reference, and
    guessing that it were would translate something the user did not write."""
    with pytest.raises(KqlUnsupportedError):
        duckdb_kql.to_sql('database("Sales").Orders.Extra | count', schema=SCHEMA)
