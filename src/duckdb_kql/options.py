"""How this translator treats each Kusto **request option**, and why.

One table, two spellings. Kusto lets a caller send an option beside the request
*or* write it into the query text as a ``set`` statement, and they mean the same
thing — so they are classified in one place rather than twice. The client reaches
it through :mod:`duckdb_kql.kusto`; `lower` reaches it directly, because a
``set`` statement is Layer 0 and cannot depend on Layer 2.

Kusto has dozens of these and a local translator can implement only some. The
tempting shortcut — store them all, act on the ones we know — means a caller who
sets ``truncationmaxrecords`` gets no truncation, silently, and finds out when a
report is wrong rather than when the code runs. So every option is classified:
implemented, accepted-as-a-no-op *because it cannot change this translator's
answers*, or refused outright.

Kusto itself accepts an **unknown** option silently — measured, ``set
not_a_real_option = 5`` runs there. We refuse instead: an option nobody
recognises is not a safe one, and a caller who misspells ``truncationmaxrecords``
should hear about it.
"""

from __future__ import annotations

__all__ = ["OPTION_SUPPORT", "OptionSupport", "SET_STATEMENT_ONLY_AT_EXECUTION"]


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
    "query_now": (
        OptionSupport.IMPLEMENTED,
        "Pins the query clock: `now()` and `ago()` resolve against the supplied "
        "instant instead of the wall clock, through one binding shared by the "
        "whole statement. The point is deterministic tests of queries written "
        "against `now()`, unchanged.",
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
    # Both of these are in the frozen corpus, scraped from the product
    # documentation, which is how their spelling is known to be real. They
    # reached the generic "not one of the options this translator implements"
    # until a `set` statement started being classified rather than refused
    # wholesale, and the generic message is the right answer for an option
    # nobody has written down — but not for one the documentation uses.
    "query_results_cache_per_shard": (
        "Follows query_results_cache_max_age: there is no results cache, so "
        "there are no shards of one to enable it for."
    ),
    "query_take_max_records": (
        "Same as truncationmaxrecords: a result capped without saying so is "
        "indistinguishable from a short one."
    ),
}

for _name, _reason in _REFUSED_WITH_REASON.items():
    OPTION_SUPPORT[_name] = (OptionSupport.REFUSED, _reason)
del _name, _reason


#: Options whose *request* form is implemented but whose ``set``-statement form
#: is not, with what to use instead. Both of these are enforced when the query
#: **runs** — the deadline interrupts DuckDB — and a ``set`` statement is read at
#: *translation* time, where there is no query running and nothing to interrupt.
#: Accepting one there would look honoured and do nothing, so the statement form
#: is refused and says where the working spelling is.
SET_STATEMENT_ONLY_AT_EXECUTION: dict[str, str] = {
    "servertimeout": (
        "the timeout is enforced while the query runs, and a `set` statement is "
        "read when it is translated. Pass it as a request option "
        "(ClientRequestProperties.set_option) instead"
    ),
    "norequesttimeout": (
        "same as servertimeout: pass it as a request option instead"
    ),
}
