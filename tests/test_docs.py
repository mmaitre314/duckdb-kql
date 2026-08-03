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

pytestmark = pytest.mark.skipif(not README.is_file(), reason="run from the repo root")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------

#: Markdown links, minus the image form and anything absolute.
_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)#][^)]*)\)")


@pytest.mark.parametrize(
    "doc",
    sorted(str(p) for p in [README, *DOCS.glob("*.md")]),
)
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
    try:
        import tomllib
    except ImportError:  # Python 3.9/3.10
        tomllib = pytest.importorskip("tomli")
    return tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))


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


def test_readme_coverage_number_is_not_ahead_of_the_baseline() -> None:
    """The README quotes a corpus number; it must not overstate the tested one."""
    baseline = re.search(
        r"^BASELINE_PASSING = (\d+)$",
        _read(Path("tests/test_behavior.py")),
        re.MULTILINE,
    )
    assert baseline, "tests/test_behavior.py no longer declares BASELINE_PASSING"

    claimed = re.search(r"\*\*(\d+)\*\* of 1036", _read(README))
    assert claimed, "README no longer states a corpus coverage number"
    assert int(claimed.group(1)) <= int(baseline.group(1)), (
        "README claims more passing corpus cases than the test baseline enforces"
    )
