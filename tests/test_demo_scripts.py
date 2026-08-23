"""The shell demo in `demo/`.

`serve-multi-db.sh` is the only place the whole multi-database story is written
down as a runnable thing: two DuckDB files built by the DuckDB CLI, attached by
an init script, queried across by one KQL statement. A demo nobody runs is
documentation that lies, so this runs it.

The DuckDB *CLI* is a separate download from the Python package, so the
end-to-end run is skipped where it is absent. What is not skipped is everything
checkable without it — that the scripts exist, that the SQL they contain
actually builds the tables the KQL query names, and that the KQL query the
script embeds still translates.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

DEMO = Path("demo")
SCRIPT = DEMO / "serve-multi-db.sh"
SQL_DIR = DEMO / "sql"

pytestmark = pytest.mark.skipif(
    not SCRIPT.is_file(), reason="run from the repo root"
)

duckdb = pytest.importorskip("duckdb")


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_the_script_is_executable() -> None:
    """Committed without the bit set, `./serve-multi-db.sh` is a permission
    error rather than a demo."""
    assert os.access(SCRIPT, os.X_OK), f"chmod +x {SCRIPT}"


def test_the_sql_scripts_the_demo_names_exist() -> None:
    for name in ("sales.sql", "customers.sql", "attach.sql"):
        assert (SQL_DIR / name).is_file(), f"{SQL_DIR / name} is missing"
        assert f"sql/{name}" in _script(), f"{name} is not referenced by the script"


def test_the_init_script_attaches_both_databases() -> None:
    attach = (SQL_DIR / "attach.sql").read_text(encoding="utf-8")
    assert "ATTACH 'sales.duckdb'" in attach
    assert "ATTACH 'customers.duckdb'" in attach
    # Read-only at the ATTACH is stronger than trusting the translated surface
    # to stay read-only by accident. Counted over the statements, not the whole
    # file, so the comment explaining why cannot satisfy the assertion.
    attaches = [line for line in attach.splitlines() if line.strip().startswith("ATTACH")]
    assert len(attaches) == 2
    assert all("READ_ONLY" in line for line in attaches)


def test_the_sample_sql_builds_the_tables_the_query_names(tmp_path) -> None:
    """Runs the demo's own .sql through DuckDB — no CLI needed for this part."""
    for name, table in (("sales", "Orders"), ("customers", "Customers")):
        con = duckdb.connect(str(tmp_path / f"{name}.duckdb"))
        con.execute((SQL_DIR / f"{name}.sql").read_text(encoding="utf-8"))
        (count,) = con.execute(f"SELECT count(*) FROM {table}").fetchone()
        assert count > 0, f"{name}.sql produced an empty {table}"
        con.close()


def test_the_embedded_kql_query_still_translates(tmp_path) -> None:
    """The demo's query is checked against the demo's own schema.

    Extracted from the script rather than copied here: a copy would keep passing
    after the script's query drifted, which is the failure this is for.
    """
    import duckdb_kql

    script = _script()
    # Everything between the heredoc marker and its terminator. The `.split`
    # on a newline first drops the rest of the `read` line (`|| true`), which
    # would otherwise arrive as a leading `||` and fail to parse.
    body = script.split("<<'KQL'", 1)[1].split("\n", 1)[1]
    query = body.split("\nKQL\n", 1)[0].strip()
    assert 'database("Sales")' in query, "the demo stopped being cross-database"
    assert 'database("Customers")' in query

    con = duckdb_kql.connect()
    for name in ("sales", "customers"):
        path = tmp_path / f"{name}.duckdb"
        build = duckdb.connect(str(path))
        build.execute((SQL_DIR / f"{name}.sql").read_text(encoding="utf-8"))
        build.close()
    con.execute(f"ATTACH '{tmp_path / 'sales.duckdb'}' AS Sales (READ_ONLY)")
    con.execute(f"ATTACH '{tmp_path / 'customers.duckdb'}' AS Customers (READ_ONLY)")

    rows = duckdb_kql.kql(con, query).fetchall()
    assert rows, "the demo query returned nothing"


@pytest.mark.skipif(shutil.which("duckdb") is None, reason="the DuckDB CLI is not installed")
@pytest.mark.skipif(shutil.which("curl") is None, reason="curl is not installed")
def test_the_whole_demo_runs(tmp_path) -> None:
    """End to end: build the files, serve them, query across them over HTTP.

    `--check` is the non-interactive path, and it exists so this test can run
    the same script a person runs rather than a paraphrase of it.
    """
    env = {
        **os.environ,
        # Not the documented port: a developer may well have the real one busy.
        "PORT": "31419",
    }
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [str(SCRIPT.resolve()), "--check"],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "==> ok" in proc.stdout
    # The query groups by Region and Tier, which live in `customers.duckdb`
    # while the money lives in `sales.duckdb` — so a result row is proof the
    # join crossed the two files.
    assert "Revenue" in proc.stdout
    assert "eu-west-1" in proc.stdout or "us-east-1" in proc.stdout
