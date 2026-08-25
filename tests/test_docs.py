"""The user docs make checkable claims. These check them.

Documentation drifts silently — that is its normal failure mode — and the claims
here are exactly the ones a reader would act on: which extras to install, which
options are refused, which layer needs which dependency. A doc that lists an
option the code no longer refuses is worse than no list at all, because it will
be believed.

Only the mechanical claims are tested. Prose is not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCS = Path("docs")
README = Path("README.md")
#: Community files live at the root, and their links rot the same way.
ROOT_DOCS = [
    Path(name)
    for name in ("CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md")
]

pytestmark = pytest.mark.skipif(not README.is_file(), reason="run from the repo root")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------

#: Markdown links, minus the image form and anything absolute.
_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)#][^)]*)\)")


#: The README doubles as the PyPI landing page, where a relative link 404s, so
#: its links into this repo are absolute. They still have to point somewhere.
_REPO_BLOB = "https://github.com/mmaitre314/duckdb-kql/blob/main/"

MARKDOWN = sorted(str(p) for p in [README, *DOCS.glob("*.md"), *ROOT_DOCS])


@pytest.mark.parametrize("doc", MARKDOWN)
def test_relative_links_resolve(doc: str) -> None:
    """A broken link in the entry-point docs is the first thing a reader hits."""
    path = Path(doc)
    missing = []
    for target in _LINK.findall(_read(path)):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        resolved = (path.parent / target.split("#")[0]).resolve()
        if not resolved.exists():
            missing.append(target)
    assert not missing, f"{doc} links to files that do not exist: {missing}"


@pytest.mark.parametrize("doc", MARKDOWN)
def test_absolute_links_into_this_repo_resolve(doc: str) -> None:
    """An absolute link to our own files is still a link that can rot.

    Making them absolute for PyPI's sake removes them from the check above, so
    they get their own: the URL is resolved back to a path in the working tree.
    """
    missing = []
    for target in _LINK.findall(_read(Path(doc))):
        if not target.startswith(_REPO_BLOB):
            continue
        relative = target[len(_REPO_BLOB) :].split("#")[0]
        if relative and not Path(relative).exists():
            missing.append(target)
    assert not missing, f"{doc} links to repo files that do not exist: {missing}"


# ---------------------------------------------------------------------------
# The request-option table
# ---------------------------------------------------------------------------


def _option_table() -> str:
    """The `## Request options` section, and nothing else.

    Other tables in the page also start a row with a backticked lower-case
    name — the wire-format one lists Kusto *types* — so the section boundary is
    what makes this an option list rather than a grep.
    """
    doc = _read(DOCS / "kusto-client.md")
    section = re.search(r"^## Request options$(.*?)^## ", doc, re.MULTILINE | re.DOTALL)
    assert section, "docs/kusto-client.md no longer has a `## Request options` section"
    return section.group(1)


def test_option_table_matches_the_code() -> None:
    """``docs/kusto-client.md`` lists every option, and only real ones.

    The table is the reader's answer to "will this option do anything?". An
    entry that has quietly stopped matching OPTION_SUPPORT answers it wrongly.
    """
    pytest.importorskip("duckdb")
    from duckdb_kql.kusto import OPTION_SUPPORT

    documented = set(re.findall(r"^\| `([a-z_]+)` \|", _option_table(), re.MULTILINE))

    undocumented = sorted(set(OPTION_SUPPORT) - documented)
    assert not undocumented, (
        f"{len(undocumented)} request options are classified in OPTION_SUPPORT but "
        f"missing from docs/kusto-client.md: {undocumented}"
    )

    invented = sorted(documented - set(OPTION_SUPPORT))
    assert not invented, (
        f"docs/kusto-client.md documents options the code does not know about: "
        f"{invented}"
    )


def test_documented_support_level_matches_the_code() -> None:
    pytest.importorskip("duckdb")
    from duckdb_kql.kusto import OPTION_SUPPORT, OptionSupport

    labels = {
        "**Implemented**": OptionSupport.IMPLEMENTED,
        "No-op": OptionSupport.NO_OP,
        "Refused": OptionSupport.REFUSED,
    }

    wrong = []
    for name, label in re.findall(
        r"^\| `([a-z_]+)` \| (\*\*Implemented\*\*|No-op|Refused) \|",
        _option_table(),
        re.MULTILINE,
    ):
        expected = OPTION_SUPPORT[name][0]
        if labels[label] != expected:
            wrong.append((name, label, expected))
    assert not wrong, f"documented support level disagrees with the code: {wrong}"


# ---------------------------------------------------------------------------
# Install instructions
# ---------------------------------------------------------------------------


def _pyproject() -> dict:
    from conftest import read_pyproject

    return read_pyproject()


def test_documented_extras_exist() -> None:
    """``pip install 'duckdb-kql[kusto]'`` has to be a thing you can type."""
    extras = set(_pyproject()["project"]["optional-dependencies"])
    documented = set(re.findall(r"duckdb-kql\[([a-z,]+)\]", _read(README)))
    named = {name for group in documented for name in group.split(",")}

    missing = sorted(named - extras)
    assert not missing, f"README documents extras that pyproject does not define: {missing}"


def test_layer_zero_has_exactly_one_dependency() -> None:
    """The whole layering claim rests on this one line of pyproject."""
    deps = _pyproject()["project"]["dependencies"]
    assert len(deps) == 1 and deps[0].startswith("antlr4"), (
        "Layer 0 is documented as needing only the ANTLR runtime; a new hard "
        f"dependency breaks that promise: {deps}"
    )


# ---------------------------------------------------------------------------
# Coverage numbers
# ---------------------------------------------------------------------------


def test_readme_counts_match_the_generated_support_matrix() -> None:
    """The README quotes the matrix's numbers; the matrix is generated.

    Two numbers in two files is one number too many, and the README is the one
    nobody regenerates.
    """
    matrix = _read(DOCS / "kql-support.md")
    readme = _read(README)

    for pattern, label in (
        (r"^(\d+) of (\d+) supported\.$", "tabular operators"),
        (r"^(\d+) supported, grouped by family\.$", "scalar functions"),
    ):
        found = re.search(pattern, matrix, re.MULTILINE)
        assert found, f"the support matrix no longer states its {label} count"

    operators = re.search(r"^(\d+) of (\d+) supported\.$", matrix, re.MULTILINE)
    scalars = re.search(r"^(\d+) supported, grouped by family\.$", matrix, re.MULTILINE)

    claimed_ops = re.search(r"\| Tabular operators \| \*\*(\d+) / (\d+)\*\* \|", readme)
    assert claimed_ops, "README no longer states a tabular-operator count"
    assert claimed_ops.groups() == operators.groups(), (
        f"README says {claimed_ops.groups()} tabular operators, the matrix says "
        f"{operators.groups()}"
    )

    claimed_scalars = re.search(
        r"\| Scalar functions / aggregates / binary operators \| \*\*(\d+) /", readme
    )
    assert claimed_scalars, "README no longer states a scalar-function count"
    assert claimed_scalars.group(1) == scalars.group(1), (
        f"README says {claimed_scalars.group(1)} scalar functions, the matrix says "
        f"{scalars.group(1)}"
    )


def test_readme_coverage_number_matches_the_baseline() -> None:
    """The README quotes a corpus number; it must be *the* tested one.

    This asked only that the README not **overstate** the baseline, which let it
    understate one indefinitely — and it did, sitting at 285 while the baseline
    climbed to 312. Understating is not the safe direction it looks like: it is
    still a wrong number in the first table a reader sees, and the one-sided
    check is why nobody noticed for five commits. Equality is what the sibling
    test above already requires of the matrix counts, on the reasoning in its
    docstring: two numbers in two files is one number too many.
    """
    baseline = re.search(
        r"^BASELINE_PASSING = (\d+)$",
        _read(Path("tests/test_behavior.py")),
        re.MULTILINE,
    )
    assert baseline, "tests/test_behavior.py no longer declares BASELINE_PASSING"

    claimed = re.search(r"\*\*(\d+)\*\* of 1036", _read(README))
    assert claimed, "README no longer states a corpus coverage number"
    assert int(claimed.group(1)) == int(baseline.group(1)), (
        f"README says {claimed.group(1)} passing corpus cases, the baseline "
        f"enforces {baseline.group(1)} — update the README table"
    )


def test_the_query_entry_point_is_not_called_sql() -> None:
    """`kql(con, query)` takes KQL. `to_sql(kql)` returns SQL. Both say so.

    The package exists because KQL and SQL look alike and behave differently, so
    an entry point named `sql()` that takes KQL was the one piece of the API
    arguing against the whole premise — `duckdb_kql.sql(con, "T | count")` reads
    as though the string were SQL.

    Asserted rather than assumed because the old name is the natural thing to
    reach for: DuckDB's own method is `con.sql()`, and a well-meaning alias would
    put the confusion straight back.
    """
    import duckdb_kql

    assert "kql" in duckdb_kql.__all__
    assert "sql" not in duckdb_kql.__all__, (
        "a `sql` entry point is back; the argument is KQL, and only `to_sql` "
        "should carry that name"
    )
    assert not hasattr(duckdb_kql, "sql"), "duckdb_kql.sql resolves again"

    # `to_sql` keeps its name: it is the one that genuinely produces SQL.
    assert callable(duckdb_kql.to_sql)


def test_the_docs_call_it_kql() -> None:
    """Docs that still say `duckdb_kql.sql(` would teach the old name."""
    for path in [README, *sorted(DOCS.glob("*.md"))]:
        if path.parts[:2] == ("docs", "code-review"):
            continue
        text = _read(path)
        assert "duckdb_kql.sql(" not in text, f"{path} still documents duckdb_kql.sql()"


_TRAP_LINE = re.compile(r"^\*Traps?: (.+)\*$", re.MULTILINE)
_TRAP_PATH = re.compile(r"`(tests/[A-Za-z0-9_./]+)`")


def test_every_r_rule_names_a_trap_test_that_exists() -> None:
    """§4's own standard: *"a rule is not 'known' until its test exists"*.

    These citations used to be invented IDs — `trap-r7-identifiers`,
    `trap-r5-join-kinds` — that appeared nowhere in the test tree. That is not
    bookkeeping pedantry. It is how R7's second clause went unimplemented
    without anyone noticing: there was no artifact to open and find missing, so
    the rule read as covered.

    This test is the thing that would have noticed. It is deliberately about
    *existence*, not content — a citation that resolves is a claim someone can
    check, which is the whole difference from an ID that resolves to nothing.
    """
    spec = Path("docs/TRANSLATION.md")
    text = _read(spec)
    citations = _TRAP_LINE.findall(text)
    assert len(citations) >= 21, (
        f"§4 has 21 R-rules; found {len(citations)} trap citations. A rule "
        "without one is a rule nobody has to prove."
    )

    missing = []
    for line in citations:
        paths = _TRAP_PATH.findall(line)
        if not paths:
            missing.append(f"cites no test file at all: {line}")
            continue
        missing += [p for p in paths if not Path(p).exists()]
    assert not missing, f"docs/TRANSLATION.md cites tests that do not exist: {missing}"
