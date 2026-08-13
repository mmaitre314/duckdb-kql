# Demo

[**`duckdb-kql-demo.ipynb`**](duckdb-kql-demo.ipynb) — a tour of all three API
layers, the parameter binding that makes injection structurally impossible, and
the KQL/SQL semantic traps the project exists to get right.

It is committed **with its outputs**, so it reads as a document on GitHub without
running anything. Everything in it is self-contained: the data is generated
in-process, and nothing is downloaded.

## Running it

### In a container, with nothing installed locally

[`.devcontainer/demo/`](../.devcontainer/demo/devcontainer.json) is a
**user's** environment, not a contributor's: a stock Python image plus
`pip install duckdb-kql[all] ipykernel`, and none of the development machinery —
no JDK, no Docker-in-Docker, no Kusto Emulator, no editable install of this
repository. The notebook therefore exercises the package as published, not the
working tree.

Open the repository in VS Code, **Reopen in Container**, and pick
*duckdb-kql demo (PyPI)*. Or start it in the cloud:

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/mmaitre314/duckdb-kql?devcontainer_path=.devcontainer%2Fdemo%2Fdevcontainer.json)

### Locally

The notebook installs the package itself — from PyPI if it is published there,
and otherwise from the checkout it is sitting in — so this is enough:

```bash
pip install jupyterlab
jupyter lab demo/duckdb-kql-demo.ipynb
```

## Editing it

The notebook is **generated**. Edit [`build_notebook.py`](build_notebook.py),
not the `.ipynb`: a notebook is JSON with embedded outputs, which reviews badly
and merges worse.

```bash
pip install -e ".[dev]"
python demo/build_notebook.py   # regenerate the .ipynb from the Python source
python demo/run_notebook.py     # execute it in place, so the outputs are real
```

`tests/test_demo_notebook.py` re-executes the committed notebook on every CI run.
A demo that has quietly stopped working is worse than no demo — it is a
documented claim that the package does something it no longer does — so it fails
the build instead.
