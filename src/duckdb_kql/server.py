"""A local Kusto REST endpoint, so existing Kusto tools can talk to DuckDB.

Speaks enough of the `Kusto REST API`_ that the Azure Data Explorer web UI at
https://dataexplorer.azure.com can *Add connection* to it and browse and query a
local DuckDB database: the v1 management endpoint, and the v2 query endpoint
with its frame protocol.

Standard library only — `http.server` — because a local convenience is not worth
a dependency in a package whose whole shape is "each layer adds exactly one".

.. _Kusto REST API: https://learn.microsoft.com/kusto/api/rest/

Two deliberate limits, both security rather than scope
------------------------------------------------------

**It listens on the loopback interface only.** `127.0.0.1`, never `0.0.0.0`.
This process answers unauthenticated queries against whatever database it was
pointed at; on a shared network, binding it to a routable address would publish
that. The bind is the guarantee, and the handler re-checks the peer address
anyway — a bind is easy to widen by accident, and a second check costs nothing.

**Cross-origin requests are allowed only from the Azure Data Explorer web UI.**
This matters more than it looks. The browser will happily let *any* page you
visit issue requests to `http://localhost:31415`; with a permissive
`Access-Control-Allow-Origin`, a page in another tab could read your local data
and the browser would hand it over. The allow-list is what stops that, so
widening it with ``--allow-origin`` is a decision about who may read this data,
not a formatting preference.

There is no authentication, because there is nothing to authenticate against.
That is exactly why the two limits above are not options.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from . import __version__
from .control import SCHEMA, CommandColumn, is_control_command, split_command
from .errors import KqlError
from .types import kusto_type, rest_datatype

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection

__all__ = [
    "serve",
    "build_server",
    "read_init_script",
    "run_init_script",
    "INIT_SCRIPT_LANGUAGES",
    "KustoRestServer",
    "Result",
    "RestColumn",
    "check_options",
    "v1_response",
    "v2_response",
    "error_response",
    "DEFAULT_PORT",
    "ADX_ORIGINS",
    "VALUE_CONDITIONAL",
]

#: Chosen to be memorable and unlikely to collide. Override with ``--port``.
DEFAULT_PORT = 31415

#: The web UIs allowed to make cross-origin requests. Every national cloud has
#: its own host, and each is the same first-party application.
ADX_ORIGINS = (
    "https://dataexplorer.azure.com",
    "https://dataexplorer.azure.cn",
    "https://dataexplorer.azure.us",
)

#: What this endpoint claims to be. The web UI shows it, and a name that looked
#: like a real cluster would be an invitation to confuse the two.
CLUSTER_NAME = "duckdb-kql"


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------


def _iso(value: dt.datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return value.isoformat(timespec="microseconds") + "Z"


class RestColumn(NamedTuple):
    """One result column, with everything the two envelopes need to describe it.

    Three fields rather than one because Kusto's own answers are three:

    * *kql* is the type DuckDB actually produced, and it is what decides the
      wire encoding of every value in the column.
    * *data_type* and *column_type* are **labels**. For a query result they are
      derived from *kql*; for a bare control command they are transcribed from
      :data:`duckdb_kql.control.SCHEMA`, because Kusto's command schemas use a
      different and inconsistent vocabulary (`Boolean` where a query says
      `SByte`, `time` where a query says `timespan`, and no `ColumnType` at all
      from `.show version`).
    """

    name: str
    kql: str
    data_type: str
    column_type: str | None

    @classmethod
    def derived(cls, name: str, kql: str) -> RestColumn:
        """The labels a *query* result carries, which follow from the type."""
        return cls(name, kql, rest_datatype(kql), kql)


#: What :meth:`KustoRestServer.run` hands back: a described result, not a
#: DuckDB relation. Named so the two envelope builders read the same way.
class Result(NamedTuple):
    columns: list[RestColumn]
    rows: list[Any]


def _wire(value: Any, kind: str) -> Any:
    """A DuckDB value as the REST wire form, which is mostly JSON's."""
    from .kusto._models import to_wire  # noqa: PLC0415

    return to_wire(value, kind)


def _rows(result: Result) -> list[list[Any]]:
    return [
        [_wire(value, column.kql) for value, column in zip(row, result.columns, strict=True)]
        for row in result.rows
    ]


def v1_response(result: Result) -> dict[str, Any]:
    """The v1 envelope: ``{"Tables": [...]}`` with one table."""
    columns: list[dict[str, str]] = []
    for column in result.columns:
        described = {"ColumnName": column.name, "DataType": column.data_type}
        # Omitted, not null: `.show version` sends the field away entirely, and
        # a `"ColumnType": null` is a different message from no field at all.
        if column.column_type is not None:
            described["ColumnType"] = column.column_type
        columns.append(described)
    return {"Tables": [{"TableName": "Table_0", "Columns": columns, "Rows": _rows(result)}]}


#: The empty visualization blob every Kusto v2 response carries. The web UI
#: reads it to decide how to render, and treats its absence as a malformed
#: response rather than as "no chart".
_VISUALIZATION = json.dumps(
    {
        "Visualization": None,
        "Title": None,
        "XColumn": None,
        "Series": None,
        "YColumns": None,
        "AnomalyColumns": None,
        "XTitle": None,
        "YTitle": None,
        "XAxis": None,
        "YAxis": None,
        "Legend": None,
        "YSplit": None,
        "Accumulate": False,
        "IsQuerySorted": False,
        "Kind": None,
        "Ymin": "NaN",
        "Ymax": "NaN",
        "Xmin": None,
        "Xmax": None,
    }
)


def v2_response(result: Result, client_request_id: str) -> list[dict[str, Any]]:
    """The v2 frame protocol: header, three tables, completion.

    The shape is not decoration. The web UI reads the frames positionally-ish —
    `QueryProperties` first, then `PrimaryResult`, then
    `QueryCompletionInformation` — and a response missing the bookends is
    reported to the user as a failed query rather than as an empty one.
    """
    now = _iso(dt.datetime.now(dt.timezone.utc))
    activity = str(uuid.uuid4())
    return [
        {
            "FrameType": "DataSetHeader",
            "IsProgressive": False,
            "Version": "v2.0",
            "IsFragmented": False,
            "ErrorReportingPlacement": "InData",
        },
        {
            "FrameType": "DataTable",
            "TableId": 0,
            "TableKind": "QueryProperties",
            "TableName": "@ExtendedProperties",
            "Columns": [
                {"ColumnName": "TableId", "ColumnType": "int"},
                {"ColumnName": "Key", "ColumnType": "string"},
                {"ColumnName": "Value", "ColumnType": "dynamic"},
            ],
            "Rows": [[1, "Visualization", _VISUALIZATION]],
        },
        {
            "FrameType": "DataTable",
            "TableId": 1,
            "TableKind": "PrimaryResult",
            "TableName": "PrimaryResult",
            # v2 carries only the CSL name. A command that omits it on v1 still
            # needs one here, so fall back to the type DuckDB produced.
            "Columns": [
                {"ColumnName": c.name, "ColumnType": c.column_type or c.kql} for c in result.columns
            ],
            "Rows": _rows(result),
        },
        {
            "FrameType": "DataTable",
            "TableId": 2,
            "TableKind": "QueryCompletionInformation",
            "TableName": "QueryCompletionInformation",
            "Columns": [
                {"ColumnName": "Timestamp", "ColumnType": "datetime"},
                {"ColumnName": "ClientRequestId", "ColumnType": "string"},
                {"ColumnName": "ActivityId", "ColumnType": "guid"},
                {"ColumnName": "SubActivityId", "ColumnType": "guid"},
                {"ColumnName": "ParentActivityId", "ColumnType": "guid"},
                {"ColumnName": "Level", "ColumnType": "int"},
                {"ColumnName": "LevelName", "ColumnType": "string"},
                {"ColumnName": "StatusCode", "ColumnType": "int"},
                {"ColumnName": "StatusCodeName", "ColumnType": "string"},
                {"ColumnName": "EventType", "ColumnType": "int"},
                {"ColumnName": "EventTypeName", "ColumnType": "string"},
                {"ColumnName": "Payload", "ColumnType": "string"},
            ],
            "Rows": [
                [
                    now,
                    client_request_id,
                    activity,
                    activity,
                    activity,
                    4,
                    "Info",
                    0,
                    "S_OK (0)",
                    4,
                    "QueryInfo",
                    json.dumps({"Count": 1, "Text": "Query completed successfully"}),
                ]
            ],
        },
        {"FrameType": "DataSetCompletion", "HasErrors": False, "Cancelled": False},
    ]


# ---------------------------------------------------------------------------
# Request options
# ---------------------------------------------------------------------------

#: Options whose acceptability depends on the *value*, and why the other values
#: are refused. Layer 2 refuses these outright, and for a Python caller that is
#: right: `set_option` fails at the line that asks for something impossible.
#:
#: On the wire the calculus differs, because the caller is a web UI that sends
#: them on every single query. Refusing `queryconsistency` unconditionally would
#: make the product unusable while telling the truth about nothing — the value
#: it sends, `strongconsistency`, is exactly what a single local database gives.
#: So the option is honoured when what it asks for is what happens, and refused
#: when it is not. Neither branch ignores it, which is the rule that matters.
VALUE_CONDITIONAL: dict[str, tuple[frozenset[str], str]] = {
    "queryconsistency": (
        frozenset({"strongconsistency"}),
        "one local database has one consistency level, and it is the strong "
        "one; 'weakconsistency' would name a choice that does not exist here",
    ),
    "query_language": (
        frozenset({"kql", "csl"}),
        "this endpoint speaks KQL; accepting 'sql' would promise a dialect it does not translate",
    ),
}

#: Accepted because doing nothing is what they ask for. `request_readonly` is
#: already a no-op in Layer 2 for the same reason — translated KQL only reads —
#: and the hardline variant asks for that guarantee to be enforced rather than
#: assumed, which it is: nothing here can write.
_WIRE_NO_OP = frozenset({"request_readonly_hardline"})


def check_options(options: dict[str, Any]) -> list[str]:
    """Refusals for the options in a request, one message each.

    Empty when every option is honoured or is a no-op that means what it says.
    """
    from .kusto.client_request_properties import (  # noqa: PLC0415
        OPTION_SUPPORT,
        OptionSupport,
    )

    refusals = []
    for raw, value in options.items():
        name = raw.lower()
        if name in _WIRE_NO_OP:
            continue
        conditional = VALUE_CONDITIONAL.get(name)
        if conditional is not None:
            accepted, reason = conditional
            if str(value).lower() not in accepted:
                refusals.append(f"request option {raw}={value!r}: {reason}")
            continue
        support, reason = OPTION_SUPPORT.get(
            name,
            (
                OptionSupport.REFUSED,
                "not a request option this endpoint recognises; an unrecognised "
                "option cannot be assumed harmless",
            ),
        )
        if support == OptionSupport.REFUSED:
            refusals.append(f"request option {raw!r}: {reason}")
    return refusals


def error_response(message: str, *, code: str = "General_BadRequest") -> dict[str, Any]:
    """Kusto's error envelope, so a client reports the reason rather than a 500."""
    return {
        "error": {
            "code": code,
            "message": "Request is invalid or malformed.",
            "@type": "Kusto.Data.Exceptions.KustoBadRequestException",
            "@message": message,
            "@context": {"service": CLUSTER_NAME},
            "@permanent": True,
        }
    }


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------

_LOOPBACK = re.compile(r"^(127\.\d+\.\d+\.\d+|::1)$")

#: Only for a preflight that names no headers, which no browser sends — a
#: preflight exists *because* there is something non-simple to declare. Kept so
#: the header is never emitted empty.
_DEFAULT_ALLOW_HEADERS = "Authorization, Content-Type, Accept"


class _Handler(BaseHTTPRequestHandler):
    """One request. The connection and the allow-list come from the server."""

    # `duckdb-kql/0.1.2`, not `duckdb-kql/duckdb-kql`: the slot after the slash
    # is a version, and this used to interpolate the cluster name into it.
    server_version = f"duckdb-kql/{__version__}"
    # Announce HTTP/1.1 so the web UI's keep-alive works; every response below
    # sets Content-Length, which is what makes that safe.
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------

    @property
    def kusto(self) -> KustoRestServer:
        """The server, named at its real type. ``self.server`` is declared as the
        base class, and every use here needs the subclass."""
        return cast("KustoRestServer", self.server)

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.kusto.quiet:
            return
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def _origin_allowed(self) -> str | None:
        origin = self.headers.get("Origin")
        if origin is None:
            return None  # not a browser request; CORS does not apply
        return origin if origin in self.kusto.allowed_origins else None

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        origin = self._origin_allowed()
        if origin is not None:
            self.send_header("Access-Control-Allow-Origin", origin)
            # The UI sends an Authorization header it does not need here, and a
            # credentialed request requires the origin to be echoed, never `*`.
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def _is_local(self) -> bool:
        return bool(_LOOPBACK.match(self.client_address[0]))

    # -- routes -----------------------------------------------------------

    def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's interface
        """CORS preflight. Without this the browser never sends the real request."""
        origin = self._origin_allowed()
        if origin is None:
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", self._allow_headers())
        # Only when asked. Chrome gates requests from a public page to a private
        # address behind this; answering unprompted would claim a policy the
        # browser did not ask about.
        if self.headers.get("Access-Control-Request-Private-Network") == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin, Access-Control-Request-Headers")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _allow_headers(self) -> str:
        """The request headers the preflight permits: whatever was asked for.

        Echoed rather than listed, because a hand-written list is a second place
        to be wrong about someone else's client. It *was* wrong: the list said
        `x-ms-user` where the Azure Data Explorer UI sends `x-ms-user-id`, CORS
        matches header names exactly, and the browser answered by failing the
        real POST with a bare `net::ERR_FAILED` — a preflight that returned 204
        and still blocked the request. Every header the UI adds in future would
        break it the same way.

        This gives up nothing. Naming a header does not authorise anything; the
        origin allow-list and the loopback bind are what decide who may talk to
        this endpoint, and both are checked before we get here. `*` would be the
        lazy version of this and is not equivalent — it is invalid alongside
        `Allow-Credentials: true`, which is why the exact list is echoed back.
        """
        return self.headers.get("Access-Control-Request-Headers", _DEFAULT_ALLOW_HEADERS)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's interface
        """A human landing page. Anything else 404s."""
        if not self._is_local():
            self._send(403, error_response("local connections only"))
            return
        if self.path.rstrip("/") in ("", "/"):
            self._send(
                200,
                {
                    "name": CLUSTER_NAME,
                    "database": self.kusto.database,
                    "source": self.kusto.source,
                    "endpoints": ["/v1/rest/mgmt", "/v1/rest/query", "/v2/rest/query"],
                    "connect": (
                        "Add connection in https://dataexplorer.azure.com with " + self.kusto.url
                    ),
                },
            )
            return
        self._send(404, error_response(f"no route for {self.path}"))

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's interface
        if not self._is_local():
            # Unreachable while the socket is bound to loopback. Kept because a
            # bind address is one edit away from being widened, and this is the
            # check that would still be true afterwards.
            self._send(403, error_response("local connections only"))
            return

        route = self.path.split("?", 1)[0].rstrip("/")
        if route not in ("/v1/rest/mgmt", "/v1/rest/query", "/v2/rest/query", "/v2/rest/mgmt"):
            self._send(404, error_response(f"no route for {self.path}"))
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError) as exc:
            self._send(400, error_response(f"malformed request body: {exc}"))
            return

        if not isinstance(request, dict):
            self._send(400, error_response("request body must be a JSON object"))
            return

        csl = request.get("csl") or ""
        if not isinstance(csl, str) or not csl.strip():
            self._send(400, error_response("request has no 'csl' to run"))
            return

        database = request.get("db")
        if isinstance(database, str) and database and not self.kusto.serves(database):
            self._send(
                404,
                error_response(
                    f"database {database!r} is not attached to this connection; "
                    f"serving {self.kusto.database!r}",
                    code="General_DatabaseNotFound",
                ),
            )
            return

        properties = request.get("properties") or {}
        options = properties.get("Options") or {} if isinstance(properties, dict) else {}
        refusals = check_options(options if isinstance(options, dict) else {})
        if refusals:
            self._send(400, error_response("; ".join(refusals)))
            return
        parameters = properties.get("Parameters") if isinstance(properties, dict) else None

        try:
            result = self.kusto.run(
                csl,
                parameters if isinstance(parameters, dict) else None,
                database if isinstance(database, str) and database else None,
            )
        except KqlError as exc:
            # A statement about the query, which is what the client should show.
            self._send(400, error_response(str(exc), code="General_BadRequest"))
            return
        except Exception as exc:  # noqa: BLE001 - any engine failure is the answer
            self._send(400, error_response(str(exc), code="General_BadRequest"))
            return

        if route.startswith("/v2/"):
            request_id = self.headers.get("x-ms-client-request-id", str(uuid.uuid4()))
            self._send(200, v2_response(result, request_id))
        else:
            self._send(200, v1_response(result))


class KustoRestServer(ThreadingHTTPServer):
    """The socket, the connection, and the policy the handler enforces."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        con: DuckDBPyConnection,
        *,
        port: int = DEFAULT_PORT,
        host: str = "127.0.0.1",
        allowed_origins: tuple[str, ...] = ADX_ORIGINS,
        source: str = ":memory:",
        quiet: bool = False,
    ) -> None:
        super().__init__((host, port), _Handler)
        self._con = con
        self.allowed_origins = allowed_origins
        #: Where the data came from — a path or `:memory:`. For the operator.
        self.source = source
        #: What a Kusto client calls it. Not the same thing: a DuckDB file at
        #: `./logs.duckdb` is the database named `logs`, and that name is what
        #: `.show databases` reports and what the client sends back to us.
        #
        # `fetchone()` is Optional, and a scalar SELECT cannot return no row —
        # so the check is not defensiveness, it is the type saying that a
        # connection which has already been closed would come back empty here.
        current = con.execute("SELECT current_database()").fetchone()
        if current is None:  # pragma: no cover - a closed connection
            raise ValueError("the connection reports no current database")
        self.database = str(current[0])
        self.quiet = quiet
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        """The address to hand a client. Read back off the socket, not the
        arguments, so ``--port 0`` reports the port it actually got."""
        host, port = self.server_address[:2]
        if isinstance(host, (bytes, bytearray)):
            host = host.decode()
        return f"http://{host}:{port}"

    def databases(self) -> list[str]:
        """Every database reachable on this connection, `.show databases` order.

        More than one once an init script has attached others: each is a Kusto
        database here, addressed as `database("Name").Table`.
        """
        with self._lock:
            rows = self._con.execute(
                "SELECT database_name FROM duckdb_databases() "
                "WHERE NOT internal ORDER BY database_name"
            ).fetchall()
        return [str(row[0]) for row in rows]

    def serves(self, name: str) -> bool:
        """Whether *name* is a database this connection can answer for.

        A client picks a database from `.show databases` and then names it on
        every request. Answering a request for some *other* database out of the
        one we have would be a wrong answer wearing the right label, so an
        unrecognised name is a 404 instead.
        """
        # `default` is what a client sends before it has asked what exists —
        # the Azure Data Explorer UI uses it as the initial database name.
        return name in self.databases() or name == "default"

    def run(
        self,
        csl: str,
        parameters: dict[str, Any] | None = None,
        database: str | None = None,
    ) -> Result:
        """Translate and execute *csl*, described the way Kusto describes it.

        *database* is the one the client selected, and it is honoured rather
        than merely validated. Before this the request's `db` was checked
        against `serves()` and then dropped, so a client that picked `sales`
        from `.show databases` and ran `T | count` was answered from whichever
        database this process started in — the wrong table, with no error.

        It is applied by qualifying names during translation, not by `USE`:
        this connection is shared by every request thread, and switching it
        would race (docs/session-state-proposal.md).

        Serialized: a DuckDB connection is not safe to use from several threads
        at once, and ThreadingHTTPServer will happily try.
        """
        from .engine import kql  # noqa: PLC0415

        # `default` is the placeholder name the ADX UI shows before a database
        # has been chosen; it is not a database this process can qualify with.
        target = database if database and database != "default" else None

        with self._lock:
            rel = kql(self._con, csl, parameters or None, target)
            names = list(rel.columns)
            kinds = [kusto_type(t) for t in rel.types]
            rows = rel.fetchall()

        declared = _declared_schema(csl)
        if declared is not None and len(declared) == len(names):
            columns = [
                RestColumn(d.name, kind, d.data_type, d.column_type)
                for d, kind in zip(declared, kinds, strict=True)
            ]
        else:
            columns = [
                RestColumn.derived(name, kind) for name, kind in zip(names, kinds, strict=True)
            ]
        return Result(columns, rows)


def _declared_schema(csl: str) -> tuple[CommandColumn, ...] | None:
    """The schema Kusto declares for *csl*, if *csl* is a bare control command.

    A command with a pipeline on it is no longer a command result — Kusto runs
    the query operators over it and the answer is typed like any other query —
    so only the bare form gets the transcribed labels.
    """
    if not is_control_command(csl):
        return None
    command, pipeline = split_command(csl)
    if pipeline:
        return None
    return SCHEMA.get(command)


# ---------------------------------------------------------------------------
# Startup scripts
# ---------------------------------------------------------------------------

#: What an init script may be written in, keyed by file extension.
#:
#: `.sql` is what makes several databases reachable at once: `ATTACH` is a
#: DuckDB statement with no KQL counterpart, so the setup step is necessarily in
#: SQL even though every query afterwards is KQL.
#:
#: `.kql` is deliberately *listed and refused* rather than left to fall through
#: to the "unknown extension" message. A KQL init script is a coherent idea —
#: `let` definitions and views shared by every session — and the refusal should
#: say that it is not built yet rather than imply the extension is a typo.
INIT_SCRIPT_LANGUAGES = {
    ".sql": "DuckDB SQL, executed as written",
    ".kql": None,
}

_KQL_INIT_HINT = (
    "a KQL init script is not implemented yet; only .sql is executed today. "
    "ATTACH is a SQL statement with no KQL spelling, so attaching databases "
    "belongs in a .sql script either way"
)


def read_init_script(path: str | Path) -> str:
    """The text of an init script, refusing anything not executable.

    Dispatch is on the **extension**, not on the content, so a `.kql` file is
    refused with a reason instead of being handed to DuckDB and failing as a
    syntax error halfway through.
    """
    script = Path(path)
    suffix = script.suffix.lower()
    if suffix not in INIT_SCRIPT_LANGUAGES:
        raise ValueError(
            f"{script}: unsupported init script type {suffix or '(no extension)'!r}; "
            f"expected one of {', '.join(sorted(INIT_SCRIPT_LANGUAGES))}"
        )
    if INIT_SCRIPT_LANGUAGES[suffix] is None:
        raise ValueError(f"{script}: {_KQL_INIT_HINT}")
    return script.read_text(encoding="utf-8")


def run_init_script(con: DuckDBPyConnection, path: str | Path) -> None:
    """Run *path* against *con* before the first request is served.

    Executed as one script rather than split on `;`, because DuckDB's own parser
    knows where a statement ends and a naive split does not — a semicolon inside
    a string literal or a `$$`-quoted body would cut a statement in half.

    A failure here is fatal by design. Serving anyway would answer queries out
    of a half-attached database, and "no such table" is a far worse way to learn
    that an ATTACH failed than the error itself.
    """
    sql = read_init_script(path)
    if sql.strip():
        con.execute(sql)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def build_server(
    database: str = ":memory:",
    *,
    port: int = DEFAULT_PORT,
    host: str = "127.0.0.1",
    allowed_origins: tuple[str, ...] = ADX_ORIGINS,
    init: str | Path | None = None,
    quiet: bool = False,
) -> KustoRestServer:
    """A server ready to `serve_forever()`, with its own DuckDB connection.

    *init* runs before the socket is bound, so a client can never observe the
    database halfway through its own setup.
    """
    from .engine import connect  # noqa: PLC0415

    con = connect(database)
    if init is not None:
        run_init_script(con, init)

    return KustoRestServer(
        con,
        port=port,
        host=host,
        allowed_origins=allowed_origins,
        source=database,
        quiet=quiet,
    )


def serve(
    database: str = ":memory:",
    *,
    port: int = DEFAULT_PORT,
    allowed_origins: tuple[str, ...] = ADX_ORIGINS,
    init: str | Path | None = None,
) -> None:
    """Run until interrupted. This is what the CLI's ``serve`` calls."""
    server = build_server(
        database, port=port, allowed_origins=allowed_origins, init=init
    )
    print(f"duckdb-kql serving {database} as database {server.database!r}")
    if init is not None:
        print(f"  init {init}")
    attached = [name for name in server.databases() if name != server.database]
    if attached:
        # Named at startup because a client reaches these as
        # `database("Name").Table`, and the name is the attach alias rather
        # than anything derivable from the file path.
        print(f"  attached: {', '.join(attached)}")
    print(f"  {server.url}")
    print("Connect from https://dataexplorer.azure.com -> Add connection")
    print("Local connections only. Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
