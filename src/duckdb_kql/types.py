"""DuckDB types, named the way Kusto names them.

One mapping, two consumers. The Kusto client labels result columns with it, and
``getschema`` reports it as a *table* — so it has to exist both as a Python
function and as a SQL expression. Generating the SQL from the same table is the
only way those two cannot drift, and a `getschema` that disagreed with the
column types alongside it would be a peculiarly confusing thing to ship.

Layer 0: this imports nothing but the standard library.
"""

from __future__ import annotations

import re

__all__ = [
    "kusto_type",
    "net_type",
    "rest_datatype",
    "DUCKDB_TO_KQL",
    "KQL_TO_NET",
    "kusto_type_sql",
]

#: DuckDB type name -> Kusto scalar type. DuckDB has more integer widths than
#: Kusto does; anything wider than 32 bits reports as ``long`` because that is
#: the only 64-bit integer Kusto has. (A value too wide even for that is
#: re-typed per column at the boundary — see kusto._models.widen_out_of_range.)
DUCKDB_TO_KQL: dict[str, str] = {
    "BOOLEAN": "bool",
    "TINYINT": "int",
    "UTINYINT": "int",
    "SMALLINT": "int",
    "USMALLINT": "int",
    "INTEGER": "int",
    "UINTEGER": "long",
    "BIGINT": "long",
    "UBIGINT": "long",
    "HUGEINT": "long",
    "UHUGEINT": "long",
    "FLOAT": "real",
    "DOUBLE": "real",
    "VARCHAR": "string",
    "UUID": "guid",
    "DATE": "datetime",
    "TIMESTAMP": "datetime",
    "TIMESTAMP WITH TIME ZONE": "datetime",
    "TIMESTAMP_S": "datetime",
    "TIMESTAMP_MS": "datetime",
    "TIMESTAMP_NS": "datetime",
    "TIME": "timespan",
    "INTERVAL": "timespan",
    "JSON": "dynamic",
    "BLOB": "string",
    "BIT": "string",
}

#: Kusto type -> the .NET type name `getschema` reports in its ``DataType``
#: column. Measured on the emulator, not inferred: `bool` is `System.SByte` and
#: `decimal` is `System.Data.SqlTypes.SqlDecimal`, neither of which is the
#: obvious guess.
KQL_TO_NET: dict[str, str] = {
    "bool": "System.SByte",
    "int": "System.Int32",
    "long": "System.Int64",
    "real": "System.Double",
    "decimal": "System.Data.SqlTypes.SqlDecimal",
    "string": "System.String",
    "datetime": "System.DateTime",
    "timespan": "System.TimeSpan",
    "guid": "System.Guid",
    "dynamic": "System.Object",
}

_DECIMAL = re.compile(r"^DECIMAL\(", re.IGNORECASE)
#: Composite types have no Kusto counterpart other than dynamic, which is
#: exactly what they are: a nested document.
_COMPOSITE = re.compile(r"(\[\]$|^STRUCT|^MAP|^UNION|^LIST)", re.IGNORECASE)

#: What an unmapped type reports as. A faithful string form is the one answer
#: that cannot make a value look like something it is not.
_FALLBACK = "string"


def kusto_type(duckdb_type: object) -> str:
    """Name the Kusto scalar type a DuckDB column corresponds to."""
    name = str(duckdb_type).upper()
    if name in DUCKDB_TO_KQL:
        return DUCKDB_TO_KQL[name]
    if _DECIMAL.match(name):
        return "decimal"
    if _COMPOSITE.search(name):
        return "dynamic"
    return _FALLBACK


def net_type(kql_type: str) -> str:
    """The .NET type name Kusto reports for a KQL type."""
    return KQL_TO_NET.get(kql_type, KQL_TO_NET[_FALLBACK])


def rest_datatype(kql_type: str) -> str:
    """The ``DataType`` a v1 REST *query* response carries for a KQL type.

    The same .NET name as `getschema` reports, with the namespace dropped:
    `System.SByte` -> `SByte`, `System.Data.SqlTypes.SqlDecimal` ->
    `SqlDecimal`. Derived rather than tabulated a second time — measured on the
    emulator across all ten types, and the rule holds for every one.

    Query results only. A **control command** declares its own result schema
    inside Kusto, and those declarations do not follow this rule — `.show
    databases` labels a bool column `Boolean`, not `SByte`. They are transcribed
    in :data:`duckdb_kql.control.SCHEMA` instead.
    """
    return net_type(kql_type).rsplit(".", 1)[-1]


def kusto_type_sql(column: str) -> str:
    """:func:`kusto_type`, as a SQL expression over a DuckDB type *name*.

    Generated from the same table, so the types ``getschema`` reports and the
    types the Kusto client labels its columns with cannot disagree.
    """
    from .translate import quote_string

    whens = [
        f"WHEN {column} = {quote_string(duckdb)} THEN {quote_string(kql)}"
        for duckdb, kql in DUCKDB_TO_KQL.items()
    ]
    # The two regex cases above, as patterns. `_` is a LIKE wildcard, so the
    # composite names are matched with prefixes rather than equality.
    whens.append(f"WHEN {column} LIKE 'DECIMAL(%' THEN 'decimal'")
    whens.append(
        f"WHEN {column} LIKE '%[]' OR {column} LIKE 'STRUCT%' "
        f"OR {column} LIKE 'MAP%' OR {column} LIKE 'UNION%' "
        f"OR {column} LIKE 'LIST%' THEN 'dynamic'"
    )
    return "CASE " + " ".join(whens) + f" ELSE {quote_string(_FALLBACK)} END"


def net_type_sql(column: str) -> str:
    """:func:`net_type`, as a SQL expression over a *KQL* type name."""
    from .translate import quote_string

    whens = [
        f"WHEN {column} = {quote_string(kql)} THEN {quote_string(net)}"
        for kql, net in KQL_TO_NET.items()
    ]
    return "CASE " + " ".join(whens) + f" ELSE {quote_string(KQL_TO_NET[_FALLBACK])} END"
