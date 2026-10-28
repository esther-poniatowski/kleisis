# Schema Closure

Schema Closure lets a consumer repository reproduce a local set of schema contracts. That local set contains owned files and exact external files. A relative `$ref` connects files from the directory of its containing schema. A vault-rooted `$ref` connects files from the source root. Finally, the generated registry maps each contract identifier to one file.

Runtime consumers need only these local files. To reproduce the same state, the resource record fixes each external source by a full Git commit or an artifact digest.

## Prerequisites

Running Schema Closure requires Git and Conda. Before the first command, the consumer repository must also contain:

- owned concrete schemas under the configured schema directory;
- the resource record at the default path `specs/schema-resources.yml`;
- required child repositories at their declared paths;
- generated registries and materialized dependency closures for those child repositories.

Materialize each required child before running a command in its parent repository. The parent reads the child outputs as immutable inputs.

## Installation

Conda reads the file `environment.yml` to install the command and its development tools:

```console
conda env create --solver libmamba -f environment.yml
```

## Resource record

The resource record governs four inputs:

- repository paths;
- owned exports;
- exact external dependencies;
- declared child projects.

This record template uses the conventional paths and selects one contract from an exact Git state. Replace the example commit with the required source commit:

```yaml
format_version: 1

exports:
  - mixture:question@1

dependencies:
  - id: geonexus
    source:
      kind: git
      repository: https://github.com/esther-poniatowski/geonexus.git
      commit: 0123456789abcdef0123456789abcdef01234567
    registry: specs/schema-registry.yml
    contracts:
      - geonexus:glossary@1
```

Each dependency identifier uses lower kebab-case. Each Git source uses a web address that starts with `https://`, contains no authentication data and names a full lowercase commit. Instead, an artifact source declares a web address that starts with `https://` and contains no authentication data. The artifact source also declares its source commit and a 256-bit digest.

A private Git source can also name one variable through the optional field `credential_env`. The variable must contain the complete `Authorization` header. The resource record and receipt store only the variable name.

The optional key `paths` overrides three repository-relative paths:

```yaml
paths:
  owned_schemas: utils/schemas
  registry: specs/schema-registry.yml
  resources: .schema-resources
```

Keep the resource directory disjoint from these source and control paths:

- the owned schema directory;
- the generated registry;
- the resource record;
- each declared child checkout.

## Commands

The three commands keep writes separate from checks:

- The validation command `schema-closure validate --root .` builds the complete candidate state without changing the repository.
- The materialization command `schema-closure materialize --root .` replaces both generated outputs. If either write fails, the command attempts to restore both outputs immediately. If the command cannot restore them, it retains the transaction record so a later run can recover them.
- The check command `schema-closure check --root .` rematerializes in an isolated directory and fails when either generated output differs.

Each command accepts the record-path option `--record` when the resource record does not use its default path.

These commands validate the closure through Schema Closure's resolver. The separate consumer qualification gate must still prove that the released Ekphrasis bundle loads every exported schema.

## Generated state

The materialization command writes two consumer outputs and one receipt:

| Output | Version-control status | Function |
| --- | --- | --- |
| `.schema-resources/` | ignored | stores exact external schemas and their relative and vault-rooted reference closure |
| `.schema-resources/receipt.yml` | ignored | records each exact source and copied-file digest |
| `specs/schema-registry.yml` | tracked | maps owned, selected external and imported child identifiers to local paths |

The generated registry lists only project-owned identifiers under the key `exports`. By contrast, the key `schemas` can include a dependency identifier outside the export set.

An owned contract uses the root path `properties._schema.const` to state its identifier. Its root `required` list must also name `_schema`. The resource record must export every contract with both entries. A file with neither entry remains an owned support schema. The support schema can still serve a `$ref`. A file with only one entry or an invalid value fails the command.

## Child projects

A parent imports child exports from explicitly declared repositories:

```yaml
projects:
  - path: projects/mixture-input-encoding
    required: true
```

The materialization command treats each child registry and dependency closure as immutable input. The parent rejects these invalid states:

- an absent required child checkout or registry;
- an export absent from the child `schemas` entries;
- an export target outside its child repository;
- an invalid or incomplete child reference closure;
- duplicate exports across repositories;
- a child dependency absent from the parent registry;
- different bytes for one shared contract.

## Design

The [architecture and invariants](docs/architecture.md) specify three properties:

- permitted dependencies between modules;
- how Schema Closure follows the keyword `$ref`;
- how Schema Closure replaces both outputs safely.

## Development

The following commands run the targeted project gates through the dedicated Conda name:

```console
conda run -n schema-closure python -m pytest
conda run -n schema-closure ruff check --no-cache .
conda run -n schema-closure mypy src tests
```

## License

The [license terms](LICENSE) apply under the General Public License, release 3 or any subsequent release.
