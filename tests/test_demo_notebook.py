"""The demo notebook is a claim about what the package does. Check it.

Everything in `demo/` is committed with its outputs, because that is how it gets
read — on GitHub, by someone deciding whether this package is worth installing.
Those committed outputs are exactly what makes it dangerous: they keep looking
authoritative long after the code they describe has moved. A demo that has
quietly stopped working is a documented claim that the package does something it
no longer does.

So the notebook is re-executed here, from the committed source, and any cell
that raises fails the build. Outputs are not diffed — the notebook prints
timings and random-ish aggregates, and asserting on those would make this a test
of the demo's prose rather than of the package.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

DEMO = Path("demo")

nbformat = pytest.importorskip("nbformat")

# Skip only when the directory itself is absent — that means we are not at the
# repo root. Anything else is checked, including "the directory is there and has
# no notebook in it", which is a bug rather than a reason to stay quiet.
pytestmark = pytest.mark.skipif(not DEMO.is_dir(), reason="run from the repo root")

#: Found, not named. Renaming the file used to make every test below skip with
#: "run from the repo root" — including the one that executes it — so the demo
#: went unverified while the suite stayed green. A missing notebook is now a
#: failure; a renamed one is simply picked up.
NOTEBOOKS = sorted(DEMO.glob("*.ipynb"))


def test_the_demo_directory_still_has_a_notebook() -> None:
    assert NOTEBOOKS, (
        "demo/ contains no .ipynb — if it moved, the tests below are testing "
        "nothing at all"
    )


def _notebook(path: Path) -> nbformat.NotebookNode:
    return nbformat.read(path, as_version=4)


@pytest.mark.parametrize("path", NOTEBOOKS, ids=str)
def test_the_notebook_json_has_no_duplicate_keys(path: Path) -> None:
    """A notebook can be malformed in a way every Python reader forgives.

    An editor wrote `"execution_count"` twice into three cells. Python's `json`
    keeps the last of a duplicated key, so `nbformat` loaded it, the notebook
    executed, and every check here passed — while `ruff`, whose parser is
    stricter, rejected the file outright and failed CI with a message pointing
    at line 1 of a 500-line JSON document.

    Checked here so the failure names the key and the cell instead.
    """
    import json  # noqa: PLC0415
    from collections import Counter  # noqa: PLC0415

    duplicated: list[str] = []

    def find(pairs: list[tuple[str, object]]) -> dict[str, object]:
        counts = Counter(key for key, _ in pairs)
        duplicated.extend(key for key, n in counts.items() if n > 1)
        return dict(pairs)

    json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=find)
    assert not duplicated, (
        f"{path} repeats {sorted(set(duplicated))} within a single JSON object. "
        "Python reads it happily and ruff does not; re-save the notebook."
    )


@pytest.mark.parametrize("path", NOTEBOOKS, ids=str)
def test_the_notebook_has_code_in_it(path: Path) -> None:
    cells = [c for c in _notebook(path).cells if c.cell_type == "code"]
    assert cells, f"{path} has no code cells"


@pytest.mark.parametrize("path", NOTEBOOKS, ids=str)
def test_every_code_cell_compiles(path: Path) -> None:
    """A cheap check that runs even where a kernel is not available.

    Cells are run through IPython's input transformer first. A notebook is not
    plain Python — `!duckdb-kql demo.kql` and `%timeit` are valid in a cell and a
    SyntaxError to `compile()` — so checking the raw source would report the
    notebook's own syntax as broken.
    """
    transform = pytest.importorskip(
        "IPython.core.inputtransformer2"
    ).TransformerManager().transform_cell

    for i, cell in enumerate(_notebook(path).cells):
        if cell.cell_type != "code":
            continue
        compile(transform(cell.source), f"<{path} cell {i}>", "exec")


@pytest.mark.parametrize("path", NOTEBOOKS, ids=str)
def test_every_cell_was_actually_run(path: Path) -> None:
    """An unexecuted notebook renders as a wall of empty cells on GitHub.

    Measured by ``execution_count`` rather than by counting outputs: a cell that
    assigns without printing has no output and was still run, and a threshold on
    "how many cells have output" silently stops meaning anything the moment the
    notebook is resized — which is exactly what happened when it went from 43
    cells to 18.
    """
    unrun = [
        i
        for i, c in enumerate(_notebook(path).cells)
        if c.cell_type == "code" and c.get("execution_count") is None
    ]
    assert not unrun, (
        f"cells {unrun} of {path} were never run — run every cell and save "
        "before committing, or the published outputs are a fiction"
    )


@pytest.mark.skipif(
    importlib.util.find_spec("nbclient") is None
    or importlib.util.find_spec("ipykernel") is None,
    reason="nbclient and ipykernel are needed to execute the notebook",
)
@pytest.mark.parametrize("path", NOTEBOOKS, ids=str)
def test_the_notebook_still_runs(path: Path) -> None:
    """The one that matters: every cell executes against the current code.

    The notebook is hand-maintained, so nothing but this stops it drifting from
    the API it demonstrates. Its first cell installs the package only when it is
    not already importable, so this runs against the working tree, not PyPI.
    """
    from nbclient import NotebookClient  # noqa: PLC0415
    from nbclient.exceptions import CellExecutionError  # noqa: PLC0415

    nb = _notebook(path)
    client = NotebookClient(
        nb,
        timeout=600,
        kernel_name="python3",
        resources={"metadata": {"path": str(path.parent)}},
    )
    try:
        client.execute()
    except CellExecutionError as exc:  # pragma: no cover - only on a real break
        pytest.fail(f"{path} no longer runs:\n{exc}")
