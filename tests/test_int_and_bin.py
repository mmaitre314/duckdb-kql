"""Trap tests — `int` is 32-bit, its arithmetic is `long`, and `bin` keeps the operand's type.

Reported: `print x = int(1)` answered `gettype` **long**, and `bin(7, 5)` was
the real **5.0** where Kusto answers the long 5. Both are silent: the numbers
look right and the types are wrong, which is what `gettype`, `tostring`, a
`union` and every typed client read.

**The `int` literal.** It rendered as BIGINT, so an `int(…)` column was a
`long`. The obvious fix — render it as INTEGER — is half a fix and a regression
on its own, measured: Kusto computes `int` arithmetic in `long` (`int(2147483647)
+ int(1)` is 2147483648, not an overflow), and `abs`, `bin` and the `binary_*`
family of an `int` are `long` too (`binary_shift_left(int(5), 40)` is
5497558138880, where DuckDB's `INTEGER << 40` is an out-of-range error). Only
`iff`, `coalesce`, `max_of`, `min_of` and a folded `-int(5)` stay `int`.

So the operand is widened, and *how* is the second trap. A static `CAST(x AS
BIGINT)` is right for an `int` literal and wrong for the commonest operand
there is, a column whose type the IR does not carry: it **rounds** a real —
`abs(-1.5)` would be 2. The functions therefore widen at run time
(`functions.long_or_wider`, CASE type unification); arithmetic cannot, because
the same widening makes `T + 1d` fail to bind, so `+ - * / %` widen only an
operand the IR proves is an `int`. An `int` *column* under arithmetic still
meets DuckDB's rule — `docs/column-types-proposal.md`.

The converter also takes Kusto's literal grammar, measured: hex is unsigned
and wraps (`int(0xFFFFFFFF)` is -1), a sign before hex and anything out of
range is SYN0002, and a leading zero is decimal (`int(010)` is 10).

**`bin`.** It was `floor(v / r) * r`: a double for every input, and wrong past
2^53 (`bin(9007199254740993, 1)` is exact in Kusto). The obvious fix, casting
the floor back to BIGINT, would truncate every real bin; deciding by the
*literal* kind of `roundTo` fails for `bin(RealColumn, 5)`. The operand types
are known only at run time, and CASE unifies its branches' types, so every
branch is written to *keep* the operand type (`render_bin`/`_numeric_bin`).
A zero size is null for integers and NaN for reals. Near ±2^63, Kusto's
integer path wraps — `bin(-9223372036854775806, 4)` is 9223372036854775804 —
and here it is an overflow error rather than the mathematically right answer,
which would disagree with the oracle just as silently.

**Two siblings found on the way.** `sign` is a real for every input and 0.0 —
not null — for a null; it was DuckDB's TINYINT, null for null, and failed to
bind a `bool`. And `tostring` of a NaN is `NaN`, where DuckDB writes `nan`, or
`-nan` when the sign bit is set as `bin(1.0, real(+inf))` leaves it.

Every expectation below was measured on the Kusto Emulator, folded and over
rows separately, because the two engines are not guaranteed to agree.
"""

from __future__ import annotations

import math

import pytest

import duckdb_kql
from duckdb_kql.errors import KqlError

duckdb = pytest.importorskip("duckdb")

NAN = math.nan
#: A cell not compared: Kusto's string form of that double is its own question.
ANY = object()

#: `(expression, value, gettype)` for `print v = <expression>` — measured.
FOLDED: list[tuple[str, object, str]] = [
    # The literal, and Kusto's grammar for it.
    ("int(1)", 1, "int"),
    ("int(0x10)", 16, "int"),
    ("int(0xFFFFFFFF)", -1, "int"),
    ("int(010)", 10, "int"),
    ("int(-2147483648)", -2147483648, "int"),
    ("int(2147483647)", 2147483647, "int"),
    ("long(0xFFFFFFFFFFFFFFFF)", -1, "long"),
    ("0xFFFFFFFF", 4294967295, "long"),
    ("0x8000000000000000", -9223372036854775808, "long"),
    ("long(-9223372036854775808)", -9223372036854775808, "long"),
    # Arithmetic on two ints is long, and does not overflow at 2^31.
    ("int(2147483647) + int(1)", 2147483648, "long"),
    ("int(7) - int(9)", -2, "long"),
    ("int(7) * int(3)", 21, "long"),
    ("int(7) / int(2)", 3, "long"),
    ("int(7) % int(4)", 3, "long"),
    # An expression the IR proves is an int is widened too; nothing else is.
    ("iff(true, int(2147483647), int(0)) + iff(true, int(1), int(0))", 2147483648, "long"),
    ("toint(2147483647) + toint(1)", 2147483648, "long"),
    ("coalesce(int(2147483647), int(0)) * coalesce(int(2), int(0))", 4294967294, "long"),
    ("max_of(int(2147483647), int(0)) - min_of(int(-1), int(0))", 2147483648, "long"),
    ("1.5 + int(1)", 2.5, "real"),
    ("int(7) / 2.0", 3.5, "real"),
    # ... except what stays int.
    ("-int(5)", -5, "int"),
    ("iff(true, int(1), int(2))", 1, "int"),
    ("coalesce(int(null), int(2))", 2, "int"),
    ("max_of(int(1), int(2))", 2, "int"),
    ("min_of(int(1), int(2))", 1, "int"),
    # Functions of an int are long.
    ("abs(int(-3))", 3, "long"),
    ("abs(-1.5)", 1.5, "real"),  # the static-cast trap: not 2
    ("binary_and(int(6), int(3))", 2, "long"),
    ("binary_or(int(4), int(1))", 5, "long"),
    ("binary_xor(int(6), int(3))", 5, "long"),
    ("binary_not(int(5))", -6, "long"),
    ("binary_shift_left(int(5), 40)", 5497558138880, "long"),
    ("binary_shift_right(int(5), 1)", 2, "long"),
    # sign is real, and 0.0 for null.
    ("sign(int(-3))", -1.0, "real"),
    ("sign(2.5)", 1.0, "real"),
    ("sign(true)", 1.0, "real"),
    ("sign(long(null))", 0.0, "real"),
    # bin keeps the operand's type.
    ("bin(7, 5)", 5, "long"),
    ("bin(-7, 5)", -10, "long"),
    ("bin(7, -5)", 10, "long"),
    ("bin(-7, -5)", -5, "long"),
    ("bin(int(7), int(5))", 5, "long"),
    ("bin(9007199254740993, 1)", 9007199254740993, "long"),
    ("bin(true, 1)", 1, "long"),
    ("bin(7, 0)", None, "long"),
    ("bin(7.5, 2)", 6.0, "real"),
    ("bin(-7.5, 2)", -8.0, "real"),
    ("bin(-1.5, 1)", -2.0, "real"),
    ("bin(0.3, 0.1)", 0.2, "real"),
    ("bin(7, 2.5)", 5.0, "real"),
    ("bin(7.0, 0.0)", NAN, "real"),
    ("bin(7.5, 0)", NAN, "real"),
    ("floor(-7, 5)", -10, "long"),
    # NaN's string form.
    ("tostring(real(nan))", "NaN", "string"),
    ("tostring(bin(1.0, real(+inf)))", "NaN", "string"),
    ("strcat(real(nan))", "NaN", "string"),
    ("tostring(real(-inf))", "-inf", "string"),
]

#: `(query, rows)` — measured over rows, where Kusto's row engine answers.
OVER_ROWS: list[tuple[str, list[tuple]]] = [
    (
        "datatable(v:long, r:long)[7,5, -7,5, 7,-5, -7,-5, 0,5, 7,0, long(null),5,"
        " -9223372036854775807,2, -9223372036854775805,4, 9223372036854775805,-3]"
        " | project b = bin(v, r), t = gettype(bin(v, r))",
        [(5, "long"), (-10, "long"), (10, "long"), (-5, "long"), (0, "long"), (None, "long"),
         (None, "long"), (-9223372036854775808, "long"), (-9223372036854775808, "long"),
         (9223372036854775806, "long")],
    ),
    (
        "datatable(v:real, r:real)[real(+inf),1.0, real(-inf),1.0, real(nan),1.0, 1.0,real(+inf),"
        " 5.0,-2.0, 7.0,0.0, 0.0,0.0, -2.5,1.0, 1e300,1e-300, -0.3,0.1, real(null),1.0,"
        " 1.0,real(null), real(null),0.0]"
        " | project b = bin(v, r), s = tostring(bin(v, r))",
        [(math.inf, "inf"), (-math.inf, "-inf"), (NAN, "NaN"), (NAN, "NaN"), (6.0, "6.0"),
         (NAN, "NaN"), (NAN, "NaN"), (-3.0, "-3.0"), (math.inf, "inf"),
         (-0.30000000000000004, ANY), (None, ""), (None, ""), (None, "")],
    ),
    (
        "datatable(v:long, r:real)[7,2.5, -7,2.5, 7,0.0, 9007199254740993,1.0]"
        " | project b = bin(v, r), t = gettype(bin(v, r))",
        [(5.0, "real"), (-7.5, "real"), (NAN, "real"), (9007199254740992.0, "real")],
    ),
    (
        "datatable(v:real, r:long)[7.5,2, -7.5,2, 7.5,0]"
        " | project b = bin(v, r), t = gettype(bin(v, r))",
        [(6.0, "real"), (-8.0, "real"), (NAN, "real")],
    ),
    (
        "datatable(v:int, r:int)[7,5, -7,5, 7,0] | project b = bin(v, r), t = gettype(bin(v, r))",
        [(5, "long"), (-10, "long"), (None, "long")],
    ),
    (
        "datatable(b:bool, i:int)[true, 5]"
        " | project a = abs(b), x = binary_and(b, 1), s = sign(b), n = bin(b, 1),"
        " k = binary_not(i), l = binary_shift_left(i, 40), t = gettype(binary_shift_right(i, 1))",
        [(1, 1, 1.0, 1, -6, 5497558138880, "long")],
    ),
    (
        "datatable(l:long, r:real, i:int)[long(null), real(null), int(null)]"
        " | project a = sign(l), b = sign(r), c = sign(i),"
        " d = isnull(abs(l)), e = isnull(binary_and(l, 1))",
        [(0.0, 0.0, 0.0, True, True)],
    ),
    (
        "print a = int(null), b = long(null), c = real(null), d = datetime(null),"
        " e = timespan(null), f = bool(null), g = guid(null)"
        " | project gettype(a), gettype(b), gettype(c), gettype(d),"
        " gettype(e), gettype(f), gettype(g)",
        [("int", "long", "real", "datetime", "timespan", "bool", "guid")],
    ),
]


@pytest.fixture(scope="module")
def con():
    c = duckdb_kql.connect()
    yield c
    c.close()


def _same(got: object, want: object) -> bool:
    if isinstance(want, float) and math.isnan(want):
        return isinstance(got, float) and math.isnan(got)
    if isinstance(want, int) and not isinstance(want, bool):
        return type(got) is int and got == want  # exact: not a double that rounds to it
    return got == want


@pytest.mark.parametrize(("expr", "value", "kind"), FOLDED, ids=[e for e, _, _ in FOLDED])
def test_folded_value_and_type(con, expr: str, value: object, kind: str) -> None:
    got, got_kind = duckdb_kql.query(con, f"print v = {expr} | extend t = gettype(v)").fetchone()
    assert _same(got, value), got
    assert got_kind == kind


@pytest.mark.parametrize(("query", "rows"), OVER_ROWS, ids=range(len(OVER_ROWS)))
def test_over_rows(con, query: str, rows: list[tuple]) -> None:
    got = duckdb_kql.query(con, query).fetchall()
    assert len(got) == len(rows)
    for g, w in zip(got, rows, strict=True):
        assert all(x is ANY or _same(y, x) for y, x in zip(g, w, strict=True)), (g, w)


@pytest.mark.parametrize(
    "literal",
    # Each one measured: SYN0002, "could not be parsed as a literal of type".
    ["int(-0x10)", "int(+0x10)", "int(2147483648)", "int(-2147483649)",
     "long(9223372036854775808)", "int(1.0)", "0x1FFFFFFFFFFFFFFFF",
     # Python's int() reads each of these; Kusto refuses them.
     "int(1_0)", "long(0x1_F)", "long(\u0663)"],
)
def test_a_literal_kusto_cannot_parse_is_refused(literal: str) -> None:
    with pytest.raises(KqlError, match="could not be parsed as a literal"):
        duckdb_kql.to_sql(f"print v = {literal}")


@pytest.mark.parametrize(
    ("v", "r"),
    # Kusto answers 9223372036854775804, -9223372036854775804 and
    # 9223372036854775806: `(v - r ± 1) / r * r` wrapped past the range.
    [(-9223372036854775806, 4), (9223372036854775807, -4), (-9223372036854775808, 3)],
)
def test_bin_past_the_long_range_is_an_error_not_a_different_number(con, v: int, r: int) -> None:
    with pytest.raises(duckdb.Error, match="Overflow"):
        duckdb_kql.query(con, f"datatable(v:long, r:long)[{v},{r}] | project bin(v, r)").fetchall()


def test_bin_widens_at_run_time_not_by_static_cast(con) -> None:
    """A real column binned by an int literal stays a real, unrounded."""
    query = "datatable(r:real)[-1.5, 2.75] | project bin(r, 1), abs(r), gettype(bin(r, 1))"
    got = duckdb_kql.query(con, query).fetchall()
    assert got == [(-2.0, 1.5, "real"), (2.0, 2.75, "real")]


def test_datetime_arithmetic_still_binds(con) -> None:
    """Why `+` widens statically: the run-time widening cannot mix with TIMESTAMP."""
    query = "print t = datetime(2020-01-01) + 1d - 1h | extend k = gettype(t)"
    assert "CASE WHEN false" not in str(duckdb_kql.to_sql(query))
    t, kind = duckdb_kql.query(con, query).fetchone()
    assert (str(t), kind) == ("2020-01-01 23:00:00", "datetime")
