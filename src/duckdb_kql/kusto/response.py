"""``KustoResponseDataSet`` — the object ``execute()`` returns.

Shaped like ``azure.kusto.data.response``: iterate it for tables, index it by
position or by table name, and read ``primary_results`` for the query's own
output. A response carries the same three tables real Kusto returns for a query
— the result, ``QueryProperties``, and ``QueryCompletionInformation`` — because
code that walks a response looking for them should find them.
"""

from __future__ import annotations

from collections.abc import Iterator

from ._models import JsonTable, KustoResultTable, WellKnownDataSet

__all__ = ["KustoResponseDataSet"]


class KustoResponseDataSet:
    """The parsed data set carried by the response to a request."""

    _status_column = "Payload"
    _error_column = "Level"
    _crid_column = "ClientRequestId"

    def __init__(self, json_response: list[JsonTable]) -> None:
        self.tables = [KustoResultTable(t) for t in json_response]
        self.tables_count = len(self.tables)
        self.tables_names = [t.table_name for t in self.tables]

    @property
    def primary_results(self) -> list[KustoResultTable]:
        """The query's own output tables.

        A single-table response is returned whole, matching the SDK: a response
        that never labelled its tables would otherwise look empty.
        """
        if self.tables_count == 1:
            return self.tables
        return [t for t in self.tables if t.table_kind == WellKnownDataSet.PrimaryResult]

    @property
    def errors_count(self) -> int:
        """Always 0 — a failed query raises instead of returning.

        Real Kusto can report per-shard failures inside a successful response;
        one process against one DuckDB file has no equivalent, so there is
        nothing to under-report.
        """
        return 0

    def get_exceptions(self) -> list[str]:
        return []

    def __iter__(self) -> Iterator[KustoResultTable]:
        return iter(self.tables)

    def __getitem__(self, key: int | str) -> KustoResultTable:
        if isinstance(key, int):
            return self.tables[key]
        try:
            return self.tables[self.tables_names.index(key)]
        except ValueError:
            raise LookupError(key) from None

    def __len__(self) -> int:
        return self.tables_count
