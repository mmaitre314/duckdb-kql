"""The demo container must stay a *user's* environment, not a contributor's.

`.devcontainer/demo/` exists so someone can open the notebook and see what the
published package does. Every piece of development machinery it grows — a JDK,
Docker-in-Docker, the emulator, an editable install of this repository — makes it
test the working tree instead of the artifact, and makes it slower to start for
no benefit to the person it is for.

That is easy to erode by copy-and-paste from the dev container next door, and the
erosion is invisible: the notebook still runs. So the boundary is asserted here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

DEV = Path(".devcontainer/devcontainer.json")
DEMO = Path(".devcontainer/demo/devcontainer.json")

pytestmark = pytest.mark.skipif(not DEMO.is_file(), reason="run from the repo root")


def _load(path: Path) -> dict:
    """Parse devcontainer.json, which is JSON *with comments*.

    Comments are stripped by scanning rather than by regex: the file is full of
    `https://` URLs, and a naive `//` rule truncates every one of them.
    """
    text = path.read_text(encoding="utf-8")
    out: list[str] = []
    i, n, in_string = 0, len(text), False
    while i < n:
        char = text[i]
        if in_string:
            out.append(char)
            if char == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            i += 1
            continue
        if text.startswith("//", i):
            i = text.find("\n", i)
            if i == -1:
                break
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i)
            i = n if end == -1 else end + 2
            continue
        out.append(char)
        i += 1
    return json.loads("".join(out))


def test_the_demo_container_is_parseable_and_named() -> None:
    config = _load(DEMO)
    assert config["name"], "a container with no name is unpickable in the UI"
    assert config["image"].startswith("mcr.microsoft.com/devcontainers/python"),(
        "the demo should start from a stock Python image — anything else means a "
        "Dockerfile to keep in sync"
    )


def test_the_demo_container_installs_the_published_package() -> None:
    """From PyPI, not from this checkout.

    An editable install would make the notebook demonstrate the working tree
    while claiming to demonstrate the package, which is the one thing this
    container exists to avoid.
    """
    command = _load(DEMO)["postCreateCommand"]
    assert "duckdb-kql[all]" in command, command
    assert " -e " not in command and "[dev]" not in command, (
        f"the demo container installs the repository rather than the release: {command}"
    )
    assert "ipykernel" in command, (
        "the notebook cannot run in VS Code without a kernel in the environment"
    )


def test_the_demo_container_carries_no_development_machinery() -> None:
    """No Java, no Docker-in-Docker, no emulator — none of it is a user's problem."""
    config = _load(DEMO)
    features = " ".join(config.get("features", {})).lower()
    for unwanted in ("java", "docker-in-docker", "docker-outside-of-docker"):
        assert unwanted not in features, f"the demo container grew a {unwanted} feature"

    blob = json.dumps(config).lower()
    for unwanted in ("kustainer", "kusto-emulator", "docker compose"):
        assert unwanted not in blob, f"the demo container references {unwanted}"


def test_the_demo_container_can_open_the_notebook() -> None:
    extensions = _load(DEMO)["customizations"]["vscode"]["extensions"]
    assert "ms-toolsai.jupyter" in extensions, (
        "without the Jupyter extension the container cannot open the one file it "
        "was built for"
    )


def test_the_demo_container_has_a_notebook_to_open() -> None:
    """By existence, not by name — the file has been renamed once already."""
    assert sorted(Path("demo").glob("*.ipynb")), "demo/ has no notebook in it"


def test_the_dev_container_still_has_what_the_demo_one_drops() -> None:
    """The counterweight.

    These two files are easy to confuse, and 'minimal' applied to the wrong one
    would quietly remove the emulator the acceptance suite is measured against.
    """
    if not DEV.is_file():
        pytest.skip("no development container")
    features = " ".join(_load(DEV).get("features", {})).lower()
    assert "docker-in-docker" in features, (
        "the *development* container lost Docker-in-Docker, which the Kusto "
        "Emulator needs (docs/test-plan.md §5.1)"
    )
    assert "java" in features, "the development container lost the JDK ANTLR needs"
