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
letting one entry cover every spelling of one host. The **short name is not
expanded**, because the emulator does not expand it either — `mycluster` is the
host `mycluster`, not shorthand for `mycluster.kusto.windows.net`. Guessing that
expansion would invent a resolution rule the engine does not apply; a query that
uses both spellings needs both entries, which is a mapping the caller can see.

Host comparison is case-insensitive (hostnames are); the **database name is
compared exactly**, matching this project's identifier rule (R7).
"""

from __future__ import annotations

from .errors import KqlSchemaError

__all__ = [
    "ClusterMap",
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


def normalize_cluster(cluster: str) -> str:
    """`https://Mycluster.EastUS.kusto.windows.net/` -> `mycluster.eastus.kusto.windows.net`.

    Strips the scheme and any trailing slash or path, and lowercases the host,
    so the three spellings Kusto accepts key one entry. Deliberately does *not*
    expand a short name — see the module docstring.
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
    return out


def resolve(cluster: str, database: str, clusters: Resolved | None) -> str:
    """The DuckDB database for *cluster*/*database*, or raise saying why not.

    The error names the reference as written and lists what *is* mapped, because
    the usual mistake is a spelling — one query saying `mycluster` and another
    the full domain — and a bare "not found" leaves the reader to guess which.
    """
    host = normalize_cluster(cluster)
    if clusters is None:
        raise KqlSchemaError(
            f'cluster("{cluster}").database("{database}")',
            hint="there is no cluster here; pass clusters={(cluster, database): "
            '"duckdb_database"} to say which local database stands in for it',
        )
    target = clusters.get((host, database))
    if target is None:
        known = sorted(f"{c}/{d}" for c, d in clusters)
        raise KqlSchemaError(
            f'cluster("{cluster}").database("{database}")',
            hint=f"not in the cluster map; mapped: {known}",
        )
    return target


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
