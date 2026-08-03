"""Parser tests — test-plan layer L1.

These cover the public parse surface and the Wave-1 syntax shapes. The
large-corpus L1 regression lives in ``test_corpus.py``.
"""

from __future__ import annotations

import pytest

import duckdb_kql
from duckdb_kql import KqlSyntaxError, KqlUnsupportedError

# Shapes Wave 1 must be able to parse (docs/frequency-scan-results.md).
WAVE1_QUERIES = [
    pytest.param('Logs | where Level == "Error"', id="where"),
    pytest.param("Logs | project a, b = c * 2", id="project"),
    pytest.param("Logs | project-away Secret", id="project-away"),
    pytest.param("Logs | extend z = a + b", id="extend"),
    pytest.param("Logs | summarize Count = count() by Component", id="summarize-by"),
    pytest.param(
        "Logs | summarize Count = count() by bin(Timestamp, 1h), Component",
        id="summarize-bin",
    ),
    pytest.param("Logs | count", id="count"),
    pytest.param("A | join kind=leftouter (B) on Key", id="join-kind"),
    pytest.param("A | join (B) on Key", id="join-default-innerunique"),
    pytest.param("A | union B", id="union"),
    pytest.param("Logs | sort by Timestamp asc", id="sort-asc"),
    pytest.param("Logs | order by Timestamp", id="order-default-desc"),
    pytest.param("Logs | top 5 by Count", id="top"),
    pytest.param("Logs | take 10", id="take"),
    pytest.param("Logs | limit 10", id="limit"),
    pytest.param("Logs | distinct Component", id="distinct"),
    pytest.param('datatable(a:int, b:string)[1, "x"]', id="datatable"),
    pytest.param("print x = 1 + 1", id="print"),
    pytest.param("range i from 1 to 10 step 1", id="range"),
    pytest.param("let T = datatable(x:long)[1, 2]; T | summarize s = sum(x)", id="let"),
    pytest.param(
        "let a = ago(5h); let b = a + 2h; T | where t > a and t < b", id="let-multi"
    ),
]

# The R2/R3 string-operator family (docs/TRANSLATION.md) — these are the
# highest-risk mappings, so make sure every spelling at least parses.
STRING_OPERATORS = [
    "==", "!=", "=~", "!~",
    "has", "!has", "has_cs", "!has_cs",
    "contains", "!contains", "contains_cs", "!contains_cs",
    "startswith", "!startswith", "startswith_cs",
    "endswith", "!endswith", "endswith_cs",
    "hasprefix", "hassuffix",
]


@pytest.mark.parametrize("kql", WAVE1_QUERIES)
def test_wave1_shapes_parse(kql: str) -> None:
    assert duckdb_kql.parse(kql).ok


@pytest.mark.parametrize("op", STRING_OPERATORS)
def test_string_operator_family_parses(op: str) -> None:
    assert duckdb_kql.parse(f'T | where Text {op} "x"').ok


@pytest.mark.parametrize(
    "kql",
    [
        pytest.param(
            "StormEvents | where State in (PopulationData | project State)",
            id="in-tabular-subquery",
        ),
        pytest.param(
            "StormEvents | where State in~ (PopulationData | project State)",
            id="in~-tabular-subquery",
        ),
        pytest.param('T | where x in ("a", "b", "c")', id="in-value-list"),
    ],
)
def test_in_operator_forms(kql: str) -> None:
    """Covers local grammar PATCH duckdb-kql/001 (grammar/UPSTREAM.md)."""
    assert duckdb_kql.parse(kql).ok


def test_syntax_error_raises_with_diagnostics() -> None:
    with pytest.raises(KqlSyntaxError) as excinfo:
        duckdb_kql.parse("T | wherex zzz ====")
    assert excinfo.value.diagnostics
    assert excinfo.value.diagnostics[0].span.line == 1


def test_validate_does_not_raise() -> None:
    assert duckdb_kql.validate("T | where x == 1") == []
    assert duckdb_kql.validate("T | wherex ====")


def test_parse_rejects_non_string() -> None:
    with pytest.raises(TypeError):
        duckdb_kql.parse(42)  # type: ignore[arg-type]


def test_translation_entry_points_translate() -> None:
    """Wave 1 constructs go all the way to SQL through the public API."""
    sql = duckdb_kql.to_sql("T | count")
    assert "count(*)" in sql
    assert '"T"' in sql  # R7: identifiers are quoted, never case-folded


def test_translation_refuses_constructs_outside_the_wave() -> None:
    """Anything unimplemented must refuse, never return something plausible.

    ``KqlUnsupportedError`` is the contract: callers can distinguish "not yet"
    from "your query is wrong", and a silent wrong answer is impossible.
    """
    for kql in (
        "T | partition by a (take 1)",  # operator not yet implemented
        "T | evaluate bag_unpack(a)",  # operator not yet implemented
        "let f = (a:int) { a + 1 }; print f(1)",  # user-defined functions
        "T | where a == totimespan_unmapped(1)",  # unmapped function
        "T | summarize unmapped_agg(a)",  # unmapped aggregate
    ):
        with pytest.raises(KqlUnsupportedError):
            duckdb_kql.to_sql(kql)


def test_translation_entry_points_still_report_syntax_errors() -> None:
    """A bad query should fail as a syntax error, not as 'not implemented'."""
    with pytest.raises(KqlSyntaxError):
        duckdb_kql.to_sql("T | wherex ====")
