"""L5 trap tests — a chain of `let`s must not lower to 2^n nodes.

A performance report translated 13 KB of KQL into 1.39 MB of SQL. The KQL was
ten scalar `let` bindings, each reading the one before it twice::

    let step1 = iff(step0 == '', '', step0);
    let step2 = iff(step1 == '', '', step1);
    ...

Two references per step is 2^10 = 1,024 copies of `step0` by the end. That part
is inherent to substitution and is what `test_a_let_is_substituted_not_shared`
below pins as *correct*: Kusto's scalar `let` is substitutional, measured — with
``let r = rand()`` referenced three times the emulator answers three different
numbers, so a translator that evaluated the binding once would be wrong.

What was **not** inherent is that the lowerer paid the 2^n twice over. A
`ColumnRef` is replaced by the object the binding holds, so the IR is a DAG: one
node for `step9`, reached by two edges. `_substitute` walks that DAG once per
path, so it rebuilt every shared node once per reference and handed the emitter
a tree — 3,095 IR nodes for a ten-step chain, 49,183 for fourteen. The memo in
`_substitute` keeps the sharing.

The trap is that removing the memo breaks **nothing visible**. Every test still
passes, every answer is still right, the snapshot is byte-identical; the query
just gets exponentially more expensive to translate, on inputs no unit test is
big enough to notice. Hence a test that counts nodes.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from duckdb_kql.lower import lower


def chain(depth: int) -> str:
    """The report's shape: `depth` bindings, each reading the previous twice."""
    lets = ["let step0 = tolower(value);"]
    for index in range(1, depth + 1):
        previous = f"step{index - 1}"
        lets.append(f"let step{index} = iff({previous} == '', '', {previous});")
    body = "\n".join(lets)
    return (
        f"let normalize = (value:string) {{\n{body}\nstep{depth}\n}};\n"
        "Input | project result = normalize(value)"
    )


def _node_counts(node: Any, counts: dict[int, int] | None = None) -> dict[int, int]:
    """How many edges reach each IR node, by identity — not by equality.

    Equality would merge nodes that are merely alike and report sharing that is
    not there, which is the opposite of the thing being measured.
    """
    counts = {} if counts is None else counts
    if isinstance(node, (list, tuple)):
        for item in node:
            _node_counts(item, counts)
        return counts
    if not dataclasses.is_dataclass(node):
        return counts
    counts[id(node)] = counts.get(id(node), 0) + 1
    if counts[id(node)] > 1:
        return counts  # already descended through it once
    for field in dataclasses.fields(node):
        _node_counts(getattr(node, field.name), counts)
    return counts


@pytest.mark.parametrize("depth", [2, 6, 10, 14])
def test_the_lowered_ir_grows_linearly_with_the_chain(depth: int) -> None:
    counts = _node_counts(lower(chain(depth)))

    # Four nodes per step, and room to spare; the point is the *shape* of the
    # growth, not the constant. Without the memo this is 14, 207, 3095, 49183.
    assert len(counts) < 8 * depth + 40, (
        f"{len(counts)} IR nodes for a {depth}-step chain — substitution has "
        "stopped sharing, and the cost is now exponential in the chain length"
    )


def test_the_sharing_is_real_and_not_an_artefact_of_counting() -> None:
    """A guard on the guard: the counter must be able to *see* sharing."""
    counts = _node_counts(lower(chain(6)))

    assert max(counts.values()) == 2, (
        "each binding is read twice, so some node must be reached twice; if "
        "every count is 1 the test above passes for the wrong reason"
    )


def test_a_let_is_substituted_not_shared() -> None:
    """The half that must not change, and why sharing can never be blind.

    Measured on the emulator: ``let r = rand(); ... | project a = r, b = r, c = r``
    answers **three different numbers**, and ``rand() == rand()`` is false. A
    scalar `let` is a macro, not one evaluation, so for a nondeterministic body
    the duplication *is* the semantics.

    (`new_guid()` looks like a counter-example and is not: two separate calls to
    it compare equal within a row, so it says nothing about `let`. That is the
    probe that nearly answered this question backwards.)

    Nothing in this test needs `rand` to work, because it does not: no volatile
    KQL function has a DuckDB mapping today, which is the only reason the
    emitter may currently share a repeated sub-expression at all. This test
    fails the day one is added, so the sharing rule gets revisited rather than
    silently turning three draws into one.
    """
    from duckdb_kql.translate.functions import lookup

    unmapped = [n for n in ("rand", "new_guid", "ingestion_time", "current_principal")
                if lookup(n) is None]
    assert unmapped == ["rand", "new_guid", "ingestion_time", "current_principal"], (
        "a volatile function gained a mapping. Two references to a `let` that "
        "calls it must still emit two calls — see this test's docstring"
    )

    # `now()` is the one volatile-*looking* mapping, and it is not volatile in
    # the sense that matters: Kusto fixes it for the query, DuckDB for the
    # transaction. Sharing it is sound, and this records that it was checked.
    assert lookup("now") is not None
