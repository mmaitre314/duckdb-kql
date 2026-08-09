"""The CI and release workflows make promises. These check they still hold.

A workflow that stops running is invisible: the checks simply go green because
there are none. The three things asserted here are the ones whose absence would
be silent — that CI runs on pull requests and on `main`, that publishing needs a
published release rather than a push, and that the badges in the README point at
workflows that exist.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOWS = Path(".github/workflows")

pytestmark = pytest.mark.skipif(
    not WORKFLOWS.is_dir(), reason="run from the repo root"
)


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    # `on` is YAML 1.1's boolean true, so PyYAML parses the key as True.
    return workflow.get("on") or workflow[True]


def test_ci_runs_on_pull_requests() -> None:
    """Every pull request must be tested before it can be merged."""
    triggers = _triggers(_load("ci.yml"))
    assert "pull_request" in triggers, (
        "CI no longer runs on pull requests — changes would reach main untested"
    )
    branches = (triggers["pull_request"] or {}).get("branches")
    assert branches is None or "main" in branches, (
        f"CI's pull_request trigger excludes main: {branches}"
    )


def test_ci_runs_on_pushes_to_main() -> None:
    """The badge reports `main`, so something has to run there."""
    triggers = _triggers(_load("ci.yml"))
    assert "main" in triggers["push"]["branches"]


def test_the_ci_matrix_spans_the_supported_range() -> None:
    """The lanes must be the floor and the ceiling of what we claim to support.

    Claiming 3.10–3.13 and testing 3.10–3.11 is how a 3.13-only failure reaches
    ``main``: numpy 2.5 ships stubs using PEP 695 syntax and only resolves on
    the newer interpreters, so it broke a lane nobody had run locally.
    """
    from conftest import read_pyproject  # noqa: PLC0415

    project = read_pyproject()["project"]
    matrix = [
        str(v)
        for v in _load("ci.yml")["jobs"]["test"]["strategy"]["matrix"]["python-version"]
    ]

    floor = project["requires-python"].lstrip(">=")
    assert floor in matrix, (
        f"requires-python is >={floor} but CI never runs it: {matrix}"
    )

    declared = sorted(
        (c.rsplit(" :: ", 1)[1] for c in project["classifiers"] if c.count(".") == 1
         and c.startswith("Programming Language :: Python :: 3.")),
        key=lambda v: tuple(int(p) for p in v.split(".")),
    )
    assert declared[-1] in matrix, (
        f"the newest declared Python is {declared[-1]} but CI never runs it: {matrix}"
    )


def test_ci_still_runs_the_tests_and_the_linter() -> None:
    steps = [
        step.get("run", "")
        for job in _load("ci.yml")["jobs"].values()
        for step in job.get("steps", [])
    ]
    joined = "\n".join(steps)
    assert "pytest" in joined, "CI no longer runs the test suite"
    assert "ruff check" in joined, "CI no longer runs the linter"


def test_release_builds_on_pull_requests_too() -> None:
    """Packaging breaks with the commit that caused it, not at release time."""
    triggers = _triggers(_load("release.yml"))
    assert "pull_request" in triggers


def test_publishing_requires_a_published_release() -> None:
    """A push must never be able to publish.

    Uploading to PyPI is irreversible — a version number cannot be reused — so
    the trigger has to be something a person did on purpose.
    """
    publish = _load("release.yml")["jobs"]["publish-to-pypi"]
    condition = publish["if"]
    assert "github.event_name == 'release'" in condition
    assert "github.event.action == 'published'" in condition


def test_publishing_uses_trusted_publishing_not_a_stored_token() -> None:
    """OIDC means there is no long-lived credential in this repository to leak."""
    jobs = _load("release.yml")["jobs"]
    for name in ("publish-to-pypi", "publish-to-testpypi"):
        job = jobs[name]
        assert job["permissions"]["id-token"] == "write", f"{name} lost OIDC access"
        steps = "\n".join(str(s) for s in job["steps"])
        assert "password" not in steps, f"{name} appears to use a stored token"


def test_readme_badges_point_at_workflows_that_exist() -> None:
    """A badge for a deleted workflow renders as a broken image, forever."""
    import re  # noqa: PLC0415

    readme = Path("README.md").read_text(encoding="utf-8")
    referenced = set(
        re.findall(r"actions/workflows/([\w.-]+\.yml)/badge\.svg", readme)
    )
    assert referenced, "the README no longer shows any CI badge"

    missing = sorted(name for name in referenced if not (WORKFLOWS / name).is_file())
    assert not missing, f"README badges reference missing workflows: {missing}"


def test_the_release_build_checks_out_the_tags() -> None:
    """hatch-vcs reads the version out of git, so the tags have to be there.

    This replaced a job that compared the tag against two hard-coded version
    strings. Deriving the version instead removes the disagreement entirely, but
    it introduces one failure that is worse for being quiet: `actions/checkout`
    is shallow by default, hatch-vcs then sees no tag, and the build publishes a
    dev version under a release's name. A PyPI version number cannot be reused,
    so this has to fail in CI rather than on the index.
    """
    build = _load("release.yml")["jobs"]["build"]
    checkout = next(
        s for s in build["steps"] if str(s.get("uses", "")).startswith("actions/checkout")
    )
    assert checkout.get("with", {}).get("fetch-depth") == 0, (
        "the release build does a shallow checkout, so hatch-vcs cannot see the "
        "tag and would publish a dev version"
    )


def test_the_version_is_not_written_down_anywhere() -> None:
    """One source of truth: the tag.

    A literal version in pyproject.toml is what made a release a multi-file
    edit, and what made it possible for the tag and the package to disagree.
    """
    from conftest import read_pyproject  # noqa: PLC0415

    project = read_pyproject()["project"]
    assert "version" not in project, (
        "pyproject.toml pins a literal version again; the tag should be the "
        "only source (dynamic = ['version'])"
    )
    assert "version" in project.get("dynamic", [])


def test_untagged_builds_do_not_get_a_local_version() -> None:
    """A `+g1a2b3c` local version is rejected by every index on upload.

    Without this the TestPyPI dry run fails on exactly the commits it exists to
    rehearse — the ones that have no tag yet.
    """
    from conftest import read_pyproject  # noqa: PLC0415

    raw = read_pyproject()["tool"]["hatch"]["version"]["raw-options"]
    assert raw.get("local_scheme") == "no-local-version"
