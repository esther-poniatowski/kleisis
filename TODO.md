---
tags:
  - tasks
aliases:
  - Project goals
---
# Project goals

This list holds the goals of `kleisis` that span several parts of the project.

## Reading of sources

- [ ] Report a merge key `<<` in a schema or a record as a finding that names its file and line, in place of the constructor error of PyYAML. The loader `UniqueKeyLoader` of [the document module](src/kleisis/domain/documents.py) replaces the mapping constructor of `SafeLoader` to reject duplicate keys, so a merge key reaches no constructor and fails with "could not determine a constructor for the tag 'tag:yaml.org,2002:merge'". Rejecting the key, rather than merging it, keeps the closure equal to what a YAML 1.2 reader such as Ekphrasis reads, where `<<` is an ordinary key. The first case met is the base schema of assessments of Gnomon, task `portable-schema-yaml` of its task list. 🆔 merge-key-finding
