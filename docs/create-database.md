# `.create database`

Reference notes for the Kusto `.create database` management command.

> **Why this document exists.** `.create database` has no page in the official
> Kusto management-commands reference. In Azure Data Explorer and Microsoft
> Fabric, databases are ARM resources created through the portal, `az kusto
> database create`, or ARM/Bicep templates — not through KQL. The command exists
> only in the standalone engine, which ships publicly as the Kusto emulator
> (`kustainer`). The syntax below is transcribed from the grammar in the
> open-source KQL parser, cross-checked against the two Microsoft Learn pages
> that use the command in examples.
>
> Because this is reconstructed from the parser rather than from a specification,
> treat the grammar as authoritative for *what parses* and the engine as
> authoritative for *what actually works*.

## Syntax

```
.create database DatabaseName
  [ persist '(' Path [, Path ...] ')' | volatile ]
  [ ifnotexists ]
  [ with '(' PropertyName = PropertyValue [, ...] ')' ]
```

Grammar as written in the parser's command-info generator:

```
create database DatabaseName=<name>
  [ persist '(' { Path=<string>, ',' }+ ')' | volatile ]
  [ IfNotExists=ifnotexists ]
  [ with '(' { PropertyName=<name> '=' PropertyValue=<value>, ',' }+ ')' ]
```

## Parameters

| Name | Type | Required | Description |
| --- | --- | --- | --- |
| `DatabaseName` | `<name>` | Yes | Identifier or bracketed name for the new database. |
| `persist (...)` | list of `<string>` | No | One or more storage paths backing the database. Mutually exclusive with `volatile`. |
| `volatile` | keyword | No | Create the database with no backing storage paths. Mutually exclusive with `persist`. |
| `ifnotexists` | keyword | No | Succeed without error if a database of that name already exists. |
| `with (...)` | property list | No | Generic name/value property list. The grammar does not enumerate accepted property names; the valid set is enforced engine-side only. |

### Notes on `persist`

The path list is one-or-more, not exactly two. A single path or three paths parse
without error. The conventional two-path form separates metadata from data:

```kusto
.create database MyDb persist (
  @"/kustodata/dbs/MyDb/md",
  @"/kustodata/dbs/MyDb/data"
)
```

Paths may also be blob container URIs rather than container-local folders:

```kusto
.create database TestDB persist (
  @"https://example.blob.core.windows.net/md",
  @"https://example.blob.core.windows.net/data"
)
```

The two-path convention is reflected in the result schema's `StoresMetadata` and
`StoresData` flags (below). In the emulator, the target folders must not already
exist — this is an overwrite guard. To bind to folders that already contain a
database, use `.attach database` instead.

### Notes on `volatile`

`volatile` is undocumented on Microsoft Learn and is the natural choice for
ephemeral test databases where nothing should be written to disk. A volatile
database can later be given backing metadata storage via
`.alter database <db> persist metadata`.

## Result schema

```
(DatabaseName: string, PersistentPath: string, Created: string,
 StoresMetadata: bool, StoresData: bool)
```

## Parse tree

The parser produces:

```
DatabaseCreateCommand(DatabaseName, Path*, IfNotExists?, (PropertyName, PropertyValue)*)
```

`volatile` has no slot in the output tree — it is syntax-only and is not captured
as a named parameter. Consumers that reconstruct the command from its parse tree
cannot distinguish `volatile` from the bare `.create database MyDb` form.

## Related commands

These are also thinly documented; parameters below come from the same grammar
source.

### `.attach database`

```
(attach | #load) [ database #[ all | metadata ] DatabaseName=<database> ]
  from Path=<string>
  [ readonly [ version '=' Version=<string> ] ]
  [ with '(' { PropertyName=<name> '=' PropertyValue=<value>, ',' }+ ')' ]
```

Attaches an existing database from a metadata path. Supports `readonly` with an
optional pinned `version`. The `#`-prefixed tokens (`#load`, `#all`, `#metadata`)
appear to mark hidden aliases in the grammar DSL; the notation is not defined in
the source file's header comment, so this is inference.

Documented form:

```kusto
.attach database MyDb from @"/kustodata/dbs/MyDb/md"
```

### `.detach database`

```
(detach | #drop) database DatabaseName=<database> [ ifexists ] [ 'skip-seal' ]
```

Releases the database from the engine while leaving metadata and data intact, so
it can be reattached later.

### `.alter database ... persist metadata`

```
alter database DatabaseName=<database> persist metadata
  [ Path=<string> [ 'allow-non-empty-container' ] ]
```

Sets or moves the metadata container for an existing database. Undocumented.

### `.set access`

```
set access DatabaseName=<database> to AccessMode=(readonly | readwrite)
```

## Availability

| Environment | Supported |
| --- | --- |
| Kusto emulator (`kustainer`) | Yes |
| Azure Data Explorer cluster | No — use ARM, portal, or `az kusto database create` |
| Microsoft Fabric Real-Time Intelligence | No |

## References

1. Microsoft Learn — *Install the Azure Data Explorer Kusto emulator*.
   The only official page documenting `.create database ... persist`,
   `.attach database`, and `.detach database`.
   <https://learn.microsoft.com/en-us/azure/data-explorer/kusto-emulator-install>
2. Microsoft Learn — *`.show journal`*. Uses `.create database ... persist` with
   blob container URIs in its example output.
   <https://learn.microsoft.com/en-us/kusto/management/journal>
3. `microsoft/Kusto-Query-Language` — `src/Kusto.Language.Generators/EngineCommandInfos.cs`.
   Source of the grammar, result schema, and parse-tree shape for `CreateDatabase`,
   `AttachDatabase`, `DetachDatabase`, `AlterDatabasePersistMetadata`, and `SetAccess`.
   <https://github.com/microsoft/Kusto-Query-Language/blob/master/src/Kusto.Language.Generators/EngineCommandInfos.cs>
4. Microsoft Learn — *`az kusto database create`*. The supported path for creating
   databases on a real ADX cluster.
   <https://learn.microsoft.com/en-us/cli/azure/kusto/database>
