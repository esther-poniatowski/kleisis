"""Resolve and validate the schema set loaded by Ekphrasis."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml

from kleisis.domain.documents import load_document
from kleisis.errors import ReferenceError
from kleisis.findings import (
    REFERENCE_CHAIN_TOO_LONG,
    REFERENCE_CONFLICTING_BYTES,
    REFERENCE_IDENTIFIER_MISMATCH,
    REFERENCE_LEAVES_SOURCE,
    REFERENCE_TARGET_ABSENT,
    REFERENCE_UNMAPPED,
    REFERENCE_VAULT_ROOTED,
    SCHEMA_UNREADABLE,
    link,
    pointer,
)

_URI = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_ASCII_ARRAY_INDEX = re.compile(r"^[0-9]+$")
_MAX_REFERENCE_CHAIN = 64


def is_absolute_uri(value: object) -> bool:
    """Tell whether a string is an absolute URI, that is, one that starts with a scheme."""
    return isinstance(value, str) and _URI.match(value) is not None


def root_identifier(document: Any) -> str | None:
    """Return the absolute root ``$id`` of a schema document, if it declares one."""
    if isinstance(document, dict) and is_absolute_uri(document.get("$id")):
        return str(document["$id"])
    return None


@dataclass(frozen=True, eq=False)
class SchemaSource:
    """One source tree, with the absolute identifiers that its own files declare.

    A source compares by identity, since the closure records the source of each loaded file.

    ``resources`` maps each absolute ``$id`` string to a file inside ``root``. An entry whose
    file is absent from the tree is ignored, since a registry also maps the materialized
    files of its repository, which a source archive omits.
    """

    name: str
    root: Path
    resources: Mapping[str, Path] = field(default_factory=dict)


def _document(path: Path) -> Any:
    try:
        return load_document(path)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise ReferenceError(
            f"cannot read schema {path}: {exc}", rule=SCHEMA_UNREADABLE, subject=path
        ) from exc


def _objects(value: Any, active: set[int] | None = None) -> list[dict[str, Any]]:
    active = set() if active is None else active
    result: list[dict[str, Any]] = []
    if isinstance(value, (dict, list)):
        identity = id(value)
        if identity in active:
            raise ReferenceError("schema contains a recursive YAML alias", rule=SCHEMA_UNREADABLE)
        active.add(identity)
    try:
        if isinstance(value, dict):
            result.append(value)
            for child in value.values():
                result.extend(_objects(child, active))
        elif isinstance(value, list):
            for child in value:
                result.extend(_objects(child, active))
        return result
    finally:
        if isinstance(value, (dict, list)):
            active.remove(id(value))


def _decode_fragment(fragment: str) -> str:
    try:
        return unquote(fragment, errors="strict")
    except UnicodeDecodeError:
        return fragment


def _split_reference(reference: str) -> tuple[str, str]:
    location, separator, fragment = reference.partition("#")
    return location, _decode_fragment(fragment) if separator else ""


def _resolve_file(current: Path, location: str) -> Path:
    return (current.parent / location).resolve()


def _resolve_pointer(document: Any, pointer: str, reference: str) -> Any:
    current = document
    if pointer == "":
        return current
    if not pointer.startswith("/"):
        raise ReferenceError(f"invalid JSON Pointer fragment in $ref {reference!r}")
    for part in pointer[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif (
            isinstance(current, list)
            and _ASCII_ARRAY_INDEX.fullmatch(key)
            and int(key) < len(current)
        ):
            current = current[int(key)]
        else:
            raise ReferenceError(f"fragment does not exist in $ref {reference!r}")
    return current


def _find_anchor(document: Any, anchor: str, reference: str) -> Any:
    for value in _objects(document):
        if value.get("$anchor") == anchor:
            return value
    raise ReferenceError(f"anchor does not exist in $ref {reference!r}")


def _label(source: SchemaSource, path: Path) -> str:
    root = source.root.resolve()
    relative = path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
    return f"{relative} in dependency {source.name}" if source.name else relative


def _resolve_identifier(
    uri: str,
    reference: str,
    source: SchemaSource,
    referrer: Path,
    declared: dict[tuple[Path, str], Path],
    dependencies: Sequence[SchemaSource],
) -> tuple[SchemaSource, Path]:
    """Resolve an absolute ``$ref`` in the current source first, then in the dependencies.

    ``reference`` is the ``$ref`` as written in ``referrer``, which a failure reports.
    """
    loaded = declared.get((source.root.resolve(), uri))
    if loaded is not None:
        return source, loaded
    local = source.resources.get(uri)
    if local is not None and local.is_file():
        return source, local.resolve()
    providers = [
        (dependency, dependency.resources[uri])
        for dependency in dependencies
        if uri in dependency.resources
    ]
    present = [(dependency, path) for dependency, path in providers if path.is_file()]
    if not present:
        absent = "".join(
            f"; dependency {dependency.name} maps it to an absent file"
            for dependency, _ in providers
        )
        raise ReferenceError(
            f"neither its source nor a declared dependency maps $ref {uri!r} in "
            f"{_label(source, referrer)}{absent}",
            rule=REFERENCE_UNMAPPED,
            subject=referrer,
            locus=link(reference),
        )
    first, first_path = present[0]
    for other, other_path in present[1:]:
        if other_path.read_bytes() != first_path.read_bytes():
            raise ReferenceError(
                f"dependencies {first.name} and {other.name} map $ref {uri!r} in "
                f"{_label(source, referrer)} to different bytes",
                rule=REFERENCE_CONFLICTING_BYTES,
                subject=referrer,
                locus=link(reference),
            )
    return first, first_path.resolve()


def _locate(
    reference: str,
    current_path: Path,
    owners: dict[Path, SchemaSource],
    documents: dict[Path, Any],
    targets: dict[tuple[Path, str], Path],
) -> tuple[Any, Path]:
    location, fragment = _split_reference(reference)
    source = owners[current_path]
    root = source.root.resolve()
    if not location:
        target: Path | None = current_path
    elif _URI.match(location):
        target = targets.get((root, location))
    else:
        target = _resolve_file(current_path, location)
    document = documents.get(target) if target is not None else None
    if target is None or document is None:
        raise ReferenceError(
            f"$ref designates no loaded schema file: {reference!r}",
            subject=current_path,
            locus=link(reference),
        )
    try:
        value = (
            _resolve_pointer(document, fragment, reference)
            if fragment == "" or fragment.startswith("/")
            else _find_anchor(document, fragment, reference)
        )
    except ReferenceError as exc:
        exc.locate(current_path, link(reference))
        raise
    return value, target


def _validate_reference_chains(
    owners: dict[Path, SchemaSource],
    documents: dict[Path, Any],
    targets: dict[tuple[Path, str], Path],
) -> None:
    for path, document in documents.items():
        for schema in _objects(document):
            if not isinstance(schema.get("$ref"), str):
                continue
            current: Any = schema
            current_path = path
            for _ in range(_MAX_REFERENCE_CHAIN):
                if not isinstance(current, dict) or not isinstance(current.get("$ref"), str):
                    break
                current, current_path = _locate(
                    current["$ref"], current_path, owners, documents, targets
                )
            else:
                raise ReferenceError(
                    f"$ref chain from {path} exceeds {_MAX_REFERENCE_CHAIN} steps",
                    rule=REFERENCE_CHAIN_TOO_LONG,
                    subject=path,
                    locus=link(schema["$ref"]),
                )


def source_closure(
    entrypoints: Sequence[tuple[SchemaSource, Path]],
    dependencies: Sequence[SchemaSource] = (),
) -> dict[Path, SchemaSource]:
    """Return and validate every file loaded from the entrypoints, with its source.

    Every loaded file is published or materialized, so it holds no vault-rooted ``$ref``: the
    consumer would resolve that path from the root of its own vault. A relative ``$ref`` stays
    inside the source of its containing file. An absolute ``$ref`` resolves, by exact string, to
    a loaded file of the same source declaring that ``$id``, then to the file mapped by the
    source's own resources, then to the file mapped by the resources of the first dependency
    that maps it, provided every dependency that maps it names the same bytes. No URI is
    fetched.
    """
    documents: dict[Path, Any] = {}
    owners: dict[Path, SchemaSource] = {}
    declared: dict[tuple[Path, str], Path] = {}
    targets: dict[tuple[Path, str], Path] = {}
    # Each pending file carries the file and the ``$ref`` that reached it, or nothing for an
    # entrypoint, so that a failure names the reference that designates the file.
    pending: list[tuple[SchemaSource, Path, Path | None, str]] = [
        (source, path.resolve(), None, "") for source, path in entrypoints
    ]
    absolute: list[tuple[SchemaSource, Path, str, str]] = []
    while pending or absolute:
        while pending:
            source, path, referrer, written = pending.pop(0)
            if path in owners:
                continue
            subject = path if referrer is None else referrer
            locus = "" if referrer is None else link(written)
            root = source.root.resolve()
            if not path.is_relative_to(root):
                raise ReferenceError(
                    f"schema target leaves its source: {path}",
                    rule=REFERENCE_LEAVES_SOURCE,
                    subject=subject,
                    locus=locus,
                )
            if not path.is_file() or path.is_symlink():
                raise ReferenceError(
                    f"schema target is not a regular source file: {path}",
                    rule=REFERENCE_TARGET_ABSENT,
                    subject=subject,
                    locus=locus,
                )
            document = _document(path)
            try:
                schemas = _objects(document)
            except ReferenceError as exc:
                exc.locate(path)
                raise
            documents[path] = document
            owners[path] = source
            identifier = root_identifier(document)
            if identifier is not None:
                declared.setdefault((root, identifier), path)
            for schema in schemas:
                reference = schema.get("$ref")
                if not isinstance(reference, str):
                    continue
                location, _ = _split_reference(reference)
                if not location:
                    continue
                if _URI.match(location):
                    absolute.append((source, path, location, reference))
                    continue
                if location.startswith("/"):
                    raise ReferenceError(
                        f"vault-rooted $ref {reference!r} in {_label(source, path)} is not "
                        "admitted in a published or materialized file; cite the target by "
                        "its absolute $id",
                        rule=REFERENCE_VAULT_ROOTED,
                        subject=path,
                        locus=link(reference),
                    )
                target = _resolve_file(path, location)
                if not target.is_relative_to(root):
                    raise ReferenceError(
                        f"schema target leaves its source: {target}",
                        rule=REFERENCE_LEAVES_SOURCE,
                        subject=path,
                        locus=link(reference),
                    )
                if target not in owners:
                    pending.append((source, target, path, reference))
        unresolved, absolute = absolute, []
        for source, referrer, uri, reference in unresolved:
            key = (source.root.resolve(), uri)
            if key in targets:
                continue
            target_source, target = _resolve_identifier(
                uri, reference, source, referrer, declared, dependencies
            )
            targets[key] = target
            if target not in owners:
                pending.append((target_source, target, referrer, reference))
    for (_, uri), target in sorted(targets.items(), key=lambda item: item[0][1]):
        identifier = root_identifier(documents[target])
        if identifier != uri:
            raise ReferenceError(
                f"resources map $ref {uri!r} to {_label(owners[target], target)}, "
                f"which declares $id {identifier!r}",
                rule=REFERENCE_IDENTIFIER_MISMATCH,
                subject=target,
                locus=pointer("$id"),
                discriminator=uri,
            )
    _validate_reference_chains(owners, documents, targets)
    return owners


def reference_closure(
    source_root: Path,
    entrypoints: list[Path],
    resources: Mapping[str, Path] | None = None,
) -> set[Path]:
    """Return and validate every file loaded from the entrypoint schemas of one source."""
    source = SchemaSource(name="", root=source_root, resources=resources or {})
    return set(source_closure([(source, path) for path in entrypoints]))
