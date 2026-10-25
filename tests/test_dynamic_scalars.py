"""Trap tests — a scalar `dynamic` is a value on the wire and JSON text in DuckDB, by design.

Reported as a wrong answer, from the differential harness: `d.a.b` over
`dynamic({'a':{'b':3}})` came back as `'3'` where Kusto answers `3`, and
`dynamic('x')` as `'"x"'`. **No public surface gives that answer.** Measured:

* `KustoClient` and `duckdb-kql serve`, which speak Kusto's protocol, decode a
  dynamic cell — `3`, `'x'`, `True`, `1.5`, and `None` for a JSON null — as the
  Azure SDK does;
* `kql()` returns a DuckDB relation, whose `dynamic` is DuckDB `JSON`
  (TRANSLATION.md §2), and DuckDB hands JSON to Python as its text.

What was wrong was the harness. `compare` decodes our JSON text only for a
column the *expected* side says is dynamic, and `tools/differential.py` built
the expected side without column types — so every scalar dynamic read as a
divergence. It keeps them now. The fix that looked obvious, decoding JSON in
`kql()`, would have changed a documented contract to paper over a test tool.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.comparison import compare

duckdb = pytest.importorskip("duckdb")

QUERY = (
    "datatable(d:dynamic)[dynamic({'a': {'b': 3, 's': 'x', 't': true, 'f': 1.5, 'n': null}})]"
    " | project b = d.a.b, s = d.a.s, t = d.a.t, f = d.a.f, n = d.a.n, missing = d.zz"
)


def test_the_kusto_client_decodes_a_scalar_dynamic() -> None:
    from duckdb_kql.kusto import KustoClient

    table = KustoClient(duckdb_kql.connect()).execute(None, QUERY).primary_results[0]
    assert table.raw_rows[0] == [3, "x", True, 1.5, None, None]


def test_the_rest_wire_form_decodes_it_too() -> None:
    """What `duckdb-kql serve` puts in a response, through the same helper."""
    from duckdb_kql.kusto._models import to_wire

    assert [to_wire(v, "dynamic") for v in ("3", '"x"', "true", "1.5", "null", None)] == [
        3, "x", True, 1.5, None, None,
    ]


def test_a_relation_keeps_duckdbs_json_text() -> None:
    relation = duckdb_kql.kql(duckdb_kql.connect(), QUERY)
    assert [str(t) for t in relation.types] == ["JSON"] * 6
    assert relation.fetchall() == [("3", '"x"', "true", "1.5", "null", None)]


def test_compare_decodes_only_with_the_expected_column_types() -> None:
    """The harness trap: the same pair, with and without the types."""
    ours = {"columns": ["b", "s"], "rows": [["3", '"x"']]}
    kusto = {"columns": ["b", "s"], "rows": [[3, "x"]]}
    assert not compare(kusto, ours).equal
    typed = {**kusto, "column_types": ["Object", "Object"]}
    assert compare(typed, ours).equal
