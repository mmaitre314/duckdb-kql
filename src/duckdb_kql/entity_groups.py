"""Resolving *named* entity groups onto a list of entities.

An entity group is a set of databases a `macro-expand` runs its body against::

    macro-expand MySecurityDatabases as scope (scope.Alerts | count)

Written inline or bound by a `let`, the entities are in the query text and
nothing here is needed. Written by **name**, they are not: a named group is
created on the cluster by `.create entity_group`, and there is no cluster here.

So the mapping has to be supplied, for the same reason `cluster()` does (see
duckdb_kql.clusters): expanding an unknown group to nothing, or to the current
database, would answer a question about several databases with one and return
plausible rows while doing it. An unmapped name raises.

**Entries are KQL entity references, as text** — ``"database('Sales')"`` or
``"cluster('prod.kusto.windows.net').database('Sales')"`` — not DuckDB
database names. Three reasons:

* it is the form `.show entity_groups` reports, so a real group's definition
  copies across verbatim;
* a `cluster(...)` entity then resolves through the **existing** `clusters=`
  mapping, so the two features compose instead of each carrying its own idea of
  what a remote database is;
* a named group and an inline one become the same thing after parsing, so there
  is one code path rather than two that have to agree.

A bare DuckDB catalog name is **not** accepted. It reads like a third dialect in
a field that is otherwise KQL, and `database('x')` is barely longer.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import KqlSchemaError

__all__ = [
    "Entity",
    "EntityGroupMap",
    "ResolvedGroups",
    "effective_entity_groups",
    "get_entity_groups",
    "parse_entity_groups",
    "resolve_group",
    "set_entity_groups",
]


@dataclass(frozen=True)
class Entity:
    """One member of an entity group: a database, possibly on a cluster."""

    database: str
    cluster: str | None = None

    def as_kql(self) -> str:
        """The reference as KQL, for error messages and `withsource` labels."""
        prefix = f"cluster('{self.cluster}')." if self.cluster else ""
        return f"{prefix}database('{self.database}')"


#: Group name -> the entity references it contains, as KQL text.
EntityGroupMap = dict[str, list[str]]

#: Group name -> parsed entities.
ResolvedGroups = dict[str, tuple[Entity, ...]]


def parse_entity_groups(groups: EntityGroupMap | None) -> ResolvedGroups | None:
    """Validate and parse a mapping, or ``None``.

    Parsing happens here rather than at query time so a malformed entry fails
    where it was written — a fixture's stack trace points at the fixture, which
    is where the mistake is.
    """
    if groups is None:
        return None
    if not isinstance(groups, dict):
        raise TypeError(f"entity_groups must be a dict, got {type(groups).__name__}")

    out: ResolvedGroups = {}
    for name, entities in groups.items():
        if not isinstance(name, str) or not name:
            raise TypeError(f"entity group name must be a non-empty str, got {name!r}")
        if isinstance(entities, str) or not isinstance(entities, (list, tuple)):
            raise TypeError(
                f"entity group {name!r} must map to a list of entity references, "
                f"got {type(entities).__name__}"
            )
        parsed = tuple(_parse_entity(name, e) for e in entities)
        if not parsed:
            raise ValueError(f"entity group {name!r} is empty")
        _refuse_duplicates(name, parsed)
        out[name] = parsed
    return out


def _parse_entity(group: str, text: object) -> Entity:
    """One ``database('d')`` / ``cluster('c').database('d')`` reference."""
    if not isinstance(text, str):
        raise TypeError(
            f"entity group {group!r}: entity must be a str like \"database('d')\", "
            f"got {type(text).__name__}"
        )

    # Parsed by the ordinary KQL path, against a probe table name, so an entry
    # behaves exactly as the same text written inline in a query would. Doing
    # the string-splitting by hand here would be a second syntax to keep in step.
    from .errors import KqlError  # noqa: PLC0415
    from .lower import parse_entity_reference  # noqa: PLC0415

    try:
        entity = parse_entity_reference(text)
    except KqlError as exc:
        raise ValueError(
            f"entity group {group!r}: cannot read entity {text!r} — expected "
            f"\"database('d')\" or \"cluster('c').database('d')\" ({exc})"
        ) from None
    if entity is None:
        raise ValueError(
            f"entity group {group!r}: entity {text!r} is not a database reference. "
            f"Write \"database('{text}')\" — a bare DuckDB catalog name is not "
            "accepted, so the mapping stays in one language."
        )
    return entity


def _refuse_duplicates(group: str, entities: tuple[Entity, ...]) -> None:
    """Kusto refuses a group with a repeated entity (SEM0614); so does this."""
    seen: set[tuple[str | None, str]] = set()
    for entity in entities:
        key = (entity.cluster, entity.database)
        if key in seen:
            raise ValueError(
                f"entity group {group!r} lists {entity.as_kql()} twice; Kusto "
                "refuses a group with duplicate entities (SEM0614)"
            )
        seen.add(key)


def resolve_group(name: str, groups: ResolvedGroups | None) -> tuple[Entity, ...]:
    """The entities *name* stands for, or raise saying it is unmapped."""
    if groups and name in groups:
        return groups[name]
    known = sorted(groups) if groups else []
    raise KqlSchemaError(
        name,
        hint=(
            "unknown entity group; a named group lives on the cluster and there "
            "is no cluster here, so pass entity_groups={"
            f"{name!r}: [\"database('...')\"]" + "} — "
            + (f"mapped: {known}" if known else "no groups are mapped")
        ),
    )


#: Process-wide default, set by :func:`set_entity_groups`.
_DEFAULT: ResolvedGroups | None = None


def set_entity_groups(groups: EntityGroupMap | None) -> None:
    """Set the mapping every later call uses when it passes no ``entity_groups=``.

    Meant for a test fixture or start-up, so a suite full of `macro-expand`
    queries is configured once::

        duckdb_kql.set_entity_groups({
            "SecurityDatabases": [
                "database('Security')",
                "cluster('prod.eastus.kusto.windows.net').database('Archive')",
            ],
        })

    ``None`` clears it. **Process-wide, not thread-local**, matching
    :func:`duckdb_kql.set_clusters`; :func:`get_entity_groups` exists so a
    fixture can save and restore.
    """
    global _DEFAULT
    _DEFAULT = parse_entity_groups(groups)


def get_entity_groups() -> ResolvedGroups | None:
    """The current default, parsed, or ``None``. A copy."""
    return None if _DEFAULT is None else dict(_DEFAULT)


def effective_entity_groups(
    groups: EntityGroupMap | None,
) -> ResolvedGroups | None:
    """What a call resolves against: its own map, or the default.

    A call's ``entity_groups=`` **replaces** the default rather than merging,
    exactly as ``clusters=`` does — so ``{}`` is how a call says "no groups at
    all", distinct from omitting the argument.
    """
    if groups is None:
        return _DEFAULT
    return parse_entity_groups(groups)
