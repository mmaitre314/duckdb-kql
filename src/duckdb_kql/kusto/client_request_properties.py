"""``ClientRequestProperties`` — request options and query parameters.

The surface is the SDK's, verbatim: ``set_parameter`` / ``set_option`` and their
``has_`` / ``get_`` companions, plus ``client_request_id``, ``application`` and
``user``.

What is *not* the SDK's is what happens to an option we cannot honour. Kusto has
dozens of request options, and a local translator can implement only some of
them. The tempting shortcut — store them all, act on the ones we know — means a
caller who sets ``truncationmaxrecords`` gets no truncation, silently, and finds
out when a report is wrong rather than when the code runs. So every option is
classified: implemented, accepted-as-a-no-op *because it cannot change this
client's answers*, or refused outright at execution time.

The classification lives in :data:`~duckdb_kql.options.OPTION_SUPPORT` — Layer 0,
because a ``set`` statement in the query text means the same thing and `lower`
has to read the same table — and is checked by a test that walks it, so an option
cannot quietly join the "stored and ignored" set.
"""

from __future__ import annotations

import json
from typing import Any

from .exceptions import KustoUnsupportedError

__all__ = ["ClientRequestProperties", "OPTION_SUPPORT", "OptionSupport"]


from ..options import OPTION_SUPPORT, OptionSupport


class ClientRequestProperties:
    """Options and parameters for one request.

    Same shape as ``azure.kusto.data.ClientRequestProperties``::

        props = ClientRequestProperties()
        props.set_parameter("state", user_input)     # bound as a value
        props.set_option(props.request_timeout_option_name, timedelta(seconds=30))
    """

    _CLIENT_REQUEST_ID = "client_request_id"

    results_defer_partial_query_failures_option_name = "deferpartialqueryfailures"
    request_timeout_option_name = "servertimeout"
    no_request_timeout_option_name = "norequesttimeout"

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._parameters: dict[str, Any] = {}
        self.client_request_id: str | None = None
        self.application: str | None = None
        self.user: str | None = None

    # -- parameters -------------------------------------------------------

    def set_parameter(self, name: str, value: Any) -> None:
        """Bind a value to a name declared by ``declare query_parameters``.

        The value is never rendered into the query. Unlike the SDK's signature
        it need not be a string: the declared KQL type decides what is accepted,
        so a ``datetime`` parameter takes a ``datetime``.
        """
        _assert_name(name)
        self._parameters[name] = value

    def has_parameter(self, name: str) -> bool:
        return name in self._parameters

    def get_parameter(self, name: str, default_value: Any = None) -> Any:
        return self._parameters.get(name, default_value)

    # -- options ----------------------------------------------------------

    def set_option(self, name: str, value: Any) -> None:
        """Set a request option.

        Validated here, at the call that sets it, rather than at execution: a
        stack trace pointing at the line that asked for something impossible is
        worth more than one pointing at the query.
        """
        _assert_name(name)
        support, reason = OPTION_SUPPORT.get(
            name.lower(),
            (
                OptionSupport.REFUSED,
                "not a request option this client recognises; an unrecognised "
                "option cannot be assumed harmless",
            ),
        )
        if support == OptionSupport.REFUSED:
            raise KustoUnsupportedError(f"request option {name!r}", hint=reason)
        self._options[name] = value

    def has_option(self, name: str) -> bool:
        return name in self._options

    def get_option(self, name: str, default_value: Any = None) -> Any:
        return self._options.get(name, default_value)

    # -- serialization ----------------------------------------------------

    def to_json(self) -> str:
        return json.dumps(
            {"Options": self._options, "Parameters": self._parameters}, default=str
        )

    def get_tracing_attributes(self) -> dict[str, str]:
        return {self._CLIENT_REQUEST_ID: str(self.client_request_id)}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ClientRequestProperties({self.to_json()})"


def _assert_name(name: str) -> None:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
