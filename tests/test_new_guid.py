"""Trap tests — `new_guid()` is a fresh GUID per call, and a cached body must not make it one.

`new_guid()` maps to DuckDB's `uuid()`, which is per call too. The mapping is
one line; where it is evaluated is the rest, and every rule below was measured
on the emulator (2026-11-08), folded and over rows:

* **Per call.** `new_guid() == new_guid()` is false in one row, and so is a
  scalar `let` holding it read twice — `let g = new_guid(); print a = g, b = g`
  is two GUIDs, the `rand()` rule (R22). `tests/test_let_sharing.py` said two
  calls compare *equal*; re-measured, they do not.
* **A column is stable.** `extend a = new_guid() | extend b = a | where a == b`
  keeps every row.
* **A tabular `let` is evaluated at every read; `materialize()` once.** Over
  three rows, `let T = …new_guid(); T | union T` is six distinct GUIDs, and
  three with `materialize(…)`. A `let` reading such a `let` is evaluated afresh
  too, and one reading a materialized one is not.
* **A `toscalar` is evaluated once per *call site*.** Two written copies differ;
  one copy read twice through a `let` agrees with itself.

**Why the obvious mapping is not enough.** Every tabular `let` became one CTE,
and DuckDB computes a CTE read twice only once — so `T | union T` answered three
GUIDs where Kusto has six — and identical `toscalar` bodies were one CTE by
design, so two copies compared equal. A volatile `let` is now `NOT MATERIALIZED`
(`MATERIALIZED` when written so), and a volatile `toscalar` is keyed by its node,
not its text. Every other body keeps the SQL it had.

**Residue:** over rows the emulator's GUIDs carry their version digit two places
later than RFC 4122 (`…-6a43-…` where DuckDB writes `…-4…`), a byte-order
artefact of no meaning; any random GUID is a correct answer.
"""

from __future__ import annotations

import re

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlError

duckdb = pytest.importorskip("duckdb")

GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
ROWS = "range x from 1 to 3 step 1"


def _one(query: str) -> tuple:
    with duckdb_kql.connect() as con:
        (row,) = duckdb_kql.kql(con, query).fetchall()
    return row


def test_a_guid_and_its_string_form() -> None:
    kind, text = _one("print t = gettype(new_guid()), s = tostring(new_guid())")
    assert kind == "guid"
    assert GUID.match(text), text


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("print same = new_guid() == new_guid()", (False,)),
        ("let g = new_guid(); print same = g == g", (False,)),
        (f"{ROWS} | extend a = new_guid(), b = new_guid() | summarize countif(a == b), dcount(a)",
         (0, 3)),
        (f"{ROWS} | extend a = new_guid() | extend b = new_guid() | summarize countif(a == b)",
         (0,)),
        (f"let g = new_guid(); {ROWS} | extend a = g, b = g | summarize dcount(a), countif(a == b)",
         (3, 0)),
        (f"{ROWS} | summarize by g = new_guid() | count", (3,)),
    ],
)
def test_every_call_is_a_new_guid(query: str, expected: tuple) -> None:
    assert _one(query) == expected


@pytest.mark.parametrize(
    "query",
    [
        f"{ROWS} | extend a = new_guid() | extend b = a | where a == b | count",
        f"{ROWS} | extend a = new_guid() | where isnotnull(a) | extend b = a"
        " | where a == b | count",
        f"{ROWS} | extend a = new_guid() | sort by a asc | extend b = a | where a == b | count",
        f"{ROWS} | extend a = new_guid() | extend c = a | extend d = c | where a == d | count",
    ],
)
def test_a_column_holds_its_value(query: str) -> None:
    assert _one(query) == (3,)


T = f"{ROWS} | extend a = new_guid()"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (f"let T = {T}; T | union T | summarize dcount(a), count()", (6, 6)),
        (f"let T = materialize({T}); T | union T | summarize dcount(a), count()", (3, 6)),
        (f"let T = {T}; T | join kind=inner T on x | summarize countif(a == a1)", (0,)),
        (f"let T = materialize({T}); T | join kind=inner T on x | summarize countif(a == a1)",
         (3,)),
        (f"let A = {T}; let B = A | extend b = 1; B | union B | summarize dcount(a), count()",
         (6, 6)),
        (f"let A = {T}; let C = materialize(A); let D = C | extend d = 1; "
         "D | union D | summarize dcount(a), count()", (3, 6)),
        (f"let T = {T}; T | summarize dcount(a), count()", (3, 3)),
    ],
)
def test_a_tabular_let_is_evaluated_at_every_read_unless_materialized(
    query: str, expected: tuple
) -> None:
    assert _one(query) == expected


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("print same = toscalar(print new_guid()) == toscalar(print new_guid())", (False,)),
        ("let x = toscalar(print new_guid()); print same = x == x", (True,)),
        (f"let s = toscalar(print new_guid()); {ROWS} | extend a = s | summarize dcount(a)",
         (1,)),
        (f"{ROWS} | extend a = toscalar(print new_guid()) | summarize dcount(a)", (1,)),
    ],
)
def test_a_toscalar_is_evaluated_once_per_call_site(query: str, expected: tuple) -> None:
    assert _one(query) == expected


def test_what_was_cached_before_is_cached_still() -> None:
    """Only a volatile body changes SQL; the rest of the corpus is byte-identical."""
    deterministic = str(duckdb_kql.to_sql(
        f"let T = {ROWS} | extend a = x * 2; T | union T"
    ))
    assert "MATERIALIZED" not in deterministic
    volatile = str(duckdb_kql.to_sql(f"let T = {T}; T | union T"))
    assert "AS NOT MATERIALIZED (" in volatile
    materialized = str(duckdb_kql.to_sql(f"let T = materialize({T}); T | union T"))
    assert "AS MATERIALIZED (" in materialized
    # Reading a materialized body is as stable as reading a table, so a `let`
    # over one is cached as any other: only `A` is evaluated at every read.
    chained = str(duckdb_kql.to_sql(
        f"let A = {T}; let C = materialize(A); let D = C | extend d = 1; D | union D"
    ))
    assert chained.count("NOT MATERIALIZED") == 1


def test_materialize_survives_naming_a_database() -> None:
    """`database=` qualifies every table reference by rebuilding the IR, and the
    rebuild once dropped any field it did not name — `materialized` included."""
    query = f"let T = materialize({T}); T | union T | summarize dcount(a), count()"
    with duckdb_kql.connect() as con:
        assert duckdb_kql.kql(con, query, database="memory").fetchall() == [(3, 6)]


def test_it_takes_no_argument() -> None:
    """SEM0219 on the emulator."""
    with pytest.raises(KqlError):
        duckdb_kql.to_sql("print x = new_guid(1)")
