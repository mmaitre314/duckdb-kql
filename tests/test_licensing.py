"""Licence obligations, as checks rather than as prose.

A notices file is the kind of document that is true on the day it is written and
quietly false a year later. Each obligation below therefore has something
mechanical standing behind it.

Verified once, by hand, and recorded here so the *conclusions* are testable:

* `microsoft/Kusto-Query-Language` at the pinned commit is **Apache-2.0**
  ("Copyright 2019 Microsoft Corporation") and has **no `NOTICE` file**, so
  §4(d) does not apply. Both vendored `.g4` files match the upstream SHA-256s
  recorded in `grammar/UPSTREAM.md` byte for byte.
* The committed logo SVGs regenerate byte-identically from **DejaVu Sans Mono**,
  which is what pins that attribution to a specific typeface.
* `azure-kusto-data` is **MIT**; the compat layer reimplements its interface but
  a few helper bodies match closely, so the notice is carried.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import tomllib

NOTICES = Path("THIRD-PARTY-NOTICES.md")
LICENSES = Path("licenses")

pytestmark = pytest.mark.skipif(not NOTICES.is_file(), reason="run from the repo root")


def _notices() -> str:
    return NOTICES.read_text(encoding="utf-8")


def _pyproject() -> dict:
    return tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Every referenced licence text exists and is a licence
# ---------------------------------------------------------------------------


def test_every_licence_file_referenced_by_the_notices_exists() -> None:
    referenced = set(re.findall(r"\(licenses/([^)]+)\)", _notices()))
    assert referenced, "the notices no longer link to any licence text"
    for name in referenced:
        assert (LICENSES / name).is_file(), f"{NOTICES} links to a missing licenses/{name}"


def test_every_licence_file_is_referenced_by_the_notices() -> None:
    """The other direction: a text nobody points at is a text nobody reads."""
    referenced = set(re.findall(r"\(licenses/([^)]+)\)", _notices()))
    for path in LICENSES.glob("*.txt"):
        assert path.name in referenced, f"{path} is not referenced by {NOTICES}"


@pytest.mark.parametrize(
    "name,marker",
    [
        ("Apache-2.0-Kusto-Query-Language.txt", "Apache License"),
        ("MIT-azure-kusto-python.txt", "MIT License"),
        ("Bitstream-Vera-DejaVu.txt", "Bitstream Vera Fonts Copyright"),
    ],
)
def test_the_licence_texts_are_the_real_thing(name, marker) -> None:
    """A placeholder or a stub would satisfy "the file exists" and nothing else."""
    text = (LICENSES / name).read_text(encoding="utf-8")
    assert marker in text, f"licenses/{name} does not look like {marker}"
    assert len(text) > 900, f"licenses/{name} is too short to be a licence"


def test_the_apache_licence_carries_the_upstream_copyright() -> None:
    """Apache-2.0 §4(c) — retain the attribution notices from the source."""
    text = (LICENSES / "Apache-2.0-Kusto-Query-Language.txt").read_text(encoding="utf-8")
    assert "Copyright 2019 Microsoft Corporation" in text
    assert "Copyright 2019 Microsoft Corporation" in _notices()


# ---------------------------------------------------------------------------
# The distributions actually carry them
# ---------------------------------------------------------------------------


def test_the_wheel_ships_the_notices_and_licence_texts() -> None:
    """The gap this suite was written for.

    The wheel contains `src/duckdb_kql/_antlr/`, generated from the Apache-2.0
    grammar, and Apache-2.0 §4(a) requires a recipient of a derivative work to
    get a copy of the licence. Before `license-files` was set, `pip install`
    delivered the derived parser and no notice at all — nothing failed, because
    nothing looked.
    """
    declared = _pyproject()["project"].get("license-files")
    assert declared, "pyproject no longer declares license-files; the wheel ships no notices"
    assert "THIRD-PARTY-NOTICES.md" in declared
    assert any("licenses/" in pattern for pattern in declared), declared


def test_the_sdist_does_not_exclude_the_notices() -> None:
    excluded = _pyproject()["tool"]["hatch"]["build"]["targets"]["sdist"].get("exclude", [])
    for pattern in excluded:
        assert "licenses" not in pattern and "NOTICES" not in pattern, pattern


# ---------------------------------------------------------------------------
# Attributions that would go stale silently
# ---------------------------------------------------------------------------


def test_the_notices_name_the_font_the_generator_actually_uses() -> None:
    """Swapping the typeface without updating the notice is the failure mode.

    The generator's default is what produced the committed SVGs, so the notice
    has to name that face and not whichever one the docs happen to mention.
    """
    generator = Path("docs/assets/generate_logo.py")
    if not generator.is_file():
        pytest.skip("no logo generator in this checkout")
    default = re.search(r'"--font",\s*default="([^"]+)"', generator.read_text(encoding="utf-8"))
    assert default, "generate_logo.py no longer declares a default font"

    face = Path(default.group(1)).stem  # DejaVuSansMono
    # Compared with spacing and case removed, so "DejaVu Sans Mono" in the
    # notices matches the filename however either side chooses to write it.
    squashed = re.sub(r"[^a-z]", "", _notices().lower())
    assert re.sub(r"[^a-z]", "", face.lower()) in squashed, (
        f"generate_logo.py defaults to {face}, which {NOTICES} does not name"
    )


def test_the_grammar_provenance_matches_the_notices() -> None:
    """One commit, recorded in two places; they must not drift."""
    upstream = Path("grammar/UPSTREAM.md").read_text(encoding="utf-8")
    (pinned,) = set(re.findall(r"\b([0-9a-f]{40})\b", upstream))
    assert pinned in _notices(), f"{NOTICES} names a different commit than grammar/UPSTREAM.md"


def test_the_notices_state_the_grammar_was_modified() -> None:
    """Apache-2.0 §4(b): modified files must say so, prominently."""
    assert re.search(r"modified", _notices(), re.IGNORECASE)
    assert "PATCH duckdb-kql/" in _notices()


def test_the_compat_layer_declares_its_provenance() -> None:
    """The review's acceptance criterion for the Kusto SDK layer."""
    doc = Path("docs/kusto-client.md").read_text(encoding="utf-8")
    assert "## Provenance" in doc
    assert "reimplements" in doc
    assert "azure-kusto-data" in _notices(), "the MIT notice for the SDK is missing"


# ---------------------------------------------------------------------------
# The emulator: correctness oracle only
# ---------------------------------------------------------------------------

#: Words that would turn a correctness claim into a performance claim. The
#: emulator's terms restrict disclosing benchmark results, so this is a licence
#: obligation and not a style preference.
_PERFORMANCE_WORDS = ("benchmark", "throughput", "latency", "faster", "slower", "speedup")

#: Files whose whole subject *is* the prohibition, so they necessarily use the
#: words in order to forbid them.
_ALLOWED_TO_SAY_IT = {
    "docs/licensing.md",
    "docs/oracle-harness.md",
    "docs/test-plan.md",
    "docs/code-review",
    "CONTRIBUTING.md",
    "docker-compose.yml",
    ".github/workflows/oracle.yml",
    "tests/test_licensing.py",
}


def test_the_oracle_harness_states_the_scope_of_use() -> None:
    doc = Path("docs/oracle-harness.md").read_text(encoding="utf-8")
    assert "## Scope of use" in doc
    assert "correctness oracle" in doc
    assert "never a runtime dependency" in doc
    assert "never redistributed" in doc


def test_the_readme_describes_coverage_as_correctness() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    coverage = readme.split("## Coverage", 1)[1].split("\n## ", 1)[0]
    assert "orrectness" in coverage or "onformance" in coverage
    for word in _PERFORMANCE_WORDS:
        assert word not in coverage.lower(), (
            f"the Coverage section says {word!r}; these are conformance numbers"
        )


def test_no_performance_claim_is_attached_to_the_emulator() -> None:
    """Grep the repo, the way the licence review does.

    Hits are allowed only in the files whose subject is the prohibition itself.
    Anything else naming the emulator alongside performance vocabulary is the
    thing the terms forbid.
    """
    offenders = []
    for path in [*Path(".").rglob("*.md"), *Path(".").rglob("*.py"), *Path(".").rglob("*.yml")]:
        text = str(path)
        if any(part in text for part in ("tests/cases", "tests/fixtures", ".git/", "_antlr")):
            continue
        if any(text.startswith(allowed) for allowed in _ALLOWED_TO_SAY_IT):
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        # Sentence-level, not file-level. A doc may discuss the emulator in one
        # paragraph and Bun's rewrite speed in another without claiming
        # anything about the emulator; only co-occurrence in one sentence is
        # the association the terms are about.
        for sentence in re.split(r"(?<=[.!?])\s+|\n\n", body):
            low = sentence.lower()
            if "emulator" not in low and "kustainer" not in low:
                continue
            for word in _PERFORMANCE_WORDS:
                if word in low:
                    offenders.append(f"{path}: {word!r} in a sentence about the emulator:\n    "
                                     + " ".join(sentence.split())[:160])
    assert not offenders, "\n".join(offenders)
