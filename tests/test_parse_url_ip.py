"""L5 trap tests — `parse_url`, `parse_ipv4`, `parse_ipv6` (R1, R9).

All three were unmapped. All three return something a caller indexes into or
compares, so a nearly-right answer is a silent one — which is why each rule
below cites a measurement rather than the RFC. Several of them are the
*parser's* behaviour and not the standard's.

`parse_ipv4`
    A CIDR suffix **masks** rather than being ignored; **leading zeros are
    rejected**; whitespace is tolerated. All validation lives in one regex, so
    the arithmetic only ever runs on well-formed input.

`parse_url`
    Userinfo splits **only on a colon** — `https://user@h.io/p` puts `user@h.io`
    in *Host*. Query keys are **sorted**, a repeated key holds an **array**, and
    a parameter with an empty key or value is **dropped**. A lone trailing slash
    is the empty path, but only at end of input.

`parse_ipv6`
    A `%zone` is dropped, a bare IPv4 becomes the IPv4-mapped address, and a
    CIDR suffix masks — but when the address has a **dotted tail the prefix is
    an IPv4 one**: `1.2.3.4/24` and `::ffff:1.2.3.4/24` agree, `/0` still keeps
    the `::ffff:` mapping, and `/120` is refused where it would be legal over
    128 bits. Invalid input is the **empty string**, not null.

Two bugs the sweep caught, both in the masking: the "group fully covered by the
prefix" branch was one group out, so `/24` asked DuckDB to left-shift by -8 and
raised; and defaulting an absent prefix to 128 made every `::1.2.3.4` fail its
own range check once that range became 32.
"""

from __future__ import annotations

import json

import pytest

import duckdb_kql

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    return c


def _one(con, kql):
    return duckdb_kql.kql(con, kql).fetchall()[0][0]


def _ip4(con, value):
    return _one(con, f"datatable(s:string)[{value!r}] | project r = parse_ipv4(s)")


def _ip6(con, value):
    return _one(con, f"datatable(s:string)[{value!r}] | project r = parse_ipv6(s)")


def _url(con, value):
    return json.loads(
        _one(con, f"datatable(u:string)[{value!r}] | project r = tostring(parse_url(u))")
    )


# ---------------------------------------------------------------------------
# parse_ipv4
# ---------------------------------------------------------------------------


def test_the_reported_ipv4(con) -> None:
    assert _one(con, "print Result = parse_ipv4('203.0.113.10')") == 3405803786


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0.0.0.0", 0),
        ("255.255.255.255", 4294967295),
        # A CIDR suffix masks: this is the /24 network, not the host.
        ("203.0.113.10/24", 3405803776),
        ("203.0.113.10/32", 3405803786),
        ("203.0.113.10/0", 0),
        (" 203.0.113.10", 3405803786),
    ],
)
def test_ipv4_forms(con, value: str, expected: int) -> None:
    assert _ip4(con, value) == expected


@pytest.mark.parametrize(
    "value", ["1.2.3", "1.2.3.4.5", "256.0.0.1", "01.02.03.04", "nope", "", "1.2.3.4/33"]
)
def test_ipv4_rejects(con, value: str) -> None:
    """`01.02.03.04` is the one to note: a leading zero is a rejection, not
    something to tidy away, so it is null rather than 16909060."""
    assert _ip4(con, value) is None


# ---------------------------------------------------------------------------
# parse_url
# ---------------------------------------------------------------------------


def test_the_reported_url(con) -> None:
    assert _url(con, "https://example.invalid:443/Path?key=Value") == {
        "Scheme": "https", "Host": "example.invalid", "Port": "443",
        "Path": "/Path", "Username": "", "Password": "",
        "Query Parameters": {"key": "Value"}, "Fragment": "",
    }


def test_userinfo_splits_only_on_a_colon(con) -> None:
    """Measured, and the opposite of what a URL parser usually does."""
    assert _url(con, "https://user@h.io/p")["Host"] == "user@h.io"
    assert _url(con, "https://user@h.io/p")["Username"] == ""
    assert _url(con, "https://u:p@h.io/x")["Username"] == "u"
    assert _url(con, "https://u:p@h.io/x")["Password"] == "p"


def test_query_keys_are_sorted_and_repeats_become_arrays(con) -> None:
    assert _url(con, "https://h.io/p?y=2&x=1")["Query Parameters"] == {"x": "1", "y": "2"}
    assert _url(con, "https://h.io/p?x=1&y=2&x=3")["Query Parameters"] == {
        "x": ["1", "3"], "y": "2"
    }


@pytest.mark.parametrize("url", ["https://h.io/p?k=", "https://h.io/p?=v", "https://h.io/p?flag"])
def test_an_empty_key_or_value_is_dropped(con, url: str) -> None:
    assert _url(con, url)["Query Parameters"] == {}


def test_a_lone_trailing_slash_is_the_empty_path_only_at_the_end(con) -> None:
    """The subtlest rule here: the same slash is `''` or `/` depending on
    whether anything follows it."""
    assert _url(con, "https://h.io/")["Path"] == ""
    assert _url(con, "https://h.io/?x=1")["Path"] == "/"
    assert _url(con, "https://h.io/#")["Path"] == "/"


def test_nothing_is_decoded_or_case_folded(con) -> None:
    assert _url(con, "https://h.io/a%20b")["Path"] == "/a%20b"
    assert _url(con, "HTTPS://H.IO/P")["Scheme"] == "HTTPS"


@pytest.mark.parametrize("url", ["not a url", "", "//h.io/p"])
def test_an_unparseable_url_is_all_empty_not_null(con, url: str) -> None:
    parsed = _url(con, url)
    assert parsed["Query Parameters"] == {}
    assert set(parsed) == {
        "Scheme", "Host", "Port", "Path", "Username", "Password",
        "Query Parameters", "Fragment",
    }
    assert all(v == "" for k, v in parsed.items() if k != "Query Parameters")


def test_a_bracketed_ipv6_host_stays_one_unit(con) -> None:
    assert _url(con, "https://[::1]:80/p")["Host"] == "[::1]"
    assert _url(con, "https://[::1]:80/p")["Port"] == "80"


# ---------------------------------------------------------------------------
# parse_ipv6
# ---------------------------------------------------------------------------


def test_the_reported_ipv6(con) -> None:
    assert _one(con, "print Result = parse_ipv6('2001:db8::10')") == (
        "2001:0db8:0000:0000:0000:0000:0000:0010"
    )


@pytest.mark.parametrize(
    "value,expected",
    [
        ("::1", "0000:0000:0000:0000:0000:0000:0000:0001"),
        ("::", "0000:0000:0000:0000:0000:0000:0000:0000"),
        ("1:2:3:4:5:6:7:8", "0001:0002:0003:0004:0005:0006:0007:0008"),
        ("2001:DB8::10", "2001:0db8:0000:0000:0000:0000:0000:0010"),
        ("2001:db8:0:0:1::", "2001:0db8:0000:0000:0001:0000:0000:0000"),
        # a `%zone` is dropped
        ("fe80::1%eth0", "fe80:0000:0000:0000:0000:0000:0000:0001"),
        # a bare IPv4 is the IPv4-mapped address
        ("203.0.113.10", "0000:0000:0000:0000:0000:ffff:cb00:710a"),
        ("::ffff:203.0.113.10", "0000:0000:0000:0000:0000:ffff:cb00:710a"),
        ("::1.2.3.4", "0000:0000:0000:0000:0000:0000:0102:0304"),
        ("1:2:3:4:5:6:1.2.3.4", "0001:0002:0003:0004:0005:0006:0102:0304"),
    ],
)
def test_ipv6_forms(con, value: str, expected: str) -> None:
    assert _ip6(con, value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2001:db8::10/64", "2001:0db8:0000:0000:0000:0000:0000:0000"),
        ("2001:db8::10/128", "2001:0db8:0000:0000:0000:0000:0000:0010"),
        ("2001:db8::10/0", "0000:0000:0000:0000:0000:0000:0000:0000"),
        # /17 keeps one bit of the second group, and that bit is 0 — this
        # expectation was hand-written wrong first, and the emulator settled it.
        ("2001:db8::10/17", "2001:0000:0000:0000:0000:0000:0000:0000"),
        ("2001:db8::10/113", "2001:0db8:0000:0000:0000:0000:0000:0000"),
    ],
)
def test_a_prefix_masks(con, value: str, expected: str) -> None:
    assert _ip6(con, value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        # The prefix is an IPv4 one when the address has a dotted tail — with or
        # without the `::ffff:` written out, and /0 keeps the mapping.
        ("1.2.3.4/24", "0000:0000:0000:0000:0000:ffff:0102:0300"),
        ("::ffff:1.2.3.4/24", "0000:0000:0000:0000:0000:ffff:0102:0300"),
        ("1.2.3.4/32", "0000:0000:0000:0000:0000:ffff:0102:0304"),
        ("1.2.3.4/0", "0000:0000:0000:0000:0000:ffff:0000:0000"),
    ],
)
def test_a_dotted_tail_makes_the_prefix_an_ipv4_one(con, value: str, expected: str) -> None:
    """`/24` on group 1 is also the case that raised: the branch for a group the
    prefix fully covers was one group out, so it took the partial path and
    asked DuckDB to shift by -8."""
    assert _ip6(con, value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "1:2:3:4:5:6:7:8:9", "gggg::1", "1::2::3", "nope", "",
        "2001:db8::10/129", "256.1.1.1", "::ffff:1.2.3.400",
        # legal over 128 bits, refused because the address is dotted
        "::ffff:1.2.3.4/120", "1.2.3.4/33",
    ],
)
def test_ipv6_rejects_with_an_empty_string(con, value: str) -> None:
    """Not null — measured. A caller testing `isempty` sees what Kusto shows."""
    assert _ip6(con, value) == ""


# ---------------------------------------------------------------------------
# The emission, not the answer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("column", ["a", "g", "p", "m", "u", "r", "s", "t", "d", "ok"])
@pytest.mark.parametrize("function", ["parse_ipv4", "parse_ipv6", "parse_url"])
def test_a_bound_alias_cannot_collide_with_the_column(con, function, column) -> None:
    """Regression. Binding the stages introduced this and the sweep caught it.

    A correlated subquery that aliases an intermediate `a` sits in a scope where
    the outer column `a` is also visible, so `SELECT <expr over "a"> AS a` is
    circular and DuckDB refuses the whole query — `parse_ipv6(a)` stopped
    working the moment the inline version was replaced, for every input.

    The aliases carry a `_kql` prefix now. That is not proof against an escaped
    KQL name spelled `['_kqla']`, which nothing here can rule out; it moves the
    collision from a name people write to one they do not.
    """
    duckdb_kql.kql(
        con,
        f"datatable({column}:string)['1.2.3.4'] "
        f"| project x = tostring({function}({column}))",
    ).fetchall()


def test_parse_url_binds_its_stages_instead_of_repeating_them() -> None:
    """`parse_url` was the worst of the three: a 3.4 KB body with the subject in
    it fourteen times, so one nested call — `parse_url(strcat(a, b))` — reached
    46 KB. One `regexp_extract` with a *name list* returns all eight groups as a
    struct, which removes most of it on its own."""
    sql = str(
        duckdb_kql.to_sql("T | project r = parse_url(s)", schema={"T": ["s"]})
    )
    assert len(sql) < 4000, f"parse_url emits {len(sql)} characters"


def test_parse_ipv6_binds_its_stages_instead_of_repeating_them() -> None:
    """A size assertion, because the cost was invisible in every other test.

    The address feeds the dotted-quad test, the `::` expansion, the validity
    check and the mask, each needing it more than once. Spelled inline that
    nested to about twenty copies and **63 KB of SQL for one call**, which made
    this the corpus's slowest query at ~450ms — correct, and the kind of correct
    the budget test exists to catch. Nested scalar subqueries bind each stage
    once; a correlated subquery is the only binding form a scalar expression
    has, and DuckDB resolves the outer column through it.

    The bound is generous on purpose: this guards the order of magnitude, not a
    byte count.
    """
    sql = str(
        duckdb_kql.to_sql("datatable(s:string)['::1'] | project r = parse_ipv6(s)")
    )
    assert len(sql) < 8000, f"parse_ipv6 emits {len(sql)} characters"
