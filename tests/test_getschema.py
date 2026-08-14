"""`getschema` — the operator that turns a query's shape into rows.

Useful in its own right, and useful as a test instrument: it is the only way to
assert a *type* without inspecting the engine's own metadata, so it is what
`tests/test_control_commands.py` uses to check the control commands report the
columns Kusto reports.

The expected values are measured on the Kusto Emulator, and two of them are not
the obvious guess — `bool` is `System.SByte` (not `System.Boolean`) and
`decimal` is `System.Data.SqlTypes.SqlDecimal`:

    datatable(b:bool, i:int, l:long, r:real, d:decimal, s:string,
              dt:datetime, ts:timespan, g:guid, dy:dynamic)
    [...] | getschema

Reproduce with:

    docker compose up -d kusto
    python -c "
    from duckdb_kql.oracle import KustoEmulator
    print(KustoEmulator().query('print x=1 | getschema').to_dict())"
"""

from __future__ import annotations

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")

#: KQL type -> the .NET name Kusto's `getschema` reports. From the emulator.
KUSTO_DATATYPES = [
    ("b", "BOOLEAN", "System.SByte", "bool"),
    ("i", "INTEGER", "System.Int32", "int"),
    ("l", "BIGINT", "System.Int64", "long"),
    ("r", "DOUBLE", "System.Double", "real"),
    ("d", "DECIMAL(38,9)", "System.Data.SqlTypes.SqlDecimal", "decimal"),
    ("s", "VARCHAR", "System.String", "string"),
    ("dt", "TIMESTAMP", "System.DateTime", "datetime"),
    ("ts", "INTERVAL", "System.TimeSpan", "timespan"),
    ("g", "UUID", "System.Guid", "guid"),
    ("dy", "JSON", "System.Object", "dynamic"),
]


@pytest.fixture(scope="module")
def con():
    c = duckdb_kql.connect()
    columns = ", ".join(f"{name} {sql}" for name, sql, _, _ in KUSTO_DATATYPES)
    c.execute(f"CREATE TABLE Every({columns})")
    c.execute("CREATE TABLE T(a INTEGER, b VARCHAR); INSERT INTO T VALUES (1, 'x')")
    return c


def test_the_output_columns_are_kustos() -> None:
    """Four columns, in this order. A caller reads them by name."""
    assert list(duckdb_kql.kql(duckdb_kql.connect(), "print x = 1 | getschema").columns) == [
        "ColumnName",
        "ColumnOrdinal",
        "DataType",
        "ColumnType",
    ]


def test_every_type_maps_the_way_kusto_maps_it(con) -> None:
    rows = duckdb_kql.kql(con, "Every | getschema").fetchall()
    expected = [
        (name, i, net, kql)
        for i, (name, _, net, kql) in enumerate(KUSTO_DATATYPES)
    ]
    assert [tuple(r) for r in rows] == expected


def test_the_ordinal_is_zero_based(con) -> None:
    """Kusto counts from 0. Counting from 1 would look right and be off by one."""
    ordinals = [r[1] for r in duckdb_kql.kql(con, "Every | getschema").fetchall()]
    assert ordinals == list(range(len(KUSTO_DATATYPES)))


def test_it_describes_the_pipeline_not_the_table(con) -> None:
    """`getschema` reports what reaches it, so it follows the operators before it."""
    rows = duckdb_kql.kql(con, "T | project b | getschema").fetchall()
    assert [(r[0], r[3]) for r in rows] == [("b", "string")]

    rows = duckdb_kql.kql(con, "T | extend c = 1.5 | getschema").fetchall()
    assert [(r[0], r[3]) for r in rows] == [
        ("a", "int"),
        ("b", "string"),
        ("c", "real"),
    ]


def test_it_reports_no_rows_of_data(con) -> None:
    """One row per column, whatever the input holds — including nothing.

    The shape does not depend on the values, which is what makes it usable as a
    test instrument on an empty result.
    """
    rows = duckdb_kql.kql(con, "T | where a > 1000 | getschema").fetchall()
    assert [r[0] for r in rows] == ["a", "b"]


def test_an_unknown_duckdb_type_reports_as_string(con) -> None:
    """The fallback is the one answer that cannot misrepresent a value.

    A DuckDB type with no Kusto counterpart still has a faithful string form;
    calling it `real` or `long` would make it look like something it is not.
    """
    con.execute("CREATE OR REPLACE TABLE Odd AS SELECT [1, 2] AS arr, {'a': 1} AS st")
    rows = duckdb_kql.kql(con, "Odd | getschema").fetchall()
    # Composites are documents, so Kusto's word for them is `dynamic`.
    assert [(r[0], r[3]) for r in rows] == [("arr", "dynamic"), ("st", "dynamic")]


def test_the_sql_expression_and_the_python_function_agree() -> None:
    """`getschema` and the Kusto client must not disagree about a type.

    They share one table (`duckdb_kql.types`) precisely so they cannot, and this
    is what would catch the SQL generator drifting from the Python one — a
    `getschema` contradicting the column types printed beside it is a peculiarly
    confusing thing to ship.
    """
    from duckdb_kql.types import DUCKDB_TO_KQL, kusto_type  # noqa: PLC0415

    con = duckdb_kql.connect()
    for duckdb_type, expected in DUCKDB_TO_KQL.items():
        if duckdb_type.startswith("TIMESTAMP_") or "WITH TIME ZONE" in duckdb_type:
            continue  # not spellable in a bare CREATE TABLE on every version
        con.execute(f"CREATE OR REPLACE TABLE One(c {duckdb_type})")
        (row,) = duckdb_kql.kql(con, "One | getschema").fetchall()
        assert row[3] == expected == kusto_type(duckdb_type), (
            f"{duckdb_type}: getschema says {row[3]}, kusto_type says "
            f"{kusto_type(duckdb_type)}, table says {expected}"
        )
