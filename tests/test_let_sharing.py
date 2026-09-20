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

import duckdb_kql
from duckdb_kql.lower import lower

duckdb = pytest.importorskip("duckdb")


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


# --------------------------------------------------------------------------
# Binding the repeat: translate/sharing.py
# --------------------------------------------------------------------------
#
# Keeping the sharing in the IR is only half of it. The emitter still walked the
# DAG as a tree, so the *SQL* stayed exponential: 1.39 MB for the report's ten
# steps, 101 MB at twenty. A repeated node now gets a column of its own in a
# derived table under the operator's FROM, and the references read the column.
#
# Three encodings were measured over a million rows before that one was picked
# (chain depth 20): inline is 101 MB of SQL; one SELECT whose later items read
# earlier aliases is 2.1 KB and no faster, because DuckDB resolves a lateral
# alias by substituting it and arrives back where it started; a scalar subquery
# per binding is 2.2 KB and slower still, being correlated. The nested derived
# table is 2.1 KB and 0.38s, against 4.3s for inline at depth *14*.


ROWS = "datatable(value:string)['EXAMPLE.INVALID', '', 'Mixed.Case']"

#: A table and a function whose local `let` is read twice — the smallest shape
#: that binds. `tolower(v)` renders to about 240 characters of `typeof` dispatch
#: (see `render_kql_tostring`), so one saved copy clears the 200-character
#: threshold on its own.
T = "datatable(s:string, n:long)['Abc', 1, '', 2, 'ZZ', 3]"
F = "let f = (v:string) { let a = tolower(v); iff(a == '', 'empty', a) };"

#: What the emulator answers for `chain(d)` over ROWS, at every depth tried.
CHAIN_ANSWER = [("example.invalid",), ("",), ("mixed.case",)]


def sized(depth: int) -> str:
    return str(duckdb_kql.to_sql(chain(depth).replace("Input", ROWS)))


@pytest.mark.parametrize("depth", [2, 6, 10, 20])
def test_the_sql_grows_linearly_with_the_chain(depth: int) -> None:
    """The report's number. Before binding this was 2^depth copies of step0.

    The bound SQL is ~120 characters per step. The bound is loose on purpose —
    pinning the exact size would fail on any unrelated rewording of `tolower` —
    but no linear bound can be met by a doubling, which is the thing being
    measured. At depth 20 the unbound form is 101 MB.
    """
    assert len(sized(depth)) < 400 * depth + 800


def test_the_chain_still_answers_what_the_emulator_answers() -> None:
    """Measured, at depths 0, 1 and 10 — the answer does not depend on depth."""
    with duckdb_kql.connect() as con:
        for depth in (0, 1, 10):
            rows = duckdb_kql.kql(con, chain(depth).replace("Input", ROWS)).fetchall()
            assert rows == CHAIN_ANSWER, f"depth {depth}"


def test_a_bound_column_does_not_reach_the_output() -> None:
    """`where` selects `*`, and the binding is not one of the user's columns.

    Measured: `| where f(s) != 'empty' | project s, n` is `Abc, 1` and `ZZ, 3`.
    Without the EXCLUDE the query still answers those rows — with an extra
    column bolted on, which `project` then happens to discard. The test asks
    for the shape *before* the project for that reason.
    """
    query = f"{F} {T} | where f(s) != 'empty'"
    assert '_kqlbind0' in str(duckdb_kql.to_sql(query))  # the binding did happen

    with duckdb_kql.connect() as con:
        relation = duckdb_kql.kql(con, query)
        assert relation.columns == ["s", "n"]
        assert relation.fetchall() == [("Abc", 1), ("ZZ", 3)]


def test_extend_keeps_a_replaced_column_in_place() -> None:
    """The binding goes under the FROM, so the select list is unchanged.

    Measured: `extend s = f(s)` answers columns `s, n` — the replacement stays
    in position 0 — and `extend r = f(s)` appends, giving `s, n, r`.
    """
    with duckdb_kql.connect() as con:
        replaced = duckdb_kql.kql(con, f"{F} {T} | extend s = f(s)")
        assert replaced.columns == ["s", "n"]
        assert replaced.fetchall() == [("abc", 1), ("empty", 2), ("zz", 3)]

        appended = duckdb_kql.kql(con, f"{F} {T} | extend r = f(s)")
        assert appended.columns == ["s", "n", "r"]
        assert appended.fetchall() == [
            ("Abc", 1, "abc"), ("", 2, "empty"), ("ZZ", 3, "zz"),
        ]


def test_a_read_in_every_branch_is_needed_on_every_row() -> None:
    """`iff(c, a, a)` reads `a` exactly once per row, whichever way `c` goes.

    The guard is the *longest common prefix* of the paths that reach a node,
    and these two paths diverge at the first step, so the prefix is empty and
    the binding is unguarded. Getting this wrong in the safe direction — a
    guard per branch — would emit two bindings where one is needed; getting it
    wrong in the other direction is what the next test is about.
    """
    both = ("let k = (v:string) { let a = tolower(v);"
            " iff(v startswith 'A', a, a) };")
    sql = str(duckdb_kql.to_sql(f"{both} {T} | project r = k(s)", schema={}))

    assert sql.count('AS "_kqlbind') == 1
    assert "THEN NULL" not in sql, "bound behind a branch it does not need"
    with duckdb_kql.connect() as con:
        assert con.execute(sql).fetchall() == [("abc",), ("",), ("zz",)]


def test_a_read_only_inside_one_branch_is_bound_behind_that_branch() -> None:
    """The guard that keeps this a speedup rather than a correctness bug.

    A binding is a column, and a column is computed for every row whether or
    not the `iff` reading it takes that branch. Hoisting out of an untaken
    branch can turn an answer into a DuckDB error — not hypothetical: it is why
    `_render_parse_ipv4` carries `TRY_CAST`, after `parse_ipv4('1.2.3.4/x')`
    raised ConversionException where Kusto answers null.

    The first version of this module answered that by **refusing** to bind, and
    a tester found the hole immediately: wrapping the result of a ten-step
    chain in one `iff` put every step behind a branch and restored the full 2^n
    expansion — 1,024 copies and 366 KB. Rebuilding the branch around the
    binding keeps the protection and the sharing both.
    """
    one = ("let k = (v:string) { let a = tolower(v);"
           " iff(v startswith 'A', 'x', strcat(a, a)) };")
    sql = str(duckdb_kql.to_sql(f"{one} {T} | project r = k(s)", schema={}))

    assert sql.count('AS "_kqlbind') == 1
    assert "THEN NULL ELSE" in sql, "bound without the branch that selects it"
    with duckdb_kql.connect() as con:
        assert con.execute(sql).fetchall() == [("x",), ("",), ("zzzz",)]


def test_one_unconditional_read_is_enough_to_hoist() -> None:
    """The other half: a read the row already performs costs nothing to hoist.

    `iff(a == '', ...)` reads `a` in the **condition**, which every row
    evaluates, so computing it in a derived table adds no evaluation that was
    not already happening. Without this half the guard would refuse every
    binding the report is about, since `iff` is how the chain is written.
    """
    assert "_kqlbind0" in str(duckdb_kql.to_sql(f"{F} {T} | project r = f(s)"))


def test_a_volatile_binding_would_not_be_hoisted() -> None:
    """Pinned on the predicate, because `rand` has no mapping to test through.

    `let r = rand()` read three times is three numbers on the emulator. If that
    ever renders, it must render three times.
    """
    from duckdb_kql import ir
    from duckdb_kql.translate import sharing

    call = ir.FunctionCall("rand", ())
    assert not sharing._bindable(call)
    assert not sharing._bindable(ir.FunctionCall("strcat", (call, call)))
    assert sharing._bindable(ir.FunctionCall("tolower", (ir.ColumnRef("s"),)))


def test_nothing_is_bound_without_a_known_column_scope() -> None:
    """A column already called `_kqlbind0` answered in place of the binding.

    The stages carry the input through with `SELECT *`, so a column of the slot's
    name arrives beside it and DuckDB resolves the reference to the first of the
    two, silently. Measured, over a table with columns `s` and `_kqlbind0`:
    `project r = f(s)` answered that column's value. Where the columns *are*
    known the prefix lengthens until it is free, and where they are not, nothing
    is bound.
    """
    with duckdb_kql.connect() as con:
        con.execute('CREATE TABLE V(s VARCHAR, "_kqlbind0" VARCHAR)')
        con.execute("INSERT INTO V VALUES ('Ab', 'keep')")

        schemaless = str(duckdb_kql.to_sql(f"{F} V | project r = f(s)"))
        assert "_kqlbind" not in schemaless
        assert con.execute(schemaless).fetchall() == [("ab",)]

        # Layer 1 has the schema, so it binds — under a name nothing shadows.
        with_schema = str(
            duckdb_kql.to_sql(f"{F} V | project r = f(s)", schema={"V": ["s", "_kqlbind0"]})
        )
        assert '"__kqlbind0"' in with_schema
        assert con.execute(with_schema).fetchall() == [("ab",)]
        assert duckdb_kql.kql(con, f"{F} V | project r = f(s)").fetchall() == [("ab",)]


def test_a_nested_query_does_not_read_the_outer_binding() -> None:
    """The barrier. A nested query has its own FROM and no `_kqlbind0` in it."""
    from duckdb_kql.translate import sharing

    assert sharing._ACTIVE == [], "a scope leaked out of an earlier test"

    query = (
        f"{F} let Other = {T} | project s;\n"
        f"{T} | where f(s) != 'empty' | project r = f(s)"
    )
    with duckdb_kql.connect() as con:
        assert duckdb_kql.kql(con, query).fetchall() == [("abc",), ("zz",)]
    assert sharing._ACTIVE == [], "the scope stack is not balanced"


def test_the_emitted_shape_is_pinned() -> None:
    """The frozen corpus does not reach this path, so something has to.

    `tools/sql_snapshot.py` is the gate for everything else the emitter does,
    and it comes back byte-identical for this change — no corpus query has a
    repeat large enough to bind. That is the right outcome, and it leaves the
    new emission with no snapshot coverage at all, so the shape is written out
    here once, in full: one derived table under the operator's FROM, carrying
    the input through with `SELECT *`, and the select list reading the column.
    """
    fn = "let g = (m:long) { let a = m * 2 + 1; a + a + a + a + a + a };"
    sql = str(duckdb_kql.to_sql(f"{fn} T | project r = g(n)", schema={"T": ["n"]}))

    assert sql == (
        'WITH _s0 AS (SELECT * FROM "T"),\n'
        '     _s1 AS (SELECT ((((("_kqlbind0" + "_kqlbind0") + "_kqlbind0")'
        ' + "_kqlbind0") + "_kqlbind0") + "_kqlbind0") AS "r"'
        ' FROM (SELECT *, (("n" * CAST(2 AS BIGINT)) + CAST(1 AS BIGINT))'
        ' AS "_kqlbind0" FROM _s0))\n'
        "SELECT * FROM _s1"
    )


def _small_chain(depth: int) -> str:
    """A doubling chain whose *unit* is far too small to clear the threshold."""
    lets = ["let s0 = m + 1;"]
    lets += [f"let s{i} = s{i - 1} + s{i - 1};" for i in range(1, depth + 1)]
    return (
        "let f = (m:long) { " + " ".join(lets) + f" s{depth} }};\n"
        "T | project r = f(n)"
    )


@pytest.mark.parametrize("depth", [5, 10, 20, 40])
def test_a_threshold_cannot_reintroduce_the_doubling(depth: int) -> None:
    """The threshold is greedy and per-node, which looks like a hole and is not.

    `a + a` with a short `a` saves too little to be worth a derived table, so
    nothing binds — and that is the exact shape the report is about. The reason
    it is still safe: the inlined text **doubles with it**, so after two or
    three steps one copy is over the threshold on its own and binds. The gap
    between bindings is therefore bounded by the threshold, and the growth is
    linear whatever the unit size. Measured: 415 characters at depth 5, 2,939 at
    depth 40, for a chain that would otherwise be 2^40 copies.
    """
    sql = str(duckdb_kql.to_sql(_small_chain(depth), schema={"T": ["n"]}))

    assert len(sql) < 100 * depth + 400


def test_the_small_chain_still_computes_the_right_number() -> None:
    """A size bound proves nothing if the arithmetic stopped being right."""
    with duckdb_kql.connect() as con:
        con.execute("CREATE TABLE T(n BIGINT)")
        con.execute("INSERT INTO T VALUES (1)")
        rows = duckdb_kql.kql(con, _small_chain(20)).fetchall()

    assert rows == [(2 * 2**20,)]


def test_the_guard_keeps_an_untaken_branch_from_raising() -> None:
    """The mechanism, on an expression that *does* raise, since KQL's do not.

    Almost everything this translator emits is total by construction (R1), so
    a KQL query cannot easily demonstrate the hazard the guard exists for —
    which is exactly how `parse_ipv4` shipped broken. DuckDB's `error()` makes
    it visible: it raises when evaluated and is skipped inside an untaken CASE
    arm. Hoisting it into a select list evaluates it; hoisting it *under the
    rebuilt branch* does not.

    The NULL arm is the part that looks redundant. `CASE WHEN NOT c THEN …`
    reads the same and is not: a null predicate takes KQL's ELSE, `NOT null`
    is null, and the binding would then skip an evaluation the query performs.
    The third case below is that difference.
    """
    with duckdb_kql.connect() as con:
        con.execute("CREATE TABLE T(n BIGINT)")
        con.execute("INSERT INTO T VALUES (1), (2)")

        # A row-dependent predicate on a real table, because `WHEN TRUE` lets
        # DuckDB fold the arm away and prune the column — which makes the
        # unguarded version pass and proves nothing.
        assert con.execute(
            "SELECT CASE WHEN n > 0 THEN 'safe' ELSE error('boom') END FROM T"
        ).fetchall() == [("safe",), ("safe",)]

        # hoisted without a guard — the bug the guard prevents
        with pytest.raises(duckdb.Error, match="boom"):
            con.execute(
                "SELECT CASE WHEN n > 0 THEN 'safe' ELSE b END"
                " FROM (SELECT *, error('boom') AS b FROM T)"
            ).fetchall()

        # hoisted under the rebuilt branch — evaluated on exactly the old rows
        assert con.execute(
            "SELECT CASE WHEN n > 0 THEN 'safe' ELSE b END FROM (SELECT *,"
            " CASE WHEN n > 0 THEN NULL ELSE error('boom') END AS b FROM T)"
        ).fetchall() == [("safe",), ("safe",)]

        # and a NULL predicate still reaches the ELSE, as KQL's `iff` does
        assert con.execute(
            "SELECT CASE WHEN NULLIF(n, n)::BOOLEAN THEN 'no' ELSE b END"
            " FROM (SELECT *, CASE WHEN NULLIF(n, n)::BOOLEAN THEN NULL"
            " ELSE 'reached' END AS b FROM T)"
        ).fetchall() == [("reached",), ("reached",)]


def wrapped(depth: int, conditional: bool) -> str:
    """The tester's shape: the chain, optionally behind one final `iff`."""
    lets = ["let step0 = tolower(value);"]
    for index in range(1, depth + 1):
        previous = f"step{index - 1}"
        lets.append(f"let step{index} = iff({previous} == '', '', {previous});")
    result = f"step{depth}"
    if conditional:
        result = f"iff(value == 'skip', '', {result})"
    return (
        "let normalize = (value:string) {\n" + "\n".join(lets) + f"\n{result}\n}};\n"
        "Input | project result = normalize(value)"
    )


@pytest.mark.parametrize("depth", [4, 8, 10, 20])
@pytest.mark.parametrize("conditional", [False, True])
def test_a_conditional_result_does_not_restore_the_doubling(
    depth: int, conditional: bool
) -> None:
    """The regression the tester reported, in both shapes.

    Before the guard: depth 8 was 91,607 characters and depth 10 was 365,783,
    with 256 and 1,024 copies of `lower(...)`. The unconditional form was
    already 1,334 and 1,567 with one copy — the wrapper was the whole
    difference. A count of `lower(` is the stable signal; the character bound
    is loose enough to survive an unrelated rewording.
    """
    sql = str(duckdb_kql.to_sql(wrapped(depth, conditional), schema={"Input": ["value"]}))

    assert sql.lower().count("lower(") == 1
    assert len(sql) < 400 * depth + 1200


@pytest.mark.parametrize("value", ["skip", "", "EXAMPLE.INVALID"])
def test_the_conditional_shape_answers_what_the_emulator_answers(value: str) -> None:
    """Measured on the emulator for all three inputs, at depths 0, 1 and 10.

    `skip` takes the wrapper's THEN, so the whole chain is behind the branch
    that is *not* taken — the row where a wrongly-hoisted binding would show.
    """
    expected = {"skip": "", "": "", "EXAMPLE.INVALID": "example.invalid"}[value]
    rows = f"datatable(value:string)['{value}']"

    with duckdb_kql.connect() as con:
        for depth in (0, 1, 10):
            query = wrapped(depth, conditional=True).replace("Input", rows)
            assert duckdb_kql.kql(con, query).fetchall() == [(expected,)], (
                f"value={value!r} depth={depth}"
            )
