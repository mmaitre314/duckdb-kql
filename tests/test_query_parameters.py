"""L5 traps — ``declare query_parameters``.

The reason this feature exists is security, so the tests are written as attacks
rather than as demonstrations. A parameter that *works* but can be talked into
becoming query text is worse than no parameter support at all: it looks safe.

The property being asserted throughout is structural, not lexical — not "the
value was escaped correctly" but "the value never entered the SQL text", which
is a claim a test can actually check.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest

import duckdb_kql
from duckdb_kql import engine
from duckdb_kql.errors import KqlSchemaError, KqlSyntaxError, KqlUnsupportedError

duckdb = pytest.importorskip("duckdb")


@pytest.fixture(scope="module")
def con():
    c = engine.connect()
    c.sql(
        "CREATE TABLE Users AS SELECT * FROM (VALUES "
        "('alice', 'public'), ('bob', 'secret')) t(name, tier)"
    )
    return c


LOOKUP = "declare query_parameters(name_p:string);\nUsers | where name == name_p"


# ---------------------------------------------------------------------------
# Injection
# ---------------------------------------------------------------------------

#: Payloads that would each change the meaning of the statement if the value
#: were concatenated into it rather than bound. The last two are LIKE
#: metacharacters rather than SQL syntax — they test that a parameter is
#: compared as a value, not expanded as a pattern.
PAYLOADS = [
    "' OR 1=1 --",
    "'; DROP TABLE Users; --",
    "alice' UNION ALL SELECT name, tier FROM Users WHERE tier='secret' --",
    "\\'; SELECT 1; --",
    "'' OR ''=''",
    'a" OR "1"="1',
    "%",
    "_",
]

#: The subset worth asserting *textual* absence for. A single metacharacter
#: appears in generated CTE names by coincidence, which would make the assertion
#: fail for a reason that has nothing to do with the value.
TEXT_PAYLOADS = [p for p in PAYLOADS if len(p) > 1]


@pytest.mark.parametrize("payload", TEXT_PAYLOADS)
def test_payload_never_reaches_the_sql_text(payload: str) -> None:
    """The generated SQL must not contain the value at all.

    This is the invariant worth testing. "Correctly escaped" depends on getting
    the escaping right; "absent" does not depend on anything.
    """
    translated = duckdb_kql.to_sql(LOOKUP, parameters={"name_p": payload})
    assert payload not in str(translated)
    assert "$kqlp0" in str(translated)
    assert translated.parameters == {"kqlp0": payload}


@pytest.mark.parametrize("payload", PAYLOADS)
def test_payload_is_matched_as_a_literal_string(con, payload: str) -> None:
    """A payload is data: it matches no row, rather than selecting them all."""
    rows = engine.sql(con, LOOKUP, {"name_p": payload}).fetchall()
    assert rows == []


def test_table_survives_a_drop_payload(con) -> None:
    engine.sql(con, LOOKUP, {"name_p": "'; DROP TABLE Users; --"}).fetchall()
    assert con.sql("SELECT count(*) FROM Users").fetchone()[0] == 2


def test_a_real_value_still_matches(con) -> None:
    """The obvious counterweight: the safe path must not be a broken path."""
    assert engine.sql(con, LOOKUP, {"name_p": "alice"}).fetchall() == [
        ("alice", "public")
    ]


def test_parameter_cannot_smuggle_in_an_operator(con) -> None:
    """A value spelling a whole KQL pipeline stays a string."""
    payload = "alice | project tier"
    assert engine.sql(con, LOOKUP, {"name_p": payload}).fetchall() == []


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------


def test_declarations_are_reported_in_order() -> None:
    decls = duckdb_kql.query_parameters(
        "declare query_parameters(a:string, b:long = 5, c:datetime);\nprint 1"
    )
    assert [(d.name, d.type, d.required) for d in decls] == [
        ("a", "string", True),
        ("b", "long", False),
        ("c", "datetime", True),
    ]


def test_default_is_used_when_no_value_is_supplied(con) -> None:
    kql = "declare query_parameters(n:long = 7);\nprint x = n"
    assert engine.sql(con, kql).fetchall() == [(7,)]


def test_supplied_value_wins_over_the_default(con) -> None:
    kql = "declare query_parameters(n:long = 7);\nprint x = n"
    assert engine.sql(con, kql, {"n": 9}).fetchall() == [(9,)]


def test_escaped_parameter_name_is_reported_unescaped() -> None:
    """``['odd name']`` declares ``odd name`` — the brackets are syntax.

    Referring to such a name in the query *body* needs escaped-identifier
    support in the expression lowerer, which is a separate gap; what matters
    here is that the declaration is read correctly rather than being reported
    with its brackets still attached.
    """
    kql = "declare query_parameters(['odd name']:long = 3);\nprint x = 1"
    assert [d.name for d in duckdb_kql.query_parameters(kql)] == ["odd name"]


def test_parameter_name_is_not_reused_as_the_slot() -> None:
    """The slot is generated, so a hostile *name* cannot reach the SQL either."""
    kql = "declare query_parameters(['a\"; DROP TABLE T; --']:long = 1);\nprint x = 1"
    assert "DROP TABLE" not in str(duckdb_kql.to_sql(kql))


def test_a_let_may_read_a_parameter(con) -> None:
    kql = (
        "declare query_parameters(lo:long = 2);\n"
        "let bound = lo * 10;\n"
        "print x = bound"
    )
    assert engine.sql(con, kql, {"lo": 3}).fetchall() == [(30,)]


def test_duplicate_declaration_is_refused() -> None:
    with pytest.raises(KqlSchemaError):
        duckdb_kql.query_parameters(
            "declare query_parameters(a:string, a:long);\nprint 1"
        )


def test_type_outside_the_grammar_is_a_syntax_error() -> None:
    with pytest.raises(KqlSyntaxError):
        duckdb_kql.query_parameters("declare query_parameters(a:widget);\nprint 1")


def test_int8_is_refused_rather_than_guessed_at() -> None:
    """The grammar allows it; its meaning is not what a reader would assume."""
    with pytest.raises(KqlUnsupportedError):
        duckdb_kql.query_parameters("declare query_parameters(a:int8);\nprint 1")


# ---------------------------------------------------------------------------
# Binding
# ---------------------------------------------------------------------------


def test_unknown_parameter_name_is_refused() -> None:
    """A typo must not degrade into a filter that silently does nothing."""
    with pytest.raises(KqlSchemaError) as exc:
        duckdb_kql.to_sql(LOOKUP, parameters={"nmae_p": "alice"})
    assert "name_p" in str(exc.value)


def test_missing_value_is_reported_at_translation_but_only_fails_on_execute(con) -> None:
    translated = duckdb_kql.to_sql(LOOKUP)
    assert translated.unbound == ("name_p",)
    with pytest.raises(KqlSchemaError):
        engine.sql(con, LOOKUP)


@pytest.mark.parametrize(
    "kind,value",
    [
        ("string", 5),
        ("long", 1.5),
        ("long", True),
        ("long", "5"),
        ("real", "1.5"),
        ("bool", 1),
        ("datetime", 20200101),
        ("timespan", 5),
        ("guid", "not-a-guid"),
    ],
)
def test_value_of_the_wrong_type_is_refused(kind: str, value: object) -> None:
    """KQL declared a type; silently coercing to it is how wrong numbers spread."""
    kql = f"declare query_parameters(p:{kind});\nprint x = p"
    with pytest.raises(KqlSchemaError):
        duckdb_kql.to_sql(kql, parameters={"p": value})


@pytest.mark.parametrize(
    "kind,value,expected",
    [
        ("string", "hi", "hi"),
        ("long", 2**40, 2**40),
        ("int", 3, 3),
        ("real", 1.5, 1.5),
        ("real", 2, 2.0),
        ("decimal", "1.25", Decimal("1.250000000")),
        ("bool", True, True),
        ("datetime", "2020-01-02T03:04:05Z", dt.datetime(2020, 1, 2, 3, 4, 5)),
        ("datetime", dt.datetime(2020, 1, 2), dt.datetime(2020, 1, 2)),
        ("timespan", "1.02:03:04", dt.timedelta(days=1, hours=2, minutes=3, seconds=4)),
        ("timespan", "90m", dt.timedelta(minutes=90)),
        ("timespan", dt.timedelta(hours=2), dt.timedelta(hours=2)),
    ],
)
def test_value_round_trips_with_its_declared_type(con, kind, value, expected) -> None:
    kql = f"declare query_parameters(p:{kind});\nprint x = p"
    assert engine.sql(con, kql, {"p": value}).fetchone()[0] == expected


def test_guid_round_trips(con) -> None:
    g = uuid.UUID("12345678-1234-5678-1234-567812345678")
    kql = "declare query_parameters(p:guid);\nprint x = p"
    assert engine.sql(con, kql, {"p": str(g)}).fetchone()[0] == g


def test_dynamic_parameter_is_indexable(con) -> None:
    """A dynamic value crosses as JSON text and must come back as a document."""
    kql = "declare query_parameters(p:dynamic);\nprint x = p.items[1]"
    assert engine.sql(con, kql, {"p": {"items": [10, 20]}}).fetchone()[0] == "20"


def test_aware_datetime_is_converted_to_utc(con) -> None:
    """KQL datetimes are UTC (R8); an offset must be applied, not dropped."""
    aware = dt.datetime(2020, 1, 1, 12, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    kql = "declare query_parameters(p:datetime);\nprint x = p"
    assert engine.sql(con, kql, {"p": aware}).fetchone()[0] == dt.datetime(2020, 1, 1, 10)


def test_offset_in_a_datetime_string_is_applied(con) -> None:
    kql = "declare query_parameters(p:datetime);\nprint x = p"
    got = engine.sql(con, kql, {"p": "2020-01-01T12:00:00+02:00"}).fetchone()[0]
    assert got == dt.datetime(2020, 1, 1, 10)


def test_parameters_are_rejected_when_none_are_declared() -> None:
    with pytest.raises(KqlSchemaError):
        duckdb_kql.to_sql("print x = 1", parameters={"p": 1})


# ---------------------------------------------------------------------------
# Layer boundary
# ---------------------------------------------------------------------------


def test_translation_needs_no_connection() -> None:
    """Layer 0 stays Layer 0: parameters do not drag a database in."""
    translated = duckdb_kql.to_sql(LOOKUP, parameters={"name_p": "alice"})
    assert "SELECT" in str(translated)
    assert translated.parameters == {"kqlp0": "alice"}


def test_translation_result_is_still_a_string() -> None:
    """Existing callers treat to_sql's result as a str; it must stay one."""
    translated = duckdb_kql.to_sql("print x = 1")
    assert isinstance(translated, str)
    assert translated.parameters == {}
    assert translated.unbound == ()


# ---------------------------------------------------------------------------
# Numbers that parse but are not durations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["inf", "-inf", "nan", "1e400", "1e10", "-1e10"])
def test_an_unrepresentable_timespan_raises_a_kql_error(value: str) -> None:
    """These reached `timedelta(days=float(text))` and escaped as OverflowError.

    A raw stdlib exception past the public boundary is a broken contract: the
    caller was told `bind()` raises `KqlSchemaError`, so `except KqlError`
    around it does nothing and the process dies on a bad parameter value. No
    wrong answer and no SQL is reached — but the failure mode is the one the
    error taxonomy exists to prevent.
    """
    kql = 'declare query_parameters(window:timespan);\nT | where d > ago(window)'
    with pytest.raises(duckdb_kql.KqlSchemaError):
        duckdb_kql.to_sql(kql, parameters={"window": value})


def test_a_representable_timespan_still_works() -> None:
    """The counterweight: widening the rejection must not reject real values."""
    kql = 'declare query_parameters(window:timespan);\nT | where d > ago(window)'
    for value in ("3", "1.5", "4.00:00:00", "00:05:00"):
        translated = duckdb_kql.to_sql(kql, parameters={"window": value})
        assert translated.parameters, value
