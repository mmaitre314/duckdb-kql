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
    """Parse ``pyproject.toml``.

    ``tomllib`` arrived in 3.11 and the package supports 3.10, so the older
    interpreter needs ``tomli`` — which the ``dev`` extra installs for exactly
    this. Skipping rather than failing keeps a bare checkout usable.
    """
    try:
        import tomllib
    except ImportError:  # Python 3.10
        tomllib = pytest.importorskip("tomli")
    parsed: dict[str, Any] = tomllib.loads(
        Path("pyproject.toml").read_text(encoding="utf-8")
    )
    return parsed
