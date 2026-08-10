#!/usr/bin/env python3
"""Execute `demo/duckdb-kql-demo.ipynb` in place, so its outputs are real.

A demo notebook is read on GitHub far more often than it is run, which means the
committed outputs are the demo. Executing it before committing is what keeps
those outputs honest — and `tests/test_demo_notebook.py` re-executes it in CI so
they cannot drift.

    python demo/build_notebook.py && python demo/run_notebook.py
"""

from __future__ import annotations

from pathlib import Path

import nbformat as nbf
from nbclient import NotebookClient

NOTEBOOK = Path(__file__).with_name("duckdb-kql-demo.ipynb")


def execute(path: Path, timeout: int = 300) -> nbf.NotebookNode:
    nb = nbf.read(path, as_version=4)
    # cwd=the repo root: the install cell looks for a pyproject.toml above the
    # working directory, and the reader's copy will be run from somewhere else.
    NotebookClient(
        nb,
        timeout=timeout,
        kernel_name="python3",
        resources={"metadata": {"path": str(path.parent)}},
    ).execute()
    return nb


def main() -> None:
    nb = execute(NOTEBOOK)
    nbf.write(nb, NOTEBOOK)
    print(f"executed and wrote {NOTEBOOK}")


if __name__ == "__main__":
    main()
