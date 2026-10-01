"""Trap tests — `query_results_cache_max_age`: a zero age runs, any other is refused.

Requested: portable queries open with `set query_results_cache_max_age =
time(0s);` to turn result caching off, and the option was refused outright —
"there is no results cache, so a max age would govern nothing" — which blocked
every query body behind it. A zero age asks that no cached result be returned,
and none ever is here, so for that value the option is a no-op that means what
it says.

The obvious fix — move the option to the no-op table — is wrong: it would also
accept `time(5m)`, an age that governs a cache that does not exist, and that
is exactly the silent acceptance the option table exists to prevent. So the
classification depends on the value, one rule shared by the `set` statement,
`ClientRequestProperties.set_option` and `serve`.

The second trap is the spelling. Measured on the emulator, Kusto accepts every
timespan literal and a string that reads as one, and refuses `0`, `-0s`,
`time(null)`, a missing value and `' 0s '`. Its string parse is looser than its
literal syntax: `'0 s'` and `'-0s'` run there. Only the spellings this can read
for certain are accepted; a looser one is refused, never guessed at.
"""

from __future__ import annotations

import datetime as dt

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlError
from duckdb_kql.options import OPTION_SUPPORT, OptionSupport, accepts

BODY = "print Answer=42"


@pytest.fixture(scope="module")
def apis():
    from duckdb_kql.kusto import KustoClient

    con = duckdb_kql.connect()
    client = KustoClient(con)
    yield con, client
    client.close()
    con.close()


def _both(apis, query: str) -> tuple[list, list]:
    con, client = apis
    engine = duckdb_kql.query(con, query).fetchall()
    sdk = [row.to_dict() for row in client.execute(None, query).primary_results[0]]
    return engine, sdk


@pytest.mark.parametrize(
    "value",
    # Each one measured: Kusto runs the query.
    ["time(0s)", "0s", "0d", "0ms", "0tick", "timespan(0)", "time(0)", "time(00:00:00)",
     "time(-00:00:00)", "'0s'", '"0s"', "'00:00:00'", "'0'", "@'0s'"],
)
def test_a_zero_age_runs_as_the_query_without_it(apis, value: str) -> None:
    assert _both(apis, f"set query_results_cache_max_age = {value};\n{BODY}") == _both(apis, BODY)
    assert duckdb_kql.validate(f"set query_results_cache_max_age = {value};\n{BODY}") == []


@pytest.mark.parametrize(
    "value",
    [
        "time(5m)", "1h", "time(-1h)",  # an age that would govern a cache
        "0", "time(null)", "true", "' 0s '", "'time(0s)'",  # Kusto refuses each
        "'0 s'",  # Kusto runs it; its string parse is looser than this reads
    ],
)
def test_any_other_age_is_refused_by_both(apis, value: str) -> None:
    con, client = apis
    query = f"set query_results_cache_max_age = {value};\n{BODY}"
    with pytest.raises(KqlError, match="cache"):
        duckdb_kql.query(con, query)
    from duckdb_kql.kusto.exceptions import KustoServiceError

    with pytest.raises(KustoServiceError, match="cache"):
        client.execute(None, query)


def test_a_missing_value_is_refused() -> None:
    with pytest.raises(KqlError, match="cache"):
        duckdb_kql.to_sql(f"set query_results_cache_max_age;\n{BODY}")


def test_the_corpus_example_is_still_refused() -> None:
    """The documentation's own example sets five minutes."""
    with pytest.raises(KqlError, match="Any other age"):
        duckdb_kql.to_sql("set query_results_cache_max_age = time(5m);\nprint 1")


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        (dt.timedelta(0), True),
        ("00:00:00", True),
        (str(dt.timedelta(0)), True),  # `0:00:00`, how the client serialises it
        ("0s", True),
        (dt.timedelta(minutes=5), False),
        ("00:05:00", False),
        (0, False),
        (None, False),
    ],
)
def test_the_request_option_takes_the_same_rule(apis, value, accepted: bool) -> None:
    from duckdb_kql.kusto import ClientRequestProperties
    from duckdb_kql.kusto.exceptions import KustoUnsupportedError
    from duckdb_kql.server import check_options

    _, client = apis
    props = ClientRequestProperties()
    if accepted:
        props.set_option("query_results_cache_max_age", value)
        rows = [row.to_dict() for row in client.execute(None, BODY, props).primary_results[0]]
        assert rows == [{"Answer": 42}]
        assert check_options({"query_results_cache_max_age": value}) == []
    else:
        with pytest.raises(KustoUnsupportedError, match="cache"):
            props.set_option("query_results_cache_max_age", value)
        assert check_options({"query_results_cache_max_age": value})


def test_only_this_option_is_conditional() -> None:
    """Its neighbours are not quietly loosened with it."""
    conditional = [n for n, (s, _) in OPTION_SUPPORT.items() if s == OptionSupport.CONDITIONAL]
    assert conditional == ["query_results_cache_max_age"]
    assert OPTION_SUPPORT["query_results_cache_per_shard"][0] == OptionSupport.REFUSED
    assert not accepts("query_results_cache_per_shard", "0s")
    with pytest.raises(KqlError):
        duckdb_kql.to_sql(f"set query_results_cache_per_shard;\n{BODY}")
