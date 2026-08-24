"""L5 trap tests — the **string form** of a value, as KQL spells it.

`tostring` is not a CAST. Four rules differ, and every one of them fails
*silently*: the answer is still a plausible string, so nothing raises and the
query just returns the wrong text.

* a **bool** is ``True``/``False`` (.NET capitalisation), not ``true``;
* a **datetime** is ``2020-01-02T03:04:05.6000000Z``, not ``2020-01-02
  03:04:05.6``;
* a **dynamic** is its value, not its JSON encoding (that family has its own
  file, ``test_dynamic_strings.py``);
* **null is the empty string**, for every type — `tostring` is total.

The rendering used to be picked from an allow-list walked over the IR, and the
allow-list was wrong in two separate ways. It missed most of the ways to spell
a bool — ``tostring(x has 'a')`` gave ``'false'``, ``tostring(1 == 1)`` gave
``'True'``, from the same expression tree. And a bare **column** carries no
static type at all, so a `bool` column and a `datetime` column both stringified
as SQL rather than as KQL. The dispatch is now DuckDB's own `typeof` at run
time, which cannot have that hole.

The digests are the reason this is worth a file of its own: `hash_md5` hashes
the string form, so a wrong spelling is a wrong digest with no error anywhere.
Every expectation below was measured on the Kusto Emulator.
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


def _one(con, kql: str):
    return duckdb_kql.kql(con, kql).fetchall()[0][0]


def _rows(con, kql: str):
    return duckdb_kql.kql(con, kql).fetchall()


# ---------------------------------------------------------------------------
# Bools — every way to spell one
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        # These four the old allow-list already had.
        ("true", "True"),
        ("false", "False"),
        ("1 == 1", "True"),
        ("1 > 0 and 2 > 1", "True"),
        # ...and these it did not, so they came back lowercase.
        ('"abc" has "a"', "False"),
        ('"abc" !contains "z"', "True"),
        ('"abc" matches regex "b"', "True"),
        ('isnull("abc")', "False"),
        ('isempty("")', "True"),
        ("isnan(0.0/0.0)", "True"),
        ("1 in (1,2,3)", "True"),
        ("4 !in (1,2,3)", "True"),
        ('"abc def" has_any ("zzz")', "False"),
        ('"abc def" has_all ("abc","def")', "True"),
        ("not(1 == 2)", "True"),
        ('tobool("true")', "True"),
        ("set_has_element(dynamic([1,2]), 1)", "True"),
        ("iff(1 > 0, true, false)", "True"),
        ("case(1 > 0, true, false)", "True"),
    ],
)
def test_a_bool_is_dotnet_capitalised(con, expr: str, expected: str) -> None:
    assert _one(con, f"print p = tostring({expr})") == expected


def test_a_bool_column_too(con) -> None:
    """The case a static check cannot see, and the reason the dispatch moved.

    A column reaching `tostring` is an `ir.ColumnRef` and nothing more — no
    type travels with it. `typeof` at run time does know.
    """
    t = "datatable(b:bool)[true, false, bool(null)]"
    assert _rows(con, f"{t} | project p = tostring(b)") == [
        ("True",), ("False",), ("",)
    ]
    assert _rows(con, "datatable(x:long)[1] | project p = tostring(x > 0)") == [
        ("True",)
    ]


def test_a_string_that_merely_looks_like_a_bool_is_left_alone(con) -> None:
    """`'true'` the *string* stays lowercase — the guard is on the type.

    This is what a blanket ``initcap``-style fix would corrupt, and it is why
    the branch is chosen by ``typeof(x) = 'BOOLEAN'`` rather than by the text.
    """
    assert _one(con, 'print p = tostring("true")') == "true"
    assert _one(con, 'datatable(s:string)["true"] | project p = tostring(s)') == "true"
    # A JSON `true` is a dynamic, and KQL prints *that* lowercase.
    assert _one(con, "print p = tostring(dynamic(true))") == "true"


# ---------------------------------------------------------------------------
# Datetimes — the same hole, on the type that fills log data
# ---------------------------------------------------------------------------


def test_a_datetime_column_uses_kqls_iso_spelling(con) -> None:
    """Seven fractional digits and a ``Z``; DuckDB's CAST gives neither.

    A datetime *literal* was already right — the allow-list saw the call — so
    the bug only appeared once the value arrived as a column, which is how it
    arrives in every real query.
    """
    t = "datatable(d:datetime)[datetime(2020-01-02 03:04:05.6)]"
    assert _one(con, f"{t} | project p = tostring(d)") == "2020-01-02T03:04:05.6000000Z"
    assert _one(con, f"{t} | project p = strcat('t=', d)") == (
        "t=2020-01-02T03:04:05.6000000Z"
    )
    assert _one(con, f"{t} | project p = reverse(d)") == "Z0000006.50:40:30T20-10-0202"


def test_a_timespan_column_is_already_duckdbs_spelling(con) -> None:
    """Pinned so the TIMESTAMP branch is not widened to INTERVAL by analogy."""
    t = "datatable(t:timespan)[3h]"
    assert _one(con, f"{t} | project p = tostring(t)") == "03:00:00"
    assert _one(con, f"{t} | project p = reverse(t)") == "00:00:30"


# ---------------------------------------------------------------------------
# Totality — null is '', for every type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kql_type", ["bool", "int", "long", "real", "datetime", "timespan", "guid"]
)
def test_tostring_of_a_null_is_the_empty_string(con, kql_type: str) -> None:
    """Measured for every type the emulator will construct a null of.

    Not cosmetic: `isnull(tostring(x))` is **false** and `strlen` is 0, so a
    ``where isnull(tostring(x))`` filter matched nothing on a cluster and every
    row here. It is also what lets `strcat` skip null handling entirely.
    """
    null = f"{kql_type}(null)"
    assert _one(con, f"print p = tostring({null})") == ""
    assert _one(con, f"print p = isnull(tostring({null}))") is False
    assert _one(con, f"print p = strlen(tostring({null}))") == 0


def test_a_null_keeps_its_slot_in_the_strcat_family(con) -> None:
    """`concat_ws` **skips** a NULL argument, so `a--b` would have been `a-b`.

    The empty-string totality above is what stops that; this pins the
    consequence rather than the mechanism.
    """
    assert _one(con, "print p = strcat('x', int(null))") == "x"
    assert _one(con, "print p = strcat_delim('-', 'a', int(null), 'b')") == "a--b"


# ---------------------------------------------------------------------------
# The digests — where a wrong spelling stops being cosmetic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expr", "digest"),
    [
        # Lowercase 'false' hashes to 68934a3e9455fa72420237eb05902327, which is
        # what this returned before, and which no cluster would ever produce.
        ('hash_md5(tostring("abc" has "a"))', "f8320b26d30ab433c5a54546d21f414c"),
        ("hash_md5(tostring(true))", "f827cf462f62848df37c5e1e94a4da74"),
        ("hash_md5(tostring(false))", "f8320b26d30ab433c5a54546d21f414c"),
        (
            "hash_sha256(tostring(datetime(2020-01-02)))",
            "94eb8ad4999be93fc5d8b45515b10957dc086abb7e9afdfb52e7e402d8bf43c2",
        ),
    ],
)
def test_the_digest_is_of_kqls_spelling(con, expr: str, digest: str) -> None:
    assert _one(con, f"print p = {expr}") == digest


def test_a_datetime_column_hashes_the_same_as_the_literal(con) -> None:
    t = "datatable(d:datetime)[datetime(2020-01-02 03:04:05.6)]"
    assert _one(con, f"{t} | project p = hash_md5(tostring(d))") == (
        "2ec9def8f0e9bc2e8780756a954935d4"
    )


@pytest.mark.parametrize("fn", ["hash_md5", "hash_sha1", "hash_sha256"])
def test_hashing_the_empty_string_gives_the_empty_string(con, fn: str) -> None:
    """Measured, and it is not the digest of ``""``.

    Kusto answers `''` where DuckDB answers `d41d8cd9…`. Since `tostring` maps
    every null to `''`, this also settles `hash_md5(x)` for a null `x` — which
    is how the divergence surfaced.
    """
    assert _one(con, f'print p = {fn}("")') == ""
    assert _one(con, f"print p = {fn}(tostring(long(null)))") == ""
    assert _one(con, f'print p = isnull({fn}(""))') is False


# ---------------------------------------------------------------------------
# The dispatch itself
# ---------------------------------------------------------------------------


def test_every_branch_binds_for_every_operand_type(con) -> None:
    """DuckDB binds all `CASE` branches, not only the one that fires.

    So the `strftime` branch needs its CAST and the bool branch has to compare
    the VARCHAR form rather than test the operand as a condition — otherwise
    `tostring` over a string or a list is a *bind* error, and the guard never
    gets the chance to route around it.
    """
    for table, col in [
        ("datatable(s:string)['ab']", "s"),
        ("datatable(n:long)[1]", "n"),
        ("datatable(r:real)[1.5]", "r"),
        ("datatable(g:guid)[guid(11111111-2222-3333-4444-555555555555)]", "g"),
        ("datatable(t:timespan)[3h]", "t"),
        ("datatable(d:dynamic)[dynamic([1,2])]", "d"),
    ]:
        assert _one(con, f"{table} | project p = tostring({col})") is not None


def test_a_statically_known_datetime_skips_the_dispatch(con) -> None:
    """Size, not semantics — pinned because the shortcut is easy to delete.

    ``datetime('...')`` renders as a multi-line `try_strptime` list; running it
    through the dispatch would substitute that blob five times into one
    expression, for an answer the shortcut already gets right.
    """
    sql = str(duckdb_kql.to_sql("print p = tostring(datetime(2020-01-02))"))
    assert "typeof" not in sql
    assert "strftime" in sql
