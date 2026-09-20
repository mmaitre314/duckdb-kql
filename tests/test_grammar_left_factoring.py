"""L5 trap tests — the two left-factored rules, and the shape they produce.

A tester profiled a cold first parse at 10.8s on their machine and traced 81%
of it to two grammar decisions. Both are the same ALL(*) worst case: upstream
writes several alternatives that begin with the *same* rule, so ANTLR must
parse an entire expression before the operator token tells it which alternative
it is in, and it builds a large DFA doing so. Measured here across the frozen
corpus, before the patch:

    equalityExpression              7.61s   42% of all prediction time
    functionCallOrPathExpression    3.29s   18%

`PATCH duckdb-kql/002` and `/003` parse the shared prefix once. Measured on the
same box: a first parse went 0.808s -> 0.138s, and the corpus warm-up cost —
the one-time DFA construction — went ~16.6s -> 1.81s.

**Two things make this worth a test rather than a comment.** The first is that
nothing else would notice it going away: `grammar/UPSTREAM.md` tells the next
maintainer to re-apply each patch after an upstream bump, and a patch that is
quietly dropped leaves a translator that is correct and six times slower to
start. Timing cannot be the guard — it is the symptom, and asserting on it buys
a flaky build — so the test reads the grammar and checks the *shape*.

The second is the half that is not about speed at all. Left-factoring moves the
left operand onto the parent rule, so `a == b` lowers from two nodes rather
than one. Getting that wrong does not fail loudly: `_lower_binary` folds what it
is given, so a lost left operand returns the *right* operand alone, and `where s
== "abc"` becomes `where s` — a query that runs, returns rows, and is wrong.
That is not hypothetical; it is in `_lower_binary`'s own docstring, from the
last time this shape changed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import duckdb_kql

GRAMMAR = Path("grammar/Kql.g4")


def rule(name: str) -> str:
    text = GRAMMAR.read_text(encoding="utf-8")
    match = re.search(rf"^{name}:(.*?)^\s*;", text, re.S | re.M)
    assert match, f"grammar rule {name} is gone — did an upstream re-sync drop a patch?"
    return match.group(1)


def test_equality_parses_its_left_operand_once() -> None:
    """PATCH 002. Upstream repeats `relationalExpression` in four alternatives."""
    body = rule("equalityExpression")

    assert body.count("relationalExpression") <= 1, (
        "equalityExpression names relationalExpression more than once, so every "
        "alternative shares a prefix and ALL(*) must look ahead over a whole "
        "expression to choose. See PATCH duckdb-kql/002 in grammar/UPSTREAM.md"
    )
    # The alternatives moved to a tail, under the names upstream gave the rules.
    tail = rule("equalityExpressionTail")
    for label in ("equalsEqualityExpression", "listEqualityExpression",
                  "betweenEqualityExpression"):
        assert f"# {label}" in tail, f"{label} lost its label; the lowerer reads it"


def test_path_parses_its_root_once() -> None:
    """PATCH 003. Upstream spells it `root` and `root (operation)+`."""
    body = rule("functionCallOrPathExpression")

    assert body.count("functionCallOrPathRoot") <= 1, (
        "functionCallOrPathExpression names functionCallOrPathRoot more than "
        "once. See PATCH duckdb-kql/003 in grammar/UPSTREAM.md"
    )
    assert "# functionCallOrPathPathExpression" in body


@pytest.mark.parametrize(
    ("kql", "expected"),
    [
        # The loud half: the left operand must survive the move to the parent.
        ("T | where s == 'abc'", '"s" = \'abc\''),
        ("T | where s != 'abc'", '"s" <> \'abc\''),
        ("T | where n < 3", '"n" < CAST(3 AS BIGINT)'),
        ("T | where n between (1 .. 3)", '"n" BETWEEN CAST(1 AS BIGINT)'),
        ("T | where s in ('a', 'b')", '"s" IN'),
        ("T | where s !in ('a', 'b')", "NOT"),
    ],
)
def test_the_left_operand_reaches_the_sql(kql: str, expected: str) -> None:
    """Each of these silently drops to the right operand if `Left` is lost."""
    sql = str(duckdb_kql.to_sql(kql, schema={"T": ["s", "n"]}))

    assert expected in sql, sql


def test_a_bare_tail_is_refused_rather_than_half_lowered() -> None:
    """The tails carry no left operand, so lowering one alone must not answer.

    Reached only through `_lower_equality`, which puts the left back. If a
    future change routes one here directly, refusing is the difference between
    a loud failure and `where s == 'abc'` quietly meaning `where s`.
    """
    from duckdb_kql import lower as lower_module
    from duckdb_kql.errors import KqlUnsupportedError

    class FakeTail:
        pass

    FakeTail.__name__ = "EqualsEqualityExpressionContext"
    with pytest.raises(KqlUnsupportedError, match="binary-expression"):
        lower_module._lower_expr(FakeTail())


def test_a_column_path_with_no_steps_still_collapses() -> None:
    """PATCH 003 routes every root through the loop, including with zero steps.

    `_collapse` skips a context holding exactly one child, which is what makes
    the patch free: `a` and `a.b[0]` both reach the lowerer as they did before.
    """
    plain = str(duckdb_kql.to_sql("T | project s", schema={"T": ["s"]}))
    stepped = str(duckdb_kql.to_sql("T | project d.a[0]", schema={"T": ["d"]}))

    assert '"s" AS "s"' in plain
    assert "json_extract(\"d\", '$.a[0]')" in stepped, stepped
