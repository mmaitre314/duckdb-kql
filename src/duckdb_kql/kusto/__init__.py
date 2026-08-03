"""Layer 2 — the ``azure-kusto-data`` interface, backed by DuckDB.

For code already written against the Kusto SDK. Change the import and the
connection string; leave the queries, the request properties and the result
handling alone::

    -from azure.kusto.data import KustoClient, ClientRequestProperties
    -from azure.kusto.data.helpers import dataframe_from_result_table
    +from duckdb_kql.kusto import KustoClient, ClientRequestProperties
    +from duckdb_kql.kusto.helpers import dataframe_from_result_table

     client = KustoClient(connection_string)
     response = client.execute("Logs", query, properties)
     df = dataframe_from_result_table(response.primary_results[0])

Where it stops short of the SDK it says so rather than pretending:

* **Credentials are discarded.** There is no service to present them to. This is
  safe only because a cluster URL is refused outright — see
  :class:`~duckdb_kql.kusto.client.KustoConnectionStringBuilder`.
* **Request options are implemented, or refused.** ``set_option`` raises for
  anything this client cannot honour, at the line that sets it. The full
  classification, with a reason for each entry, is
  :data:`~duckdb_kql.kusto.client_request_properties.OPTION_SUPPORT`.
* **Control commands are mostly refused.** ``.show version``,
  ``.show databases`` and ``.show tables`` work; the rest administer a cluster
  that does not exist here.
* **Async and streaming are absent.** ``KustoStreamingResponseDataSet`` exists to
  avoid holding a large remote result in memory. A local query has no such
  round trip to amortise, so a streaming API here would be ceremony around a
  list — and an async one would be a coroutine wrapping a synchronous call,
  which buys concurrency nobody gets.

``pandas`` is needed only by :mod:`duckdb_kql.kusto.helpers`; the client itself
runs without it.
"""

from __future__ import annotations

from ._models import (
    KustoResultColumn,
    KustoResultRow,
    KustoResultTable,
    WellKnownDataSet,
)
from .client import KustoClient, KustoConnectionStringBuilder
from .client_request_properties import (
    OPTION_SUPPORT,
    ClientRequestProperties,
    OptionSupport,
)
from .exceptions import (
    KustoClientError,
    KustoClosedError,
    KustoError,
    KustoServiceError,
    KustoUnsupportedError,
)
from .response import KustoResponseDataSet

__all__ = [
    "KustoClient",
    "KustoConnectionStringBuilder",
    "ClientRequestProperties",
    "OPTION_SUPPORT",
    "OptionSupport",
    "KustoResponseDataSet",
    "KustoResultTable",
    "KustoResultRow",
    "KustoResultColumn",
    "WellKnownDataSet",
    "KustoError",
    "KustoServiceError",
    "KustoClientError",
    "KustoClosedError",
    "KustoUnsupportedError",
]
