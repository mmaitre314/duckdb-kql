"""L5 trap tests — ``lookup`` (``docs/TRANSLATION.md`` R14).

``lookup`` reads like a synonym for ``join kind=leftouter``, and treating it as
one gets two things wrong. Both were measured on the Kusto Emulator; the
expected values below are what the reference engine actually returned, not what
the documentation says.

1. **The default kind is ``leftouter``**, where a bare ``join`` is
   ``innerunique``. So ``lookup`` never de-duplicates the left key set, and a
   left side with duplicate keys keeps every row.
2. **The right side's key columns are dropped.** With left ``(Row, Key, V)`` and
   right ``(Key, V, Alias)`` on ``Key``, ``join`` gives
   ``Row, Key, V, Key1, V1, Alias`` but ``lookup`` gives ``Row, Key, V, V1,
   Alias`` — ``Key1`` is gone while ``V1`` stays, so the rule is about *keys*,
   not about collisions generally.

Only ``leftouter`` and ``inner`` exist: the emulator rejects every other kind
outright, so accepting one would let a query pass here and fail on a real
cluster.

The third trap is shared with ``join`` and is why both emit ``IS NOT DISTINCT
FROM``: **a null key matches a null key** in KQL, where SQL's ``=`` answers NULL
and drops the pair.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlUnsupportedError

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    # F has a duplicate key ('b' twice) and an unmatched key ('z'); D has a key
    # that matches nothing on the left ('c'). Without all three, leftouter,
    # inner and innerunique agree and every trap here is invisible.
    c.execute("CREATE TABLE F(Row VARCHAR, Key VARCHAR, V VARCHAR)")
    c.execute("INSERT INTO F VALUES ('1','a','fa'),('2','b','fb'),('3','b','fb2'),('4','z','fz')")
    c.execute("CREATE TABLE D(Key VARCHAR, Alias VARCHAR)")
    c.execute("INSERT INTO D VALUES ('a','da'),('b','db'),('c','dc')")
    return c


def _rows(con, kql):
    rel = duckdb_kql.kql(con, kql)
    return list(rel.columns), sorted(
        rel.fetchall(), key=lambda r: tuple(str(x) for x in r)
    )


# ---------------------------------------------------------------------------
# The kind defaults (R14a)
# ---------------------------------------------------------------------------


def test_default_kind_is_leftouter_not_inner(con) -> None:
    """The unmatched left row survives — emulator: 4 rows, including 'z'."""
    _, bare = _rows(con, "F | lookup D on Key")
    _, explicit = _rows(con, "F | lookup kind=leftouter D on Key")
    assert bare == explicit
    assert len(bare) == 4
    assert ("4", "z", "fz", None) in bare


def test_inner_drops_the_unmatched_left_row(con) -> None:
    _, rows = _rows(con, "F | lookup kind=inner D on Key")
    assert len(rows) == 3
    assert all(r[1] != "z" for r in rows)


def test_lookup_does_not_deduplicate_left_keys(con) -> None:
    """The difference from a bare ``join``, which *would* de-duplicate.

    Both 'b' rows must survive with the same Alias. Measured on the emulator.
    """
    _, rows = _rows(con, "F | lookup D on Key")
    b_rows = [r for r in rows if r[1] == "b"]
    assert len(b_rows) == 2
    assert {r[3] for r in b_rows} == {"db"}


def test_duplicate_right_keys_multiply_rows(con) -> None:
    """`lookup` is a real join, not a first-match dictionary lookup."""
    con.execute("CREATE TABLE D2(Key VARCHAR, Alias VARCHAR)")
    con.execute("INSERT INTO D2 VALUES ('a','d1'),('a','d2')")
    _, rows = _rows(con, "F | lookup D2 on Key")
    a_rows = [r for r in rows if r[1] == "a"]
    assert len(a_rows) == 2
    assert {r[3] for r in a_rows} == {"d1", "d2"}


@pytest.mark.parametrize(
    "kind",
    ["innerunique", "rightouter", "fullouter", "leftsemi", "rightsemi",
     "leftanti", "rightanti", "anti"],
)
def test_kinds_kusto_rejects_are_refused_here(con, kind: str) -> None:
    """Being *more* permissive than Kusto is the dangerous direction.

    Every one of these is a kind ``join`` accepts and ``lookup`` does not; the
    emulator answers HTTP 400. Translating them would produce SQL that runs
    locally and a query that fails in production.
    """
    with pytest.raises(KqlUnsupportedError):
        duckdb_kql.kql(con, f"F | lookup kind={kind} D on Key")


# ---------------------------------------------------------------------------
# The column rule (R14a)
# ---------------------------------------------------------------------------


def test_right_key_column_is_dropped(con) -> None:
    cols, _ = _rows(con, "F | lookup D on Key")
    assert cols == ["Row", "Key", "V", "Alias"]
    assert "Key1" not in cols


def test_join_keeps_the_key_column_that_lookup_drops(con) -> None:
    """The contrast that makes `lookup` its own operator rather than an alias."""
    join_cols, _ = _rows(con, "F | join kind=leftouter D on Key")
    lookup_cols, _ = _rows(con, "F | lookup D on Key")
    assert "Key1" in join_cols
    assert "Key1" not in lookup_cols


def test_non_key_collisions_still_get_the_suffix(con) -> None:
    """Only *keys* are dropped. A colliding non-key still becomes ``V1``."""
    con.execute("CREATE TABLE DV(Key VARCHAR, V VARCHAR, Alias VARCHAR)")
    con.execute("INSERT INTO DV VALUES ('a','dv','da')")
    cols, _ = _rows(con, "F | lookup DV on Key")
    assert cols == ["Row", "Key", "V", "V1", "Alias"]


def test_qualified_keys_drop_the_right_name_and_keep_the_left(con) -> None:
    """``on $left.K1 == $right.K2`` drops ``K2``; ``K1`` survives."""
    con.execute("CREATE TABLE FK(Row VARCHAR, K1 VARCHAR)")
    con.execute("INSERT INTO FK VALUES ('1','a'),('2','b')")
    con.execute("CREATE TABLE DK(K2 VARCHAR, Alias VARCHAR)")
    con.execute("INSERT INTO DK VALUES ('a','da'),('b','db')")
    cols, rows = _rows(con, "FK | lookup DK on $left.K1 == $right.K2")
    assert cols == ["Row", "K1", "Alias"]
    assert rows == [("1", "a", "da"), ("2", "b", "db")]


def test_multiple_keys_mixing_shorthand_and_qualified(con) -> None:
    con.execute("CREATE TABLE FM(Row VARCHAR, Key VARCHAR, C1 VARCHAR)")
    con.execute("INSERT INTO FM VALUES ('1','a','x')")
    con.execute("CREATE TABLE DM(Key VARCHAR, C2 VARCHAR, Alias VARCHAR)")
    con.execute("INSERT INTO DM VALUES ('a','x','da')")
    cols, rows = _rows(con, "FM | lookup DM on Key, $left.C1 == $right.C2")
    assert cols == ["Row", "Key", "C1", "Alias"]
    assert rows == [("1", "a", "x", "da")]


# ---------------------------------------------------------------------------
# Null keys match null keys (R14b) — shared with `join`
# ---------------------------------------------------------------------------


@pytest.fixture
def nulls():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    c.execute("CREATE TABLE FN(Row VARCHAR, Key INTEGER)")
    c.execute("INSERT INTO FN VALUES ('1',1),('2',NULL),('3',2)")
    c.execute("CREATE TABLE DN(Key INTEGER, Alias VARCHAR)")
    c.execute("INSERT INTO DN VALUES (1,'d1'),(NULL,'dnull')")
    return c


def test_lookup_matches_a_null_key_to_a_null_key(nulls) -> None:
    """SQL's ``=`` would drop this pair; KQL matches it.

    Emulator: row '2' comes back with Alias 'dnull'.
    """
    _, rows = _rows(nulls, "FN | lookup DN on Key")
    assert ("2", None, "dnull") in rows


def test_lookup_inner_keeps_the_null_matched_row(nulls) -> None:
    _, rows = _rows(nulls, "FN | lookup kind=inner DN on Key")
    assert sorted(rows) == [("1", 1, "d1"), ("2", None, "dnull")]


def test_join_matches_null_keys_too(nulls) -> None:
    """The same rule, on the operator that had it wrong before R14."""
    _, rows = _rows(nulls, "FN | join kind=leftouter DN on Key")
    assert ("2", None, None, "dnull") in rows


def test_join_leftanti_excludes_the_null_match(nulls) -> None:
    """The mirror check: if null matched, anti must *not* return that row."""
    _, rows = _rows(nulls, "FN | join kind=leftanti DN on Key")
    assert rows == [("3", 2)]


def test_null_key_equality_is_emitted_as_is_not_distinct_from() -> None:
    """Pin the SQL, so a refactor to ``=`` fails here rather than in the data."""
    schema = {"FN": ["Row", "Key"], "DN": ["Key", "Alias"]}
    sql = str(duckdb_kql.to_sql("FN | lookup DN on Key", schema=schema))
    assert "IS NOT DISTINCT FROM" in sql
    assert '_l."Key" = _r."Key"' not in sql


# ---------------------------------------------------------------------------
# Shape and plumbing
# ---------------------------------------------------------------------------


def test_right_side_may_be_a_pipeline(con) -> None:
    cols, rows = _rows(con, 'F | lookup (D | where Key == "a") on Key')
    assert cols == ["Row", "Key", "V", "Alias"]
    assert sorted((r[3] or "") for r in rows) == ["", "", "", "da"]


def test_lookup_result_pipes_onward(con) -> None:
    cols, rows = _rows(con, "F | lookup D on Key | project Row, Alias")
    assert cols == ["Row", "Alias"]
    assert len(rows) == 4


def test_distribution_hints_are_accepted_and_ignored(con) -> None:
    """`hint.remote` and `hint.strategy` are cluster placement; results cannot move."""
    _, plain = _rows(con, "F | lookup D on Key")
    for hint in ("hint.remote=left", "hint.strategy=broadcast"):
        _, hinted = _rows(con, f"F | lookup {hint} D on Key")
        assert hinted == plain


def test_shufflekey_is_refused_because_kusto_refuses_it(con) -> None:
    """`join` takes ``hint.shufflekey``; ``lookup`` does not. Measured: HTTP 400."""
    with pytest.raises(KqlUnsupportedError):
        duckdb_kql.kql(con, "F | lookup hint.shufflekey=Key D on Key")


def test_lookup_without_a_schema_refuses_loudly() -> None:
    """Like `join`, the column rule needs both sides' columns."""
    from duckdb_kql.errors import KqlSchemaError

    with pytest.raises(KqlSchemaError):
        duckdb_kql.to_sql("F | lookup D on Key")


def test_lookup_needs_an_on_clause(con) -> None:
    """The grammar makes `on` mandatory — unlike `join`, where it is optional."""
    from duckdb_kql.errors import KqlError

    with pytest.raises(KqlError):
        duckdb_kql.kql(con, "F | lookup D")


# ---------------------------------------------------------------------------
# The documented residue
# ---------------------------------------------------------------------------


def test_unmatched_string_is_null_here_but_empty_in_kusto(con) -> None:
    """A known, documented divergence — asserted so it cannot drift silently.

    KQL has no null string: an unmatched outer-join `string` column is `''`
    there and NULL here, so `isempty()` agrees but `!= ""` does not. Fixing it
    needs column *types*, which the schema (names only) does not carry. The same
    residue applies to `join kind=leftouter`; see TRANSLATION.md R14.
    """
    _, empty = _rows(con, 'F | lookup D on Key | where isempty(Alias) | project Row')
    assert empty == [("4",)]

    # Kusto returns only rows 1-3 here, because unmatched Alias is '' and
    # '' != "" is false. We keep row 4 because NULL != "" is true under R4.
    _, ne = _rows(con, 'F | lookup D on Key | where Alias != "" | project Row')
    assert ne == [("1",), ("2",), ("3",), ("4",)]
