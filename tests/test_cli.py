"""The build-time CLI.

Its whole reason to exist is that the *output* outlives the tool: translate in
CI, run the SQL anywhere, depend on nothing. So the tests care about the things
that would quietly undermine that — a header that churns and makes ``--check``
cry wolf, a parameterized query whose placeholders arrive unexplained, an error
that names a file but not a line, and the tool reaching for a database it
promised not to need.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from duckdb_kql.cli import EXIT_OK, EXIT_STALE, EXIT_TRANSLATION_ERROR, main

SIMPLE = 'StormEvents\n| where State == "TEXAS"\n| count\n'
PARAMETERIZED = (
    "declare query_parameters(state:string, since:datetime = datetime(2007-01-01));\n"
    "StormEvents | where State == state and StartTime > since | count\n"
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A directory of ``.kql`` files, as a user's repository would have."""
    queries = tmp_path / "queries"
    queries.mkdir()
    (queries / "count.kql").write_text(SIMPLE, encoding="utf-8")
    (queries / "by_state.kql").write_text(PARAMETERIZED, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Output routing
# ---------------------------------------------------------------------------


def test_writes_to_stdout_by_default(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Writing files unasked would be a surprising default for a translator."""
    assert main([str(project / "queries" / "count.kql")]) == EXIT_OK
    out = capsys.readouterr().out
    assert "SELECT" in out
    assert not list(project.glob("**/*.sql"))


def test_single_input_and_a_file_target(project: Path) -> None:
    target = project / "out" / "count.sql"
    assert main([str(project / "queries" / "count.kql"), "-o", str(target)]) == EXIT_OK
    assert "SELECT" in target.read_text(encoding="utf-8")


def test_a_directory_input_expands_to_its_kql_files(project: Path) -> None:
    """A build script has a directory, not a shell-expanded list."""
    out = project / "build"
    assert main([str(project / "queries"), "-o", str(out)]) == EXIT_OK
    assert sorted(p.name for p in out.glob("*.sql")) == ["by_state.sql", "count.sql"]


def test_output_directory_is_created(project: Path) -> None:
    out = project / "deep" / "nested" / "build"
    assert main([str(project / "queries"), "-o", str(out)]) == EXIT_OK
    assert (out / "count.sql").is_file()


def test_stdin_is_readable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import io  # noqa: PLC0415

    monkeypatch.setattr("sys.stdin", io.StringIO("print x = 1"))
    assert main(["-"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "<stdin>" in out
    assert 'AS "x"' in out


# ---------------------------------------------------------------------------
# --check: the CI gate
# ---------------------------------------------------------------------------


def test_check_passes_on_freshly_generated_output(project: Path) -> None:
    out = project / "build"
    assert main([str(project / "queries"), "-o", str(out)]) == EXIT_OK
    assert main([str(project / "queries"), "-o", str(out), "--check"]) == EXIT_OK


def test_check_fails_when_the_kql_changed(project: Path) -> None:
    """The point of the mode: an edited query cannot ship a stale .sql."""
    out = project / "build"
    main([str(project / "queries"), "-o", str(out)])
    (project / "queries" / "count.kql").write_text(SIMPLE + "| take 5\n", encoding="utf-8")
    assert main([str(project / "queries"), "-o", str(out), "--check"]) == EXIT_STALE


def test_check_fails_when_the_output_is_missing(project: Path) -> None:
    assert (
        main([str(project / "queries"), "-o", str(project / "nothing"), "--check"])
        == EXIT_STALE
    )


def test_check_writes_nothing(project: Path) -> None:
    out = project / "build"
    main([str(project / "queries"), "-o", str(out), "--check"])
    assert not out.exists()


def test_check_without_an_output_is_a_usage_error(project: Path) -> None:
    """There is nothing to compare stdout against; saying so beats exiting 0."""
    assert main([str(project / "queries"), "--check"]) == 2


def test_check_names_the_stale_files(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = project / "build"
    main([str(project / "queries"), "-o", str(out), "--check"])
    assert "count.sql" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The header
# ---------------------------------------------------------------------------


def test_header_warns_about_the_time_zone(project: Path) -> None:
    """The one thing a build-time consumer cannot infer from the SQL.

    Without ``SET TimeZone='UTC'`` the query runs and returns shifted datetimes.
    Someone running generated SQL has no library to set it for them.
    """
    out = project / "build"
    main([str(project / "queries"), "-o", str(out)])
    assert "SET TimeZone='UTC'" in (out / "count.sql").read_text(encoding="utf-8")


def test_header_carries_no_version_or_timestamp(project: Path) -> None:
    """Otherwise ``--check`` fails after an unrelated upgrade.

    A gate that cries wolf gets ignored, and then it is not a gate.
    """
    from duckdb_kql import __version__  # noqa: PLC0415

    out = project / "build"
    main([str(project / "queries"), "-o", str(out)])
    header = (out / "count.sql").read_text(encoding="utf-8").split("\n\n")[0]
    assert __version__ not in header
    assert "20" not in header.replace("UTC", "")  # no year, no date


def test_header_maps_placeholders_back_to_parameter_names(project: Path) -> None:
    """``$kqlp0`` alone tells a caller nothing about what to bind."""
    out = project / "build"
    main([str(project / "queries"), "-o", str(out)])
    sql = (out / "by_state.sql").read_text(encoding="utf-8")

    assert "$kqlp0" in sql and "state" in sql
    assert "string, required" in sql
    assert "datetime, default 2007-01-01T00:00:00" in sql
    # A Python repr leaking into a SQL comment is noise from another language.
    assert "datetime.datetime(" not in sql


def test_no_header_produces_bare_sql(project: Path) -> None:
    out = project / "build"
    main([str(project / "queries"), "-o", str(out), "--no-header"])
    assert (out / "count.sql").read_text(encoding="utf-8").startswith("WITH")


def test_a_query_without_parameters_gets_no_parameter_block(project: Path) -> None:
    out = project / "build"
    main([str(project / "queries"), "-o", str(out)])
    assert "Query parameters" not in (out / "count.sql").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_a_syntax_error_reports_file_line_and_column(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``file:line:col:`` is what editors and CI annotators parse."""
    bad = tmp_path / "bad.kql"
    bad.write_text("StormEvents | where State ==\n", encoding="utf-8")
    assert main([str(bad)]) == EXIT_TRANSLATION_ERROR
    err = capsys.readouterr().err
    # The shape is what matters, not which line the parser stopped on.
    assert re.match(rf"^{re.escape(bad.as_posix())}:\d+:\d+: error: ", err), err


def test_an_unsupported_construct_is_an_error_not_a_guess(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "unsupported.kql"
    bad.write_text("StormEvents | parse State with * 'x' *\n", encoding="utf-8")
    assert main([str(bad)]) == EXIT_TRANSLATION_ERROR
    assert "error:" in capsys.readouterr().err


def test_one_bad_file_does_not_stop_the_others(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A build reporting one error at a time is a slow build."""
    (project / "queries" / "bad.kql").write_text("| where", encoding="utf-8")
    out = project / "build"
    assert main([str(project / "queries"), "-o", str(out)]) == EXIT_TRANSLATION_ERROR
    assert (out / "count.sql").is_file()


def test_a_missing_file_is_reported_not_raised(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(tmp_path / "nope.kql")]) == EXIT_TRANSLATION_ERROR
    assert "nope.kql" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_schema_file_enables_join(tmp_path: Path) -> None:
    """``join`` is the one operator that needs the input columns."""
    (tmp_path / "j.kql").write_text("T | join kind=inner (R) on a\n", encoding="utf-8")
    (tmp_path / "schema.json").write_text(
        '{"T": ["a", "b"], "R": ["a", "c"]}', encoding="utf-8"
    )
    assert (
        main(
            [
                str(tmp_path / "j.kql"),
                "-o",
                str(tmp_path / "j.sql"),
                "--schema",
                str(tmp_path / "schema.json"),
            ]
        )
        == EXIT_OK
    )
    assert "JOIN" in (tmp_path / "j.sql").read_text(encoding="utf-8")


def test_a_malformed_schema_says_what_was_expected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "q.kql").write_text("print 1", encoding="utf-8")
    (tmp_path / "schema.json").write_text('{"T": "not a list"}', encoding="utf-8")
    assert (
        main([str(tmp_path / "q.kql"), "--schema", str(tmp_path / "schema.json")])
        == EXIT_TRANSLATION_ERROR
    )
    assert "list of column" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The layering promise
# ---------------------------------------------------------------------------


def test_the_cli_does_not_import_duckdb(tmp_path: Path) -> None:
    """Layer 0 only — the reason this command can run in a minimal CI image.

    Checked in a subprocess because this test session has duckdb imported
    already, so an in-process check would prove nothing.
    """
    query = tmp_path / "q.kql"
    query.write_text(SIMPLE, encoding="utf-8")
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys\n"
        "from duckdb_kql.cli import main\n"
        f"main([{str(query)!r}, '-o', {str(tmp_path / 'q.sql')!r}])\n"
        "assert 'duckdb' not in sys.modules, 'the CLI imported duckdb'\n"
        "print('clean')\n",
        encoding="utf-8",
    )
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, str(probe)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "clean" in proc.stdout


def test_python_m_duckdb_kql_works(tmp_path: Path) -> None:
    """The console script may not be on PATH inside an unactivated venv."""
    query = tmp_path / "q.kql"
    query.write_text(SIMPLE, encoding="utf-8")
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "duckdb_kql", str(query)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == EXIT_OK, proc.stderr
    assert "SELECT" in proc.stdout


def test_the_console_script_is_declared() -> None:
    from conftest import read_pyproject  # noqa: PLC0415

    scripts = read_pyproject()["project"]["scripts"]
    assert scripts["duckdb-kql"] == "duckdb_kql.cli:main"
