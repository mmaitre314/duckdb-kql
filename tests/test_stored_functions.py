"""Stored functions, registered locally with ``functions=`` (TRANSLATION.md R24).

The feature request: register ``ReadEvents()`` against local fixture tables so a
query written against it runs unchanged. docs/stored-functions-proposal.md has
the design and every measurement quoted here, taken on the emulator on
2026-09-27 and -29 against two databases; a sweep of 56 queries through the same
definitions on both engines agreed on all of them, refusals included, each with
the same SEM code, and 15 more on `withsource` labels.

The measurement that decides the design: a stored function is a **macro
expanded at the call site**. A caller's `let` captures a name inside its body —
`let FpT = <100>; FpF()` answers 100 where `FpF() { FpT }` alone reads the
table's 7 — while a query-local `let F = () { FpT }` is lexical and answers 7.
So the obvious implementation, each function as an implicit `let` prepended to
the query, answers 7 where Kusto answers 100. Each call is lowered afresh, into
a binding placed just before whatever holds the call.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlSchemaError, KqlUnsupportedError

duckdb = pytest.importorskip("duckdb")

READ = ".create-or-alter function ReadEvents() { Events }"

#: The first line is the export form, as `.show database D schema as csl script`
#: prints it — one line, which is why it is assembled rather than written out.
EXPORTED = (
    '.create-or-alter function with (folder = "Tests", docstring = "Reads it", '
    'skipvalidation = "true") FpF() { FpT }'
)

DEFINITIONS = EXPORTED + """

.create-or-alter function FpAbove(x:long) { FpT | where Value > x }
.create-or-alter function FpDef(x:long = 5) { FpT | where Value > x }
.create-or-alter function FpLets() { let k = 5; FpT | where Value > k }
.create-or-alter function FpP(Value:long) { FpT | extend k = Value }
.create-or-alter function FpQ(x:long) { FpT | extend k = x }
.create-or-alter function FpH() { FpF() | count }
.create-or-alter function FpSame() { print y = 2 }
"""


@pytest.fixture
def con():
    c = duckdb_kql.connect()
    c.execute("CREATE TABLE FpT (Value BIGINT); INSERT INTO FpT VALUES (7)")
    c.execute("CREATE TABLE Events (Value BIGINT); INSERT INTO Events VALUES (7)")
    yield c
    c.close()


def _rows(con, kql: str, functions=DEFINITIONS, **kwargs) -> list[tuple]:
    return duckdb_kql.kql(con, kql, functions=functions, **kwargs).fetchall()


def _one(con, kql: str, functions=DEFINITIONS):
    ((value,),) = _rows(con, kql, functions)
    return value


# ---------------------------------------------------------------------------
# The request's reproduction
# ---------------------------------------------------------------------------


def test_the_reported_reproduction_through_the_client() -> None:
    from duckdb_kql.kusto import KustoClient

    with duckdb_kql.connect() as connection:
        connection.execute("CREATE TABLE Events (Value BIGINT)")
        connection.execute("INSERT INTO Events VALUES (7)")
        with KustoClient(connection, functions=READ) as client:
            response = client.execute(None, "ReadEvents() | summarize Total = sum(Value)")
            assert response.primary_results[0][0]["Total"] == 7


def test_the_management_command_says_what_to_use_instead() -> None:
    """Out of scope (proposal §7), and the refusal names `functions=`."""
    from duckdb_kql.kusto import KustoClient, KustoUnsupportedError

    with KustoClient(duckdb_kql.connect()) as client:
        with pytest.raises(KustoUnsupportedError, match="functions="):
            client.execute(None, READ)


# ---------------------------------------------------------------------------
# Expanded at the call site
# ---------------------------------------------------------------------------


def test_a_callers_let_captures_a_name_in_the_body(con) -> None:
    """Kusto 100, not the table's 7 — the design-deciding measurement."""
    let = "let FpT = datatable(Value:long)[100]; "
    assert _one(con, let + "FpF() | summarize s = sum(Value)") == 100
    assert _one(con, let + "FpF | summarize s = sum(Value)") == 100
    assert _one(con, "FpF() | summarize s = sum(Value)") == 7


def test_only_a_let_declared_before_the_call_captures(con) -> None:
    """Placement is the rule: a binding after the call is not yet in scope (7)."""
    assert _one(
        con,
        "let X = FpF() | summarize s = sum(Value); "
        "let FpT = datatable(Value:long)[100]; X",
    ) == 7


def test_a_callers_let_differing_only_in_case_does_not_capture(con) -> None:
    """R7, through a function: `fpt` is not `FpT`."""
    assert _one(con, "let fpt = datatable(Value:long)[100]; FpF() | summarize s = sum(Value)") == 7


def test_a_callers_scalar_reaches_only_a_name_the_body_does_not_bind(con) -> None:
    """Dynamic scope covers scalars too, measured with `skipvalidation` — which
    Kusto's own export writes on every function, and which is how a registry
    that never sees the database behaves. The first version shielded bodies from
    the caller's scalars, on the reasoning that a validated body cannot read
    one; the emulator answers 5 here, and that version raised."""
    kx = ".create-or-alter function with (skipvalidation = 'true') FpG() { FpT | extend k = Kx }"
    assert _rows(con, "let Kx = 5; FpG()", kx) == [(7, 5)]
    # ... but a parameter, and the body's own `let`, bind over the caller's:
    # Kusto 3 and 1, whatever the caller says.
    assert _rows(con, "let x = 100; FpQ(3)") == [(7, 3)]
    assert _one(con, "let k = 100; FpLets() | count") == 1
    # ... and a column over all of them (R23): Kusto 7
    col = ".create-or-alter function FpC() { FpT | extend k = Value }"
    assert _rows(con, "let Value = 5; FpC()", col) == [(7, 7)]


def test_a_column_beats_a_parameter_of_the_same_name(con) -> None:
    """R23 inside a body: Kusto 7, not the argument 0."""
    assert _rows(con, "FpP(0)") == [(7, 7)]


# ---------------------------------------------------------------------------
# Calls, anywhere a table can appear
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kql", "expected"),
    [
        ("FpF() | summarize s = sum(Value)", 7),
        ("FpF | summarize s = sum(Value)", 7),
        ("let X = FpF(); X | summarize s = sum(Value)", 7),
        ("FpT | join kind=inner (FpF()) on Value | count", 1),
        ("union FpF(), FpT | count", 2),
        ("FpT | where Value in (FpF() | project Value) | count", 1),
        ("FpT | where Value in (FpF()) | count", 1),
        ("FpH()", 1),
        ("FpAbove(5) | count", 1),
        ("FpAbove(10) | count", 0),
        ("let n = 5; FpAbove(n) | count", 1),
        ("FpDef() | count", 1),
        ("FpDef | count", 1),
        ("FpDef(10) | count", 0),
    ],
)
def test_a_call_answers_as_kusto_does(con, kql, expected) -> None:
    assert _one(con, kql) == expected


def test_the_functions_columns_reach_a_join_downstream(con) -> None:
    """Schema discovery: a join renames by the function's output columns."""
    rel = duckdb_kql.kql(
        con, "FpT | join kind=inner (FpF() | extend tag = 'f') on Value", functions=DEFINITIONS
    )
    assert rel.columns == ["Value", "Value1", "tag"]
    assert _rows(con, "FpF() | getschema") == [("Value", 0, "System.Int64", "long")]


def test_a_body_may_bind_a_table_of_its_own(con) -> None:
    """A tabular `let` inside a body is scoped to it by the reserved CTE names
    (R7), and a join still learns the function's columns through it — which
    failed until `output_columns` read a query's own bindings."""
    local = ".create-or-alter function Loc() { let t = FpT | where Value > 0; t }"
    assert _rows(con, "FpT | join kind=inner (Loc()) on Value | project Value1", local) == [(7,)]
    assert _rows(con, "let t = datatable(Value:long)[100]; Loc() | count", local) == [(1,)]


def test_other_places_a_table_can_be(con, two_databases) -> None:
    words = ".create-or-alter function Words() { datatable(w:string)['a'] }"
    kql = "datatable(s:string)['a b', 'c'] | where s has_any (Words()) | count"
    assert _rows(con, kql, words) == [(1,)]
    assert _rows(con, "union database('FpOther').FpF(), FpT | count", two_databases) == [(2,)]


@pytest.mark.parametrize(
    ("body", "label"),
    [
        ("{ FpT }", "FpT"),
        ("{ let t = FpT; t }", "FpT"),
        ("{ FpF() }", "FpT"),
        ("{ FpT | where Value > 0 }", "union_arg0"),
        ("{ print Value = 1 }", "union_arg0"),
    ],
)
def test_withsource_labels_a_call_as_kusto_does(con, body, label) -> None:
    """Measured: a call is labelled with the table it only aliases, else
    positionally — the same rule as a `let` alias."""
    con.execute("CREATE TABLE FpU AS SELECT 8::BIGINT AS Value")
    functions = [f".create-or-alter function G() {body}", ".create-or-alter function FpF() { FpT }"]
    rows = _rows(con, "union withsource=Src G(), FpU | project Src", functions)
    assert {r[0] for r in rows} == {label, "FpU"}


def test_a_function_beats_a_table_of_the_same_name(con) -> None:
    """Kusto resolves a bare name to the function, whichever came first."""
    con.execute("CREATE TABLE FpSame AS SELECT 1::BIGINT AS x")
    assert duckdb_kql.kql(con, "FpSame", functions=DEFINITIONS).columns == ["y"]
    assert duckdb_kql.kql(con, "FpSame").columns == ["x"]


def test_an_ingestion_source_may_call_one(con) -> None:
    """Every branch of `to_sql` takes the registry — the ingestion branch is the
    one that dropped `entity_groups` once."""
    duckdb_kql.kql(con, ".set-or-replace Copy <| FpF()", functions=DEFINITIONS)
    assert con.execute("SELECT * FROM Copy").fetchall() == [(7,)]


def test_a_script_may_call_one(con) -> None:
    results = duckdb_kql.script(con, "FpF() | count\n\nFpDef | count", functions=DEFINITIONS)
    assert [r.rows for r in results] == [[(1,)], [(1,)]]


# ---------------------------------------------------------------------------
# Databases
# ---------------------------------------------------------------------------


@pytest.fixture
def two_databases(con):
    con.execute("ATTACH ':memory:' AS FpOther")
    con.execute("CREATE TABLE FpOther.FpT (Value BIGINT); INSERT INTO FpOther.FpT VALUES (50)")
    con.execute("CREATE TABLE FpOther.FpOnlyOther (Value BIGINT)")
    con.execute("INSERT INTO FpOther.FpOnlyOther VALUES (9)")
    return {
        None: [".create-or-alter function FpF() { FpT }"],
        "FpOther": [
            ".create-or-alter function FpF() { FpT }",
            ".create-or-alter function FpReadOnlyOther() { FpOnlyOther }",
            ".create-or-alter function FpInner() { FpT | where Value > 0 }",
            ".create-or-alter function FpOuter() { FpInner() }",
        ],
    }


@pytest.mark.parametrize(
    ("kql", "expected"),
    [
        ("FpF() | summarize s = sum(Value)", 7),
        ("database('FpOther').FpF() | summarize s = sum(Value)", 50),
        ("database('FpOther').FpF | summarize s = sum(Value)", 50),
        ("database('FpOther').FpReadOnlyOther() | summarize s = sum(Value)", 9),
        ("database('FpOther').FpOuter() | summarize s = sum(Value)", 50),
        # the caller's let still captures across databases: Kusto 100
        (
            "let FpT = datatable(Value:long)[100]; "
            "database('FpOther').FpF() | summarize s = sum(Value)",
            100,
        ),
    ],
)
def test_same_named_functions_resolve_in_their_own_database(
    con, two_databases, kql, expected
) -> None:
    assert _one(con, kql, two_databases) == expected


def test_a_function_of_another_database_is_not_called_unqualified(con, two_databases) -> None:
    """Kusto: SEM0260 — never a table, and never the other database's."""
    with pytest.raises(KqlUnsupportedError, match="FpReadOnlyOther"):
        _rows(con, "FpReadOnlyOther() | count", two_databases)


def test_a_call_through_cluster_goes_through_the_map_or_nowhere(con, two_databases) -> None:
    kql = "cluster('prod').database('Other').FpF() | summarize s = sum(Value)"
    with pytest.raises(KqlSchemaError, match="cluster"):
        _rows(con, kql, two_databases)
    assert (
        _rows(con, kql, two_databases, clusters={("prod", "Other"): "FpOther"}) == [(50,)]
    )


# ---------------------------------------------------------------------------
# Refused, clearly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kql", "match"),
    [
        ("FpNoSuch() | count", "no stored function by that name is registered"),
        ("fpf() | count", "did you mean 'FpF'"),       # Kusto SEM0260
        ("FpF(1) | count", "expects 0 argument"),       # SEM0219
        ("FpAbove | count", "expects 1 argument"),      # SEM0219: a bare name
        ("FpAbove(x = 5) | count", "named argument"),
        ("print v = FpF()", "tabular expression is not expected"),  # SEM0085
    ],
)
def test_a_call_that_kusto_refuses_is_refused(con, kql, match) -> None:
    with pytest.raises(KqlUnsupportedError, match=match):
        _rows(con, kql)


def test_a_bare_name_with_required_parameters_is_not_read_as_the_table(con) -> None:
    con.execute("CREATE TABLE FpAbove AS SELECT 1 AS x")
    with pytest.raises(KqlUnsupportedError, match="expects 1 argument"):
        _rows(con, "FpAbove | count")


def test_recursion_is_refused_naming_the_cycle(con) -> None:
    """Kusto stores a cycle only with skipvalidation and refuses the call:
    SEM0057, "Recursive call to 'FpA' … is not allowed"."""
    cycle = (
        ".create-or-alter function FpA() { FpB() }\n"
        ".create-or-alter function FpB() { FpA() | count }"
    )
    with pytest.raises(KqlUnsupportedError, match="FpA → FpB → FpA"):
        _rows(con, "FpA()", cycle)


@pytest.mark.parametrize(
    ("definition", "match"),
    [
        (".create-or-alter function strlen() { T }", "SEM0515"),
        (".create-or-alter function F(T:(x:long)) { T }", "tabular parameter"),
        (".create-or-alter function F(x:long) { x + 1 }", "scalar stored function"),
        (".create-or-alter function with (view = true) F() { T }", "view"),
        (".create-or-alter function with (colour = 'red') F() { T }", "unknown property"),
        (".create-or-alter function F() { T | }", "does not parse"),
        (".create-merge table T (x:long)", "not a function definition"),
        (".create-or-alter function F() { T }\n.create-or-alter function F() { U }", "twice"),
    ],
)
def test_a_bad_definition_fails_where_it_is_registered(definition, match) -> None:
    with pytest.raises(ValueError, match=match):
        duckdb_kql.set_functions(definition)
    assert duckdb_kql.get_functions() is None


# ---------------------------------------------------------------------------
# Registration: isolated, replaced, never remote
# ---------------------------------------------------------------------------


def test_a_calls_registry_replaces_the_default_and_empty_means_none(con) -> None:
    previous = duckdb_kql.get_functions()
    try:
        duckdb_kql.set_functions(".create-or-alter function FpG() { print v = 'default' }")
        assert duckdb_kql.kql(con, "FpG()").fetchall() == [("default",)]
        own = ".create-or-alter function FpG() { print v = 'own' }"
        assert duckdb_kql.kql(con, "FpG()", functions=own).fetchall() == [("own",)]
        with pytest.raises(KqlUnsupportedError, match="FpG"):
            duckdb_kql.kql(con, "FpG()", functions={})
        duckdb_kql.set_functions(None)
        with pytest.raises(KqlUnsupportedError, match="FpG"):
            duckdb_kql.kql(con, "FpG()")
    finally:
        duckdb_kql.set_functions(previous)


def test_two_clients_on_one_connection_keep_their_own(con) -> None:
    from duckdb_kql.kusto import KustoClient

    a = KustoClient(con, functions=".create-or-alter function FpG() { print v = 'a' }")
    b = KustoClient(con, functions=".create-or-alter function FpG() { print v = 'b' }")
    assert a.execute(None, "FpG()").primary_results[0][0]["v"] == "a"
    assert b.execute(None, "FpG()").primary_results[0][0]["v"] == "b"
    assert a.execute(None, ".show functions").primary_results[0][0]["Name"] == "FpG"


def test_show_functions_reports_the_registry(con) -> None:
    rows = _rows(con, ".show functions | where Name == 'FpF'")
    assert rows == [("FpF", "()", "{ FpT }", "Tests", "Reads it")]
    assert _rows(con, ".show function FpAbove") == [
        ("FpAbove", "(x:long)", "{ FpT | where Value > x }", "", "")
    ]


def test_the_export_form_pastes_across_verbatim() -> None:
    """What `.show database D schema as csl script` emits, measured, with the
    blank line and comments a hand-edited script collects."""
    from duckdb_kql.stored_functions import parse_functions

    parsed = parse_functions(
        "// exported\n"
        '.create-or-alter function with (folder = "Tests", docstring = "Reads it", '
        'skipvalidation = "true") FpF(x:long=0) { FpT | where Value > x }\n'
        "\n"
        ".create-or-alter function Multi() {\n    FpT\n\n    | count // rows\n}\n"
    )
    assert parsed is not None
    assert sorted(parsed[None]) == ["FpF", "Multi"]
    assert parsed[None]["FpF"].parameters == "(x:long=0)"


def test_registration_evaluates_no_python_and_calls_nothing_remote() -> None:
    """A definition is KQL text; translating a call needs no connection at all,
    and a `cluster()` inside a body resolves only through the map."""
    functions = ".create-or-alter function R() { cluster('prod').database('d').T }"
    with pytest.raises(KqlSchemaError, match="cluster"):
        duckdb_kql.to_sql("R() | count", functions=functions)
    sql = duckdb_kql.to_sql(
        "R() | count", functions=functions, clusters={("prod", "d"): "local"}
    )
    assert '"local"."T"' in sql


def test_the_server_takes_them_from_a_file(tmp_path) -> None:
    from duckdb_kql.cli import _read_functions

    (tmp_path / "current.csl").write_text(READ, encoding="utf-8")
    (tmp_path / "sales.csl").write_text(
        ".create-or-alter function Orders() { T }", encoding="utf-8"
    )
    read = _read_functions([str(tmp_path / "current.csl"), f"Sales={tmp_path / 'sales.csl'}"])
    assert read == {None: [READ], "Sales": [".create-or-alter function Orders() { T }"]}
    (tmp_path / "bad.csl").write_text(".create-or-alter function F(x:long) { x }")
    with pytest.raises(ValueError, match="scalar"):
        _read_functions([str(tmp_path / "bad.csl")])
