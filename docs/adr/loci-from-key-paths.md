# Loci from key paths

**Status**: Accepted on 2026-11-20. It replaces the conversion of printed field paths that [Findings output](findings-output.md#subject-and-locus) described.

## Problem

Kleisis reports a defect of its configuration with a locus, the JSON Pointer of the faulty field. Before this decision, each check printed the name of the field as text, such as `dependencies[0].source`. The function `field_pointer` of `src/kleisis/findings.py` then parsed that text back into segments, with the pattern `[A-Za-z_]+` for a key and `[n]` for a position. A text outside that pattern received the empty locus, which denotes the whole file.

The parsing produced two kinds of fault:

- A key holding `-`, `/`, `~` or a digit, such as `x-identifier` under `keywords`, lost its locus when it reached a shared validator. Each site with such a key therefore called `pointer` itself, so it stated its keys twice: once in the message, once in the locus.
- A name for a reader could parse as a key. The name `configuration`, which labels the root, became the locus `/configuration`, a node that does not exist.

A handoff of the workspace asked Kleisis to convert field paths through Anagraphe, which specifies their grammar. However, the read package of Anagraphe depends on Kleisis, so that conversion would close a dependency cycle.

## Decision

Kleisis builds each locus from the keys and the positions that lead to the field. It never prints a field path and then parses it. Therefore, it keeps no parser of field paths and needs no dependency on Anagraphe.

The value object `KeyPath` of `src/kleisis/findings.py` holds that tuple of keys and positions. A site extends a path by one key or one position with `/`, as in `place / "source"` or `place / index`. The path yields two values:

- `pointer`, the RFC 6901 pointer of the field, from the function `pointer(*keys)`;
- `label`, the name of the field for a reader, such as `dependencies[0].source`.

The label writes a key of the form `[A-Za-z_][A-Za-z0-9_-]*` bare, after a dot. It writes every other key as a JSON string between brackets, such as `schemas["a/b"]`, and a position as its decimal between brackets. The root takes the name of its document, such as `configuration`. The label is presentation only: the identity of a finding uses the locus, never the message.

The validators of `src/kleisis/domain/records.py` and the function `safe_relative_path` of `src/kleisis/domain/models.py` take a `KeyPath`. Each validator derives its message and its locus from that one path. The reader of registries passes the path of each entry of `schemas` and `resources` in the same way.

## Options weighed

The options are compared on four criteria:

- separation of concerns: each package owns one reading of field paths, or none;
- decoupling: the dependency graph stays acyclic, including at test time;
- robustness: every key yields its exact locus, whatever its characters;
- extensibility: a new check states its keys once.

| Option | Benefit | Limitation | Verdict |
| --- | --- | --- | --- |
| loci built from key paths | no field path is parsed, so no key loses its locus; no dependency is added | the label departs from the canonical form of Anagraphe for some keys, which matters only to a reader of the message | selected |
| Kleisis tests its parser against the field-path cases of Anagraphe, installed at a pinned commit as a test-only dependency | the parser follows the specification | Kleisis keeps a second parser of a grammar owned by Anagraphe, and its tests depend on a package that depends on Kleisis | rejected |
| a separate distribution of field paths, imported by Anagraphe and Kleisis | one parser serves both tools without a cycle | it solves the sharing of a parser, and Kleisis needs no parser | rejected |

The second option would win if Kleisis read field paths written by a user. The third option would win if several tools that Anagraphe cannot serve had to read them. Kleisis reads no field path today, so neither condition holds.

## Consequences

- A new check of the configuration names its field by a `KeyPath`, and takes both its message and its locus from that path.
- A registry entry whose path fails is labelled with a JSON string, such as `schemas["kleisis:x@1"]`, instead of the Python form `schemas['kleisis:x@1']`. The TypeScript reader already writes this form.
- An unknown key at the root of the configuration is reported as `configuration contains unknown fields`, instead of `record contains unknown fields`.
- A configuration that is no mapping is reported with the empty locus, instead of `/configuration`.
