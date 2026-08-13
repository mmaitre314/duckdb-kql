"""What a *caller's* type checker sees. The reason ``py.typed`` is shippable.

Shipping the marker is a claim: "this package's annotations are worth trusting."
Before it was added, a checker told the user plainly that the package was
untyped, and they could react. After it, an unannotated function silently
resolves to ``Any`` — the errors disappear, nothing is checked, and nobody is
told. That is a worse position than having no marker at all, and it is invisible
from inside the package: ``mypy src/`` passing says nothing about what a
consumer sees.

So the check runs mypy over a small consumer script, from outside, and asserts
the *revealed* types are real. If a public signature ever decays to ``Any``,
this fails; a green ``mypy src/`` will not notice.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("duckdb")

pytestmark = [
    pytest.mark.skipif(
        importlib.util.find_spec("mypy") is None, reason="mypy is not installed"
    ),
    pytest.mark.skipif(
        not Path("src/duckdb_kql").is_dir(), reason="run from the repo root"
    ),
]


SRC = Path("src").resolve()


@pytest.fixture(scope="module")
def cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One mypy cache for the module.

    Each check spawns a full mypy run over the whole package; sharing the
    incremental cache turns eleven cold analyses into one.
    """
    return tmp_path_factory.mktemp("mypy-cache")


def _reveal(body: str, tmp_path: Path, cache: Path) -> tuple[list[str], str]:
    """Run mypy over *body*; return its revealed types and its raw output.

    The consumer is checked against the *source tree* rather than an installed
    copy, so the result reflects the working tree, and with a bare config so the
    package's own mypy settings cannot flatter it.
    """
    script = tmp_path / "consumer.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    config = tmp_path / "mypy.ini"
    # No `python_version` pin, for the same reason as pyproject's mypy config:
    # pinning it below the installed stubs' syntax makes mypy refuse to parse
    # them. The consumer's checker targets whatever they run anyway.
    #
    # `follow_imports = silent` reproduces the consumer's position exactly: the
    # package's *types* are used, but errors inside it are not reported. A real
    # caller has it in site-packages, where mypy already behaves this way; here
    # it is on MYPYPATH, where mypy would otherwise also grade the vendored
    # ANTLR parser and drown the result.
    config.write_text("[mypy]\nfollow_imports = silent\n", encoding="utf-8")

    # `sys.executable -m mypy`, not whichever `mypy` is on PATH: a tool-installed
    # mypy runs in its own environment and cannot see duckdb or pandas-stubs, so
    # every type below would silently be Any and the test would pass by failing
    # to look.
    env = dict(os.environ, MYPYPATH=str(SRC))
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(config),
            "--cache-dir",
            str(cache),
            "--no-error-summary",
            str(script),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    output = proc.stdout + proc.stderr

    # The failure this whole file guards against is types quietly degrading to
    # Any. An unresolved import degrades *everything* to Any while looking like
    # a pass, so it has to be caught here rather than asserted around.
    for blind_spot in ("import-untyped", "import-not-found", "import-error"):
        assert blind_spot not in output, (
            f"mypy could not resolve an import ({blind_spot}), so every revealed "
            f"type below is meaningless:\n{output}"
        )
    revealed = re.findall(r'note: Revealed type is "([^"]+)"', output)
    # mypy 1.x prints `builtins.list[...]`, mypy 2.x prints `list[...]`. The
    # distinction is cosmetic and would otherwise pin these tests to one release.
    return [t.replace("builtins.", "") for t in revealed], output


def _types(body: str, tmp_path: Path, cache: Path) -> list[str]:
    revealed, output = _reveal(body, tmp_path, cache)
    assert revealed, f"mypy revealed nothing; it probably failed:\n{output}"
    return revealed


# ---------------------------------------------------------------------------
# The marker itself
# ---------------------------------------------------------------------------


def test_py_typed_marker_is_present() -> None:
    """PEP 561: without this file the annotations are invisible to callers."""
    assert Path("src/duckdb_kql/py.typed").is_file()


def test_py_typed_is_shipped_in_the_wheel() -> None:
    """A marker that is not packaged does nothing.

    Hatchling includes package data by default, but "by default" is a thing
    that changes; the wheel is what the user gets.
    """
    from conftest import read_pyproject  # noqa: PLC0415

    config = read_pyproject()
    packages = config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert packages == ["src/duckdb_kql"], (
        "the wheel no longer ships the package directory wholesale, so py.typed "
        "may not be included — check it explicitly"
    )
    sdist_excludes = config["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"]
    assert not any("py.typed" in e for e in sdist_excludes)


# ---------------------------------------------------------------------------
# What a consumer actually gets
# ---------------------------------------------------------------------------


def test_layer_0_signatures_are_real(tmp_path: Path, cache: Path) -> None:
    types = _types(
        """
        import duckdb_kql

        def go() -> None:
            reveal_type(duckdb_kql.to_sql("print 1"))
            reveal_type(duckdb_kql.to_sql("print 1").parameters)
            reveal_type(duckdb_kql.to_sql("print 1").unbound)
            reveal_type(duckdb_kql.validate("print 1"))
            reveal_type(duckdb_kql.query_parameters("print 1"))
            reveal_type(duckdb_kql.parse("print 1").ok)
        """,
        tmp_path,
        cache,
    )
    assert types[0].endswith("TranslationResult")
    assert types[1] == "dict[str, Any]"
    assert types[2] == "tuple[str, ...]"
    assert types[3] == "list[duckdb_kql.errors.Diagnostic]"
    assert types[4] == "list[duckdb_kql.params.ParameterDeclaration]"
    assert types[5] == "bool"


def test_layer_1_returns_duckdb_types(tmp_path: Path, cache: Path) -> None:
    """The layering claim in reverse: an *optional* dependency is still typed.

    ``duckdb`` is imported only under ``TYPE_CHECKING``, so this passing is what
    shows the annotations survived being kept out of the runtime import graph.
    """
    types = _types(
        """
        import duckdb_kql

        def go() -> None:
            con = duckdb_kql.connect()
            reveal_type(con)
            reveal_type(duckdb_kql.kql(con, "T | count"))
            reveal_type(duckdb_kql.execute(con, "T | count"))
            reveal_type(duckdb_kql.engine.schema(con))
        """,
        tmp_path,
        cache,
    )
    assert types[0].endswith("DuckDBPyConnection"), types[0]
    assert types[1].endswith("DuckDBPyRelation"), types[1]
    assert types[2].endswith("DuckDBPyConnection"), types[2]
    assert types[3] == "dict[str, list[str]]"


def test_layer_2_signatures_are_real(tmp_path: Path, cache: Path) -> None:
    types = _types(
        """
        from duckdb_kql.kusto import ClientRequestProperties, KustoClient

        def go() -> None:
            client = KustoClient(":memory:")
            response = client.execute("db", "T | count")
            reveal_type(response)
            reveal_type(response.primary_results)
            reveal_type(response.primary_results[0].raw_rows)
            reveal_type(ClientRequestProperties().to_json())
        """,
        tmp_path,
        cache,
    )
    assert types[0].endswith("KustoResponseDataSet"), types[0]
    assert types[1].endswith("KustoResultTable]"), types[1]
    assert types[2] == "list[list[Any]]"
    assert types[3] == "str"


# ---------------------------------------------------------------------------
# Mistakes a caller can now be told about
# ---------------------------------------------------------------------------

WRONG_USAGE = [
    pytest.param(
        'duckdb_kql.to_sql(123)',
        "arg-type",
        id="to_sql-takes-a-string",
    ),
    pytest.param(
        'duckdb_kql.kql("not a connection", "T | count")',
        "arg-type",
        id="sql-takes-a-connection",
    ),
    pytest.param(
        'duckdb_kql.connect().no_such_method()',
        "attr-defined",
        id="connection-is-not-Any",
    ),
    pytest.param(
        'duckdb_kql.validate("print 1").no_such_method()',
        "attr-defined",
        id="diagnostics-are-a-real-list",
    ),
]


@pytest.mark.parametrize("call,code", WRONG_USAGE)
def test_misuse_is_reported(tmp_path: Path, cache: Path, call: str, code: str) -> None:
    """Each of these was silently accepted before the marker had teeth."""
    _, output = _reveal(
        f"import duckdb_kql\n\ndef go() -> None:\n    {call}\n", tmp_path, cache
    )
    assert f"[{code}]" in output, f"expected a {code} error, got:\n{output}"


def test_a_correct_call_is_not_reported(tmp_path: Path, cache: Path) -> None:
    """The counterweight: real types must not reject legitimate code."""
    _, output = _reveal(
        """
        import duckdb_kql

        def go() -> None:
            con = duckdb_kql.connect("analytics.duckdb")
            rel = duckdb_kql.kql(con, "T | count", {"p": "x"})
            rows = rel.fetchall()
            print(rows, duckdb_kql.to_sql("print 1").parameters)
        """,
        tmp_path,
        cache,
    )
    assert "error:" not in output, output


def test_arrow_is_any_because_pyarrow_ships_no_types(
    tmp_path: Path, cache: Path
) -> None:
    """``arrow()`` cannot be typed better than ``Any`` from here.

    ``pyarrow`` ships neither ``py.typed`` nor stubs, so ``pa.Table`` resolves
    to ``Any`` for everyone. Asserting it keeps the limitation visible: if
    pyarrow ever gains type information this fails, and the docs saying
    otherwise get fixed with it.
    """
    pytest.importorskip("pyarrow")
    types = _types(
        """
        import duckdb_kql

        def go() -> None:
            reveal_type(duckdb_kql.arrow(duckdb_kql.connect(), "T | count"))
        """,
        tmp_path,
        cache,
    )
    assert types[0] == "Any", (
        f"pyarrow now has type information ({types[0]}) — tighten arrow()'s "
        "return type and update docs/api.md"
    )


def test_the_parse_tree_is_declared_any_not_inferred(tmp_path: Path, cache: Path) -> None:
    """``ParseResult.tree`` is Any on purpose — the ANTLR runtime is untyped.

    Asserting it rather than leaving it undocumented is the point: a reader can
    tell the difference between "we decided" and "we forgot".
    """
    types = _types(
        """
        import duckdb_kql

        def go() -> None:
            reveal_type(duckdb_kql.parse("print 1").tree)
        """,
        tmp_path,
        cache,
    )
    assert types[0] == "Any"
