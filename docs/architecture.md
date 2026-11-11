# Architecture and invariants

Kleisis rebuilds a consumer repository's schema set from declared source states. A reproducible result requires identical consumer inputs across machines. The owned schemas, resource record and child checkout states constitute those inputs. Therefore, the package modules must isolate source access and replace both generated outputs together.

## System boundary

Kleisis builds the closure in four steps:

- parse and validate the consumer resource record;
- acquire each dependency at an exact source state;
- copy each selected schema and its reference closure, which an absolute `$ref` can extend into another declared dependency;
- generate local or composite registries from the resulting files.

The boundary excludes four concerns:

- Ekphrasis renders each structured record and validates it against the selected contract.
- A separate consumer gate must prove that the released Ekphrasis bundle loads every exported schema.
- Obsidian manages installed plugins and their settings.
- Consumer repositories declare source states and child checkouts without ambient discovery.

## Dependency direction

The package separates contract semantics from external effects:

| Layer | Responsibility | Permitted dependency |
| --- | --- | --- |
| `domain` | defines records and registries; reads structured files; resolves schema links | standard library and structured-file parser |
| `application` | materializes sources; composes registries; checks drift | domain and declared ports |
| `infrastructure` | acquires sources; checks files; replaces outputs | application ports and domain objects |
| `cli` | maps commands to the application service, and writes each failure as a line or as a SARIF result | application service and findings |

The source-provider port keeps machine access outside the application rules. Therefore, tests can supply fixed trees through that port. However, canonical resource records still reject local checkout paths.

## Materialization flow

A materialization run constructs a complete candidate before it changes the consumer repository:

1. The consumer revision fixes owned schemas and child checkout states, while the resource record fixes external states and declares all paths.
2. Each source provider acquires its declared state in an isolated staging directory.
3. Each selected registry entry must match the schema-binding path `<binding>.const` of its target, where the dependency entry names the binding, `_schema` by default.
4. The resolver closes the selected contracts across the acquired sources, and the materializer copies each reached file under the directory of its dependency.
5. The resolver closes the owned schemas in a copy of the candidate repository. A file reached in a dependency source is not yet materialized, so the materializer adds it and the resolver closes again.
6. The local registry combines owned contracts with selected external contracts, and maps each absolute `$id` of the candidate to its file.
7. The composite registry imports only explicit child exports and child `resources` entries, after checking shared bytes.
8. The publisher replaces the resource directory and registry together.

Before the publisher runs, a build failure deletes the staging directory. The publisher may fail during either write. Then, the publisher immediately tries to restore the resource directory and registry. If it cannot restore them, it retains the journal `transaction.json` of the state folder, `.kleisis/` by default, where it also keeps the backup of each output. A later run reads that journal and completes recovery before building a new candidate. The state folder lies below the root, as the outputs, so each backup is a rename within one file system.

Recovery also accepts the journals that earlier releases left at the root, `.kleisis-transaction.json` and `.schema-closure-transaction.json`, each with its backups beside their outputs and its own backup names. Publication refuses to start while any of the three journals exists, and recovery refuses to choose between two of them. The validated record confines recovery to the two output paths that it declares. A journal at a former place may instead name the resources at `.schema-resources`, their default place in format 1 of the resource record, so that an interrupted run of an earlier release is recovered after the upgrade.

## Reference semantics

The reference keyword `$ref` adds a file when its value contains a relative path. The resolver reads that path from the directory of its containing file, and the path may not leave the source of that file. The resolver rejects a vault-rooted path, which starts with `/`. Every file that the resolver loads belongs to an export closure or is materialized, and the loader of a consumer reads a vault-rooted path from the root of its own vault, not from the root of the source of the file. Text after `#` must resolve through a slash-delimited pointer or the anchor keyword `$anchor`.

An absolute `$ref` starts with a URI scheme. The resolver compares it with each root `$id` as an exact string, and fetches no URI. The `$ref` resolves in this order:

1. a loaded file of the referring source that declares that `$id`;
2. the file mapped by the `resources` of the referring source;
3. the file mapped by the `resources` of the declared dependencies, in declaration order.

A map entry whose file is absent from its source is skipped. Resolution fails when no source maps the URI, when two dependencies map it to different bytes, or when the mapped file declares another `$id`. The [closure across sources](adr/closure-across-sources.md) records the options weighed.

The containing file supplies the base directory. The schema keyword `$id` does not change that directory. This behavior matches Ekphrasis rather than the general schema rule for an identifier base.

## Registry invariants

Every generated registry must satisfy these invariants:

- every identifier is unique;
- every owned contract carries a complete root binding, named by the resource record and `_schema` by default, and enters the export set;
- every owned file without a binding trace remains outside the registry as a support schema;
- every generated target stays inside the consumer repository;
- every imported child export stays inside its declared child repository;
- every export names an owned schema;
- no materialized dependency enters the export set;
- every child dependency is available in the parent registry;
- every dependency that selects no contract has a registry in the current format or the legacy `schema_version: 2`, and the closure reaches at least one of its resources;
- no file of an export closure and no materialized file holds a vault-rooted `$ref`;
- each shared contract has identical bytes in the parent and child closures;
- every `resources` key is an absolute `$id` declared at the root of its target file;
- every owned or materialized file whose root declares an absolute `$id` enters `resources`;
- two files declaring one `$id` have identical bytes, and the first file in owned, materialized, then child order keeps the entry.

The deterministic serializer writes `_schema: kleisis:schema-registry@2` as the first key, and sorts identifiers, exports and `resources` keys. Therefore, the check command can compare generated bytes without normalizing a committed registry, and it reports a legacy registry of the consumer as drift. The registry reader also accepts the legacy forms `schema_version: 1`, which has no `resources` key, and `schema_version: 2` in dependency and child registries, since a consumer cannot regenerate them. This is the one exception to the rule that a format identifier has no alias: the resource record accepts only `_schema: kleisis:schema-resources@2`, and the check reports a receipt in another format as drift.
