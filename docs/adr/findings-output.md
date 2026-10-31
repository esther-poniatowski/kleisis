# Findings output

**Status**: Proposed. It implements the Kleisis row of the producer migration of Elenchos, and is accepted with the findings profile of Elenchos.

## Problem

The gate Elenchos reads the failures of each domain tool as a SARIF 2.1.0 log in its findings profile, version 1. It builds the identity of each failure from the tool, the rule, the resource and the locus. Before this decision, the commands `validate` and `check` reported a failure as one line on standard error. That line named neither a rule nor a file in a form that a reader can parse. Moreover, the paths in the line named the staging directory of the run, so the line changed from one run to the next.

## Decision

The decision has five parts: an output option, a catalogue of rules, the subject and the locus of each failure, the choice between the first failure and every failure, and the objects that the log omits.

### Output option

The commands `validate` and `check` accept `--format sarif`. The command then writes one log on standard output, and keeps the line on standard error and the exit status. The default `--format text` writes the line alone. The command `materialize` refuses `--format sarif`: it writes the outputs instead of checking them, and the profile serves checkers.

The log holds one run. Its driver `kleisis` declares the package version and the complete catalogue of rules. The run declares `columnKind` as `unicodeCodePoints`. Each result holds its `ruleId`, the level `error`, its message, one physical location relative to `SRCROOT`, and one logical location. The level is always `error`, since every failure of Kleisis stops the command. A successful run yields a log with an empty `results` list.

### Rules

Each kind of failure has one stable `ruleId` of the form `<area>/<kind>`. The catalogue lives in `src/kleisis/findings.py`, together with the description of each rule. The table fixes the subject and the locus of each rule:

| Rule | Subject | Locus | Discriminator |
| --- | --- | --- | --- |
| `record/unreadable` | the record | empty | — |
| `record/invalid` | the record | the pointer of the field | — |
| `record/path-overlap` | the record | `/paths/resources` or `/paths/registry` | the overlapped path |
| `path/escape` | the file declaring the path | the pointer of the declaration | the dependency or the project, when listed |
| `path/absent` | the file declaring the path | the pointer of the declaration | the project, when listed |
| `source/unavailable` | the record | `/dependencies` | the dependency |
| `registry/invalid` | the registry | the pointer of the field, or empty | — |
| `schema/unreadable` | the schema file | empty | — |
| `schema/link` | the link or non-regular file | empty | — |
| `contract/binding` | the schema file | `/properties/_schema`, its `const`, or `/required` | — |
| `contract/binding-mismatch` | the selected schema | `/properties/_schema/const` | — |
| `contract/duplicate` | the later owned schema, the owned schema shadowing a dependency, or the record or child registry that lists the identifier | `/properties/_schema/const`, `/dependencies` or `/schemas/<id>` | the identifier, under `/dependencies` |
| `contract/unexported` | the owned schema | `/properties/_schema/const` | — |
| `contract/unowned-export` | the record | `/exports` | the identifier |
| `contract/unprovided` | the record | `/dependencies` | the identifier |
| `dependency/no-resources`, `dependency/unused` | the record | `/dependencies` | the dependency |
| `reference/unmapped`, `reference/conflicting-bytes`, `reference/leaves-source`, `reference/vault-rooted`, `reference/target-absent`, `reference/unresolved`, `reference/chain-too-long` | the file holding the `$ref` | `link:<$ref as written>` | — |
| `reference/identifier-mismatch` | the mapped file | `/$id` | the URI |
| `resources/conflicting-bytes` | the later file declaring the `$id` | `/$id` | — |
| `child/missing-dependency` | the child registry | `/schemas/<id>` | — |
| `child/shared-bytes` | the child copy of the contract | empty | — |
| `drift/resources` | the resource directory, as a folder | empty | — |
| `drift/registry` | the generated registry | empty | — |
| `filesystem/failed` | the record | empty | — |

An entrypoint that designates no regular file is reported with `reference/target-absent` against the entrypoint itself, with the empty locus.

### Subject and locus

The resource of a result is `path:<path>`, relative to the root of the consumer repository. The service relocates each staging path to that root:

- a file of the candidate repository keeps its repository path;
- a file of a dependency source is reported at the path where the closure materializes it, `<resources>/<dependency>/<path>`. This holds even when the failure prevents the materialization, so the identity does not depend on the progress of the run;
- a file of a child project keeps its path below the child checkout.

A `$ref` is named by the locus `link:<$ref as written>`, the typed locus of the profile for a link target. The locus of a key path is a JSON Pointer. An entry of a list keyed by an identifier, such as a dependency, an export or a selected contract, is named by the pointer of the list and a discriminator holding the identifier. Therefore, reordering the list keeps each identity. A structural error of the record keeps the pointer with the index that the parser reports.

Kleisis does not track lines in the files it reads. No result holds a region, and each physical location denotes the whole file or folder.

### First failure or every failure

The build stops at the first failure, and the log reports that failure. A failure listing several items, such as several unowned exports, yields one result per item. The command `check` compares the resource directory and the registry independently, so it yields one result for each output that differs.

### Omitted objects

The log holds no `suppressions`, no `fingerprints` and no `baselineState`. The profile reserves the first two for the gate, and excludes the third from version 1. A result holds `partialFingerprints["discriminator/v1"]` only for the rules whose locus can be shared by two distinct failures, as the table states.

## Options weighed

### First failure or every failure

The options are compared on four criteria:

- each result denotes a defect, not the consequence of another failure;
- one run reports as many defects as possible;
- the text output, the log and the exit status describe the same failures;
- the reported set is deterministic for given inputs.

| Option | Benefit | Limitation | Verdict |
| --- | --- | --- | --- |
| the first failure of the build, and each drifted output of `check` | each result is a defect; the log and the text line report one failure | a run reveals one build failure; a later failure appears once the first is repaired | selected |
| every independent failure, each stage continuing past its failed units | one run reveals the failures of a whole stage | each stage consumes the outputs of the previous one, so a skipped unit produces consequential failures: an unreadable owned contract yields an unowned export, and an unavailable dependency yields unmapped references. Avoiding them requires a rule tracking which failure causes which | rejected |

The rejected option would win if a baseline tolerated Kleisis findings, since a tolerated first failure then hides the failures behind it. Every failure of Kleisis blocks the closure, so the gate should not tolerate one.

### Locus of a reference

| Option | Benefit | Limitation | Verdict |
| --- | --- | --- | --- |
| `link:<$ref as written>` | stable under any restructuring of the schema; a typed locus of the profile | two equal `$ref` strings in one file share the locus, but they denote the same failure | selected |
| the JSON Pointer of the `$ref` keyword | separates two occurrences | changes whenever a property above the reference moves, and the resolver tracks no pointer | rejected |

### Path of a dependency file

| Option | Benefit | Limitation | Verdict |
| --- | --- | --- | --- |
| the path of materialization below the resource directory | names the dependency and the file; equals the path of the drift results | the file is absent from the repository while the build fails | selected |
| the record, with the dependency as locus | the physical location exists | loses the file, which the message alone would name | rejected |

## Consequences

- A new kind of failure takes a new rule in `src/kleisis/findings.py`. Renaming a rule changes the identity of its results, so it requires a rename map in the gate.
- The tests of `tests/test_sarif.py` validate each log against the profile through a validator written from the profile text, and against copies of the test vectors of Elenchos.
- The profile reserves `suppressions` for the gate, so the log omits it, although the migration brief asked for an empty list on each result.
