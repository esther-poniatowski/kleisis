"""Resolve and validate the schema set loaded by Ekphrasis."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml

from kleisis.domain.documents import load_document
from kleisis.errors import ReferenceError

_URI = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_ASCII_ARRAY_INDEX = re.compile(r"^[0-9]+$")
_MAX_REFERENCE_CHAIN = 64


def _document(path: Path) -> Any:
    try:
        return load_document(path)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise ReferenceError(f"cannot read schema {path}: {exc}") from exc


def _objects(value: Any, active: set[int] | None = None) -> list[dict[str, Any]]:
    active = set() if active is None else active
    result: list[dict[str, Any]] = []
    if isinstance(value, (dict, list)):
        identity = id(value)
        if identity in active:
            raise ReferenceError("schema contains a recursive YAML alias")
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


def _resolve_file(root: Path, current: Path, location: str) -> Path:
    if location.startswith("/"):
        target = root / location.lstrip("/")
    else:
        target = current.parent / location
    return target.resolve()


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


def _locate(
    reference: str,
    current_path: Path,
    root: Path,
    documents: dict[Path, Any],
    identifiers: dict[str, Path],
) -> tuple[Any, Path]:
    location, fragment = _split_reference(reference)
    if not location:
        target = current_path
    elif _URI.match(location):
        identified_target = identifiers.get(location)
        if identified_target is None:
            raise ReferenceError(f"$ref designates no loaded schema file: {reference!r}")
        target = identified_target
    else:
        target = _resolve_file(root, current_path, location)
    document = documents.get(target)
    if document is None:
        raise ReferenceError(f"$ref designates no loaded schema file: {reference!r}")
    value = (
        _resolve_pointer(document, fragment, reference)
        if fragment == "" or fragment.startswith("/")
        else _find_anchor(document, fragment, reference)
    )
    return value, target


def _validate_reference_chains(root: Path, documents: dict[Path, Any]) -> None:
    identifiers: dict[str, Path] = {}
    for path, document in documents.items():
        if isinstance(document, dict) and isinstance(document.get("$id"), str):
            identifiers[document["$id"]] = path
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
                    current["$ref"], current_path, root, documents, identifiers
                )
            else:
                raise ReferenceError(f"$ref chain from {path} exceeds {_MAX_REFERENCE_CHAIN} steps")


def reference_closure(source_root: Path, entrypoints: list[Path]) -> set[Path]:
    """Return and validate every file loaded from the entrypoint schemas."""
    root = source_root.resolve()
    pending = [path.resolve() for path in entrypoints]
    visited: set[Path] = set()
    documents: dict[Path, Any] = {}
    while pending:
        path = pending.pop(0)
        if path in visited:
            continue
        if not path.is_relative_to(root):
            raise ReferenceError(f"schema target leaves its source: {path}")
        if not path.is_file() or path.is_symlink():
            raise ReferenceError(f"schema target is not a regular source file: {path}")
        document = _document(path)
        documents[path] = document
        visited.add(path)
        for schema in _objects(document):
            reference = schema.get("$ref")
            if not isinstance(reference, str):
                continue
            location, _ = _split_reference(reference)
            if not location or _URI.match(location):
                continue
            target = _resolve_file(root, path, location)
            if not target.is_relative_to(root):
                raise ReferenceError(f"schema target leaves its source: {target}")
            if target not in visited and target not in pending:
                pending.append(target)
    _validate_reference_chains(root, documents)
    return visited
