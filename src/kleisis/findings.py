"""Findings reported by kleisis, and the catalogue of their rules.

A finding names the kind of failure by its rule, and the file that the failure concerns by its
subject. The locus places the failure inside that file; the discriminator separates two
failures of one rule at one locus. These four values form the identity of the finding, so none
of them depends on the wording of the message or on a staging directory.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePath, PurePosixPath


@dataclass(frozen=True)
class Rule:
    """One kind of failure, with its stable identifier."""

    id: str
    description: str


RECORD_UNREADABLE = Rule("record/unreadable", "The resource record cannot be read or parsed.")
RECORD_INVALID = Rule(
    "record/invalid", "A field of the resource record violates its declared structure."
)
RECORD_PATH_OVERLAP = Rule(
    "record/path-overlap",
    "The resource directory overlaps a source or control path, or the registry is the record.",
)
PATH_ESCAPE = Rule("path/escape", "A declared path leaves its repository or source.")
PATH_ABSENT = Rule("path/absent", "A declared file or directory is absent.")
SOURCE_UNAVAILABLE = Rule(
    "source/unavailable", "An exact dependency source cannot be acquired or verified."
)
REGISTRY_INVALID = Rule(
    "registry/invalid", "A registry cannot be read or violates the registry format."
)
SCHEMA_UNREADABLE = Rule("schema/unreadable", "A schema file cannot be read as a document.")
SCHEMA_LINK = Rule(
    "schema/link", "A schema tree or a materialized target is a link or a non-regular file."
)
CONTRACT_BINDING = Rule(
    "contract/binding", "A schema declares an incomplete or invalid `_schema` binding."
)
CONTRACT_BINDING_MISMATCH = Rule(
    "contract/binding-mismatch",
    "A dependency registry maps a contract to a schema declaring another identifier.",
)
CONTRACT_DUPLICATE = Rule(
    "contract/duplicate", "Two schemas or two sources provide one contract identifier."
)
CONTRACT_UNEXPORTED = Rule(
    "contract/unexported", "An owned contract is absent from the exports of the record."
)
CONTRACT_UNOWNED_EXPORT = Rule(
    "contract/unowned-export", "An export of the record names no owned contract."
)
CONTRACT_UNPROVIDED = Rule(
    "contract/unprovided", "A dependency registry does not provide a selected contract."
)
DEPENDENCY_NO_RESOURCES = Rule(
    "dependency/no-resources",
    "A dependency that selects no contract has a registry without resources.",
)
DEPENDENCY_UNUSED = Rule(
    "dependency/unused", "A dependency that selects no contract serves no reference."
)
REFERENCE_UNMAPPED = Rule(
    "reference/unmapped", "Neither the source nor a declared dependency maps an absolute `$ref`."
)
REFERENCE_CONFLICTING_BYTES = Rule(
    "reference/conflicting-bytes", "Two dependencies map one absolute `$ref` to different bytes."
)
REFERENCE_IDENTIFIER_MISMATCH = Rule(
    "reference/identifier-mismatch", "A resources map names a file that declares another `$id`."
)
REFERENCE_LEAVES_SOURCE = Rule(
    "reference/leaves-source", "A `$ref` designates a file outside the source of its file."
)
REFERENCE_VAULT_ROOTED = Rule(
    "reference/vault-rooted", "A published or materialized file holds a vault-rooted `$ref`."
)
REFERENCE_TARGET_ABSENT = Rule(
    "reference/target-absent", "A `$ref` or an entrypoint designates no regular file."
)
REFERENCE_UNRESOLVED = Rule(
    "reference/unresolved",
    "A `$ref` designates no loaded file, or its fragment or anchor does not exist.",
)
REFERENCE_CHAIN_TOO_LONG = Rule(
    "reference/chain-too-long", "A chain of `$ref` exceeds the step limit."
)
RESOURCES_CONFLICTING_BYTES = Rule(
    "resources/conflicting-bytes", "Two files declare one absolute `$id` with different bytes."
)
CHILD_MISSING_DEPENDENCY = Rule(
    "child/missing-dependency", "A child project uses a contract absent from the parent registry."
)
CHILD_SHARED_BYTES = Rule(
    "child/shared-bytes", "A contract shared by the parent and a child has different bytes."
)
DRIFT_RESOURCES = Rule(
    "drift/resources", "The materialized resources are absent or differ from a fresh build."
)
DRIFT_REGISTRY = Rule(
    "drift/registry", "The generated registry is absent or differs from a fresh build."
)

RULES: tuple[Rule, ...] = (
    RECORD_UNREADABLE,
    RECORD_INVALID,
    RECORD_PATH_OVERLAP,
    PATH_ESCAPE,
    PATH_ABSENT,
    SOURCE_UNAVAILABLE,
    REGISTRY_INVALID,
    SCHEMA_UNREADABLE,
    SCHEMA_LINK,
    CONTRACT_BINDING,
    CONTRACT_BINDING_MISMATCH,
    CONTRACT_DUPLICATE,
    CONTRACT_UNEXPORTED,
    CONTRACT_UNOWNED_EXPORT,
    CONTRACT_UNPROVIDED,
    DEPENDENCY_NO_RESOURCES,
    DEPENDENCY_UNUSED,
    REFERENCE_UNMAPPED,
    REFERENCE_CONFLICTING_BYTES,
    REFERENCE_IDENTIFIER_MISMATCH,
    REFERENCE_LEAVES_SOURCE,
    REFERENCE_VAULT_ROOTED,
    REFERENCE_TARGET_ABSENT,
    REFERENCE_UNRESOLVED,
    REFERENCE_CHAIN_TOO_LONG,
    RESOURCES_CONFLICTING_BYTES,
    CHILD_MISSING_DEPENDENCY,
    CHILD_SHARED_BYTES,
    DRIFT_RESOURCES,
    DRIFT_REGISTRY,
)


@dataclass(frozen=True)
class Finding:
    """One failure: its rule, its explanation, and the file and the locus that it concerns.

    ``subject`` is the file or folder concerned. Inside the build it may be an absolute path in
    a staging tree; the service relocates it to a repository-relative path. ``None`` stands for
    a failure whose subject the caller supplies. ``locus`` is empty for the whole subject, a
    JSON Pointer for a key path, or ``link:<$ref as written>`` for a reference.
    """

    rule: Rule
    message: str
    subject: PurePath | None = None
    locus: str = ""
    discriminator: str = ""
    folder: bool = False


def pointer(*keys: str | int) -> str:
    """Return the JSON Pointer of a key path, escaping ``~`` and ``/`` in each key."""
    return "".join("/" + str(key).replace("~", "~0").replace("/", "~1") for key in keys)


_FIELD = re.compile(r"[A-Za-z_]+(?:\.[A-Za-z_]+|\[[0-9]+\])*")
_FIELD_PART = re.compile(r"([A-Za-z_]+)|\[([0-9]+)\]")


def field_pointer(field_name: str) -> str:
    """Convert a record field name such as ``dependencies[0].source`` into a JSON Pointer.

    A name outside that form, such as ``resource record``, denotes the whole document.
    """
    if not _FIELD.fullmatch(field_name) or field_name == "record":
        return ""
    return pointer(*(key or index for key, index in _FIELD_PART.findall(field_name)))


def link(reference: str) -> str:
    """Return the locus of a ``$ref``, written as in its file."""
    return f"link:{reference}"


def relocate(finding: Finding, roots: Sequence[tuple[Path, PurePosixPath]]) -> Finding:
    """Map an absolute subject below one of ``roots`` to the repository path of that root.

    Each root pairs a resolved directory with its repository path; the first root containing
    the subject applies, so a nested root must precede its parent. Only the parent of the
    subject is resolved, so that a subject that is itself a link keeps its own name.
    """
    subject = finding.subject
    if subject is None or not subject.is_absolute():
        return finding
    resolved = Path(subject).parent.resolve() / subject.name
    for base, target in roots:
        if resolved.is_relative_to(base):
            relative = PurePosixPath(resolved.relative_to(base).as_posix())
            return replace(finding, subject=target / relative)
    return finding


def complete(finding: Finding, record_path: PurePosixPath) -> Finding:
    """Give a finding a repository-relative subject.

    A finding without subject concerns the resource record. A subject outside the repository,
    which the profile cannot name, falls back to the repository root.
    """
    subject = record_path if finding.subject is None else finding.subject
    relative = PurePosixPath(subject.as_posix())
    if relative.is_absolute() or ".." in relative.parts:
        return replace(finding, subject=PurePosixPath("."), folder=True)
    return replace(finding, subject=relative)
