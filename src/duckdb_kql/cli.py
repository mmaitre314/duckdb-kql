"""``duckdb-kql`` — the command line, one subcommand per job.

::

    duckdb-kql translate queries/ -o build/sql/           # KQL files -> SQL files
    duckdb-kql translate queries/ -o build/sql/ --check   # ... and fail if stale
    duckdb-kql serve logs.duckdb                          # a local Kusto endpoint

**translate** is the build-time path, and the point of it is that the *output*
has no dependencies. Translate your queries in CI, commit or ship the ``.sql``,
and the thing that runs them needs nothing from this package — not even DuckDB's
Python bindings. A Go service, a dbt model, a psql script and a notebook can all
read the same file. ``--check`` is the mode that belongs in CI: it regenerates in
memory and compares, so a ``.kql`` edited without regenerating its ``.sql`` fails
the build instead of shipping a stale query.

It is Layer 0 only — no database is opened and ``duckdb`` is never imported, so
``pip install duckdb-kql`` alone is enough to run it.

**serve** is a different job entirely: a local Kusto-compatible HTTP endpoint
over a DuckDB database, so Kusto tools — including the Azure Data Explorer web
UI — can query it. It needs the ``duckdb`` extra.

Every subcommand is explicit. An earlier version took a bare list of files
(``duckdb-kql queries/ -o build/``), which read well while translation was the
only thing this command did, but leaves no room for a second verb: any new one
would be ambiguous with a file of the same name, and the ambiguity would be
silent. Naming the verb costs one word and keeps the space open.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

from . import __version__, to_sql
from .errors import KqlError, KqlSyntaxError

# Layer 0: `server` is stdlib-only at import time and reaches for duckdb only
# once a server is actually built, so naming its defaults here costs nothing.
from .server import ADX_ORIGINS, DEFAULT_PORT

__all__ = ["main"]

#: Exit codes. Distinct so a CI step can tell "your query is wrong" from "your
#: generated file is stale" without scraping the message.
EXIT_OK = 0
EXIT_TRANSLATION_ERROR = 1
#: 2 is argparse's usage error; left alone.
EXIT_STALE = 3


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code rather than raising."""
    args = _parser().parse_args(argv)
    return cast("Callable[[argparse.Namespace], int]", args.run)(args)


# ---------------------------------------------------------------------------
# translate
# ---------------------------------------------------------------------------


def _translate_command(args: argparse.Namespace) -> int:
    """``duckdb-kql translate`` — KQL files in, SQL files out."""
    try:
        schema = _load_schema(args.schema)
    except (OSError, ValueError) as exc:
        print(f"duckdb-kql: --schema: {exc}", file=sys.stderr)
        return EXIT_TRANSLATION_ERROR

    inputs = _expand(args.files)
    if args.check and args.output is None:
        print(
            "duckdb-kql: --check needs -o/--output: there is nothing to compare "
            "against when the SQL goes to stdout",
            file=sys.stderr,
        )
        return 2

    failures = 0
    stale: list[Path] = []

    for source in inputs:
        try:
            sql = _translate(source, schema=schema, header=not args.no_header)
        except KqlError as exc:
            print(_diagnose(source, exc), file=sys.stderr)
            failures += 1
            continue
        except OSError as exc:
            print(f"duckdb-kql: {exc}", file=sys.stderr)
            failures += 1
            continue

        target = _target(source, args.output, len(inputs))
        if target is None:
            sys.stdout.write(sql)
            continue

        if args.check:
            if not target.is_file() or target.read_text(encoding="utf-8") != sql:
                stale.append(target)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(sql, encoding="utf-8")
        if args.verbose:
            print(f"{source} -> {target}", file=sys.stderr)

    if failures:
        return EXIT_TRANSLATION_ERROR
    if stale:
        listing = "\n".join(f"  {p}" for p in stale)
        print(
            f"duckdb-kql: {len(stale)} generated file(s) are missing or out of "
            f"date:\n{listing}\n"
            "Re-run without --check to regenerate.",
            file=sys.stderr,
        )
        return EXIT_STALE
    return EXIT_OK


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


def _serve_command(args: argparse.Namespace) -> int:
    """``duckdb-kql serve`` — a local Kusto endpoint over a DuckDB database."""
    # Imported here, not at module scope: this is the only subcommand that needs
    # a database, and `translate` is documented to run without one installed.
    from .server import serve  # noqa: PLC0415

    origins = tuple(args.allow_origin) if args.allow_origin else ADX_ORIGINS
    try:
        serve(args.database, port=args.port, allowed_origins=origins)
    except ImportError as exc:  # pragma: no cover - depends on the install
        # `engine._require_duckdb` already names the extra to install; repeating
        # it here would print the same instruction twice.
        print(f"duckdb-kql serve: {exc}", file=sys.stderr)
        return EXIT_TRANSLATION_ERROR
    except OSError as exc:
        print(f"duckdb-kql serve: {exc}", file=sys.stderr)
        return EXIT_TRANSLATION_ERROR
    return EXIT_OK




# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------


def _translate(source: Path, *, schema: dict[str, list[str]] | None, header: bool) -> str:
    kql = _read(source)
    translated = to_sql(kql, schema=schema)
    body = str(translated).rstrip("\n") + "\n"
    if not header:
        return body
    return _header(source, translated) + body


def _read(source: Path) -> str:
    if str(source) == "-":
        return sys.stdin.read()
    return source.read_text(encoding="utf-8")


def _header(source: Path, translated: Any) -> str:
    """The comment block above the SQL.

    Deliberately carries **no version and no timestamp**. Both would change the
    file for reasons that have nothing to do with the query, which would make
    ``--check`` fail after an unrelated upgrade and train people to ignore it.

    What it does carry is the two things a reader cannot recover from the SQL
    itself: that the statement assumes a UTC session, and — for a parameterized
    query — which generated placeholder corresponds to which declared parameter.
    Without the latter, a build-time consumer is handed ``$kqlp0`` and no way to
    know what belongs in it.
    """
    name = "<stdin>" if str(source) == "-" else source.as_posix()
    lines = [
        f"-- Generated by duckdb-kql from {name}. Do not edit.",
        "--",
        "-- Run with TimeZone set to UTC:  SET TimeZone='UTC';",
        "-- KQL datetimes are UTC, and DuckDB reads the session zone when casting",
        "-- text without an offset. Without it, datetimes are silently shifted",
        "-- rather than rejected.",
    ]

    slots = _slot_map(translated)
    if slots:
        lines += [
            "--",
            "-- Query parameters. Bind these as VALUES — never by string",
            "-- substitution, which is the whole reason they are placeholders.",
        ]
        width = max(len(kql_name) for _, kql_name, _ in slots)
        for slot, kql_name, detail in slots:
            lines.append(f"--   ${slot:<8} {kql_name:<{width}}  {detail}")
    return "\n".join(lines) + "\n\n"


def _slot_map(translated: Any) -> list[tuple[str, str, str]]:
    """``[(slot, kql_name, detail)]`` for a parameterized query, else ``[]``.

    Read back off the declarations rather than the bound values, because at
    build time there are no values — only names, types and defaults.
    """
    declarations = getattr(translated, "declarations", None)
    if not declarations:
        return []
    rows = []
    for decl in declarations:
        if decl.default is None:
            detail = f"{decl.type}, required"
        else:
            detail = f"{decl.type}, default {_readable(decl.default)}"
        rows.append((decl.slot, decl.name, detail))
    return rows


def _readable(value: Any) -> str:
    """A default value as a person would write it, not as Python reprs it.

    ``datetime.datetime(2007, 1, 1, 0, 0)`` in a SQL comment is noise from
    another language; the reader wants the value they typed in the KQL.
    """
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return str(value)
    return repr(value)


def _diagnose(source: Path, exc: KqlError) -> str:
    """One line per problem, in ``file:line:col: message`` form.

    That shape is what editors and CI annotators parse, so a failed build points
    at the offending line rather than just naming the file.
    """
    name = "<stdin>" if str(source) == "-" else source.as_posix()
    if isinstance(exc, KqlSyntaxError) and exc.diagnostics:
        return "\n".join(
            f"{name}:{d.span.line}:{d.span.column}: error: {d.message}"
            for d in exc.diagnostics
        )
    span = getattr(exc, "span", None)
    where = f":{span.line}:{span.column}" if span is not None else ""
    return f"{name}{where}: error: {exc}"


# ---------------------------------------------------------------------------
# Inputs and outputs
# ---------------------------------------------------------------------------


def _expand(files: list[str]) -> list[Path]:
    """Expand any directory argument into the ``.kql`` files under it.

    Shells expand globs already; a bare directory is the case they do not cover
    and the one a build script most often has.
    """
    out: list[Path] = []
    for raw in files:
        if raw == "-":
            out.append(Path("-"))
            continue
        path = Path(raw)
        if path.is_dir():
            out.extend(sorted(path.rglob("*.kql")))
        else:
            out.append(path)
    return out


def _target(source: Path, output: str | None, count: int) -> Path | None:
    """Where one input's SQL goes. ``None`` means stdout."""
    if output is None:
        return None
    out = Path(output)
    # A single input plus a name that is not an existing directory means "write
    # exactly this file"; anything else is a directory of outputs.
    if count == 1 and not out.is_dir() and not output.endswith(("/", "\\")):
        return out
    stem = "stdin" if str(source) == "-" else source.stem
    return out / f"{stem}.sql"


def _load_schema(path: str | None) -> dict[str, list[str]] | None:
    """Read a ``{"Table": ["col", ...]}`` JSON file.

    Only ``join`` needs this — it has to know both sides' columns to reproduce
    KQL's column renaming. Everything else translates schema-free, which is why
    it is optional rather than required.
    """
    if path is None:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(
        isinstance(v, list) and all(isinstance(c, str) for c in v) for v in data.values()
    ):
        raise ValueError(
            f"{path}: expected an object mapping table name to a list of column "
            'names, e.g. {"StormEvents": ["State", "EventType"]}'
        )
    return {str(k): list(v) for k, v in data.items()}


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    """The whole command line. Each subparser stores its handler in ``run``.

    Dispatching through ``set_defaults(run=...)`` rather than a chain of
    ``if args.command == ...`` means a new subcommand is added in exactly one
    place, and cannot be registered without being wired up.
    """
    parser = argparse.ArgumentParser(
        prog="duckdb-kql",
        description=(
            "Run KQL on DuckDB. `translate` turns .kql files into .sql at build "
            "time; `serve` puts a local Kusto REST endpoint in front of a DuckDB "
            "database."
        ),
        epilog=(
            "exit codes: 0 ok; 1 a query failed to translate, or the server "
            "could not start; 2 bad usage; 3 --check found a missing or stale "
            "output"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"duckdb-kql {__version__}")
    # `required` so a bare `duckdb-kql` prints usage rather than a traceback
    # about a missing `run` attribute.
    subcommands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    translate = subcommands.add_parser(
        "translate",
        help="translate .kql files to .sql",
        description=(
            "Translate KQL files to DuckDB SQL. The generated SQL has no "
            "dependency on this package, so queries can be translated once at "
            "build time and run anywhere DuckDB runs."
        ),
        epilog=(
            "exit codes: 0 ok; 1 a query failed to translate; 2 bad usage; "
            "3 --check found a missing or stale output"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    translate.set_defaults(run=_translate_command)
    translate.add_argument(
        "files",
        nargs="+",
        metavar="FILE",
        help="KQL files or directories to translate; '-' reads stdin",
    )
    translate.add_argument(
        "-o",
        "--output",
        metavar="PATH",
        help=(
            "output file (with a single input) or directory. Omit to write to "
            "stdout."
        ),
    )
    translate.add_argument(
        "--check",
        action="store_true",
        help=(
            "do not write; exit 3 if any output is missing or differs. Put this "
            "in CI so an edited .kql cannot ship with a stale .sql."
        ),
    )
    translate.add_argument(
        "--schema",
        metavar="FILE",
        help=(
            'JSON file mapping table name to column names, e.g. {"T": ["a"]}. '
            "Only `join` needs it."
        ),
    )
    translate.add_argument(
        "--no-header",
        action="store_true",
        help="omit the generated-file comment block",
    )
    translate.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="report each file written, on stderr",
    )

    serve = subcommands.add_parser(
        "serve",
        help="serve a DuckDB database over the Kusto REST API",
        description=(
            "Serve a DuckDB database over the Kusto REST API, so Kusto tools "
            "can query it. Open https://dataexplorer.azure.com, choose Add "
            "connection, and give it the URL this prints."
        ),
        epilog=(
            "Listens on 127.0.0.1 only and cannot be made to listen anywhere "
            "else: it answers unauthenticated queries, so reaching it has to "
            "mean already being on this machine."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    serve.set_defaults(run=_serve_command)
    serve.add_argument(
        "database",
        nargs="?",
        default=":memory:",
        metavar="DATABASE",
        help="DuckDB database file to serve. Omit for an empty in-memory one.",
    )
    serve.add_argument(
        "-p",
        "--port",
        type=int,
        default=DEFAULT_PORT,
        metavar="PORT",
        help=f"TCP port to listen on (default: {DEFAULT_PORT})",
    )
    serve.add_argument(
        "--allow-origin",
        action="append",
        metavar="ORIGIN",
        help=(
            "additionally allow a browser origin to make cross-origin requests. "
            "Repeatable. Replaces the Azure Data Explorer default list, and is a "
            "decision about who may read this database from another browser tab."
        ),
    )
    return parser


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
