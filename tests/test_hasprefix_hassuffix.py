"""L5 trap tests — `hasprefix` / `hassuffix` (R3).

Documented by R3, present in the corpus, spelled by the lexer — and with no row
in `BINARY_OPERATORS` at all, so `Logs | where Text hasprefix "er"` was refused
outright. A live gap between what the spec claimed as behaviour and what the
translator would accept.

They are **term** operators, not string ones, which is the trap: `hasprefix` is
not `startswith`. It asks whether *some term* starts with the needle, so
`'x-error' hasprefix 'err'` is true — the hyphen ends the previous term — while
`'err or' hasprefix 'r o'` is false, because the `r` inside `err` has no
boundary before it.

`hassuffix` is the mirror image, and the pair is the same regex `has` uses with
one boundary dropped. Every expectation measured on the Kusto Emulator, over a
fixture chosen so each punctuation and case rule shows up in a different row.
"""

from __future__ import annotations

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")

#: Rows picked so that each answer below differs from at least one other
#: operator's — a fixture where `has`, `hasprefix` and `startswith` agree would
#: prove nothing.
ROWS = [
    "error code", "the error", "an errorlog", "ERROR", "x-error", "a_error",
    "code error", "erro", "the code", "a.error.b", "err or",
]
T = "datatable(t:string)[" + ", ".join(f"'{r}'" for r in ROWS) + "]"


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    return c


def _matched(con, op: str, needle: str) -> list[str]:
    q = f"{T} | where t {op} '{needle}' | project t"
    return sorted(r[0] for r in duckdb_kql.kql(con, q).fetchall())


def test_hasprefix_is_a_term_prefix_not_a_string_prefix(con) -> None:
    """`'the error' hasprefix 'err'` is true — `startswith` would say false."""
    assert _matched(con, "hasprefix", "err") == [
        "ERROR", "a.error.b", "a_error", "an errorlog", "code error", "err or",
        "erro", "error code", "the error", "x-error",
    ]
    # `startswith` over the same needle keeps only rows the STRING starts with.
    assert _matched(con, "startswith", "err") == [
        "ERROR", "err or", "erro", "error code"
    ]


def test_hassuffix_is_a_term_suffix(con) -> None:
    """`'an errorlog' hassuffix 'or'` is false — the term ends `log`."""
    assert _matched(con, "hassuffix", "or") == [
        "ERROR", "a.error.b", "a_error", "code error", "err or", "error code",
        "the error", "x-error",
    ]
    assert "an errorlog" not in _matched(con, "hassuffix", "or")
    assert "erro" not in _matched(con, "hassuffix", "or")


def test_the_two_are_not_each_other(con) -> None:
    """The one comparison that shows they are different operators.

    `'err or' hasprefix 'or'` is true (the term `or`); `hasprefix 'err'` picks
    up every row where a term begins `err`. Only `err or` has a term that IS
    `or`.
    """
    assert _matched(con, "hasprefix", "or") == ["err or"]
    assert _matched(con, "hasprefix", "error") == [
        "ERROR", "a.error.b", "a_error", "an errorlog", "code error",
        "error code", "the error", "x-error",
    ]


@pytest.mark.parametrize(
    ("op", "needle", "kept"),
    [
        # Case-insensitive by default; `_cs` is the sensitive form.
        ("hasprefix_cs", "ERR", ["ERROR"]),
        ("hassuffix_cs", "ERR", []),
        ("hasprefix_cs", "err", [
            "a.error.b", "a_error", "an errorlog", "code error", "err or",
            "erro", "error code", "the error", "x-error",
        ]),
    ],
)
def test_the_case_sensitive_forms(con, op: str, needle: str, kept: list) -> None:
    assert _matched(con, op, needle) == kept


@pytest.mark.parametrize(
    ("haystack", "op", "needle", "expected"),
    [
        # The boundary edge rule, shared with `has`: a boundary applies at an
        # edge only when the NEEDLE's own character there is a term character.
        ("xa b", "hasprefix", "a ", False),   # needle starts `a`, `x` blocks it
        ("a b", "hasprefix", "a ", True),
        ("x ab y", "hasprefix", " a", True),  # needle starts with a delimiter
        ("x ab y", "hassuffix", " a", False),  # ...but ends with a term char
        ("xa b", "hassuffix", "a ", True),
        ("x-error", "hasprefix", "err", True),
        ("x-error", "hasprefix", "-err", True),
        ("x-error", "hassuffix", "err", False),
        ("err or", "hasprefix", "r o", False),
        # An all-delimiter needle is a plain substring test, degenerately so
        # for the empty one.
        ("b .b-", "hasprefix", " ", True),
        ("b .b-", "hassuffix", " ", True),
        ("abc", "hasprefix", "", True),
        ("abc", "hassuffix", "", True),
    ],
)
def test_the_boundary_edge_rule(
    con, haystack: str, op: str, needle: str, expected: bool
) -> None:
    q = f"print x = '{haystack}' {op} '{needle}'"
    assert duckdb_kql.kql(con, q).fetchall()[0][0] is expected


@pytest.mark.parametrize(
    ("op", "on_null"),
    [
        ("hasprefix", False),
        ("hassuffix", False),
        ("hasprefix_cs", False),
        ("hassuffix_cs", False),
        # The negated forms do NOT propagate: a null haystack is true.
        ("!hasprefix", True),
        ("!hassuffix", True),
        ("!hasprefix_cs", True),
        ("!hassuffix_cs", True),
    ],
)
def test_a_null_haystack_does_not_propagate(con, op: str, on_null: bool) -> None:
    """R4's totality, pinned per operator rather than derived from `NOT (…)`.

    Measured: a null haystack answers false for the positive forms and true for
    the negated ones — which is not what `NOT NULL` would give.
    """
    q = f"datatable(t:string)[dynamic(null)] | project v = t {op} 'err'"
    assert duckdb_kql.kql(con, q).fetchall() == [(on_null,)]


@pytest.mark.parametrize(
    "op",
    ["hasprefix", "hassuffix", "hasprefix_cs", "hassuffix_cs",
     "!hasprefix", "!hassuffix", "!hasprefix_cs", "!hassuffix_cs"],
)
def test_every_spelling_translates(op: str) -> None:
    """Including the `_cs` variants, which the lexer's op list omitted."""
    assert "regexp_matches" in str(duckdb_kql.to_sql(f"T | where t {op} 'x'"))


def test_the_negations_are_the_complement(con) -> None:
    kept = set(_matched(con, "hasprefix", "err"))
    dropped = set(_matched(con, "!hasprefix", "err"))
    assert kept | dropped == set(ROWS)
    assert not (kept & dropped)
