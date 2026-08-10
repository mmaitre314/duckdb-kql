"""The demo notebook is a claim about what the package does. Check it.

`demo/duckdb-kql-demo.ipynb` is committed with its outputs, because that is how
it gets read — on GitHub, by someone deciding whether this package is worth
installing. Committed outputs are exactly what makes it dangerous: they keep
looking authoritative long after the code they describe has moved. A demo that
has quietly stopped working is a documented claim that the package does
something it no longer does.

So the notebook is re-executed here, from the committed source, and any cell
that raises fails the build. Outputs are not diffed — the notebook prints
timings and random-ish aggregates, and asserting on those would make this a test
of the demo's prose rather than of the package.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

NOTEBOOK = Path("demo/duckdb-kql-demo.ipynb")
SOURCE = Path("demo/build_notebook.py")

nbformat = pytest.importorskip("nbformat")

pytestmark = pytest.mark.skipif(
    not NOTEBOOK.is_file(), reason="run from the repo root"
)


def _notebook() -> nbformat.NotebookNode:
    return nbformat.read(NOTEBOOK, as_version=4)


def test_the_notebook_is_generated_from_the_committed_source() -> None:
    """Editing the `.ipynb` directly is how the two drift apart."""
    assert SOURCE.is_file(), "demo/build_notebook.py is missing"
    cells = [c for c in _notebook().cells if c.cell_type == "code"]
    assert cells, "the demo has no code cells"


def test_every_code_cell_compiles() -> None:
    """A cheap check that runs even where a kernel is not available."""
    for i, cell in enumerate(_notebook().cells):
        if cell.cell_type != "code":
            continue
        compile(cell.source, f"<cell {i}>", "exec")


def test_the_committed_outputs_are_not_empty() -> None:
    """An unexecuted notebook renders as a wall of empty cells on GitHub.

    It is also the state the file lands in if someone regenerates without
    re-running, which is easy to do and invisible in a diff full of JSON.
    """
    executed = [
        c
        for c in _notebook().cells
        if c.cell_type == "code" and c.get("outputs")
    ]
    assert len(executed) >= 15, (
        f"only {len(executed)} code cells carry output — the committed notebook "
        "was not executed; run `python demo/run_notebook.py`"
    )


@pytest.mark.skipif(
    importlib.util.find_spec("nbclient") is None
    or importlib.util.find_spec("ipykernel") is None,
    reason="nbclient and ipykernel are needed to execute the notebook",
)
def test_the_notebook_still_runs() -> None:
    """The one that matters: every cell executes against the current code.

    The notebook's first cell installs the package only when it is not already
    importable, so this runs against the working tree rather than PyPI.
    """
    from nbclient import NotebookClient  # noqa: PLC0415
    from nbclient.exceptions import CellExecutionError  # noqa: PLC0415

    nb = _notebook()
    client = NotebookClient(
        nb,
        timeout=600,
        kernel_name="python3",
        resources={"metadata": {"path": str(NOTEBOOK.parent)}},
    )
    try:
        client.execute()
    except CellExecutionError as exc:  # pragma: no cover - only on a real break
        pytest.fail(f"the demo notebook no longer runs:\n{exc}")
