"""L5 trap tests — ``dynamic``/JSON, ``mv-expand``, and hashing.

All expectations measured against the Kusto Emulator. The traps here are
unusually dense because KQL's dynamic type is *untyped at the edges*:

* **navigation never errors** — a missing property or an out-of-range index is
  null (R9), and a negative index counts from the END;
* **``mv-expand`` has three behaviours** depending on what it is given, and the
  empty-array and null cases differ from each other;
* **``tostring`` is .NET's spelling, not SQL's** — which matters beyond
  cosmetics, because ``hash_md5`` hashes the string form.
"""

from __future__ import annotations

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    return c


def _one(con, kql):
    return duckdb_kql.kql(con, kql).fetchall()[0][0]


def _rows(con, kql):
    return duckdb_kql.kql(con, kql).fetchall()


def _json(con, kql):
    import json

    v = _one(con, kql)
    return json.loads(v) if isinstance(v, str) else v


# --- navigation (R9) --------------------------------------------------------
@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("dynamic([1,2,3])[0]", 1),
        ("dynamic([1,2,3])[2]", 3),
        # Negative indexing counts from the END. DuckDB's JSON path spells that
        # `$[#-1]`; a bare `$[-1]` silently returns null instead.
        ("dynamic([1,2,3])[-1]", 3),
        ("dynamic({'a':1}).a", 1),
        ("dynamic({'a':1})['a']", 1),
        ("dynamic({'a':{'b':7}}).a.b", 7),
    ],
)
def test_navigation(con, expr: str, expected) -> None:
    assert _json(con, f"print x = {expr}") == expected


@pytest.mark.parametrize(
    "expr",
    ["dynamic([1,2,3])[9]", "dynamic({'a':1}).zzz", "dynamic({'a':1}).a.b"],
)
def test_missing_navigation_is_null_never_an_error(con, expr: str) -> None:
    """R9 — the whole point of the dynamic type is that lookups don't throw."""
    assert _one(con, f"print x = {expr}") is None


# --- array functions --------------------------------------------------------
def test_array_index_of_returns_minus_one_when_absent(con) -> None:
    """NOT null: `== -1` is how KQL queries test for absence."""
    assert _one(con, "print array_index_of(dynamic(['a','b']), 'b')") == 1
    assert _one(con, "print array_index_of(dynamic(['a','b']), 'z')") == -1


def test_array_slice_endpoints_are_inclusive(con) -> None:
    assert _json(con, "print array_slice(dynamic([1,2,3,4]), 1, 2)") == [2, 3]


def test_array_sort_puts_nulls_last(con) -> None:
    assert _json(
        con, "print array_sort_asc(dynamic([null,'blue','yellow','green',null]))"
    ) == ["blue", "green", "yellow", None, None]


def test_array_length_and_concat(con) -> None:
    assert _one(con, "print array_length(dynamic([1,2,3]))") == 3
    assert _json(con, "print array_concat(dynamic([1,2]), dynamic([3]))") == [1, 2, 3]


def test_pack_array_accepts_mixed_types(con) -> None:
    """A timespan inside an array is rendered KQL-style, as "00:00:02"."""
    assert _json(con, "print pack_array(1, 'a', 2*1s)") == [1, "a", "00:00:02"]


def test_in_against_a_dynamic_array(con) -> None:
    """`x in (dynamic([...]))` is membership, not equality with the array."""
    rows = _rows(
        con, "datatable(s:string)['a','B','z'] | where s in~ (dynamic(['A','b']))"
    )
    assert sorted(r[0] for r in rows) == ["B", "a"]


# --- mv-expand --------------------------------------------------------------
def test_mv_expand_array(con) -> None:
    assert _rows(
        con, "datatable(a:int, b:dynamic)[1, dynamic([10,20])] | mv-expand b"
    ) == [(1, "10"), (1, "20")]


def test_mv_expand_object_yields_one_row_per_key(con) -> None:
    """A bag expands to single-key bags — two rows, not one."""
    rows = _rows(
        con,
        'datatable(a:int, b:dynamic)[1, dynamic({"p":"x","q":"y"})] | mv-expand b',
    )
    assert rows == [(1, '{"p":"x"}'), (1, '{"q":"y"}')]


def test_mv_expand_empty_array_drops_the_row(con) -> None:
    assert _rows(con, "datatable(a:int, b:dynamic)[1, dynamic([])] | mv-expand b") == []


def test_mv_expand_null_keeps_one_row(con) -> None:
    """The null case differs from the empty-array case — one row, not zero."""
    assert _rows(
        con, "datatable(a:int, b:dynamic)[1, dynamic(null)] | mv-expand b"
    ) == [(1, None)]


def test_mv_expand_with_itemindex(con) -> None:
    rel = duckdb_kql.kql(
        con,
        "range x from 1 to 4 step 1 | summarize x = make_list(x) "
        "| mv-expand with_itemindex=Index x",
    )
    assert list(rel.columns) == ["x", "Index"]
    assert [r[1] for r in rel.fetchall()] == [0, 1, 2, 3]


#: `id, a, b` where `a` is a two-element array — enough to see both where the
#: expanded column lands and whether the source column survives.
_SHAPE = "datatable(id:long, a:dynamic, b:dynamic)[1, dynamic([10,20]), dynamic(['p'])]"


@pytest.mark.parametrize(
    ("operator", "columns"),
    [
        # The expansion REPLACES the column in place — it does not append.
        ("mv-expand a", ["id", "a", "b"]),
        ("mv-expand b", ["id", "a", "b"]),
        # An alias names an OUTPUT column: new ones are appended and `a` keeps
        # the whole array, while an existing name is replaced where it stands.
        ("mv-expand x = a", ["id", "a", "b", "x"]),
        ("mv-expand b = a", ["id", "a", "b"]),
        ("mv-expand a = a", ["id", "a", "b"]),
        # The item index always lands last, and collides like a join key.
        ("mv-expand with_itemindex=i a", ["id", "a", "b", "i"]),
        ("mv-expand with_itemindex=b a", ["id", "a", "b", "b1"]),
        ("mv-expand with_itemindex=i x = a", ["id", "a", "b", "x", "i"]),
    ],
)
def test_mv_expand_column_shape(con, operator: str, columns: list[str]) -> None:
    """Column order is user-visible (R1), and this operator gets it twice wrong.

    Rendering as ``* EXCLUDE (a), UNNEST(...) AS a`` appended the expanded
    column — `id, b, a` where Kusto says `id, a, b` — and dropped `a` entirely
    under an alias, where Kusto keeps it holding the un-expanded array.

    `with_itemindex=b` over a table that already has `b` is the naming trap:
    Kusto answers **b1**, the join-collision spelling, where two columns both
    called `b` would have left DuckDB naming the second `b_1`.
    """
    assert list(duckdb_kql.kql(con, f"{_SHAPE} | {operator}").columns) == columns


def test_an_alias_leaves_the_source_column_holding_the_whole_array(con) -> None:
    """The half of the shape a column list cannot show."""
    rows = _rows(con, f"{_SHAPE} | mv-expand x = a | project a, x")
    assert rows == [("[10,20]", "10"), ("[10,20]", "20")]


# ---------------------------------------------------------------------------
# The same rules with NO schema — the branch every test above is blind to
# ---------------------------------------------------------------------------
#
# `_SHAPE` is a `datatable`, which carries its columns in the IR, so `cols` is
# never None in any test above and the star-fallback branch never runs. It is
# reachable: `to_sql()` over a bare table with no schema takes it, and there
# `mv-expand b = a` emitted `SELECT *, UNNEST(...) AS "b"` — two columns named
# `b`, with `project b` binding to the stale one and answering 'orig' twice.


@pytest.mark.parametrize(
    "operator",
    [
        "mv-expand b = a",              # the alias may collide; cannot tell
        "mv-expand x = a",              # ...and neither can this one, here
        "mv-expand with_itemindex=b a",
        "mv-expand with_itemindex=i a",
        "mv-expand with_itemindex=i x = a",
    ],
)
def test_an_alias_without_a_schema_is_refused_not_guessed(operator: str) -> None:
    """R18's replace-in-place needs the input columns; say so, don't degrade.

    Whether the alias collides with an existing column is precisely what is
    unknowable here, and guessing wrong is a wrong *answer* rather than a wrong
    column order. So these forms join `join`/`lookup` in forcing resolution.
    """
    from duckdb_kql.errors import KqlSchemaError

    with pytest.raises(KqlSchemaError) as exc:
        duckdb_kql.to_sql(f"T3 | {operator}")
    assert "T3" in str(exc.value)


@pytest.mark.parametrize("operator", ["mv-expand a", "mv-expand a, b"])
def test_a_plain_mv_expand_still_needs_no_schema(operator: str) -> None:
    """The refusal is scoped to the forms that can corrupt, not to the operator.

    A same-name expansion renders as ``* EXCLUDE (a), UNNEST(...) AS a``, which
    cannot produce a duplicate. Its residue is position — `a` moves to the end
    — which is `extend`'s documented residue too, and visible rather than
    silent.
    """
    assert "UNNEST" in str(duckdb_kql.to_sql(f"T3 | {operator}"))


def test_the_schema_may_come_from_the_connection(con) -> None:
    """`kql(con, ...)` derives it, so the refusal never reaches that caller.

    Pinned because it is the difference between "a schema is required" and
    "a schema is required and you already have one".
    """
    con.execute(
        "CREATE TABLE T3 AS SELECT 1 AS id, 'orig' AS b, CAST('[10,20]' AS JSON) AS a"
    )
    assert _rows(con, "T3 | mv-expand b = a | project b") == [("10",), ("20",)]
    assert list(duckdb_kql.kql(con, "T3 | mv-expand with_itemindex=b a").columns) == [
        "id", "b", "a", "b1"
    ]


@pytest.mark.parametrize(
    ("a", "b", "rows"),
    [
        # The row count is the LONGEST list's; the shorter pads with null.
        ("[10,20,30]", "['p']", [("10", '"p"'), ("20", None), ("30", None)]),
        # An empty array beside a non-empty one contributes a **null row** —
        # unlike the single-column case, where an empty array yields nothing.
        ("[]", "['p']", [(None, '"p"')]),
        # ...and all-empty still yields nothing.
        ("[]", "[]", []),
        # A null is one element, not zero, so it pads across the longer list.
        ("null", "['p','q']", [(None, '"p"'), (None, '"q"')]),
        ("[1,2]", "null", [("1", None), ("2", None)]),
        # An object counts as one element per key.
        ("{'k':1}", "['p','q']", [('{"k":1}', '"p"'), (None, '"q"')]),
    ],
)
def test_mv_expand_of_several_columns_zips(con, a: str, b: str, rows: list) -> None:
    """Lockstep, not a cross product — and the padding rule is its own trap.

    DuckDB's rule for several `UNNEST`s in one select list is exactly KQL's,
    both edges included, so the zip needs no arithmetic. What it does need is
    the row count for `with_itemindex`, which is the longest list's.
    """
    q = f"datatable(a:dynamic, b:dynamic)[dynamic({a}), dynamic({b})] | mv-expand a, b"
    assert _rows(con, q) == rows


def test_mv_expand_of_several_columns_keeps_the_source_columns(con) -> None:
    rel = duckdb_kql.kql(con, f"{_SHAPE} | mv-expand x = a, y = b")
    assert list(rel.columns) == ["id", "a", "b", "x", "y"]


@pytest.mark.parametrize(
    ("to_type", "values"),
    [
        # A JSON **number** converts, truncating toward zero — not flooring,
        # and not rounding: -2.5 is -2 in Kusto and -3 under a plain cast.
        ("long", [1, 2, -2, None, None, None]),
        ("int", [1, 2, -2, None, None, None]),
        ("real", [1.0, 2.5, -2.5, None, None, None]),
        # A JSON string is NOT a number here, which is what makes this a
        # conversion rather than a declaration: `'2' to typeof(long)` is null.
        ("string", ["1", "2.5", "-2.5", "2", "true", ""]),
        # A boolean, or a whole number: `2` is true and `2.5` is null.
        ("bool", [True, None, None, None, True, None]),
    ],
)
def test_mv_expand_to_typeof_converts_rather_than_declares(con, to_type, values) -> None:
    src = "dynamic([1, 2.5, -2.5, '2', true, null])"
    q = f"datatable(a:dynamic)[{src}] | mv-expand a to typeof({to_type})"
    assert [r[0] for r in _rows(con, q)] == values


def test_mv_expand_to_typeof_refuses_the_types_with_no_measured_rule(con) -> None:
    """Every input tried — `'1.00:00:00'` included — converts to null there.

    Accepting them would answer null for everything and look like a working
    conversion, which is the wrong kind of wrong.
    """
    for to_type in ("timespan", "decimal"):
        with pytest.raises(duckdb_kql.KqlUnsupportedError):
            duckdb_kql.to_sql(f"T | mv-expand a to typeof({to_type})")


@pytest.mark.parametrize(("limit", "count"), [(0, 0), (1, 1), (2, 2), (9, 3)])
def test_mv_expand_limit_caps_rows_per_input_row(con, limit: int, count: int) -> None:
    q = f"datatable(a:dynamic)[dynamic([1,2,3])] | mv-expand a limit {limit}"
    assert len(_rows(con, q)) == count


@pytest.mark.parametrize(
    "operator",
    ["mv-expand kind=array d", "mv-expand bagexpansion=array d"],
)
def test_bag_expansion_as_array_gives_key_value_pairs(con, operator: str) -> None:
    """On an **object** only: `{"p":1}` becomes `["p",1]` rather than a bag."""
    q = f"datatable(d:dynamic)[dynamic({{'p':1,'q':2}})] | {operator}"
    assert _rows(con, q) == [('["p",1]',), ('["q",2]',)]


@pytest.mark.parametrize("operator", ["mv-expand kind=bag d", "mv-expand d"])
def test_bag_expansion_defaults_to_bag(con, operator: str) -> None:
    q = f"datatable(d:dynamic)[dynamic({{'p':1,'q':2}})] | {operator}"
    assert _rows(con, q) == [('{"p":1}',), ('{"q":2}',)]


def test_with_itemindex_over_an_empty_array_yields_no_rows(con) -> None:
    """The index list is as long as the longest expansion, with no floor of 1.

    Clamping it to at least one — which an earlier version did — gave an empty
    array one row carrying null where Kusto answers none.
    """
    q = "datatable(a:dynamic)[dynamic([])] | mv-expand with_itemindex=i a"
    assert _rows(con, q) == []


def test_mv_expand_of_an_expression_refuses(con) -> None:
    """The operator rewrites a column in place, and there is none to rewrite."""
    with pytest.raises(duckdb_kql.KqlUnsupportedError):
        duckdb_kql.to_sql("T | mv-expand strcat(a, 'x')")


# --- tostring / hashing -----------------------------------------------------
def test_tostring_uses_dotnet_spelling(con) -> None:
    """Not cosmetic: hash_md5 hashes this string, so a wrong form is a wrong digest."""
    assert _one(con, "print tostring(datetime(2020-01-01))") == (
        "2020-01-01T00:00:00.0000000Z"
    )


def test_tostring_of_a_dynamic_string_is_unquoted(con) -> None:
    assert _one(con, "print tostring(dynamic({'a':'s'}).a)") == "s"


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("hash_md5('World')", "f5a7924e621e84c9280a9a27e1bcb7f6"),
        ("hash_sha1('abc')", "a9993e364706816aba3e25717850c26c9cd0d89d"),
        (
            "hash_sha256('abc')",
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        ),
        # Hashing a non-string goes through KQL's string form.
        ("hash_md5(datetime(2020-01-01))", "786c530672d1f8db31fee25ea8a9390b"),
        ("hash_md5(123)", "202cb962ac59075b964b07152d234b70"),
    ],
)
def test_hash_digests_match_the_engine(con, expr: str, expected: str) -> None:
    assert _one(con, f"print {expr}") == expected


def test_unmappable_hashes_refuse(con) -> None:
    """`hash()`/`hash_xxhash64()` are xxhash64, which DuckDB lacks.

    DuckDB's own hash() is a *different* function, so mapping to it would return
    plausible-looking wrong digests — the worst outcome for a hash.
    """
    for kql in ("print hash('abc')", "print hash_xxhash64('abc')"):
        with pytest.raises(duckdb_kql.KqlUnsupportedError):
            duckdb_kql.to_sql(kql)


# --- modulo -----------------------------------------------------------------
@pytest.mark.parametrize(
    ("expr", "expected"),
    [("10 % 4", 2), ("-10 % 4", 2), ("10 % -4", 2), ("-10 % -4", 2)],
)
def test_modulo_is_always_non_negative(con, expr: str, expected: int) -> None:
    """KQL's % is a mathematical modulo; SQL's takes the dividend's sign."""
    assert _one(con, f"print x = {expr}") == expected


# --- summarize over dynamic -------------------------------------------------
def test_make_set_unions_dynamic_arrays(con) -> None:
    """A column of arrays gives the union of their ELEMENTS, not a list of arrays."""
    got = _json(
        con,
        "datatable(a:dynamic)[dynamic(['A1','A2']), dynamic(['A2','C1'])] "
        "| summarize make_set(a)",
    )
    assert sorted(got) == ["A1", "A2", "C1"]
