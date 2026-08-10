#!/usr/bin/env python3
"""Build `demo/duckdb-kql-demo.ipynb`.

The notebook is generated rather than hand-edited so the source of truth is a
readable Python file: a `.ipynb` is JSON with embedded outputs, which reviews
badly and merges worse. Regenerate and re-execute with:

    python demo/build_notebook.py && python demo/run_notebook.py

`tests/test_demo_notebook.py` executes the committed notebook, so a demo that
stops working fails the build instead of quietly misleading a reader.
"""

from __future__ import annotations

from pathlib import Path

import nbformat as nbf

OUT = Path(__file__).with_name("duckdb-kql-demo.ipynb")

CELLS: list[tuple[str, str]] = []


def md(text: str) -> None:
    CELLS.append(("md", text.strip("\n")))


def code(text: str) -> None:
    CELLS.append(("code", text.strip("\n")))


# ---------------------------------------------------------------------------

md("""
# duckdb-kql — run KQL queries on DuckDB

[`duckdb-kql`](https://github.com/mmaitre314/duckdb-kql) translates
[Kusto Query Language](https://learn.microsoft.com/azure/data-explorer/kusto/query/)
into DuckDB SQL, so you can run KQL against local files and in-process data with
no cluster and no network.

The project has one governing rule, and most of its design follows from it:

> **A wrong answer is worse than no answer.** KQL and SQL look alike in places
> where they behave differently, so a mapping that is *nearly* right runs
> cleanly and returns numbers nobody questions. Every mapping is verified
> against the real KQL engine, and anything that cannot be verified raises.

The API comes in three layers, each adding exactly one dependency. This notebook
walks all three, then shows the semantic traps that motivate the whole approach.

| Layer | What it does | Needs |
|---|---|---|
| **0** | KQL text → DuckDB SQL | `antlr4-python3-runtime` |
| **1** | Run the SQL | `+ duckdb` |
| **2** | An `azure-kusto-data`-shaped client | `+ pandas` |
""")

md("## Setup")

code('''
# Installs from PyPI if it is published there, and otherwise from the checkout
# this notebook lives in — so it works both as a standalone download and inside
# a clone of the repository.
import importlib.util
import subprocess
import sys
from pathlib import Path


def ensure_installed() -> None:
    if importlib.util.find_spec("duckdb_kql") is not None:
        return
    repo = Path.cwd()
    for candidate in (repo, *repo.parents):
        if (candidate / "pyproject.toml").is_file():
            target = f"{candidate}[all]"
            break
    else:
        # `--pre` because the current releases are pre-releases; pip skips those
        # by default and would report the package as not found at all.
        target = "duckdb-kql[all]"
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet", "--pre", target]
    )


ensure_installed()

# Imported after the install, not at the top: on a fresh machine there is
# nothing to import until the cell above has run.
import duckdb_kql  # noqa: E402

print("duckdb-kql", duckdb_kql.__version__)
''')

# ---------------------------------------------------------------------------

md("""
## Layer 0 — KQL in, SQL out

No database, no connection, no data. This layer is pure text-to-text, and it is
the one you want if you are generating SQL to run somewhere else.
""")

code('''
kql = """
Requests
| where Status >= 500 and Region has "west"
| summarize Failures = count(), Users = dcount(UserId) by Service
| sort by Failures desc
| take 3
"""

print(duckdb_kql.to_sql(kql))
''')

md("""
Note the shape: **one CTE per KQL operator**, in pipeline order. DuckDB's
optimizer collapses the chain, so there is no cost to it, and it means you can
read the generated SQL against the query you wrote. That matters when you are
debugging a result you did not expect.

Two other Layer 0 entry points — checking a query without running it, and
finding out what parameters it declares:
""")

code('''
# validate() reports syntax problems with source spans and never raises.
for diagnostic in duckdb_kql.validate("Requests | wehre Status == 500"):
    print(diagnostic)

print()
print("well-formed query ->", duckdb_kql.validate("Requests | take 1"))
''')

md("""
### Refusing is a feature

An unsupported construct raises with the construct's name. It never falls back
to something *close enough* — that is precisely how a plausible wrong answer
gets shipped.
""")

code('''
for query in [
    "Requests | evaluate bag_unpack(Payload)",
    "Requests | summarize hll(UserId)",
]:
    try:
        duckdb_kql.to_sql(query)
    except duckdb_kql.KqlError as exc:
        print(f"{type(exc).__name__}: {exc}")
''')

# ---------------------------------------------------------------------------

md("""
## Layer 1 — actually run it

`duckdb_kql.connect()` is `duckdb.connect()` plus the `TimeZone='UTC'` that KQL
datetime semantics require. Everything else is a normal DuckDB connection, so
your own tables, Parquet files and CSVs are all queryable.
""")

code('''
import datetime as dt
import random

con = duckdb_kql.connect()

# A small synthetic request log, built in-process. Nothing is downloaded.
random.seed(7)
services = ["checkout", "search", "auth", "catalog"]
regions = ["us-west-2", "us-east-1", "eu-west-1"]
start = dt.datetime(2026, 3, 1)

rows = []
for i in range(2_000):
    service = random.choice(services)
    # `auth` is deliberately the unhealthy one, so the demo queries find signal.
    status = random.choice([200, 200, 200, 500] if service == "auth" else [200, 200, 200, 200, 503])
    rows.append((
        start + dt.timedelta(seconds=i * 37),
        service,
        random.choice(regions),
        status,
        round(random.lognormvariate(3.2, 0.6), 1),
        f"user-{random.randint(1, 120):03d}",
    ))

con.execute("""
    CREATE TABLE Requests (
        Timestamp TIMESTAMP, Service VARCHAR, Region VARCHAR,
        Status INTEGER, LatencyMs DOUBLE, UserId VARCHAR
    )
""")
con.executemany("INSERT INTO Requests VALUES (?, ?, ?, ?, ?, ?)", rows)

print(duckdb_kql.engine.schema(con))
''')

code('''
# sql() returns a DuckDB relation — lazy, composable, and printable.
duckdb_kql.sql(con, """
Requests
| where Status >= 500
| summarize Failures = count(), Users = dcount(UserId) by Service, Region
| sort by Failures desc
| take 5
""")
''')

md("`df()` gives you a pandas DataFrame, and `arrow()` a PyArrow table:")

code('''
frame = duckdb_kql.df(con, """
Requests
| summarize
    Requests = count(),
    P50 = percentile(LatencyMs, 50),
    P95 = percentile(LatencyMs, 95)
  by Service
| sort by P95 desc
""")
frame
''')

md("""
Time bucketing works the way KQL spells it, with `bin()` and timespan literals:
""")

code('''
duckdb_kql.df(con, """
Requests
| where Status >= 500
| summarize Errors = count() by bin(Timestamp, 4h)
| sort by Timestamp asc
""")
''')

# ---------------------------------------------------------------------------

md("""
## Parameters, and why they are not string formatting

`declare query_parameters` values are bound through DuckDB's parameter API. The
generated SQL contains a placeholder — **not the value, and not even the
parameter's name**. There is nothing to escape because nothing is interpolated.
""")

code('''
parameterized = """
declare query_parameters(service:string, floor:long);
Requests
| where Service == service and Status >= floor
| count
"""

translated = duckdb_kql.to_sql(parameterized, parameters={"service": "auth", "floor": 500})
print(translated)
print()
print("values travel beside the SQL:", translated.parameters)
''')

code('''
# The point of that design, demonstrated. This payload is a valid KQL fragment
# and a classic SQL injection attempt; it is neither here, because it never
# reaches the SQL text at all — it is only ever compared as a string.
payload = "auth' OR 1=1 --"

print("rows for a real service :", duckdb_kql.sql(con, parameterized,
      {"service": "auth", "floor": 500}).fetchall())
print("rows for the payload    :", duckdb_kql.sql(con, parameterized,
      {"service": payload, "floor": 500}).fetchall())

sql_text = str(duckdb_kql.to_sql(parameterized, parameters={"service": payload, "floor": 500}))
assert payload not in sql_text
print("\\npayload present in generated SQL:", payload in sql_text)
''')

md("You can also ask a query what it wants before supplying anything:")

code('''
for declaration in duckdb_kql.query_parameters(parameterized):
    print(f"{declaration.name:>8} : {declaration.type}")
''')

# ---------------------------------------------------------------------------

md("""
## Layer 2 — the `azure-kusto-data` shape

If you already have code written against the Kusto Python SDK, this layer lets
it run against DuckDB with the import swapped. The response objects match the
SDK's attribute for attribute, and `raw_rows` carries Kusto's *wire* format, so
the SDK's own converters work on them unchanged.
""")

code('''
from duckdb_kql.kusto import KustoClient
from duckdb_kql.kusto.helpers import dataframe_from_result_table

client = KustoClient(con)

response = client.execute("NetDefaultDB", """
Requests
| where Status >= 500
| summarize Errors = count() by Service
| sort by Errors desc
""")

table = response.primary_results[0]
print("columns  :", [(c.column_name, c.column_type) for c in table.columns])
print("raw_rows :", table.raw_rows[:2])
print()
dataframe_from_result_table(table)
''')

md("""
Parameters go through `ClientRequestProperties`, exactly as they do against a
real cluster:
""")

code('''
from duckdb_kql.kusto import ClientRequestProperties

properties = ClientRequestProperties()
properties.set_parameter("service", "auth")

result = client.execute(
    "NetDefaultDB",
    "declare query_parameters(service:string);\\nRequests | where Service == service | count",
    properties,
)
print(result.primary_results[0].rows[0]["Count"])
''')

md("""
### Options are implemented or refused — never ignored

A request option that silently did nothing would be the worst kind of
compatibility: your code keeps running and stops doing what it says. Every
`ClientRequestProperties` option is classified, and the ones that cannot be
honoured raise.
""")

code('''
from duckdb_kql.kusto.exceptions import KustoError

properties = ClientRequestProperties()
properties.set_option(ClientRequestProperties.request_timeout_option_name, dt.timedelta(seconds=30))
print("servertimeout: accepted and enforced")

try:
    properties.set_option("query_results_cache_max_age", dt.timedelta(minutes=5))
except KustoError as exc:
    print(f"{type(exc).__name__}: {exc}")
''')

# ---------------------------------------------------------------------------

md("""
## The traps this project exists for

KQL and SQL share an enormous amount of syntax and disagree in small, quiet
places. Each of the following is verified against the real Kusto engine (the
Kusto Emulator) rather than inferred from documentation — several of them
contradict the documentation.
""")

md("""
### `has` is term-based; `contains` is substring

`Region has "west"` is **false** for `"westward"`. Translating `has` as
`LIKE '%west%'` would return extra rows on real log data, and nothing would
look wrong.
""")

code('''
duckdb_kql.df(con, """
datatable(Text:string) ["west", "westward", "us-west-2", "WEST"]
| extend
    has_west = Text has "west",
    contains_west = Text contains "west",
    has_cs_west = Text has_cs "west"
""")
''')

md("""
### Null is the *smallest* value when sorting

Ascending puts nulls first; descending puts them last. DuckDB's own default is
`NULLS LAST` in both directions.
""")

code('''
duckdb_kql.sql(con, "datatable(x:int) [3, int(null), 1] | sort by x asc").fetchall()
''')

md("""
### Negated operators keep null rows

`s !contains "x"` is **true** when `s` is null — a plain `NOT (...)` in SQL
yields `NULL`, and `where` then drops the row. That turns into a count that is
quietly too low.
""")

code('''
con.execute("CREATE OR REPLACE TABLE Notes(rid INTEGER, note VARCHAR)")
con.executemany("INSERT INTO Notes VALUES (?, ?)", [(1, "timeout"), (2, None)])

kept = duckdb_kql.sql(con, 'Notes | where note !contains "retry" | count').fetchone()[0]
naive = con.sql("SELECT count(*) FROM Notes WHERE NOT (note ILIKE '%retry%')").fetchone()[0]

print(f"KQL semantics           : {kept} rows")
print(f"a naive NOT (...) in SQL: {naive} rows   <- the null row silently vanishes")
print()
print("Neither note mentions 'retry', so both qualify. Nothing errors either way;")
print("the naive translation just returns a smaller number.")
''')

md("""
### `join` defaults to `innerunique`, not SQL's inner join

This is the most dangerous default in the language: a bare `join` deduplicates
the **left** side's join keys first. Two rows below, where SQL's inner join
gives three — and on real data that difference shows up as inflated counts and
sums that nobody thinks to question.
""")

code('''
duckdb_kql.sql(con, """
let L = datatable(k:string, v:int) ["a", 1, "a", 2, "b", 3];
let R = datatable(k:string, w:int) ["a", 10, "b", 20];
L | join R on k
| sort by k asc
""").fetchall()
''')

md("""
Ask for SQL's semantics and you get them — but you have to ask:
""")

code('''
duckdb_kql.sql(con, """
let L = datatable(k:string, v:int) ["a", 1, "a", 2, "b", 3];
let R = datatable(k:string, w:int) ["a", 10, "b", 20];
L | join kind=inner R on k
| sort by k asc
""").fetchall()
''')

# ---------------------------------------------------------------------------

md("""
## Translating at build time

There is also a `duckdb-kql` command that turns `.kql` files into `.sql` files.
The point is that the *output* has no dependency on this package: translate in
CI, ship the SQL, and run it with nothing but a DuckDB driver.
""")

code('''
import tempfile

workdir = Path(tempfile.mkdtemp())
(workdir / "queries").mkdir()
(workdir / "queries" / "failures.kql").write_text(
    'Requests\\n| where Status >= 500\\n| summarize Errors = count() by Service\\n'
)

# The trailing separator on -o means "a directory of outputs". Without it, a
# single input is written to exactly that path, as a file.
outdir = f"{workdir / 'sql'}/"
subprocess.run(
    [sys.executable, "-m", "duckdb_kql", str(workdir / "queries"), "-o", outdir],
    check=True,
)

print((workdir / "sql" / "failures.sql").read_text())
''')

md("""
The header is not decoration: `SET TimeZone='UTC'` is a *requirement* of the
generated SQL — KQL datetimes are UTC, and DuckDB reads the session timezone
when casting — and nothing else would tell whoever runs the file about it.

`--check` fails a build when a generated `.sql` has fallen behind its `.kql`,
which is what makes this safe to commit and rely on:
""")

code('''
# Edit the query, leave the generated SQL alone, and the check fails.
(workdir / "queries" / "failures.kql").write_text(
    'Requests\\n| where Status >= 500\\n| summarize Errors = count() by Service\\n| take 10\\n'
)

stale = subprocess.run(
    [sys.executable, "-m", "duckdb_kql", str(workdir / "queries"), "-o", outdir, "--check"],
    capture_output=True,
    text=True,
)
print("exit code:", stale.returncode)
print(stale.stdout.strip() or stale.stderr.strip())
''')

# ---------------------------------------------------------------------------

md("""
## Where to go next

- **[KQL support matrix](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/kql-support.md)**
  — every operator and function, supported or not, each with its known
  limitations and divergences. Generated from the translator's registries, so it
  cannot claim support that does not exist.
- **[Getting started](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/getting-started.md)**
  and the **[API reference](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/api.md)**.
- **[Kusto SDK compatibility](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/kusto-client.md)**
  — what Layer 2 implements, what it refuses, and why.
- **[Build-time translation](https://github.com/mmaitre314/duckdb-kql/blob/main/docs/cli.md)**.

Found a query that runs and returns something different from Kusto? That is the
most valuable bug report this project can get —
[open an issue](https://github.com/mmaitre314/duckdb-kql/issues).
""")


def main() -> None:
    nb = nbf.v4.new_notebook()
    nb.cells = [
        nbf.v4.new_markdown_cell(body) if kind == "md" else nbf.v4.new_code_cell(body)
        for kind, body in CELLS
    ]
    nb.metadata = {
        "kernelspec": {
            "display_name": "Python 3",
            "language": "python",
            "name": "python3",
        },
        "language_info": {"name": "python"},
    }
    nbf.write(nb, OUT)
    print(f"wrote {OUT} ({len(nb.cells)} cells)")


if __name__ == "__main__":
    main()
