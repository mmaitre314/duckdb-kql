# A local Kusto endpoint (`duckdb-kql serve`)

Serve a DuckDB database over the Kusto REST API, so tools written for Kusto —
including the [Azure Data Explorer web UI](https://dataexplorer.azure.com) — can
query it.

```bash
pip install 'duckdb-kql[duckdb]'
duckdb-kql serve analytics.duckdb
```

```
duckdb-kql serving analytics.duckdb as database 'analytics'
  http://127.0.0.1:31415
Connect from https://dataexplorer.azure.com -> Add connection
Local connections only. Ctrl-C to stop.
```

Then in the web UI: **Add connection**, paste `http://127.0.0.1:31415`, and your
tables appear in the schema tree.

- [What it is for](#what-it-is-for)
- [Security](#security)
- [Usage](#usage)
- [What it implements](#what-it-implements)
- [Request options](#request-options)
- [Limits](#limits)

## What it is for

A DuckDB file has no query UI worth the name. Kusto has an excellent one, and it
talks to a documented HTTP API. This bridges the two: a few hundred lines of
`http.server` over the same translator Layers 0–2 use, so a query typed in the
Azure Data Explorer UI goes through exactly the code path `duckdb_kql.kql()`
does. No new package dependency — it is the Python standard library only.

It is not a Kusto cluster and does not pretend to be one. It is a local
convenience for looking at local data.

## Security

There is no authentication, because there is nothing to authenticate against.
That is not an oversight to fix later — it is the reason the following two
limits are not configurable.

**It listens on `127.0.0.1` only.** Never `0.0.0.0`. This process answers
unauthenticated queries against whatever database it was pointed at; on a shared
network, binding it to a routable address would publish that to everyone on the
subnet. There is no `--host` flag, and the request handler re-checks the peer
address regardless — a bind address is one careless edit away from being
widened, and the second check is what would still be true afterwards.

**Cross-origin requests are allowed only from the Azure Data Explorer web UI.**
This matters more than it looks. A browser will happily let *any* page you visit
issue requests to `http://localhost:31415`; what stops that page reading the
reply is the absence of an `Access-Control-Allow-Origin` header for its origin.
The allow-list is the whole mechanism, and the response never carries a wildcard
origin. The default list is the three Azure Data Explorer hosts:

```
https://dataexplorer.azure.com
https://dataexplorer.azure.cn
https://dataexplorer.azure.us
```

`--allow-origin` replaces that list. Widening it is a decision about who may read
this database from another browser tab, not a formatting preference.

**Nothing it serves can write.** The translated surface is read-only: no
supported KQL operator produces a statement that modifies the database, and
control commands that would administer a cluster are refused rather than
approximated.

## Usage

```
duckdb-kql serve [DATABASE] [-p PORT] [--allow-origin ORIGIN]
```

| | |
|---|---|
| `DATABASE` | DuckDB database file to serve. Omit for an empty in-memory one. |
| `-p, --port` | TCP port (default `31415`). |
| `--allow-origin` | Allow a browser origin. Repeatable; replaces the default list. |

The database is opened with `TimeZone=UTC`, the same as
[`duckdb_kql.connect()`](api.md), because KQL datetimes are UTC and DuckDB reads
the session zone when casting.

Exposing files rather than tables works the way it does everywhere else in this
package — create a view first:

```sql
CREATE VIEW Logs AS SELECT * FROM 'logs/*.parquet';
```

## What it implements

| Route | |
|---|---|
| `POST /v1/rest/mgmt` | Control commands. The v1 envelope, `{"Tables": [...]}`. |
| `POST /v1/rest/query` | Queries, v1 envelope. |
| `POST /v2/rest/query` | Queries, v2 frame protocol. What the web UI uses. |
| `OPTIONS *` | CORS preflight, for the origins above. |
| `GET /` | A small JSON page naming the database and the URL to connect with. |

The control commands are the five the web UI needs to open a connection and draw
a schema tree: `.show version`, `.show databases`, `.show databases entities`,
`.show tables` and `.show materialized-views`. Their column shapes are
**measured on the Kusto Emulator and on the service**, not designed — see
[`src/duckdb_kql/control.py`](../src/duckdb_kql/control.py).

Two of those measurements are worth knowing about, because they look like bugs:

- A control command's result schema is declared inside Kusto, and the
  declarations do not agree with the query path. `.show databases` labels its
  `IsCurrent` column `Boolean`, while the same `bool` column reached through a
  query operator is `SByte`. `.show materialized-views` labels `Lookback` with
  the legacy CSL name `time`, where a query says `timespan`.
- `.show version` omits `ColumnType` from its columns entirely — the field is
  absent, not null.

Both are reproduced faithfully. A bare command reports the schema Kusto
declares; the moment an operator is piped onto it (`.show tables | limit 3`) the
result is a query result and is typed like one.

## Request options

A client may send `properties.Options` with a request. Every option is
**implemented or refused** — none is accepted and quietly ignored, which is the
failure mode that produces a plausible wrong answer.

The classification is [Layer 2's](kusto-client.md), with one deliberate
difference: on the wire, two options are judged by their *value* rather than
their name.

| Option | On the wire |
|---|---|
| `queryconsistency` | `strongconsistency` accepted — one local database has one consistency level, and it is that one. `weakconsistency` refused. |
| `query_language` | `kql` and `csl` accepted. `sql` refused. |

Layer 2's Python API refuses both outright, which is right for a caller who can
change the line that sets them. On the wire the caller is a web UI that sends
them on every single query, and refusing a request that asks for exactly what it
is going to get would break the product while telling the truth about nothing.

`properties.Parameters` is bound as values, never substituted into the query
text — the same guarantee [query parameters](getting-started.md) carry
everywhere else.

A refused option, an unsupported command and a query that fails to translate all
come back as HTTP 400 in Kusto's error envelope, carrying the reason.

## Limits

- **One database per process.** A request naming a different database is a 404
  rather than an answer out of the wrong one.
- **No ingestion, no streaming, no progressive frames.** `.ingest`, `.create`
  and the rest administer a cluster; there is none here.
- **No authentication and no TLS.** Both follow from "local only" — see
  [Security](#security). Do not put this behind a reverse proxy to reach it from
  elsewhere; the loopback bind is the guarantee, and a tunnel around it removes
  it.
- **Only the KQL this package translates.** The
  [support matrix](kql-support.md) applies unchanged; an unsupported construct
  is an error in the UI, not a silent approximation.
