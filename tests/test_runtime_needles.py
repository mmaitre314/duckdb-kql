"""Trap tests — a `has` needle known only at run time compiles no regex per row.

A literal needle folds into a constant pattern RE2 compiles once. A needle
known only at run time — a `toscalar`, each element of a `has_any` array, the
rows of `has_any (T)` — had its pattern concatenated per row, so RE2 compiled
a Unicode program per row: about 1.5 ms each, measured. On 200,000 rows
`has_any (toscalar(print 'world'))` took about 110 s and
`has_any (split('world,x', ','))` about 530 s, where a literal takes 40 ms.

**The fix is exact, not approximate.** When haystack and needle are both
ASCII, RE2's `(?i)` *is* `lower()` and its `[^\\pL\\pN]` *is* `[^A-Za-z0-9]`, so:
a needle made only of term characters is tested with `contains` on the
haystack with its delimiters turned into spaces (no per-row compile at all),
any other ASCII needle with a small ASCII pattern, and anything else with the
Unicode pattern exactly as before. `test_every_tier_answers_as_the_unicode_test`
holds the three to the old expression over randomised text; an offline sweep
of 72,000 pairs found no difference, and caught each of three planted faults.

**Why the obvious fixes are wrong.**

* A `contains(lower(h), lower(n))` *prefilter* is not sound against the
  pattern: RE2's `(?i)` folds the Kelvin sign to `k` and the long s `ſ` to
  `s`, which `lower()` does not, so it would drop rows the pattern matches and
  a run-time needle would stop answering as the same needle written as a
  literal. (Kusto, measured, folds neither — see `test_two_characters_re2_folds`
  and the open item in TRANSLATION.md §9. That is a divergence of the whole
  `has` family, to be fixed in both paths at once, not in one by accident.)
* Flattening the needles with a `json_tree` subquery, as this did, is a
  *correlated* subquery once the sharing pass binds the `toscalar` into a
  column: DuckDB ran it for every row, whichever branch the row took, and
  `has_any (toscalar(...))` stayed at 4 s after the regex was fixed.
  `json_extract(j, '$..*')` is the same leaves as a scalar expression.

Literal `hasprefix`/`hassuffix` now fold as `has` always did, rather than
reaching the run-time template: the same pattern, written as a constant.
"""

from __future__ import annotations

import json
import random

import pytest

import duckdb_kql
from duckdb_kql.translate import _runtime_needles
from duckdb_kql.translate.functions import _unicode_term_match, term_match_sql

duckdb = pytest.importorskip("duckdb")

#: Characters chosen to reach every tier and every boundary: ASCII term and
#: delimiter characters, regex metacharacters, and the non-ASCII characters
#: whose case folding differs from `lower()` (Kelvin sign, long s, the sigmas,
#: the Turkish i's) or that sit on a boundary (NBSP, a combining mark).
_ALPHABET = list("abcxyzABCXYZ019 -_.,'\"\\%()*+?[]|^$/\t") + [
    "é", "É", "ß", "ẞ", "\u212a", "ſ", "ς", "σ", "Σ", "İ", "ı", "中", "٣", "\u00a0",
    "😀", "\u0301", "ǅ",
]


def _text(rng: random.Random, lo: int, hi: int, alphabet: list[str] = _ALPHABET) -> str:
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(lo, hi)))


def _pairs(seed: int, count: int) -> list[tuple[str | None, str | None]]:
    rng = random.Random(seed)
    out: list[tuple[str | None, str | None]] = []
    for _ in range(count):
        shape = rng.random()
        if shape < 0.3:  # an ASCII word, in some case, between random neighbours
            needle = _text(rng, 1, 4, list("abcXYZ019"))
            hay = _text(rng, 0, 3) + rng.choice([needle, needle.swapcase()]) + _text(rng, 0, 3)
        elif shape < 0.55:  # the needle is a substring of the haystack
            hay = _text(rng, 1, 12)
            i = rng.randint(0, len(hay))
            needle = hay[i:rng.randint(i, len(hay))]
        elif shape < 0.7:  # Kelvin sign and long s against their ASCII folds
            needle = _text(rng, 1, 3, list("ksKS"))
            swap = {"k": "\u212a", "s": "ſ", "K": "k", "S": "s"}
            hay = "".join(swap.get(c, c) if rng.random() < 0.5 else c for c in needle)
        else:
            hay, needle = _text(rng, 0, 10), _text(rng, 0, 3)
        out.append((None if rng.random() < 0.03 else hay, None if rng.random() < 0.03 else needle))
    return out


@pytest.fixture(scope="module")
def pairs():
    con = duckdb.connect()
    data = _pairs(seed=20261108, count=400)
    con.execute(
        "CREATE TABLE P AS SELECT unnest(?) AS h, unnest(?) AS n",
        [[h for h, _ in data], [n for _, n in data]],
    )
    return con


@pytest.mark.parametrize("case_sensitive", [False, True])
@pytest.mark.parametrize(("lead", "trail"), [(True, True), (True, False), (False, True)])
def test_every_tier_answers_as_the_unicode_test(
    pairs, case_sensitive: bool, lead: bool, trail: bool
) -> None:
    """`has`, `hasprefix` and `hassuffix`, each case-sensitive or not."""
    new = term_match_sql("h", "n", case_sensitive=case_sensitive, lead=lead, trail=trail)
    old = _unicode_term_match("h", "n", "" if case_sensitive else "(?i)", lead=lead, trail=trail)
    differ = pairs.sql(f"SELECT h, n, {new}, {old} FROM P WHERE ({new}) IS DISTINCT FROM ({old})")
    assert differ.fetchall() == []
    matched = pairs.sql(f"SELECT count(*) FILTER (WHERE {old}) FROM P").fetchall()[0][0]
    assert 50 < matched < 350, "the sample must exercise both answers"


@pytest.mark.parametrize(
    ("hay", "needle", "expected"),
    [
        ("x-error", "err", False),
        ("x-error", "error", True),
        ("a b", "a ", True),
        ("xa b", "a ", False),
        ("b .b-", " ", True),
        ("anything", "", True),
    ],
)
def test_the_cases_each_tier_exists_for(hay: str, needle: str, expected: bool) -> None:
    """Kusto's answers, measured with the needle behind a `toscalar` too."""
    con = duckdb.connect()
    sql = term_match_sql("$1", "$2")
    assert con.execute(f"SELECT {sql}", [hay, needle]).fetchall() == [(expected,)]


@pytest.mark.parametrize(("hay", "needle"), [("\u212a", "k"), ("ſ", "s")])
def test_two_characters_re2_folds(hay: str, needle: str) -> None:
    """**Not Kusto's answer.** Measured, Kusto says false for both, folded and
    over rows; RE2's `(?i)` says true. Pinned here only so that a run-time
    needle keeps answering as a literal one does until the `has` family is
    fixed as a whole (TRANSLATION.md §9)."""
    con = duckdb.connect()
    folded = term_match_sql("$1", f"'{needle}'", needle_text=needle)
    literal = con.execute(f"SELECT {folded}", [hay]).fetchall()
    runtime = con.execute(f"SELECT {term_match_sql('$1', '$2')}", [hay, needle]).fetchall()
    assert literal == runtime == [(True,)]


def _json_values(seed: int, count: int) -> list[str]:
    rng = random.Random(seed)

    def value(depth: int = 0) -> object:
        r = rng.random()
        if depth < 3 and r < 0.35:
            return [value(depth + 1) for _ in range(rng.randint(0, 4))]
        if depth < 3 and r < 0.40:
            return {"k": value(depth + 1)}
        return rng.choice([None, True, False, 0, -3, 2.5, 1e5, "", "a b", 'q"t', "é", "\\", "中"])

    return [json.dumps(value()) for _ in range(count)] + ["null", "[]", '"x"', "5", "{}", "[[[]]]"]


def test_the_flattening_is_json_trees_without_the_subquery() -> None:
    """The leaves `json_tree` gave, as a multiset; a bag wherever `json_tree` saw one."""
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE J AS SELECT CAST(j AS JSON) AS j FROM (SELECT unnest(?) AS j)",
        [_json_values(seed=7, count=2000)],
    )
    reference = (
        "(SELECT CASE WHEN bool_or(type = 'OBJECT') THEN NULL ELSE coalesce(list(coalesce("
        "value ->> '$', '')) FILTER (WHERE type NOT IN ('ARRAY', 'OBJECT')), []) END "
        "FROM json_tree(coalesce(to_json(j), 'null'::JSON)))"
    )
    flattened = _runtime_needles("j").replace(
        "error('duckdb-kql: a bag is not a has_any/has_all needle; Kusto refuses it (SEM0022)')",
        "NULL",
    )
    differ = con.sql(
        f"SELECT j FROM J WHERE list_sort({reference}) IS DISTINCT FROM list_sort({flattened})"
    )
    assert differ.fetchall() == []


T = (
    "datatable(s:string)['hello world-42', 'x-error', 'Ünïcode café', 'WORLD', '', "
    "'a b c', 'naïve-world']"
)

#: `(operator, needle, rows kept)` — each measured on the emulator with the
#: needle behind a `toscalar`, so that it is known only at run time.
MEASURED = [
    ("has", "'b c'", ["a b c"]),
    ("has", "'world'", ["WORLD", "hello world-42", "naïve-world"]),
    ("has", "'café'", ["Ünïcode café"]),
    ("has_cs", "'WORLD'", ["WORLD"]),
    ("has_cs", "'-'", ["hello world-42", "naïve-world", "x-error"]),
    ("!has", "'42'", ["", "WORLD", "a b c", "naïve-world", "x-error", "Ünïcode café"]),
    ("hasprefix", "'café'", ["Ünïcode café"]),
    ("hasprefix", "'err'", ["x-error"]),
    ("!hasprefix", "''", []),
    ("hasprefix_cs", "'ünï'", []),
    ("hassuffix", "'error'", ["x-error"]),
    ("hassuffix", "'naïve'", ["naïve-world"]),
    ("hassuffix_cs", "'code'", ["Ünïcode café"]),
]


@pytest.mark.parametrize(("op", "needle", "kept"), MEASURED)
def test_a_toscalar_needle_answers_as_kusto(op: str, needle: str, kept: list[str]) -> None:
    query = f"{T} | where s {op} toscalar(print {needle}) | project s"
    rows = duckdb_kql.kql(duckdb_kql.connect(), query).fetchall()
    assert sorted(r[0] for r in rows) == sorted(kept)


def test_no_subquery_runs_per_row() -> None:
    """The flattening was a `json_tree` subquery, correlated once the
    `toscalar` is bound into a column; it is a scalar expression now."""
    sql = str(duckdb_kql.to_sql(f"{T} | where s has_any (toscalar(print dynamic(['x'])))"))
    assert "json_tree" not in sql


@pytest.mark.parametrize(
    "op", ["hasprefix", "!hasprefix", "hasprefix_cs", "hassuffix", "hassuffix_cs"]
)
def test_a_literal_needle_folds_for_every_term_operator(op: str) -> None:
    """The pattern is a constant: no `CASE` deciding the boundaries per row."""
    sql = str(duckdb_kql.to_sql(f"{T} | where s {op} 'wor'"))
    assert "CASE WHEN regexp_matches('wor'" not in sql
    assert "regexp_escape('wor')" in sql


@pytest.mark.parametrize(
    "where",
    [
        "s has_any (toscalar(print 'world'))",
        "s has_all (toscalar(print dynamic(['hello', 'world'])))",
        "s has_any (split('world,x', ','))",
        "s has toscalar(print 'world')",
    ],
)
def test_a_run_time_needle_costs_no_compile_per_row(where: str) -> None:
    """A guard with a wide margin: 20,000 rows took ~30 s in the old shape
    (1.5 ms of RE2 compile each) and take tens of milliseconds now."""
    import time

    con = duckdb_kql.connect()
    con.execute("CREATE TABLE R AS SELECT 'hello world ' || i AS s FROM range(20000) t(i)")
    started = time.perf_counter()
    rows = duckdb_kql.kql(con, f"R | where {where} | count").fetchall()
    assert rows == [(20000,)]
    assert time.perf_counter() - started < 5.0
