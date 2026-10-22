"""Shared test helpers.

Small enough that a conftest is the right home: two test modules read
``pyproject.toml`` to check that what the package *claims* — its extras, its
one hard dependency, that ``py.typed`` is shipped — matches what it declares.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(scope="session")
def pyproject() -> dict[str, Any]:
    return read_pyproject()


def read_pyproject() -> dict[str, Any]:
    """Parse ``pyproject.toml``."""
    import tomllib

    parsed: dict[str, Any] = tomllib.loads(
        Path("pyproject.toml").read_text(encoding="utf-8")
    )
    return parsed


# ---------------------------------------------------------------------------
# Coverage notes that survive parallel runs
# ---------------------------------------------------------------------------
#
# A few tests print a coverage line rather than asserting — the corpus sweep's
# pass count, its slowest query, the Azure Monitor profile. They are the numbers
# a human reads to see whether a change moved anything, and under `pytest-xdist`
# a plain `print` from a worker is swallowed: worker output only reaches the
# terminal when a test fails.
#
# So the line is collected instead, handed back over xdist's `workeroutput`
# channel, and written by the controller in the terminal summary. Works
# unchanged with xdist disabled, where the "worker" and the controller are the
# same process and the list is simply already full.

_NOTES: list[str] = []


def report_note(line: str) -> None:
    """Record a line for the end-of-run summary. Safe under xdist."""
    _NOTES.append(line)


def pytest_sessionfinish(session: pytest.Session) -> None:
    output = getattr(session.config, "workeroutput", None)
    if output is not None:  # running as an xdist worker
        output["duckdb_kql_notes"] = _NOTES


def pytest_testnodedown(node: Any, error: Any) -> None:
    """Controller side: drain each worker's notes as it finishes."""
    _NOTES.extend(getattr(node, "workeroutput", {}).get("duckdb_kql_notes") or [])


def pytest_terminal_summary(terminalreporter: Any) -> None:
    for line in _NOTES:
        terminalreporter.write_line(line)
