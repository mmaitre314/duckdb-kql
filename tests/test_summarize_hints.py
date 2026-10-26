"""Trap tests — a `summarize` shuffle hint is accepted as the no-op it is here, and nothing else is.

Reported: `T | summarize hint.strategy = shuffle total = sum(v) by k` was refused
as `expression:StrictQueryOperatorParameter`, so a production query carrying its
execution hint could not be run unchanged. The lowering *meant* to refuse a hint,
but tested for a parse-tree class the grammar never produces, so the parameter
fell through and was lowered as an **aggregate**.

Microsoft documents shuffle as preserving the result, and DuckDB is one node, so
the hint changes nothing here. **The obvious fix is wrong**, though: ignoring
every `hint.*`, as `join` does, would take `hint.strategy = broadcast`, which
Kusto refuses on a `summarize`. Measured on the emulator, 2026-10-26:

* `hint.strategy` takes `shuffle` alone — any case, quoted or not — and
  refuses every other value (SEM0249);
* `hint.shufflekey` takes a bare column that a group key reads, and refuses a
  string (SEM0259), an alias, a column no key reads, and a `summarize` with no
  `by`. Here only a key that *is* the column counts: Kusto also takes `by
  tostring(k)` but not `by bin(k, 1)`, a rule two points cannot give;
* `hint.num_partitions` Kusto takes with any value at all, `'a'` and `-1`
  included. Only a positive integer is accepted here.

Kusto also silently ignores every other parameter name the grammar allows on a
`summarize` — `kind = foo`, `isfuzzy`, `hint.materialized`. Those are refused, so
a typo, or a parameter meant for another operator, is not taken silently.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlError

duckdb = pytest.importorskip("duckdb")

SOURCE = 'datatable(k:string, k2:long, v:long) ["a", 1, 1, "a", 1, 2, "b", 2, 3]'
EMPTY = "datatable(k:string, k2:long, v:long) []"


def _run(query: str) -> tuple[list[str], list[tuple]]:
    cursor = duckdb_kql.query(duckdb_kql.connect(), query)
    return [d[0] for d in cursor.description], sorted(cursor.fetchall(), key=repr)


@pytest.mark.parametrize(
    "hint",
    [
        "hint.strategy = shuffle",
        "hint.strategy=shuffle",
        "hint.strategy = Shuffle",
        "hint.strategy = SHUFFLE",
        "hint.strategy = 'shuffle'",
        'hint.strategy = "shuffle"',
        "hint.strategy = shuffle hint.strategy = shuffle",
        "hint.shufflekey = k",
        "hint.strategy = shuffle hint.shufflekey = k",
        "hint.num_partitions = 4",
        "hint.strategy = shuffle hint.num_partitions = 4",
    ],
)
@pytest.mark.parametrize(
    ("source", "body"),
    [
        (SOURCE, "total = sum(v) by k"),
        (SOURCE, "total = sum(v), n = count() by k, k2"),
        (SOURCE, "count() by bin(k2, 1), k"),
        (SOURCE, "arg_max(v, *) by k"),
        (SOURCE, "by k"),
        (EMPTY, "total = sum(v) by k"),
        (EMPTY, "total = sum(v), n = count() by k, k2"),
    ],
)
def test_the_answer_is_the_unhinted_one(source: str, body: str, hint: str) -> None:
    """Every body groups by `k` itself, which `hint.shufflekey = k` needs."""
    assert _run(f"{source} | summarize {hint} {body}") == _run(f"{source} | summarize {body}")


@pytest.mark.parametrize(
    ("source", "body", "expected"),
    [
        # Measured on the emulator, with and without the hint.
        (SOURCE, "total = sum(v) by k", (["k", "total"], [("a", 3), ("b", 3)])),
        (SOURCE, "total = sum(v)", (["total"], [(6,)])),
        (SOURCE, "count()", (["count_"], [(3,)])),
        (EMPTY, "total = sum(v) by k", (["k", "total"], [])),
        (EMPTY, "total = sum(v)", (["total"], [(0,)])),
    ],
)
def test_measured_answers(source: str, body: str, expected: tuple) -> None:
    assert _run(f"{source} | summarize hint.strategy = shuffle {body}") == expected


def test_a_shuffle_key_may_be_the_column_behind_a_named_key() -> None:
    assert _run(f"{SOURCE} | summarize hint.shufflekey = k total = sum(v) by kk = k") == (
        ["kk", "total"], [("a", 3), ("b", 3)],
    )


@pytest.mark.parametrize(
    "parameters",
    [
        # hint.strategy: every value but shuffle, as Kusto refuses (SEM0249)
        "hint.strategy = broadcast",
        "hint.strategy = native",
        "hint.strategy = bogus",
        "hint.strategy = 1",
        "hint.strategy = ''",
        "hint.strategy = bogus hint.strategy = shuffle",
        # Kusto validates only the first; every one is validated here
        "hint.strategy = shuffle hint.strategy = bogus",
        # hint.shufflekey: Kusto refuses each of these
        "hint.shufflekey = zz",
        "hint.shufflekey = 'k'",
        "hint.shufflekey = K",
        "hint.shufflekey = total",
        "hint.shufflekey = k2",
        "hint.shufflekey = v",
        # hint.num_partitions: Kusto takes these; they are not partition counts
        "hint.num_partitions = 0",
        "hint.num_partitions = -1",
        "hint.num_partitions = 'a'",
        "hint.num_partitions = 1.5",
        # names Kusto ignores on a summarize
        "kind = foo",
        "isfuzzy = true",
        "hint.materialized = true",
        "hint.concurrency = 2",
        "hint.spread = 2",
        "hint.remote = auto",
        "hint.distribution = per_node",
        "hint.progressive_top = true",
        "hint.pass_filters = true",
        "bin_legacy = true",
    ],
)
def test_anything_else_is_refused(parameters: str) -> None:
    with pytest.raises(KqlError):
        duckdb_kql.to_sql(f"{SOURCE} | summarize {parameters} total = sum(v) by k")


def test_a_shuffle_key_needs_a_group_key() -> None:
    with pytest.raises(KqlError):
        duckdb_kql.to_sql(f"{SOURCE} | summarize hint.shufflekey = k total = sum(v)")


def test_an_unknown_parameter_name_is_a_syntax_error() -> None:
    """The grammar lists the names it allows, so `hint.bogus` never reaches the check."""
    with pytest.raises(KqlError):
        duckdb_kql.to_sql(f"{SOURCE} | summarize hint.bogus = 1 total = sum(v) by k")
