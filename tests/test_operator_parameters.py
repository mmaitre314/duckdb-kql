"""Pinning tests — what an operator-parameter refusal names, operator by operator.

Six operators parse `name = value` parameters — `macro-expand`, `join`,
`summarize`, `lookup`, `union`, `mv-expand` — and each once split the text
itself. They agree on the split and **disagree on what a refusal quotes**:

* `join`, `lookup` and `union` quote the name *as written* (`Foo`), and a bad
  kind as written (`Bogus`); `summarize` quotes a bad value as written;
* `macro-expand` quotes the name *folded* (`bogus` for `Bogus = 1`);
* `mv-expand` quotes the parameter's whole text (`kind=bogus`).

A refusal, and what it names, is behaviour (docs/maintenance M3). Neither the
corpus snapshot nor any other test reached these spellings, so folding every
name in one shared parser — the natural way to write it — changed three
messages and passed the whole gate. This file is what fails instead.

`summarize` and `mv-expand` take the grammar's *strict* parameters, whose names
are keyword tokens: `summarize Kind = foo` does not parse, so there only the
value's spelling can be observed.

The macro-expand inconsistency is pinned, not endorsed: changing it is a
behaviour change and belongs in a commit of its own.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlUnsupportedError

T = "datatable(x:long, a:dynamic)[1, dynamic([1,2])]"
GROUPS = {"Sources": ["database('First')", "database('Second')"]}


def _refused(query: str, **kwargs: object) -> str:
    with pytest.raises(KqlUnsupportedError) as caught:
        duckdb_kql.to_sql(query, **kwargs)
    return caught.value.construct


@pytest.mark.parametrize(
    ("query", "construct"),
    [
        (f"{T} | join Foo = bar ({T}) on x", "join parameter:Foo"),
        (f"{T} | join Kind = Bogus ({T}) on x", "join kind:Bogus"),
        (f"{T} | lookup Foo = bar ({T}) on x", "lookup parameter:Foo"),
        (f"{T} | lookup Kind = Bogus ({T}) on x", "lookup kind:Bogus"),
        (f"union Foo = 1 ({T}), ({T})", "union parameter:Foo"),
        (f"union Kind = Bogus ({T}), ({T})", "union kind:Bogus"),
        (f"{T} | summarize isfuzzy = true count() by x", "summarize parameter:isfuzzy"),
        (f"{T} | summarize hint.strategy = Broadcast count() by x",
         "summarize hint.strategy:Broadcast"),
        (f"{T} | mv-expand kind=bogus a", "mv-expand parameter:kind=bogus"),
    ],
)
def test_a_refusal_quotes_the_parameter_as_written(query: str, construct: str) -> None:
    assert _refused(query) == construct


def test_macro_expand_quotes_the_name_folded() -> None:
    query = "macro-expand Bogus = 1 Sources as S (S.Events | count)"
    assert _refused(query, entity_groups=GROUPS) == "macro-expand parameter:bogus"


@pytest.mark.parametrize(
    "query",
    [
        f"{T} | join Kind = Inner ({T}) on x",
        f"{T} | lookup Kind = Inner ({T}) on x",
        f"union Kind = Inner ({T}), ({T})",
        f"{T} | summarize hint.strategy = Shuffle count() by x",
        f"{T} | mv-expand kind=Array a",
    ],
)
def test_these_spellings_are_accepted(query: str) -> None:
    """What this translator accepts today, not a claim about Kusto."""
    assert "SELECT" in str(duckdb_kql.to_sql(query))
