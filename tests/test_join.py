"""L5 trap tests — ``join`` (``docs/test-plan.md`` §6, TRANSLATION.md R5).

The headline trap: **the default kind is `innerunique`, not `inner`.** It
de-duplicates the *left* key set before joining, so the SQL that looks
equivalent silently returns more rows. Measured on the emulator with a left side
holding two `'a'` rows and a right side holding two `'a'` rows: innerunique
yields 2 rows, inner yields 4.

The second trap is the output schema. KQL keeps *both* key columns and suffixes
the right side's colliding names — ``k`` becomes ``k1``, and ``k1`` becomes
``k2`` when ``k1`` is taken. No separator, so ``k_1`` would be wrong.
"""

from __future__ import annotations

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    # Left has DUPLICATE keys — without them innerunique and inner agree and
    # the whole trap is invisible.
    c.execute("CREATE TABLE L(k VARCHAR, lv INTEGER)")
    c.execute("INSERT INTO L VALUES ('a',1),('a',2),('b',3),('d',4)")
    c.execute("CREATE TABLE R(k VARCHAR, rv INTEGER)")
    c.execute("INSERT INTO R VALUES ('a',10),('a',20),('c',30)")
    return c


def _rows(con, kql):
    rel = duckdb_kql.sql(con, kql)
    return list(rel.columns), sorted(rel.fetchall(), key=lambda r: tuple(str(x) for x in r))


def test_default_kind_is_innerunique_not_inner(con) -> None:
    """The single most dangerous default in KQL."""
    _, default = _rows(con, "L | join (R) on k")
    _, explicit = _rows(con, "L | join kind=innerunique (R) on k")
    _, inner = _rows(con, "L | join kind=inner (R) on k")

    assert default == explicit
    assert len(default) == 2, "innerunique de-duplicates the LEFT key set"
    assert len(inner) == 4, "a plain inner join keeps both left rows"
    assert default != inner


def test_innerunique_keeps_one_left_row_per_key(con) -> None:
    _, rows = _rows(con, "L | join (R) on k | project lv, rv")
    assert {r[0] for r in rows} == {1}, "only one of the two 'a' left rows survives"
    assert sorted(r[1] for r in rows) == [10, 20], "both right rows still match"


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("inner", 4),
        ("leftouter", 6),      # + b, d unmatched on the left
        ("rightouter", 5),     # + c unmatched on the right
        ("fullouter", 7),
        ("leftsemi", 2),       # both left 'a' rows, no duplication by right
        ("rightsemi", 2),
        ("leftanti", 2),       # b, d
        ("rightanti", 1),      # c
    ],
)
def test_join_kind_row_counts(con, kind: str, expected: int) -> None:
    _, rows = _rows(con, f"L | join kind={kind} (R) on k")
    assert len(rows) == expected


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("inner", ["k", "lv", "k1", "rv"]),
        ("leftouter", ["k", "lv", "k1", "rv"]),
        ("fullouter", ["k", "lv", "k1", "rv"]),
        # semi/anti return ONE side's columns only.
        ("leftsemi", ["k", "lv"]),
        ("leftanti", ["k", "lv"]),
        ("rightsemi", ["k", "rv"]),
        ("rightanti", ["k", "rv"]),
    ],
)
def test_output_columns(con, kind: str, expected: list[str]) -> None:
    cols, _ = _rows(con, f"L | join kind={kind} (R) on k")
    assert cols == expected


def test_collision_suffix_skips_taken_names(con) -> None:
    """`k` -> `k1`, but if `k1` already exists on the left the right gets `k2`."""
    con.execute("CREATE TABLE L2(k VARCHAR, k1 INTEGER)")
    con.execute("INSERT INTO L2 VALUES ('a', 1)")
    cols, _ = _rows(con, "L2 | join kind=inner (R) on k")
    assert cols == ["k", "k1", "k2", "rv"]


def test_join_on_explicit_sides(con) -> None:
    """`on $left.a == $right.b` joins differently-named columns."""
    cols, rows = _rows(con, "L | join kind=inner (R) on $left.k == $right.k")
    assert cols == ["k", "lv", "k1", "rv"]
    assert len(rows) == 4


def test_right_side_may_be_a_pipeline(con) -> None:
    _, rows = _rows(con, "L | join kind=inner (R | where rv > 15) on k")
    assert len(rows) == 2  # both left 'a' rows x the single rv=20


def test_distribution_hints_are_accepted_and_ignored(con) -> None:
    """hint.strategy tunes cluster execution; it cannot change the result."""
    _, plain = _rows(con, "L | join kind=inner (R) on k")
    _, hinted = _rows(con, "L | join hint.strategy=shuffle kind=inner (R) on k")
    assert plain == hinted


def test_join_without_a_schema_refuses_loudly() -> None:
    """Reproducing KQL's column renaming needs both sides' columns.

    Guessing would produce a plausible-but-wrong output schema, so to_sql()
    without a schema raises instead.
    """
    with pytest.raises(duckdb_kql.KqlSchemaError):
        duckdb_kql.to_sql("L | join (R) on k")


def test_non_join_queries_still_need_no_schema() -> None:
    """Only join forces schema resolution; everything else stays schema-free."""
    assert duckdb_kql.to_sql("L | where k == 'a' | project k")
