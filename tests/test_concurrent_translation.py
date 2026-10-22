"""Trap tests — two threads translating at once see only their own statement.

Measured: four threads translating different corpus queries concurrently
returned SQL that differed from the serial translation for 30 of 1,528 calls,
with no error, and crashed 12 (`'NoneType' object has no attribute 'scopes'`).
Nothing in DuckDB is involved — `to_sql` alone does it, on every DuckDB
version. The translator kept each statement's state in module globals: the
CTE-name table, the clock, the sharing scopes, the graph match, and lowering's
`let` functions, entity groups, macro scope and stored-function scope. A
second statement read the first one's.

The state is now per context (`contextvars`), so each thread — and each
asyncio task — has its own. The obvious fix, one lock around translation, was
not taken: it is correct only while every entry point remembers to take it,
and it serializes translation on a free-threaded interpreter for no reason.

The race is forced rather than hoped for: thread A is paused at a point where
its state is live, and B translates from start to finish meanwhile. Before the
fix, B took A's CTE-name table for its own and named its CTEs as A's nested
statement would, and a `macro-expand` in B was refused as nested inside A's.
"""

from __future__ import annotations

import ast
import asyncio
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

import duckdb_kql
from duckdb_kql import lower, translate
from duckdb_kql._state import State
from duckdb_kql.translate import graph, sharing

A = "let t = datatable(a:long)[1]; t | extend b = a + 2"
B = "let u = datatable(c:long)[5]; let v = u | project d = c * 3; v | union u"
MACRO = "macro-expand entity_group [database('d1'), database('d2')] as s (s.MT | count)"
OTHER_MACRO = "macro-expand entity_group [database('d2')] as e (e.MT | take 1)"


def _paused_in(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    name: str,
    first: str,
    second: str,
    schema: Any = None,
) -> tuple[str, str]:
    """Translate *first* in a thread paused inside ``module.name``, and *second*
    in this thread while it waits; return both results."""
    real: Callable[..., Any] = getattr(module, name)
    paused, resume = threading.Event(), threading.Event()

    def hooked(*args: Any, **kwargs: Any) -> Any:
        if threading.current_thread().name == "first" and not paused.is_set():
            paused.set()
            assert resume.wait(10), "the second translation never finished"
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, hooked)
    result: dict[str, Any] = {}

    def run() -> None:
        try:
            result["first"] = str(duckdb_kql.to_sql(first, schema=schema))
        except Exception as exc:  # noqa: BLE001 - reported by the assertion below
            result["first"] = exc

    thread = threading.Thread(target=run, name="first")
    thread.start()
    try:
        assert paused.wait(10), f"{first!r} never reached {name}"
        try:
            result["second"] = str(duckdb_kql.to_sql(second, schema=schema))
        except Exception as exc:  # noqa: BLE001 - reported by the assertion below
            result["second"] = exc
    finally:
        resume.set()
        thread.join(10)
    return result["first"], result["second"]


def test_a_statement_mid_render_does_not_capture_another() -> None:
    expected = (str(duckdb_kql.to_sql(A)), str(duckdb_kql.to_sql(B)))
    with pytest.MonkeyPatch.context() as mp:
        got = _paused_in(mp, translate, "render_literal", A, B)
    assert got == expected


def test_a_macro_expand_mid_lowering_does_not_nest_another() -> None:
    duckdb = pytest.importorskip("duckdb")
    from duckdb_kql.engine import schema

    con = duckdb.connect()
    for database in ("d1", "d2"):
        con.execute(f"ATTACH ':memory:' AS {database}")
        con.execute(f"CREATE TABLE {database}.MT(x BIGINT, s VARCHAR)")
    tables = schema(con)
    expected = (
        str(duckdb_kql.to_sql(MACRO, schema=tables)),
        str(duckdb_kql.to_sql(OTHER_MACRO, schema=tables)),
    )
    with pytest.MonkeyPatch.context() as mp:
        got = _paused_in(mp, lower, "_lower_scoped_table", MACRO, OTHER_MACRO, tables)
    assert got == expected


FUNCTIONS = ["function Fa() { print v = 'a' }", "function Fb() { print v = 'b' }"]
NAMED = {"NB": ["database('d2')"]}
EDGES_A = "let E = datatable(s:long, t:long)[1,2, 2,3];"
EDGES_B = "let F = datatable(u:string, v:string)['x','y'];"


def _in_a_sharing_scope() -> bool:
    return bool(sharing._ACTIVE_STATE.get())


#: (what the pair exercises, module, function to pause in, first, second,
#: options, and when to pause — the first call, unless a predicate says later)
CROSSED: list[tuple[str, Any, str, str, str, dict[str, Any], Callable[[], bool] | None]] = [
    ("the clock and the stored functions", lower, "_lower_head",
     "set query_now = datetime(2020-01-01); Fa | extend n = now()",
     "set query_now = datetime(2021-06-15); Fb | extend n = now()",
     {"functions": FUNCTIONS}, None),
    ("let-bound and named entity groups", lower, "_lower_head",
     "let G = entity_group [database('d1')]; macro-expand G as s (s.MT | count)",
     "macro-expand NB as s (s.MT | take 1)",
     {"entity_groups": NAMED}, None),
    ("graph-valued lets", lower, "_lower_head",
     f"{EDGES_A} let g = E | make-graph s --> t with_node_id=id;"
     " g | graph-match (a)-->(b) project a.id, b.id",
     f"{EDGES_B} let h = F | make-graph u --> v with_node_id=k;"
     " h | graph-match (p)-->(q) project q.k",
     {}, None),
    ("the graph match being rendered", graph, "_render",
     f"{EDGES_A} E | make-graph s --> t with_node_id=id | graph-match (a)-->(b) project a.id",
     f"{EDGES_B} F | make-graph u --> v with_node_id=k | graph-match (p)-->(q) project q.k",
     {}, None),
    ("let-declared functions", lower, "_lower_head",
     "let F = (x:long) { x + 1 }; print F(1)",
     "let K = (y:long) { y * 2 }; print K(3)",
     {}, None),
    ("the sharing scopes", sharing, "slot_for",
     "let f = (v:string) { let a = tolower(v); iff(a == '', 'empty', a) };"
     " datatable(s:string)['Abc', ''] | project r = f(s)",
     "let g = (w:string) { let b = toupper(w); iff(b == 'X', 'ex', b) };"
     " datatable(s:string)['x', 'Y'] | project q = g(s)",
     {}, _in_a_sharing_scope),
]


@pytest.fixture(scope="module")
def tables() -> Any:
    duckdb = pytest.importorskip("duckdb")
    from duckdb_kql.engine import schema

    con = duckdb.connect()
    for database in ("d1", "d2"):
        con.execute(f"ATTACH ':memory:' AS {database}")
        con.execute(f"CREATE TABLE {database}.MT(x BIGINT, s VARCHAR)")
    return schema(con)


@pytest.mark.parametrize(
    ("module", "name", "first", "second", "options", "when"),
    [case[1:] for case in CROSSED],
    ids=[case[0] for case in CROSSED],
)
def test_two_statements_whose_lifetimes_cross(
    tables: Any,
    module: Any,
    name: str,
    first: str,
    second: str,
    options: dict[str, Any],
    when: Callable[[], bool] | None,
) -> None:
    """A starts, B starts, A finishes while B is still running.

    Nesting alone could not show most of the state: each piece was saved and
    restored, so a statement translated entirely inside another's pause put the
    other's back on its way out. Crossed lifetimes undo that — A finishing reset
    the shared value to what it was before A, which was nothing, while B was
    still using it, and A had meanwhile read B's. Each pair exercises the state
    its id names: before the fix, B lost its stored functions and its pinned
    clock while A rendered `now()` at B's moment; a `let`-bound or named group
    went unmapped; a graph `let` or a `let` function was lost; a graph
    expression was refused as outside any graph operator; a shared binding was
    looked up in the other statement's scope.
    """

    def translate_one(kql: str) -> tuple[str, dict[str, Any]]:
        result = duckdb_kql.to_sql(kql, schema=tables, **options)
        return str(result), dict(result.parameters)

    expected = (translate_one(first), translate_one(second))
    events = {n: (threading.Event(), threading.Event()) for n in ("first", "second")}
    real: Callable[..., Any] = getattr(module, name)
    paused_once: set[str] = set()

    def hooked(*args: Any, **kwargs: Any) -> Any:
        thread = threading.current_thread().name
        if thread in events and thread not in paused_once and (when is None or when()):
            paused_once.add(thread)
            paused, resume = events[thread]
            paused.set()
            assert resume.wait(10), f"{thread} was never resumed"
        return real(*args, **kwargs)

    got: dict[str, Any] = {}

    def run(thread: str, kql: str) -> None:
        try:
            got[thread] = translate_one(kql)
        except Exception as exc:  # noqa: BLE001 - reported by the assertion below
            got[thread] = exc

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(module, name, hooked)
        a = threading.Thread(target=run, args=("first", first), name="first")
        b = threading.Thread(target=run, args=("second", second), name="second")
        a.start()
        assert events["first"][0].wait(10), f"the first statement never reached {name}"
        b.start()
        assert events["second"][0].wait(10), f"the second statement never reached {name}"
        events["first"][1].set()
        a.join(10)
        events["second"][1].set()
        b.join(10)
    assert (got["first"], got["second"]) == expected


@pytest.fixture
def frequent_switches() -> Iterator[None]:
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(interval)


def _corpus_queries(count: int) -> list[str]:
    sys.path.insert(0, str(Path("tools").resolve()))
    try:
        import sql_snapshot
    finally:
        sys.path.pop(0)
    return [kql for _, kql in sql_snapshot._cases()][:count]


def _translate_one(kql: str) -> str:
    try:
        return str(duckdb_kql.to_sql(kql))
    except Exception as exc:  # noqa: BLE001 - a refusal is an answer too
        return f"!! {type(exc).__name__}: {exc}"


def test_concurrent_translations_match_serial_ones(frequent_switches: None) -> None:
    """A net for interleavings nobody chose: corpus queries, four threads at
    once, each from a different offset.

    It is not proof per state — the forced interleavings above are. These
    queries use no schema, stored function, group, `macro-expand` or `now()`,
    so on its own this catches a shared CTE-name table or sharing scope and
    little else; measured, making any other single state shared again left it
    passing. With all of them shared, it failed in every one of five runs.
    """
    queries = _corpus_queries(60)
    serial = {q: _translate_one(q) for q in queries}
    mismatches: list[str] = []

    def run(offset: int) -> None:
        for i in range(len(queries)):
            q = queries[(i + offset) % len(queries)]
            if _translate_one(q) != serial[q]:
                mismatches.append(q)

    threads = [threading.Thread(target=run, args=(k * 71,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not mismatches, f"{len(mismatches)} translations differed, first: {mismatches[0]!r}"


def test_threads_that_inherit_a_context_translate_alone() -> None:
    """`asyncio.to_thread` runs each call in a copy of the caller's context — as
    a free-threaded build starts every thread — so whatever the caller had
    cached is inherited, the defaults `State.get` creates included. Every
    default is cached here first, so the workers start from shared objects.
    """
    for module in (lower, translate, sharing, graph):
        for value in vars(module).values():
            if isinstance(value, State):
                value.get()
    queries = _corpus_queries(60)
    serial = [_translate_one(q) for q in queries]

    async def main() -> list[str]:
        calls = (asyncio.to_thread(_translate_one, q) for q in queries * 4)
        return list(await asyncio.gather(*calls))

    assert asyncio.run(main()) == serial * 4


#: Module globals shared by every thread on purpose: the process-wide defaults
#: that `set_clusters`, `set_entity_groups` and `set_functions` document as such.
PROCESS_WIDE = {
    "clusters.py": {"_DEFAULT"},
    "entity_groups.py": {"_DEFAULT"},
    "stored_functions.py": {"_DEFAULT"},
}
_MUTATORS = frozenset({
    "append", "add", "update", "clear", "pop", "popitem", "setdefault", "extend",
    "insert", "remove", "discard",
})


def test_no_statement_state_is_a_module_global() -> None:
    """The guard the fix needs: state added the old way fails here, not in a race.

    A function that rebinds a module global (`global X`) or mutates one in place
    — a container method, an item or attribute assignment — is per-statement
    state shared by every thread. Per-statement state is a `duckdb_kql._state.State`;
    process-wide configuration is listed by name above.
    """
    offenders: list[str] = []
    for path in sorted(Path("src/duckdb_kql").rglob("*.py")):
        if "_antlr" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module_names = {
            target.id
            for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name)
        }
        allowed = PROCESS_WIDE.get(path.name, set())
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            local = {a.arg for a in ast.walk(fn.args) if isinstance(a, ast.arg)} | {
                n.id
                for n in ast.walk(fn)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
            }
            shared = module_names - local
            for node in ast.walk(fn):
                where = f"{path}:{getattr(node, 'lineno', '?')}"
                if isinstance(node, ast.Global):
                    offenders += [f"{where} global {n}" for n in node.names if n not in allowed]
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in shared
                    and node.func.attr in _MUTATORS
                ):
                    offenders.append(f"{where} {node.func.value.id}.{node.func.attr}(...)")
                elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete)):
                    multiple = isinstance(node, (ast.Assign, ast.Delete))
                    targets = node.targets if multiple else [node.target]
                    offenders += [
                        f"{where} {t.value.id} assigned into"
                        for t in targets
                        if isinstance(t, (ast.Subscript, ast.Attribute))
                        and isinstance(t.value, ast.Name)
                        and t.value.id in shared
                    ]
    assert not offenders, "\n".join(offenders)
