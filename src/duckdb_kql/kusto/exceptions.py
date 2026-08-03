"""Exceptions shaped like ``azure.kusto.data.exceptions``.

Existing code catches ``KustoServiceError`` around a query; keeping that name and
that base means a caller's error handling keeps working when the client
underneath changes.

The addition is :class:`KustoUnsupportedError`. It exists because the honest
answer to "this client cannot do what that option asks for" is to say so. An
option that is accepted and then ignored is the failure mode worth designing
against: the caller believes a timeout, a consistency level, or a truncation
limit is in force when nothing of the sort is happening.
"""

from __future__ import annotations

__all__ = [
    "KustoError",
    "KustoServiceError",
    "KustoClientError",
    "KustoClosedError",
    "KustoUnsupportedError",
]


class KustoError(Exception):
    """Base class for every error this client raises."""


class KustoServiceError(KustoError):
    """The query was rejected or failed while running.

    Mirrors the SDK's class, including ``get_partial_results`` — we never return
    partial results, so it is empty, and :meth:`has_partial_results` says so
    rather than leaving a caller to find out by indexing into nothing.
    """

    def __init__(
        self,
        messages: object,
        http_response: object = None,
        kusto_response: object = None,
    ):
        super().__init__(messages)
        self.http_response = http_response
        self.kusto_response = kusto_response

    def get_raw_http_response(self) -> object:
        return self.http_response

    def is_semantic_error(self) -> bool:
        """Whether the failure was the query's meaning rather than its running.

        The distinction the SDK draws is the server's; ours is whether the
        failure came from translation (a construct we do not support, a column
        that does not exist) or from execution.
        """
        return bool(getattr(self, "_semantic", False))

    def has_partial_results(self) -> bool:
        return False

    def get_partial_results(self) -> list:
        return []


class KustoClientError(KustoError):
    """The client was asked for something it cannot do."""


class KustoClosedError(KustoClientError):
    """The client has been closed."""

    def __init__(self) -> None:
        super().__init__("Client is closed")


class KustoUnsupportedError(KustoClientError):
    """A request property or command this client refuses rather than ignores."""

    def __init__(self, what: str, hint: str | None = None):
        self.what = what
        self.hint = hint
        because = f" ({hint})" if hint else ""
        super().__init__(f"unsupported by duckdb-kql: {what}{because}")
