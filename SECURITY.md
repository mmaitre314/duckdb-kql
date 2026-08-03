# Security policy

## Reporting a vulnerability

Please report security issues privately, through GitHub's
[private vulnerability reporting][advisory] on this repository, rather than in
the public issue tracker.

Include what you have: the KQL or the API call, what happened, and what you
expected. A proof of concept helps but is not required to file.

Expect an acknowledgement within a week. If a report is confirmed, the fix and
the advisory go out together.

[advisory]: https://github.com/mmaitre314/duckdb-kql/security/advisories/new

## Supported versions

This project is pre-alpha and pre-1.0. Fixes land on `main` and in the next
release; older releases are not patched. Pin a version if you need stability,
and expect to move forward for a fix.

## What is in scope

This is a transpiler and a client library, so the interesting surface is small
and specific.

**Query injection.** The one that matters. Callers pass untrusted values into
queries, and the mechanism for that is `declare query_parameters` — values are
bound through DuckDB's parameter API and never enter the SQL text. If you find
*any* path by which a supplied value changes the shape of the generated
statement, that is a vulnerability and we want to hear about it. The same goes
for `ClientRequestProperties.set_parameter` in the Kusto client, which uses the
same mechanism.

`tests/test_query_parameters.py` and `tests/test_kusto_client.py` assert the
property structurally — the value must not appear in the SQL text at all — but
a test suite only covers the cases someone thought of.

**Reaching outside the local database.** The Kusto client refuses cluster URLs
rather than reinterpreting them, precisely so that discarding credentials cannot
mean "sent somewhere unauthenticated". A way to make it open a network
connection, or to make a query read a file the caller did not name, is in scope.

**Denial of service through parsing.** Input that makes the parser hang, recurse
without bound, or allocate unreasonable memory is in scope. The parser is
generated from Microsoft's grammar and processes untrusted text.

**Crashes that leak internals.** The public API raises `KqlError` subclasses;
an input that produces a raw traceback through internal state is worth
reporting, though usually as a bug rather than a vulnerability.

## What is out of scope

**A wrong answer is a bug, not a vulnerability** — but it is the bug this
project cares about most, so please file it. See
[CONTRIBUTING.md](CONTRIBUTING.md#reporting-a-wrong-answer).

**DuckDB's own security surface.** This library generates SQL and hands it to
DuckDB. A vulnerability in DuckDB's execution, extensions or file readers
belongs to [DuckDB](https://github.com/duckdb/duckdb/security). If our generated
SQL is what *triggers* it, tell us too.

**The Kusto Emulator.** It is a development and CI tool, never a runtime
dependency and never shipped. Issues in the emulator image go to Microsoft.

**Untrusted queries with full database access.** If your application lets
someone supply arbitrary KQL, they can read anything the DuckDB connection can
read — that is what the query language is for. Restrict what the connection can
see; the transpiler is not an authorization layer and does not claim to be.

## Hardening notes for users

- **Never build a query by concatenating strings.** Use
  `declare query_parameters` and pass values. See
  [Getting started](docs/getting-started.md#query-parameters-and-user-input).
- **Give the connection only the data the query should reach.** DuckDB can read
  the local filesystem through `read_csv` and friends; a query you did not write
  can too, unless the connection is restricted.
- **Set a timeout** on anything driven by untrusted input. The Kusto client
  implements `servertimeout` by interrupting the running query; at Layer 1, use
  DuckDB's own controls.
