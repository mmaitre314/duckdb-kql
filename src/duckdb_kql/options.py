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

from collections.abc import Mapping
from typing import Any

__all__ = [
    "OPTION_SUPPORT",
    "SET_STATEMENT_NO_OP",
    "SET_STATEMENT_ONLY_AT_EXECUTION",
    "OptionSupport",
    "read_only_requested",
]


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
        OptionSupport.IMPLEMENTED,
        "A write — ingestion or a database command — is refused under it, as "
        "Kusto refuses one (measured). It was a no-op here on the grounds that "
        "translated KQL only reads, which stopped being the whole story when "
        "ingestion landed: the write went through.",
    ),
    "request_readonly_hardline": (
        OptionSupport.IMPLEMENTED,
        "Same as request_readonly: a write is refused under it (measured, the "
        "same refusal). The plugins it also disables are refused here anyway.",
    ),
    "request_app_name": (OptionSupport.NO_OP, "Recorded for tracing only."),
    "request_user": (OptionSupport.NO_OP, "Recorded for tracing only."),
    "request_description": (OptionSupport.NO_OP, "Recorded for tracing only."),
    "client_max_redirect_count": (
        OptionSupport.NO_OP,
        "There is no HTTP request to redirect.",
    ),
    "query_log_query_parameters": (
        OptionSupport.NO_OP,
        "There is no query journal to log parameters to: `.show queries` is "
        "refused here.",
    ),
    "query_weakconsistency_session_id": (
        OptionSupport.NO_OP,
        "Takes effect only under queryconsistency=weakconsistency_by_session_id, "
        "which is refused. On its own it selects nothing.",
    ),
    "results_error_reporting_placement": (
        OptionSupport.NO_OP,
        "Where errors go among partial results. There are none here: a query "
        "completes or raises.",
    ),
    "results_v2_fragment_primary_tables": (
        OptionSupport.NO_OP,
        "Response framing: the rows are the same rows, in one fragment or many.",
    ),
    "results_v2_newlines_between_frames": (
        OptionSupport.NO_OP,
        "Response framing, whitespace between frames.",
    ),
    "client_results_reader_allow_varying_row_widths": (
        OptionSupport.NO_OP,
        "A tolerance in the reader. Every row here has the result's width.",
    ),
    "query_results_progressive_row_count": (
        OptionSupport.NO_OP,
        "Tunes the progressive stream, which is a no-op above for the same reason.",
    ),
    "query_results_progressive_update_period": (
        OptionSupport.NO_OP,
        "Tunes the progressive stream, which is a no-op above for the same reason.",
    ),
    "push_selection_through_aggregation": (
        OptionSupport.NO_OP,
        "A plan hint for Kusto's engine. It cannot change a result, and DuckDB "
        "plans the query itself.",
    ),
    "query_optimize_fts_at_relop": (
        OptionSupport.NO_OP,
        "A plan hint for Kusto's free-text search. It cannot change a result.",
    ),
    "query_distribution_nodes_span": (
        OptionSupport.NO_OP,
        "Shapes the node hierarchy of a distributed query. There is one process "
        "here and no hierarchy; it cannot change a result.",
    ),
    "materialized_view_shuffle_query": (
        OptionSupport.NO_OP,
        "A shuffle-strategy hint for materialized views, which are refused here; "
        "a hint cannot change a result in any case.",
    ),
    "query_results_cache_force_refresh": (
        OptionSupport.NO_OP,
        "There is no results cache: every result is computed fresh, which is "
        "what a forced refresh asks for.",
    ),
    # Restrictions that already hold, because what each one forbids is refused
    # in every form. `tests/test_request_options.py` pins each of those
    # refusals by name: the day one of them starts translating, its option
    # stops being a no-op, and that test is what says so.
    "request_callout_disabled": (
        OptionSupport.NO_OP,
        "Nothing here calls out: `evaluate` (http_request, sql_request and every "
        "other plugin) is refused. The restriction already holds.",
    ),
    "request_sandboxed_execution_disabled": (
        OptionSupport.NO_OP,
        "Nothing here runs in a sandbox: `evaluate python` and `evaluate r` are "
        "refused. The restriction already holds.",
    ),
    "request_external_data_disabled": (
        OptionSupport.NO_OP,
        "`externaldata` and `external_table()` are refused. The restriction "
        "already holds.",
    ),
    "request_external_table_disabled": (
        OptionSupport.NO_OP,
        "`external_table()` is refused. The restriction already holds.",
    ),
    "query_cursor_disabled": (
        OptionSupport.NO_OP,
        "The cursor functions are refused. The restriction already holds.",
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
    # Spelled `query_datetime_scope_*` here until the documentation was read
    # against this table: the real names have no underscore inside
    # "datetimescope", so the reasons below were keyed on names no caller sends
    # and the real ones fell through to the generic refusal.
    "query_datetimescope_column": (
        "Datetime scoping rewrites the query's time filter server-side. Ignoring "
        "it would silently widen the window the caller asked for."
    ),
    "query_datetimescope_from": (
        "Half of a datetime scope; see query_datetimescope_column. Ignoring it "
        "would silently widen the window the caller asked for."
    ),
    "query_datetimescope_to": (
        "The other half; see query_datetimescope_column. Ignoring it would "
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
    # The rest of the documented options. Each one changes what a query
    # returns, or whether it runs at all, in a way nothing here reproduces.
    "maxoutputcolumns": (
        "A limit Kusto enforces by refusing the query (measured: SEM0004 past "
        "it). Ignoring it would answer a query Kusto refuses."
    ),
    "query_max_entities_in_union": (
        "A limit Kusto enforces by refusing the query; ignoring it would answer "
        "a query Kusto refuses."
    ),
    "query_results_apply_getschema": (
        "Replaces the result with its schema (measured). Ignoring it would "
        "return rows where the caller asked for columns; write `| getschema` "
        "instead."
    ),
    "validatepermissions": (
        "Returns a permissions verdict instead of running the query. There are "
        "no permissions here to validate, and running the query would answer a "
        "different question."
    ),
    "best_effort": (
        "Changes which tables a union resolves to. This translator refuses an "
        "unresolvable table rather than tolerating it, and that tolerance is "
        "what the option asks for."
    ),
    "query_datascope": (
        "'hotcache' restricts a query to cached data, and nothing here is "
        "cached or not; answering from all of it would silently widen the scope."
    ),
    "query_force_row_level_security": (
        "Row level security policies are not modelled here, so there are no "
        "rules to enforce; ignoring the request would return rows a policy "
        "hides."
    ),
    "request_block_row_level_security": (
        "Row level security policies are not modelled here, so no table is "
        "known to have one to block."
    ),
    "request_remote_entities_disabled": (
        "`cluster()` and `database()` references are answered from local "
        "stand-ins. Ignoring this would answer a query Kusto refuses."
    ),
    "request_impersonation_disabled": (
        "It stops cross-cluster queries in Kusto, and `cluster()` is answered "
        "here from a local stand-in. Ignoring it would answer a query Kusto "
        "refuses."
    ),
    "query_cursor_after_default": (
        "Database cursors are not modelled, and the cursor functions are "
        "refused; the setting would configure nothing."
    ),
    "query_cursor_before_or_at_default": (
        "Database cursors are not modelled; see query_cursor_after_default."
    ),
    "query_cursor_current": (
        "Database cursors are not modelled; see query_cursor_after_default."
    ),
    "query_cursor_scoped_tables": (
        "Scopes tables to a cursor range, and there are no cursors here. "
        "Ignoring it would silently widen the rows the caller asked for."
    ),
    "query_python_debug": (
        "`evaluate python` is refused, so the setting would configure nothing."
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
#:
#: The product documentation says neither can be set with a ``set`` statement.
#: The emulator disagrees for servertimeout — ``set servertimeout = 10ms`` times
#: the query out — which is one more reason to refuse rather than ignore: the
#: caller has every reason to believe the statement works.
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


#: Options implemented in their request form whose ``set``-statement form is a
#: no-op, and why. Read-only is enforced by refusing a *write*, and a ``set``
#: statement heads a query, which does not write — so there the guarantee holds
#: by construction. The documentation says these cannot be set by statement,
#: and Kusto accepts the statement anyway (measured), so refusing it would reject
#: a query Kusto runs over an option that changes nothing either way.
SET_STATEMENT_NO_OP: dict[str, str] = {
    "request_readonly": "a query does not write, so a read-only query is every query",
    "request_readonly_hardline": "same as request_readonly",
}


_READ_ONLY = ("request_readonly", "request_readonly_hardline")


def read_only_requested(options: Mapping[str, Any]) -> bool:
    """Whether *options* ask for the request to be read-only, in either flavour.

    Values arrive as Python booleans from `set_option` and as JSON from the wire
    — but a hand-written request can carry the string ``"true"``, and reading
    that as false would let the write through.
    """
    for name, value in options.items():
        if name.lower() in _READ_ONLY and _truthy(value):
            return True
    return False


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)
