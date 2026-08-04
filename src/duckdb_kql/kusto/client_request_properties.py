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

The classification lives in :data:`OPTION_SUPPORT` and is checked by a test that
walks it, so an option cannot quietly join the "stored and ignored" set.
"""

from __future__ import annotations

import json
from typing import Any

from .exceptions import KustoUnsupportedError

__all__ = ["ClientRequestProperties", "OPTION_SUPPORT", "OptionSupport"]


class OptionSupport:
    """How this client treats one request option."""

    #: We act on it.
    IMPLEMENTED = "implemented"
    #: We accept it and do nothing, because doing nothing *is* the behaviour it
    #: asks for here — not because we cannot be bothered.
    NO_OP = "no-op"
    #: We refuse it: honouring it is impossible or would need to be faked.
    REFUSED = "refused"


#: Every Kusto request option this client has an opinion about, and why.
#: Anything not listed is refused too — an unknown option is not a safe one.
OPTION_SUPPORT: dict[str, tuple[str, str]] = {
    # -- implemented ------------------------------------------------------
    "servertimeout": (
        OptionSupport.IMPLEMENTED,
        "Enforced by interrupting the DuckDB query when the deadline passes.",
    ),
    "norequesttimeout": (
        OptionSupport.IMPLEMENTED,
        "Disables the timeout above.",
    ),
    # -- accepted as a no-op ----------------------------------------------
    "deferpartialqueryfailures": (
        OptionSupport.NO_OP,
        "This client never returns partial results: a query either completes or "
        "raises. There is no partial failure to defer or to surface.",
    ),
    "results_progressive_enabled": (
        OptionSupport.NO_OP,
        "Progressive framing is a streaming-transport concern. There is no "
        "transport here, and the full result is already materialised.",
    ),
    "request_readonly": (
        OptionSupport.NO_OP,
        "Translated KQL only ever reads: no operator in the supported surface "
        "writes. The guarantee the option asks for already holds.",
    ),
    "request_app_name": (OptionSupport.NO_OP, "Recorded for tracing only."),
    "request_user": (OptionSupport.NO_OP, "Recorded for tracing only."),
    "request_description": (OptionSupport.NO_OP, "Recorded for tracing only."),
    "client_max_redirect_count": (
        OptionSupport.NO_OP,
        "There is no HTTP request to redirect.",
    ),
}

#: Options a caller is most likely to reach for that we deliberately refuse,
#: with the reason. Kept separate from the table above so the refusal has an
#: explanation rather than falling through to the generic "unknown option".
_REFUSED_WITH_REASON = {
    "query_now": (
        "Overriding now() means threading a clock through every datetime "
        "function. Until that exists, a query using now() with this option set "
        "would silently use the real clock."
    ),
    "queryconsistency": (
        "A single local database has one consistency level. Accepting "
        "'weakconsistency' would suggest a choice that does not exist."
    ),
    "truncationmaxrecords": (
        "Kusto truncates a result and *tells you* it did, via "
        "QueryCompletionInformation. Silently returning fewer rows without that "
        "signal would look like a complete answer."
    ),
    "truncationmaxsize": (
        "Same as truncationmaxrecords: a truncated result that does not "
        "announce itself is indistinguishable from a short one."
    ),
    "notruncation": (
        "Nothing truncates here, so this is not the no-op it looks like: a "
        "caller setting it believes truncation was otherwise in play."
    ),
    "query_datetime_scope_column": (
        "Datetime scoping rewrites the query's time filter server-side. Ignoring "
        "it would silently widen the window the caller asked for."
    ),
    "query_datetime_scope_from": (
        "Half of a datetime scope; see query_datetime_scope_column. Ignoring it "
        "would silently widen the window the caller asked for."
    ),
    "query_datetime_scope_to": (
        "The other half; see query_datetime_scope_column. Ignoring it would "
        "silently widen the window the caller asked for."
    ),
    "query_language": (
        "This client speaks KQL. Accepting 'sql' or 'csl' would promise a "
        "dialect it does not translate."
    ),
    "query_bin_auto_size": (
        "bin_auto() is not in the supported surface, so the setting would "
        "configure nothing."
    ),
    "query_bin_auto_at": (
        "The alignment point for bin_auto(), which is not in the supported "
        "surface either; the setting would configure nothing."
    ),
    "maxmemoryconsumptionperiterator": (
        "DuckDB's memory limit is a connection setting with different units and "
        "different scope; mapping one to the other would be a guess."
    ),
    "max_memory_consumption_per_query_per_node": (
        "Same as maxmemoryconsumptionperiterator: DuckDB's memory limit has "
        "different units and different scope, so mapping one to the other "
        "would be a guess dressed up as a limit."
    ),
    "query_fanout_nodes_percent": (
        "Fanout spreads a query over a cluster's nodes. There is one process "
        "here, so the setting would describe a topology that does not exist."
    ),
    "query_fanout_threads_percent": (
        "DuckDB's threading is a connection setting, not a per-query one."
    ),
    "query_results_cache_max_age": (
        "There is no results cache, so a max age would govern nothing."
    ),
}

for _name, _reason in _REFUSED_WITH_REASON.items():
    OPTION_SUPPORT[_name] = (OptionSupport.REFUSED, _reason)
del _name, _reason


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
