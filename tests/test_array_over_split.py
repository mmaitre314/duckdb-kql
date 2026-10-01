"""Trap test — the array functions over `split()`'s result.

`split()` renders as DuckDB's `str_split`, a native ``VARCHAR[]``, while the
array functions were written for a JSON array and began with
``CAST(x AS JSON[])``. Over a native list that cast re-parses each string as
JSON text, so `array_sort_asc(split("b,a", ","))` failed with "Malformed JSON …
Input: b" — on the documentation's own `array_sort_asc` example.

It went unseen because that example also uses `strcat_array`, which was
unsupported, so the whole query was refused before the cast could fail. Adding
`strcat_array` turned a refusal into an engine error — a reported crash whose
quiet siblings were every other array function over a split.

The fix is ``CAST(to_json(x) AS JSON[])``: `to_json` of a JSON value is the
value, and of a native list is its JSON array. Each answer below is the
emulator's, measured over a literal and over a column alike.
"""

from __future__ import annotations

import json

import duckdb
import pytest

import duckdb_kql

CASES = [
    ('array_reverse(split(s, ","))', ["c", "b", "a"]),
    ('array_index_of(split(s, ","), "b")', 1),
    ('array_slice(split(s, ","), 1, 2)', ["b", "c"]),
    ('set_has_element(split(s, ","), "b")', True),
    ('array_sort_asc(split(s, ","))', ["a", "b", "c"]),
    ('array_sort_desc(split(s, ","))', ["c", "b", "a"]),
    ('array_concat(split(s, ","), dynamic([1]))', ["a", "b", "c", 1]),
]


@pytest.mark.parametrize(("expression", "expected"), CASES)
def test_array_function_over_split(expression: str, expected: object) -> None:
    con = duckdb.connect()
    (value,), = duckdb_kql.kql(
        con, f"datatable(s:string)['a,b,c'] | project x = {expression}"
    ).fetchall()
    if isinstance(value, str) and isinstance(expected, list):
        value = json.loads(value)
    assert value == expected
