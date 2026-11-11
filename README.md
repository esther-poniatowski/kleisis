# Kleisis

Kleisis is a deterministic schema-closure materializer. It lets a consumer repository reproduce a local set of schema contracts. That local set contains owned files and exact external files. A relative `$ref` connects files from the directory of its containing schema. An absolute `$ref` connects files by the `$id` that they declare, including files of another declared dependency. Finally, the generated registry maps each contract identifier and each absolute `$id` to one file.

Runtime consumers need only these local files. To reproduce the same state, the resource record fixes each external source by a full Git commit or an artifact digest.

## Prerequisites

Running Kleisis requires Git and Conda. Before the first command, the consumer repository must also contain:

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
_schema: kleisis:schema-resources@2

exports:
  - mixture:question@1

dependencies:
  - id: geonexus
    source:
      kind: git
      repository: https://github.com/esther-poniatowski/geonexus.git
      commit: 0123456789abcdef0123456789abcdef01234567
      archive_roots:
        - specs/schema-registry.yml
        - utils/schemas
    registry: specs/schema-registry.yml
    contracts:
      - geonexus:project@1
```

The record declares its format in its first key, `_schema: kleisis:schema-resources@2`. A record without this key, with another value, or with the retired key `format_version` stops each command with status 2, and the message names the value to write.

Format 2 moved the default resource directory from `.schema-resources` into the state folder `.kleisis/`. A record of format 1 that omits `paths.resources` would therefore be read at another place, so the format raised its major number. To upgrade a record of format 1, write `_schema: kleisis:schema-resources@2`; to keep the former resource directory, also write `paths.resources: .schema-resources`.

Each dependency identifier uses lower kebab-case. Each Git source uses a web address that starts with `https://`, contains no authentication data and names a full lowercase commit. Instead, an artifact source declares a web address that starts with `https://` and contains no authentication data. The artifact source also declares its source commit and a 256-bit digest.

A private Git source can also name one variable through the optional field `credential_env`. The variable must contain the complete `Authorization` header. The resource record and receipt store only the variable name.

Git sends the value with each request. For a GitHub repository, the header names the `Basic` scheme and encodes a token as password:

```bash
export GITHUB_AUTHORIZATION="Authorization: Basic $(printf 'x-access-token:%s' "$TOKEN" | base64 | tr -d '\n')"
```

GitHub rejects a `Bearer` header over Git. Then, Git prompts for a user name.

The optional `archive_roots` list restricts a Git archive to the repository-relative files and directories that can contain the registry and its schema closure. Git interprets every entry as a literal path. The list must be non-empty, unique and in path order. Closure validation fails when the list omits a selected schema or one of its references. An absent list archives the complete source state.

A dependency also supplies the files that absolute references name. Kleisis compares an absolute `$ref` with each `$id` as an exact string, and accepts any URI scheme. When no file of the referring source declares that `$id`, Kleisis looks it up in the `resources` maps of the declared dependency registries, in declaration order. Kleisis then materializes the mapped file and its closure like a selected contract.

A dependency may select no contract, by an empty or absent `contracts` list. Such a dependency supplies only the `resources` map of its registry, such as the source of the addressing primitives, cited by absolute `$id`:

```yaml
  - id: anagraphe
    source:
      kind: git
      repository: https://github.com/esther-poniatowski/anagraphe.git
      commit: 0123456789abcdef0123456789abcdef01234567
    registry: specs/schema-registry.yml
```

A vault-rooted `$ref`, which starts with `/`, is resolved by Ekphrasis from the root of the consumer vault, not from the root of the source of its file. Therefore, no file of an export closure and no materialized file may hold a vault-rooted `$ref`. An export closure holds the files reached from the exports and from the owned files mapped by `resources`. A reference across sources uses an absolute `$id` instead. A vault-rooted `$ref` stays admitted in an owned file outside every export closure.

Tar archives contribute regular files and directories only. A link with a valid member path is omitted, while an invalid member path still invalidates the archive. Retained ZIP artifacts reject symbolic links. Therefore, a selected registry or schema supplied only through a link remains absent and fails validation.

The optional key `paths` overrides four repository-relative paths. The values below are the defaults:

```yaml
paths:
  owned_schemas: specs/schemas
  registry: specs/schema-registry.yml
  state: .kleisis
  resources: .kleisis/resources
```

The state folder `state` holds what Kleisis alone reads: the resource directory by default, the stage of a materialization run, and the journal and backups of a publication while it runs. When the record sets `state` and omits `resources`, the resource directory is `<state>/resources`. A record that sets `resources` keeps that directory wherever the state folder lies.

Keep the state folder and the resource directory disjoint from these source and control paths:

- the owned schema directory;
- the generated registry;
- the resource record;
- each declared child checkout.

The resource directory may lie inside the state folder, but must not contain it or equal it: a publication replaces the resource directory, and the journal of that publication lies in the state folder.

The optional key `schemas` overrides the two names by which Kleisis recognizes the owned schemas. The key `suffixes` lists the name endings of the schema files, and the key `binding` names the root property that carries the identifier of a contract. Each key keeps its default value when the record omits it:

```yaml
schemas:
  suffixes: [.schema.yaml, .schema.yml, .schema.json]
  binding: _schema
```

A suffix opens with a dot and holds no path separator. A binding is a property name of letters, digits, `_` and `-`. The convention of a dependency may differ from that of the consumer, so a dependency entry may name the binding of its own contracts under the key `binding`, which defaults to `_schema`.

## Commands

The three commands keep writes separate from checks:

- The validation command `kleisis validate --root .` builds the complete candidate state without changing the repository.
- The materialization command `kleisis materialize --root .` replaces both generated outputs. If either write fails, the command attempts to restore both outputs immediately. If the command cannot restore them, it retains its journal in the state folder so a later run can recover them.
- The check command `kleisis check --root .` rematerializes in an isolated directory and fails when either generated output differs.

Each command works on one root, the consumer repository. The root is named by the option `-r`, `--root`, or else by the environment variable `KLEISIS_ROOT`, or else is the current folder. Kleisis reads no configuration file of its own, so it never searches the ancestors of the current folder. A root that is not a folder is a usage error.

Each command accepts the option `--record PATH` when the resource record does not lie at its default path `specs/schema-resources.yml` below the root. The path is read from the current folder, and must lie below the root.

The exit status is 0 on success, 1 when the build, the validation or the check fails, and 2 on a usage error, on a resource record that is absent, unreadable or invalid, or when the SARIF log cannot be written. A resource record is the configuration of a command, so each of its faults, its format included, gives status 2. The option `-v`, `--version` prints the version.

The commands `validate` and `check` also accept the option `--format sarif`. The command then writes a SARIF 2.1.0 log on standard output, in the findings profile of the gate Elenchos. The build stops at its first failure, so the log holds one result for that failure, or one per item when the failure lists several; `check` adds one result per generated output that differs. A successful run yields a log without result. The line on standard error and the exit status stay unchanged.

The log also states whether the run is complete. A run stopped at its first failure, or by an operating-system error, declares a failed invocation whose notification states why it stopped. An operating-system error yields no result.

The option `-o`, `--output PATH` writes the log to a file instead of standard output, for a caller that runs the command without a shell, such as the gate. The command creates the missing parent directories, and exits with status 2 when it cannot write the log. The [findings output](docs/adr/findings-output.md) lists the rules and records the choices:

```console
kleisis check --root . --format sarif --output build/elenchos/kleisis.sarif
```

No command fetches a URI. Each command fails, naming the URI and the referring file, in two cases:

- no file of the referring source and no declared dependency maps an absolute `$ref`;
- two dependencies map one absolute `$ref` to different bytes.

Each command also fails, naming the dependency, when a dependency selects no contract and either its registry is a legacy registry of `schema_version: 1` or no reference of the closure reaches any of its resources. Finally, each command fails, naming the file and the reference, when a file of an export closure or a materialized file holds a vault-rooted `$ref`.

These commands validate the closure through Kleisis's resolver. The separate consumer qualification gate must still prove that the released Ekphrasis bundle loads every exported schema.

## Generated state

The materialization command writes two consumer outputs and one receipt, shown at their default paths:

| Output | Version-control status | Function |
| --- | --- | --- |
| `.kleisis/resources/` | ignored | stores exact external schemas and their reference closure across declared dependencies |
| `.kleisis/resources/receipt.yml` | ignored | records each exact source and copied-file digest |
| `specs/schema-registry.yml` | tracked | maps owned, selected external and imported child identifiers, and each absolute `$id`, to local paths |

A consumer excludes the state folder `.kleisis/` from version control. While the materialization command runs, the state folder also holds its stage `stage-<random>/`, and during the publication the journal `transaction.json` and one backup of each output. The command deletes all three when it ends, except after a failed rollback, which keeps the journal and the backups for a later run to recover. A stage left by a crash is deleted by the next materialization, after the recovery of any journal. The check and validation commands stage in the system temporary folder, and write nothing in the repository.

The registry names each materialized file by its repository path. Therefore, a consumer whose record omits `paths.resources` obtains a different registry when it first materializes with format 2, since its files move from `.schema-resources/` to `.kleisis/resources/`. The former directory `.schema-resources/` is then no longer read or written, and can be deleted.

An earlier release left its journal at the root, as `.kleisis-transaction.json` or `.schema-closure-transaction.json`, with each backup beside its output. The materialization command still recovers such a journal before it builds, including a journal that names the resources at `.schema-resources/`. Publication refuses to start while any journal exists, at the current place or a former one.

The receipt declares `_schema: kleisis:receipt@1` as its first key. No command reads a receipt back: `check` compares it by bytes with a fresh build, so a receipt in another format reports drift until `materialize` rewrites it.

The generated registry declares `_schema: kleisis:schema-registry@2` as its first key, and holds three further keys:

- `schemas` maps each contract identifier to its file, including a dependency identifier outside the export set;
- `exports` lists only project-owned identifiers;
- `resources` maps each absolute `$id` string, exactly as written in a schema file, to the repository-relative path of that file.

The key `resources` covers every owned or materialized schema file whose root declares an absolute `$id`, which starts with a URI scheme. Two files may declare one `$id` only with identical bytes. A consumer cannot regenerate the registry of a dependency or a child repository, so Kleisis also reads two legacy forms there: `schema_version: 1`, without the key `resources`, and `schema_version: 2`, with it. Any other value, both keys together, or neither key fails the command. A legacy registry of the consumer itself differs from a fresh build, so `check` reports it as drift and `materialize` rewrites it. The [closure across sources](docs/adr/closure-across-sources.md) records this format.

An owned contract uses the root path `properties.<binding>.const` to state its identifier, where `<binding>` is the binding of the resource record, `_schema` by default. Its root `required` list must also name the binding. The resource record must export every contract with both entries. A file with neither entry remains an owned support schema. The support schema can still serve a `$ref`. A file with only one entry or an invalid value fails the command.

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
- different bytes for one shared contract;
- different bytes for one absolute `$id` in the parent and a child `resources` map.

The parent imports each child `resources` entry under the child path, unless the parent already maps that `$id`.

## Design

The [architecture and invariants](docs/architecture.md) specify three properties:

- permitted dependencies between modules;
- how Kleisis follows the keyword `$ref`;
- how Kleisis replaces both outputs safely.

The [decision records](docs/adr/_index.md) fix the formats and the rules that bind later changes.

## Development

The following commands run the targeted project gates through the dedicated Conda name:

```console
conda run -n kleisis python -m pytest
conda run -n kleisis ruff check --no-cache .
conda run -n kleisis mypy src tests
```

## License

The [license terms](LICENSE) apply under the General Public License, release 3 or any subsequent release.
