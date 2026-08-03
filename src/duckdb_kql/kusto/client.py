"""``KustoClient`` — the ``azure-kusto-data`` shape, backed by DuckDB.

The point of this layer is that code already written against the Kusto SDK runs
unchanged against a local DuckDB file: same construction, same ``execute``, same
response object, same ``dataframe_from_result_table``. What changes is the
connection string.

Two things are deliberately *not* imitated.

**Authentication.** There is no service, so there is nothing to authenticate to
and no credential to check. The ``with_*_authentication`` constructors exist so
that drop-in code keeps working, and they discard the credentials they are
given. That is only defensible because the data source must be local: a cluster
URL is refused rather than opened, so ignoring credentials can never mean "sent
your query somewhere without them". :class:`KustoConnectionStringBuilder`
enforces that.

**Silent acceptance.** Every ``ClientRequestProperties`` option is implemented,
or is a no-op *because it cannot change this client's answers*, or is refused —
see ``client_request_properties.OPTION_SUPPORT``. The same goes for control
commands: a handful are implemented and the rest raise.
"""

from __future__ import annotations

import datetime as dt
import re
import threading
import uuid
from typing import Any

from ..errors import KqlError
from ._models import WellKnownDataSet, kusto_type, to_wire
from .client_request_properties import ClientRequestProperties
from .exceptions import KustoClosedError, KustoServiceError, KustoUnsupportedError
from .response import KustoResponseDataSet

__all__ = ["KustoClient", "KustoConnectionStringBuilder"]

#: A data source we refuse to open. Accepting one and quietly reading a local
#: file instead would answer a question about the cluster with data from
#: somewhere else entirely.
_REMOTE = re.compile(r"^(https?|net\.tcp)://", re.IGNORECASE)


class KustoConnectionStringBuilder:
    """A connection string, shaped like the SDK's builder.

    Accepts either a DuckDB database path (``"analytics.duckdb"``,
    ``":memory:"``) or a Kusto-style connection string
    (``"Data Source=analytics.duckdb;Initial Catalog=Logs"``).

    A cluster URL is refused. That refusal is what makes it safe for the
    ``with_*_authentication`` constructors below to ignore credentials.
    """

    #: SDK keyword -> our attribute, for the subset that means something locally.
    _KEYWORDS = {
        "data source": "data_source",
        "addr": "data_source",
        "address": "data_source",
        "network address": "data_source",
        "server": "data_source",
        "initial catalog": "database_name",
        "database": "database_name",
    }

    def __init__(self, connection_string: str):
        if not isinstance(connection_string, str):
            raise TypeError(
                f"connection_string must be str, got {type(connection_string).__name__}"
            )

        self.data_source: str = ""
        self.database_name: str | None = None
        #: Credentials a drop-in caller supplied. Kept only so that reading them
        #: back shows they were not used, never sent anywhere.
        self.ignored_credentials: dict[str, Any] = {}

        # `Data Source=x;Initial Catalog=y` versus a bare path. A Windows path
        # has no '=', and a keyword string always has one before its first ';'.
        if "=" in connection_string.split(";")[0]:
            self._parse_keyword_string(connection_string)
        else:
            self.data_source = connection_string.strip()

        if not self.data_source:
            raise ValueError("connection string has no data source")
        _reject_remote(self.data_source)

    def _parse_keyword_string(self, text: str) -> None:
        for part in text.split(";"):
            if not part.strip():
                continue
            key, _, value = part.partition("=")
            attr = self._KEYWORDS.get(key.strip().lower())
            if attr is None:
                # Unknown keywords are almost always auth material. Record that
                # they were dropped instead of pretending they configured
                # something.
                self.ignored_credentials[key.strip()] = value.strip()
                continue
            setattr(self, attr, value.strip())

    # -- SDK-compatible constructors --------------------------------------

    @classmethod
    def with_no_authentication(cls, data_source: str) -> KustoConnectionStringBuilder:
        return cls(data_source)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"KustoConnectionStringBuilder(data_source={self.data_source!r}, "
            f"database_name={self.database_name!r})"
        )


def _reject_remote(data_source: str) -> None:
    if _REMOTE.match(data_source.strip()):
        raise KustoUnsupportedError(
            f"data source {data_source!r}",
            hint=(
                "this client runs queries locally against DuckDB and never "
                "contacts a cluster; give it a database path so it is obvious "
                "which data is being queried"
            ),
        )


def _ignoring_credentials(name: str):
    """Build a ``with_*_authentication`` constructor that drops its credentials.

    Generated rather than written out because the bodies would be identical, and
    identical bodies invite one of them drifting into actually using an argument.
    """

    def constructor(cls, connection_string: str, *args: Any, **kwargs: Any):
        kcsb = cls(connection_string)
        if args or kwargs:
            kcsb.ignored_credentials[name] = "(discarded — nothing to authenticate to)"
        return kcsb

    constructor.__name__ = name
    constructor.__qualname__ = f"KustoConnectionStringBuilder.{name}"
    constructor.__doc__ = (
        f"Drop-in for the SDK's ``{name}``. The credentials are discarded: this "
        "client queries a local database, so there is no service to present "
        "them to. A cluster URL is refused, so nothing can be sent unauthenticated."
    )
    return classmethod(constructor)


for _auth in (
    "with_aad_application_key_authentication",
    "with_aad_application_certificate_authentication",
    "with_aad_application_certificate_sni_authentication",
    "with_aad_application_token_authentication",
    "with_aad_device_authentication",
    "with_aad_managed_service_identity_authentication",
    "with_aad_user_password_authentication",
    "with_aad_user_token_authentication",
    "with_az_cli_authentication",
    "with_azure_token_credential",
    "with_interactive_login",
    "with_token_provider",
):
    setattr(KustoConnectionStringBuilder, _auth, _ignoring_credentials(_auth))
del _auth


class KustoClient:
    """Runs KQL against a local DuckDB database, with the SDK's interface.

    ::

        client = KustoClient("analytics.duckdb")
        props = ClientRequestProperties()
        props.set_parameter("state", user_input)
        response = client.execute(
            "Logs",
            "declare query_parameters(state:string);"
            " StormEvents | where State == state | take 10",
            props,
        )
        for row in response.primary_results[0]:
            print(row["State"])

    The client owns the connection it opens and closes it with the client. Pass
    an existing ``duckdb`` connection instead and it is left alone — closing
    something the caller handed you is rarely what they meant.
    """

    def __init__(
        self,
        kcsb: KustoConnectionStringBuilder | str | Any,
        database: str | None = None,
    ):
        self._is_closed = False
        self._lock = threading.Lock()
        self._owns_connection = True
        self.default_database = database

        if hasattr(kcsb, "execute") and hasattr(kcsb, "sql"):
            # A duckdb connection: use it, do not adopt it.
            self._connection = kcsb
            self._owns_connection = False
            self._data_source = "<connection>"
            self._connection.execute("SET TimeZone='UTC'")
        else:
            if isinstance(kcsb, str):
                kcsb = KustoConnectionStringBuilder(kcsb)
            data_source = getattr(kcsb, "data_source", None)
            if not data_source:
                raise ValueError("connection string has no data source")
            _reject_remote(data_source)

            from ..engine import connect

            self._data_source = data_source
            self._connection = connect(data_source)
            self.default_database = database or getattr(kcsb, "database_name", None)

    # -- lifecycle --------------------------------------------------------

    def close(self) -> None:
        if not self._is_closed and self._owns_connection:
            self._connection.close()
        self._is_closed = True

    def __enter__(self) -> KustoClient:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"KustoClient({self._data_source!r})"

    # -- execution --------------------------------------------------------

    def execute(
        self,
        database: str | None,
        query: str,
        properties: ClientRequestProperties | None = None,
    ) -> KustoResponseDataSet:
        """Execute a query or a control command, dispatching on the leading dot."""
        query = query.strip()
        if query.startswith("."):
            return self.execute_mgmt(database, query, properties)
        return self.execute_query(database, query, properties)

    def execute_query(
        self,
        database: str | None,
        query: str,
        properties: ClientRequestProperties | None = None,
    ) -> KustoResponseDataSet:
        """Execute a KQL query.

        Any ``set_parameter`` values on *properties* are bound to the query's
        ``declare query_parameters`` declarations. They are bound as values, so
        a caller can pass user input directly.
        """
        self._check_open()
        con = self._select_database(database)
        parameters = dict(getattr(properties, "_parameters", {}) or {})

        from .. import to_sql
        from ..engine import schema

        try:
            translated = to_sql(query, schema=schema(con), parameters=parameters)
        except KqlError as exc:
            raise _semantic_error(exc) from exc

        unbound = getattr(translated, "unbound", ())
        if unbound:
            error = KustoServiceError(
                f"no value for declared query parameter(s) {', '.join(unbound)} — "
                "supply one with ClientRequestProperties.set_parameter"
            )
            error._semantic = True
            raise error

        bound = getattr(translated, "parameters", {})
        with self._deadline(properties):
            try:
                with self._lock:
                    rel = (
                        con.sql(str(translated), params=bound)
                        if bound
                        else con.sql(str(translated))
                    )
                    columns = list(rel.columns)
                    types = [kusto_type(t) for t in rel.types]
                    rows = rel.fetchall()
            except Exception as exc:  # noqa: BLE001 - any engine failure is the answer
                raise KustoServiceError(str(exc)) from exc

        return self._response(query, columns, types, rows, properties)

    def execute_mgmt(
        self,
        database: str | None,
        query: str,
        properties: ClientRequestProperties | None = None,
    ) -> KustoResponseDataSet:
        """Execute a control command.

        Only the handful below are implemented. The rest — ingestion, policy,
        schema management — describe a cluster's administration, and there is no
        cluster; a stub returning an empty table would look like a command that
        worked.
        """
        self._check_open()
        con = self._select_database(database)
        command = " ".join(query.strip().lower().split())

        if command == ".show version":
            columns = ["BuildVersion", "BuildTime", "ServiceType", "ProductVersion"]
            types = ["string", "datetime", "string", "string"]
            from .. import __version__ as version

            rows = [
                (
                    version,
                    dt.datetime(2026, 1, 1),
                    "Engine",
                    f"duckdb-kql {version}",
                )
            ]
        elif command == ".show databases":
            columns = ["DatabaseName", "PersistentStorage", "Version"]
            types = ["string", "string", "string"]
            rows = [
                (name, self._data_source, "v1.0")
                for (name,) in con.execute(
                    "SELECT database_name FROM duckdb_databases() "
                    "WHERE NOT internal ORDER BY database_name"
                ).fetchall()
            ]
        elif command == ".show tables":
            columns = ["TableName", "DatabaseName", "Folder", "DocString"]
            types = ["string", "string", "string", "string"]
            rows = [
                (table, db, None, None)
                for table, db in con.execute(
                    "SELECT table_name, table_catalog FROM information_schema.tables "
                    "ORDER BY table_catalog, table_name"
                ).fetchall()
            ]
        else:
            raise KustoUnsupportedError(
                f"control command {query.strip()!r}",
                hint=(
                    "this client implements .show version, .show databases and "
                    ".show tables; there is no cluster for the rest to act on"
                ),
            )

        return self._response(query, columns, types, rows, properties)

    # -- internals --------------------------------------------------------

    def _check_open(self) -> None:
        if self._is_closed:
            raise KustoClosedError()

    def _select_database(self, database: str | None) -> Any:
        """Point the connection at *database*, or explain why it cannot.

        A DuckDB connection has one database unless others are attached. Quietly
        answering from the wrong one is the failure this guards against: code
        that queries several databases through one client would otherwise get
        consistent-looking answers from whichever happened to be open.
        """
        if not database:
            return self._connection

        attached = {
            name
            for (name,) in self._connection.execute(
                "SELECT database_name FROM duckdb_databases() WHERE NOT internal"
            ).fetchall()
        }
        if database in attached:
            self._connection.execute(f'USE "{database}"')
            return self._connection

        if self.default_database and database != self.default_database:
            raise KustoUnsupportedError(
                f"database {database!r}",
                hint=(
                    f"this client is connected to {self.default_database!r}; "
                    f"ATTACH {database!r} to query it, rather than having the "
                    "name silently ignored"
                ),
            )
        # No catalog by that name and no conflicting default: the caller is
        # naming the one database there is.
        return self._connection

    def _deadline(self, properties: ClientRequestProperties | None):
        """Enforce ``servertimeout`` by interrupting the query.

        DuckDB's ``interrupt()`` cancels the running statement and leaves the
        connection usable, so the timeout is real rather than a promise to check
        the clock afterwards.
        """
        timeout = None
        if properties is not None:
            if not properties.get_option(
                ClientRequestProperties.no_request_timeout_option_name, False
            ):
                timeout = properties.get_option(
                    ClientRequestProperties.request_timeout_option_name, None
                )
        return _Deadline(self._connection, _seconds(timeout))

    def _response(
        self,
        query: str,
        columns: list,
        types: list,
        rows: list,
        properties: ClientRequestProperties | None,
    ) -> KustoResponseDataSet:
        """Assemble the three tables a Kusto query response carries."""
        crid = getattr(properties, "client_request_id", None) or f"duckdb-kql;{uuid.uuid4()}"

        primary = {
            "TableName": "PrimaryResult",
            "TableId": 0,
            "TableKind": WellKnownDataSet.PrimaryResult.value,
            "Columns": [
                {"ColumnName": name, "ColumnType": kind}
                for name, kind in zip(columns, types)
            ],
            "Rows": [
                [to_wire(value, kind) for value, kind in zip(row, types)] for row in rows
            ],
        }
        query_properties = {
            "TableName": "@ExtendedProperties",
            "TableId": 1,
            "TableKind": WellKnownDataSet.QueryProperties.value,
            "Columns": [
                {"ColumnName": "TableId", "ColumnType": "int"},
                {"ColumnName": "Key", "ColumnType": "string"},
                {"ColumnName": "Value", "ColumnType": "dynamic"},
            ],
            "Rows": [],
        }
        completion = {
            "TableName": "QueryCompletionInformation",
            "TableId": 2,
            "TableKind": WellKnownDataSet.QueryCompletionInformation.value,
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
                    to_wire(dt.datetime.now(dt.timezone.utc), "datetime"),
                    crid,
                    str(uuid.uuid4()),
                    str(uuid.uuid4()),
                    str(uuid.uuid4()),
                    4,
                    "Info",
                    0,
                    "S_OK",
                    4,
                    "QueryInfo",
                    '{"Count":1,"Text":"Query completed successfully"}',
                ]
            ],
        }
        return KustoResponseDataSet([primary, query_properties, completion])


def _seconds(timeout: Any) -> float | None:
    if timeout is None:
        return None
    if isinstance(timeout, dt.timedelta):
        return timeout.total_seconds()
    if isinstance(timeout, (int, float)):
        return float(timeout)
    from ..params import parse_timespan

    parsed = parse_timespan(str(timeout))
    if parsed is None:
        raise KustoUnsupportedError(
            f"servertimeout {timeout!r}",
            hint="expected a timedelta, a number of seconds, or a KQL timespan",
        )
    return parsed.total_seconds()


class _Deadline:
    """Interrupt a connection's running query once *seconds* have passed."""

    def __init__(self, connection: Any, seconds: float | None):
        self._connection = connection
        self._seconds = seconds
        self._timer: threading.Timer | None = None
        self._fired = False

    def __enter__(self) -> _Deadline:
        if self._seconds is not None:
            self._timer = threading.Timer(self._seconds, self._interrupt)
            self._timer.daemon = True
            self._timer.start()
        return self

    def _interrupt(self) -> None:
        self._fired = True
        try:
            self._connection.interrupt()
        except Exception:  # noqa: BLE001 - nothing useful to do from a timer thread
            pass

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        if self._timer is not None:
            self._timer.cancel()
        if self._fired and exc_type is not None:
            raise KustoServiceError(
                f"query timed out after {self._seconds}s (servertimeout)"
            ) from exc_val
        return False


def _semantic_error(exc: KqlError) -> KustoServiceError:
    """Wrap a translation failure so ``is_semantic_error()`` reports it as one.

    Everything KqlError covers — a syntax error, an unsupported construct, an
    unknown column — is a statement about the query rather than about running
    it, which is the distinction the SDK's flag draws.
    """
    error = KustoServiceError(str(exc))
    error._semantic = True
    return error
