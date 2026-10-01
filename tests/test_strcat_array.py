"""Trap test — `strcat_array`, each element in its string form.

Not `tostring` of each element, and not the JSON text of each element:
measured, a string is unquoted, a bool is JSON's `true` (where `tostring`
gives .NET's `True`), a null is the empty string, and a nested value is its
compact JSON. A value that is no array is stringified whole, and the
delimiter is read as a string. Each answer is the emulator's.
"""

from __future__ import annotations

import duckdb
import pytest

import duckdb_kql


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ('strcat_array(dynamic(["a", 1, 2.5, true, null, {"x":1}, [1,2]]), "-")',
         'a-1-2.5-true--{"x":1}-[1,2]'),
        ('strcat_array(dynamic([]), "-")', ""),
        ('strcat_array(dynamic("abc"), "-")', "abc"),
        ('strcat_array(dynamic(["a","b"]), 5)', "a5b"),
        ('strcat_array(dynamic([1,2]), dynamic(","))', "1,2"),
        # A native list, from split(): not re-parsed as JSON text.
        ('strcat_array(array_sort_desc(split("a,b,c", ",")), "+")', "c+b+a"),
    ],
)
def test_strcat_array(expression: str, expected: str) -> None:
    assert duckdb_kql.kql(duckdb.connect(), f"print x = {expression}").fetchall() == [
        (expected,)
    ]


def test_strcat_array_of_null_is_empty() -> None:
    # Measured over rows: a null dynamic is '' and isempty, not null.
    got = duckdb_kql.kql(
        duckdb.connect(),
        'datatable(d:dynamic)[dynamic(["p","q"]), dynamic(null), dynamic([])]'
        ' | project s = strcat_array(d, "|")',
    ).fetchall()
    assert sorted(got) == [("",), ("",), ("p|q",)]
