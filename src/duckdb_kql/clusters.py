"""Resolving Kusto cluster references onto local DuckDB databases.

A query written against a real service reads

    cluster('mycluster.eastus.kusto.windows.net').database('mydb').MyTable

and the point of running it here is to get the same answer from local test
data. There is no cluster, so the reference has to be *mapped*, and the mapping
has to be supplied — never guessed.

**Why unmapped means refused.** Treating a cluster reference as local would
answer a question about production with local data. That is the failure this
package exists to prevent, and it is worse than an error because the query still
returns rows. So `cluster(...)` without a mapping raises, exactly as it did
before mappings existed; a mapping opts *into* a specific, stated substitution.

**What the mapping keys are.** Measured on the emulator, Kusto resolves the
cluster argument to a URI and reports it in errors:

===============================================  ==========================================
written                                          resolved to
===============================================  ==========================================
``cluster('mycluster')``                         ``https://mycluster/``
``cluster('mycluster.eastus.kusto.windows.net')``  ``https://mycluster.eastus.kusto.windows.net/``
``cluster('https://mycluster.eastus.kusto.windows.net')``  the same as the row above
===============================================  ==========================================

So the scheme and the trailing slash are noise and are normalized away here,
letting one entry cover every spelling of one host.

**A short name names the same cluster as its domain.** Microsoft's reference for
`cluster()` says the argument "can be specified as a fully qualified domain
name, or the name of the cluster without the `.kusto.windows.net` suffix", and
gives `cluster('help')` and `cluster('help.kusto.windows.net')` as the same
cluster. A real cluster completes a bare name with its own DNS suffix; the
emulator has none to complete with, which is why the table above shows it
resolving `cluster('mycluster')` to `https://mycluster/` and never reaching
anything. That row is the emulator's URI builder, not a resolution rule — the
emulator has no cluster federation to resolve *with*, so it is not the oracle
for this one question and the documented behaviour stands.

The completion is therefore applied **when matching against the map, not when
normalizing a host**: :func:`resolve` tries the reference as written and then
its other spelling, so one entry covers both and neither `cluster('c.eastus')`
nor `cluster('c.eastus.kusto.windows.net')` has to be the one the map was
written in. Nothing else sees the completed form — `.show entity_groups` still
echoes the host as its group was written.

That containment is what makes the guess safe. `.kusto.windows.net` is the
public cloud's suffix, and a sovereign cloud's (`.kusto.chinacloudapi.cn`) or a
custom domain's is not knowable from the name alone. Because the completion only
ever *adds a candidate key*, guessing wrong costs a refusal — never a query
answered from the wrong database. A map whose two keys spell one cluster two
ways and point at different databases would cost exactly that, so
:func:`parse_cluster_map` refuses it.

Host comparison is case-insensitive (hostnames are); the **database name is
compared exactly**, matching this project's identifier rule (R7).
"""

from __future__ import annotations

from .errors import KqlSchemaError

__all__ = [
    "ClusterMap",
    "cluster_fqdn",
    "normalize_cluster",
    "parse_cluster_map",
    "resolve",
    "set_clusters",
    "get_clusters",
    "effective_clusters",
]

#: What a caller may pass as ``clusters=``. Either form is accepted because each
#: is the natural one somewhere: the flat form reads well in a Python fixture,
#: the nested form is what a JSON config file looks like.
#:
#: * ``{("host", "kustodb"): "duckdb_name"}``
#: * ``{"host": {"kustodb": "duckdb_name"}}``
ClusterMap = dict[tuple[str, str] | str, str | dict[str, str]]

#: The normalized form: ``(host, database) -> DuckDB database name``.
Resolved = dict[tuple[str, str], str]

#: The suffix a public-cloud cluster completes a bare name with. Sovereign
#: clouds use their own; see the module docstring for why guessing this one is
#: safe anyway.
KUSTO_SUFFIX = ".kusto.windows.net"


def normalize_cluster(cluster: str) -> str:
    """`https://Mycluster.EastUS.kusto.windows.net/` -> `mycluster.eastus.kusto.windows.net`.

    Strips the scheme and any trailing slash or path, and lowercases the host,
    so the three spellings Kusto accepts key one entry. Deliberately does *not*
    complete a short name to its domain — that is :func:`cluster_fqdn`, applied
    only where it is matched, so nothing that merely echoes a host shows a
    domain the caller never wrote.
    """
    text = cluster.strip()
    for scheme in ("https://", "http://"):
        if text.lower().startswith(scheme):
            text = text[len(scheme):]
            break
    # A cluster reference addresses a host, so anything after the first `/` is
    # not part of the identity.
    text = text.split("/", 1)[0]
    return text.rstrip(".").lower()


def _is_bare_cluster_name(host: str) -> bool:
    """Would a cluster complete *host* with its DNS suffix?

    True for a name that is only a name — `help`, `cluster1.eastus`. The
    exclusions are the spellings that are already an address and so cannot be
    completed: a host inside a Kusto service domain, an explicit port, an IPv4
    or IPv6 literal, and `localhost`. Each excluded case would otherwise be
    offered a nonsense second key like `localhost.kusto.windows.net`, which
    costs nothing at lookup but reads like a bug in an error message.
    """
    if not host or ":" in host:
        return False
    labels = host.split(".")
    if labels[-1] == "localhost":
        return False
    if all(label.isdigit() for label in labels):
        return False
    return "kusto" not in labels


def cluster_fqdn(host: str) -> str:
    """The fully qualified name for a normalized *host*.

    `cluster1.eastus` -> `cluster1.eastus.kusto.windows.net`; anything already
    qualified is returned unchanged. This is the form error messages suggest as
    a map key, because it is the one that identifies the cluster on its own.
    """
    return host + KUSTO_SUFFIX if _is_bare_cluster_name(host) else host


def _spellings(host: str) -> tuple[str, ...]:
    """Every key a normalized *host* may be mapped under, as-written first.

    Two at most, and they are the two the reference documents as naming one
    cluster: the short name and the `.kusto.windows.net` domain. As-written
    first so an exact entry always wins over a completed one.

    Stripping the suffix is gated on :func:`cluster_fqdn` putting it back, so
    the two are exact inverses and this returns precisely the hosts sharing one
    `cluster_fqdn`. That is what lets :func:`_refuse_aliased_conflicts` group by
    `cluster_fqdn` and be sure it sees every pair a lookup could confuse:
    `localhost.kusto.windows.net` does *not* offer `localhost`, because
    `localhost` is an address that is never completed back.
    """
    if _is_bare_cluster_name(host):
        return (host, host + KUSTO_SUFFIX)
    if host.endswith(KUSTO_SUFFIX):
        short = host[: -len(KUSTO_SUFFIX)]
        if cluster_fqdn(short) == host:
            return (host, short)
    return (host,)


def parse_cluster_map(clusters: ClusterMap | None) -> Resolved | None:
    """Normalize either accepted shape into ``(host, database) -> name``.

    Raises rather than ignoring a malformed entry: a mapping that silently
    dropped a row would send the query to the wrong place, which is the thing
    the whole module is here to avoid.
    """
    if clusters is None:
        return None

    out: Resolved = {}
    for key, value in clusters.items():
        if isinstance(key, tuple):
            if len(key) != 2 or not all(isinstance(part, str) for part in key):
                raise KqlSchemaError(
                    str(key), hint="a cluster map key must be (cluster, database)"
                )
            if not isinstance(value, str):
                raise KqlSchemaError(
                    str(key),
                    hint="a (cluster, database) key maps to one DuckDB database name",
                )
            out[(normalize_cluster(key[0]), key[1])] = value
            continue

        if not isinstance(key, str) or not isinstance(value, dict):
            raise KqlSchemaError(
                str(key),
                hint='expected {("cluster", "database"): "name"} or '
                '{"cluster": {"database": "name"}}',
            )
        host = normalize_cluster(key)
        for database, name in value.items():
            if not isinstance(database, str) or not isinstance(name, str):
                raise KqlSchemaError(
                    f"{key}.{database}", hint="database and target must both be strings"
                )
            out[(host, database)] = name
    _refuse_aliased_conflicts(out)
    return out


def _refuse_aliased_conflicts(out: Resolved) -> None:
    """Refuse a map that spells one cluster two ways and means two databases.

    `{("help", "S"): "a", ("help.kusto.windows.net", "S"): "b"}` is the one way
    the short-name completion can produce a *wrong* answer rather than a
    refusal: both keys name the cluster the reference calls `help`, so which
    local database a query reads would depend on how the query happened to spell
    it. Two spellings agreeing on one target are fine and stay allowed — it is
    the disagreement that is unanswerable.
    """
    by_cluster: dict[tuple[str, str], dict[str, str]] = {}
    for (host, database), target in out.items():
        by_cluster.setdefault((cluster_fqdn(host), database), {})[host] = target
    for (host, database), spellings in sorted(by_cluster.items()):
        if len(set(spellings.values())) > 1:
            listed = ", ".join(
                f"({written!r}, {database!r}): {target!r}"
                for written, target in sorted(spellings.items())
            )
            raise KqlSchemaError(
                f"({host!r}, {database!r})",
                hint=f"two spellings of one cluster map to different databases "
                f"— {listed}; a short name and its .kusto.windows.net domain "
                f"are the same cluster, so this has no answer",
            )


def resolve(cluster: str, database: str, clusters: Resolved | None) -> str:
    """The DuckDB database for *cluster*/*database*, or raise saying why not.

    A reference matches its host as written first, then the other spelling of
    the same cluster (`c.eastus` <-> `c.eastus.kusto.windows.net`), so one entry
    covers both — see the module docstring.

    Both errors name **the entry that is missing**, spelled as Python so it can
    be pasted into the map, because "not in the map" leaves the reader to work
    out which of the two things they got wrong.
    """
    host = normalize_cluster(cluster)
    entry = f"({cluster_fqdn(host)!r}, {database!r})"
    if clusters is None:
        raise KqlSchemaError(
            f'cluster("{cluster}").database("{database}")',
            hint=f"there is no cluster here; pass clusters={{{entry}: "
            '"duckdb_database"} to say which local database stands in for it',
        )
    for candidate in _spellings(host):
        target = clusters.get((candidate, database))
        if target is not None:
            return target
    known = ", ".join(
        f"({c!r}, {d!r}): {t!r}" for (c, d), t in sorted(clusters.items())
    )
    raise KqlSchemaError(
        f'cluster("{cluster}").database("{database}")',
        hint=f"not in the cluster map; add {entry}: \"duckdb_database\" — "
        + (f"mapped: {{{known}}}" if known else "the map is empty"),
    )


# ---------------------------------------------------------------------------
# The process-wide default
# ---------------------------------------------------------------------------

#: Set by :func:`set_clusters`, consulted when a call passes no ``clusters=``.
#:
#: Stored **parsed**, so a malformed map fails at the call that configures it
#: rather than at whichever query happens to run first — the stack trace then
#: points at the fixture, which is where the mistake is.
_DEFAULT: Resolved | None = None


def set_clusters(clusters: ClusterMap | None) -> None:
    """Set the mapping every later call uses when it passes no ``clusters=``.

    Meant for a test fixture or an application's start-up, so a suite full of
    `cluster(...)` queries is configured once::

        duckdb_kql.set_clusters({
            ("mycluster.eastus.kusto.windows.net", "mydb"): "database1",
        })

    ``None`` clears it, restoring the refuse-everything default.

    **Process-wide, not thread-local.** That is the point — one fixture
    configures every thread and every connection — but it does mean a suite
    that sets it should restore it, or a later test inherits a mapping it never
    asked for. :func:`get_clusters` exists so a fixture can save and restore.
    """
    global _DEFAULT
    _DEFAULT = parse_cluster_map(clusters)


def get_clusters() -> Resolved | None:
    """The current default, **normalized**, or ``None`` if none is set.

    A copy, so mutating the result cannot change resolution behind the back of
    the call that set it. Normalized rather than as-written because that is what
    actually matches — showing the input would hide the host normalization the
    lookup depends on.
    """
    return None if _DEFAULT is None else dict(_DEFAULT)


def effective_clusters(clusters: ClusterMap | None) -> Resolved | None:
    """What a call should resolve against: its own map, or the default.

    A call's ``clusters=`` **replaces** the default rather than merging with it.
    Merging would mean a query resolved partly by an argument the caller can see
    and partly by state set somewhere else, and the one thing a cluster mapping
    has to be is legible: `{}` is how a call says "use no mapping at all",
    distinct from omitting the argument.
    """
    if clusters is None:
        return _DEFAULT
    return parse_cluster_map(clusters)
