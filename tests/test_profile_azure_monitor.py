"""Coverage against a published KQL dialect profile.

Azure Monitor documents exactly which KQL its data-collection transformations
support. That list is a **useful external yardstick**: it is a real product's
real subset, published and dated, rather than our own idea of what matters.

Coverage is *measured*, not declared. Every entry in
``tests/profiles/azure-monitor.json`` carries a probe that must translate **and
execute** — a function sitting in the registry but broken in practice counts as
missing, which is the point.

The known gaps are enumerated below rather than merely counted, so that closing
one is a visible change and a *new* gap cannot hide inside a percentage.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import report_note

duckdb = pytest.importorskip("duckdb")

PROFILE = Path("tests/profiles/azure-monitor.json")

pytestmark = pytest.mark.skipif(not PROFILE.is_file(), reason=f"no profile at {PROFILE}")

#: Probes that must pass. May only go UP.
#:
#: Kept honest by `test_the_two_counts_describe_one_profile`: this and
#: :data:`KNOWN_GAPS` are two views of the same 119 probes, and they drifted —
#: `parse` was implemented, removed from the gap list, and this was left at 114
#: for three days, in a commit whose own message read "Azure Monitor 114 -> 115".
BASELINE_SUPPORTED = 115

#: Every known gap, with why it is still open. Checked exactly — an entry that
#: starts passing fails the build (remove it), and an unlisted failure fails the
#: build too (it is a regression or a newly-published feature).
KNOWN_GAPS = {
    "columnifexists": (
        "Needs the input schema at translation time to decide whether the "
        "column exists. The schema plumbing exists (join uses it); this just "
        "has not been wired through."
    ),
    "parse_xml": (
        "DuckDB has no XML parser: no `xml()` and no XML extension in the "
        "pinned build, checked rather than assumed. The odd one out here — the "
        "emulator implements it (`parse_xml('<a>1</a>')` answers `{'a': 1}`), so "
        "unlike parse_cef_dictionary and geo_location this one has ground truth "
        "and is the only gap of the four that is both implementable and "
        "verifiable. §7 permits a Python UDF for exactly this case, but nothing "
        "in the tree registers one yet, so it is scaffolding plus a mapping."
    ),
    "parse_cef_dictionary": (
        "Azure Monitor only — not part of KQL proper, so the emulator cannot "
        "provide ground truth for it either."
    ),
    "geo_location": (
        "Azure Monitor only, and it calls an external IP geolocation service. "
        "Out of scope for an offline transpiler by construction."
    ),
}


def _results():
    import sys

    sys.path.insert(0, "tools")
    from check_profile import evaluate

    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    return evaluate(profile)


@pytest.fixture(scope="module")
def results():
    return _results()


def _flat(results):
    return [e for entries in results.values() for e in entries]


def test_profile_records_its_provenance() -> None:
    """A dated snapshot of someone else's docs is only useful if it says so."""
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    for field in ("source", "source_repo", "source_date", "captured"):
        assert profile.get(field), f"profile is missing {field}"


def test_coverage_has_not_regressed(results) -> None:
    supported = sum(1 for e in _flat(results) if e["supported"])
    assert supported >= BASELINE_SUPPORTED, (
        f"Azure Monitor profile coverage regressed: {supported} < "
        f"{BASELINE_SUPPORTED} probes passing"
    )


def test_gaps_are_exactly_the_known_ones(results) -> None:
    """The gap list must match reality in both directions.

    A closed gap left in the list overstates what is missing; an unlisted
    failure is either a regression or a feature the upstream docs added since
    this profile was captured. Both deserve attention, so both fail.
    """
    failing = {e["name"] for e in _flat(results) if not e["supported"]}
    known = set(KNOWN_GAPS)

    closed = sorted(known - failing)
    assert not closed, (
        f"{len(closed)} known gaps now pass — remove them from KNOWN_GAPS: {closed}"
    )

    unexpected = sorted(failing - known)
    detail = {
        e["name"]: e["reason"][:100]
        for e in _flat(results)
        if e["name"] in unexpected
    }
    assert not unexpected, (
        f"{len(unexpected)} probes fail that are not recorded as known gaps: {detail}"
    )


def test_the_two_counts_describe_one_profile(results) -> None:
    """`BASELINE_SUPPORTED` and `KNOWN_GAPS` are two views of the same probes.

    Every probe either passes or is a known gap — `test_gaps_are_exactly_the_
    known_ones` enforces that in both directions — so the two must add up to the
    profile's size. They are maintained by hand and separately, which is how
    they came apart: closing the `parse` gap updated one and not the other,
    leaving the ratchet a probe behind and able to absorb a regression silently.

    Deriving the baseline would remove the redundancy, but the redundancy is
    what makes a bad edit visible; checking it costs one assertion.
    """
    total = len(_flat(results))
    assert BASELINE_SUPPORTED + len(KNOWN_GAPS) == total, (
        f"BASELINE_SUPPORTED ({BASELINE_SUPPORTED}) + {len(KNOWN_GAPS)} known "
        f"gaps != {total} probes — one of the two was not updated"
    )


#: The prose write-up. Nothing checked it until `parse` was implemented and the
#: page went on naming it as the most valuable next step for three days.
GAP_ANALYSIS = Path("docs/azure-monitor-profile.md")

#: Profile category -> the row label the write-up gives it. Two names for one
#: thing, which is the arrangement that rots; this is where they are tied.
_GROUP_ROWS = {
    "tabular_operators": "Tabular operators",
    "string_operators": "String operators",
    "scalar_functions.bitwise": "Bitwise functions",
    "scalar_functions.conversion": "Conversion functions",
    "scalar_functions.datetime": "Datetime/timespan functions",
    "scalar_functions.dynamic": "Dynamic & array functions",
    "scalar_functions.math": "Mathematical functions",
    "scalar_functions.conditional": "Conditional functions",
    "scalar_functions.string": "String functions",
    "scalar_functions.type": "Type functions",
    "scalar_functions.transformation_only": "Transformation-only functions",
}


def test_the_gap_analysis_states_the_measured_totals(results) -> None:
    """The write-up's headline numbers, against what the probes actually do."""
    import re

    text = GAP_ANALYSIS.read_text(encoding="utf-8")
    flat = _flat(results)
    supported = sum(1 for e in flat if e["supported"])

    probes = re.search(r"^\| Probes \| (\d+) \|$", text, re.MULTILINE)
    assert probes and int(probes.group(1)) == len(flat), (
        f"{GAP_ANALYSIS} states {probes and probes.group(1)} probes; there are {len(flat)}"
    )

    passing = re.search(r"^\| \*\*Passing\*\* \| \*\*(\d+) \((\d+)%\)\*\* \|$", text, re.MULTILINE)
    assert passing, f"{GAP_ANALYSIS} no longer states a passing count"
    assert int(passing.group(1)) == supported, (
        f"{GAP_ANALYSIS} says {passing.group(1)} passing; measured {supported}"
    )
    assert int(passing.group(2)) == 100 * supported // len(flat), (
        f"{GAP_ANALYSIS}'s percentage does not match its own count"
    )


def test_the_gap_analysis_lists_the_same_gaps(results) -> None:
    """Its table and `KNOWN_GAPS` are one list written twice.

    `parse` outlived its gap here by three days *and* was still headlined "most
    valuable next step" — the page is the one a reader meets first, so a stale
    entry there is worse than a stale constant.
    """
    import re

    text = GAP_ANALYSIS.read_text(encoding="utf-8")
    listed = set(re.findall(r"^\| `([a-z_0-9]+)`(?: operator)? \| ", text, re.MULTILINE))
    assert listed == set(KNOWN_GAPS), (
        f"{GAP_ANALYSIS} lists {sorted(listed)}; KNOWN_GAPS has {sorted(KNOWN_GAPS)}"
    )

    heading = re.search(r"^## The (\w+) gaps$", text, re.MULTILINE)
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
    assert heading and heading.group(1) == words[len(KNOWN_GAPS)], (
        f"{GAP_ANALYSIS} is headed 'The {heading and heading.group(1)} gaps'; "
        f"there are {len(KNOWN_GAPS)}"
    )


def test_the_gap_analysis_coverage_table_is_current(results) -> None:
    """The per-group table — the row that rotted silently was `7/9`."""
    import re

    text = GAP_ANALYSIS.read_text(encoding="utf-8")
    for category, label in _GROUP_ROWS.items():
        entries = results[category]
        expected = f"{sum(1 for e in entries if e['supported'])}/{len(entries)}"
        row = re.search(rf"^\| {re.escape(label)} \| (\S+) \|$", text, re.MULTILINE)
        assert row, f"{GAP_ANALYSIS} has no coverage row for {label!r}"
        assert row.group(1) == expected, (
            f"{GAP_ANALYSIS} says {label} is {row.group(1)}; measured {expected}"
        )


def test_the_readme_states_the_measured_coverage(results) -> None:
    """The README quotes this profile too, and nothing checked that either.

    The gap-analysis page and the README are two more copies of one measured
    number. `docs/azure-monitor-profile.md` was bound to reality in 0d78597 and
    this line was left unbound in the same pass — which is how the previous one
    got missed as well.
    """
    import re

    flat = _flat(results)
    supported = sum(1 for e in flat if e["supported"])
    readme = Path("README.md").read_text(encoding="utf-8")

    claimed = re.search(
        r"Azure Monitor's published KQL subset[^|]*\| \*\*(\d+) / (\d+) \((\d+)%\)\*\*",
        readme,
    )
    assert claimed, "README no longer states an Azure Monitor coverage figure"
    assert (int(claimed.group(1)), int(claimed.group(2))) == (supported, len(flat)), (
        f"README says {claimed.group(1)}/{claimed.group(2)} Azure Monitor probes; "
        f"measured {supported}/{len(flat)}"
    )
    assert int(claimed.group(3)) == 100 * supported // len(flat), (
        "README's Azure Monitor percentage does not match its own count"
    )


def test_every_gap_has_a_reason() -> None:
    for name, reason in KNOWN_GAPS.items():
        assert len(reason) > 30, f"{name} needs a real explanation, not a placeholder"


def test_report_coverage(results) -> None:
    """Not an assertion — reports the coverage line for visibility.

    Via `report_note` rather than `print`, so it survives a parallel run; see
    tests/conftest.py.
    """
    flat = _flat(results)
    supported = sum(1 for e in flat if e["supported"])
    report_note(
        f"  Azure Monitor profile: {supported}/{len(flat)} probes pass "
        f"({100 * supported // len(flat)}%) | {len(KNOWN_GAPS)} known gaps"
    )
