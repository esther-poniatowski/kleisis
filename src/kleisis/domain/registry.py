"""Schema discovery and deterministic registry serialization."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any, cast

import yaml

from kleisis.domain.documents import load_document
from kleisis.domain.models import Registry, safe_relative_path
from kleisis.domain.references import is_absolute_uri, root_identifier
from kleisis.errors import RegistryError, SchemaClosureError
from kleisis.findings import (
    CONTRACT_BINDING,
    CONTRACT_DUPLICATE,
    PATH_ABSENT,
    REGISTRY_INVALID,
    RESOURCES_CONFLICTING_BYTES,
    SCHEMA_UNREADABLE,
    Rule,
    pointer,
)

_SCHEMA_SUFFIXES = (".schema.yaml", ".schema.yml", ".schema.json")
BINDING_LOCUS = pointer("properties", "_schema", "const")
"""The locus of the contract identifier that a schema declares."""
IDENTIFIER_LOCUS = pointer("$id")
"""The locus of the absolute identifier that a schema declares at its root."""
REGISTRY_VERSION = 2
_REGISTRY_FIELDS = {
    1: {"schema_version", "schemas", "exports"},
    2: {"schema_version", "schemas", "exports", "resources"},
}


def _load_mapping(path: Path, rule: Rule) -> dict[str, Any]:
    try:
        value = load_document(path)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise RegistryError(f"cannot read {path}: {exc}", rule=rule, subject=path) from exc
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise RegistryError(f"{path} must contain a mapping", rule=rule, subject=path)
    return cast(dict[str, Any], value)


def schema_identifier(path: Path) -> str | None:
    """Read the logical contract of a concrete schema, or identify a support schema."""
    schema = _load_mapping(path, SCHEMA_UNREADABLE)
    properties = schema.get("properties")
    required = schema.get("required")
    has_property = isinstance(properties, dict) and "_schema" in properties
    requires_property = isinstance(required, list) and "_schema" in required
    if not has_property and not requires_property:
        return None
    if not isinstance(properties, dict) or "_schema" not in properties:
        raise RegistryError(
            f"{path} requires _schema but has no _schema property mapping",
            rule=CONTRACT_BINDING,
            subject=path,
            locus=pointer("properties", "_schema"),
        )
    binding = properties["_schema"]
    if not isinstance(binding, dict):
        raise RegistryError(
            f"{path} has an invalid _schema property mapping",
            rule=CONTRACT_BINDING,
            subject=path,
            locus=pointer("properties", "_schema"),
        )
    identifier = binding.get("const")
    if not isinstance(identifier, str) or not identifier.strip():
        raise RegistryError(
            f"{path} has no non-empty properties._schema.const",
            rule=CONTRACT_BINDING,
            subject=path,
            locus=BINDING_LOCUS,
        )
    if not requires_property:
        raise RegistryError(
            f"{path} does not require _schema at the root",
            rule=CONTRACT_BINDING,
            subject=path,
            locus=pointer("required"),
        )
    return identifier


def discover_owned_schemas(project_root: Path, schema_root: Path) -> dict[str, PurePosixPath]:
    """Discover every concrete schema below the configured owned-schema root.

    The absence of that root is reported without subject; the caller names its declaration.
    """
    if not schema_root.is_dir():
        raise RegistryError(
            f"owned schema directory is absent: {schema_root}",
            rule=PATH_ABSENT,
            locus=pointer("paths", "owned_schemas"),
        )
    result: dict[str, PurePosixPath] = {}
    for path in sorted(candidate_schemas(schema_root)):
        identifier = schema_identifier(path)
        if identifier is None:
            continue
        relative = PurePosixPath(path.relative_to(project_root).as_posix())
        previous = result.get(identifier)
        if previous is not None:
            raise RegistryError(
                f"duplicate schema identifier {identifier!r}: {previous} and {relative}",
                rule=CONTRACT_DUPLICATE,
                subject=path,
                locus=BINDING_LOCUS,
            )
        result[identifier] = relative
    return result


def candidate_schemas(root: Path) -> list[Path]:
    """Return files whose names declare JSON Schema candidates."""
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and any(path.name.endswith(suffix) for suffix in _SCHEMA_SUFFIXES)
    ]


def _registry_paths(path: Path, value: object, field_name: str) -> dict[str, PurePosixPath]:
    if not isinstance(value, dict):
        raise RegistryError(
            f"{path} must contain a {field_name} mapping", subject=path, locus=pointer(field_name)
        )
    result: dict[str, PurePosixPath] = {}
    for key, target in value.items():
        if not isinstance(key, str) or not key:
            raise RegistryError(
                f"{path} contains an invalid {field_name} key",
                subject=path,
                locus=pointer(field_name),
            )
        try:
            result[key] = safe_relative_path(target, f"{field_name}[{key!r}]")
        except SchemaClosureError as exc:
            raise RegistryError(
                str(exc),
                rule=exc.findings[0].rule,
                subject=path,
                locus=pointer(field_name, key),
            ) from exc
    return result


def load_registry(path: Path) -> Registry:
    """Load and validate a generated registry of format version 1 or 2."""
    root = _load_mapping(path, REGISTRY_INVALID)
    version = root.get("schema_version")
    if type(version) is not int or version not in _REGISTRY_FIELDS:
        raise RegistryError(
            f"{path} must declare schema_version 1 or 2",
            subject=path,
            locus=pointer("schema_version"),
        )
    unknown = sorted(set(root) - _REGISTRY_FIELDS[version])
    if unknown:
        raise RegistryError(f"{path} contains unknown fields: {', '.join(unknown)}", subject=path)
    schemas = _registry_paths(path, root.get("schemas"), "schemas")
    raw_exports = root.get("exports", [])
    if not isinstance(raw_exports, list) or not all(
        isinstance(identifier, str) and identifier for identifier in raw_exports
    ):
        raise RegistryError(
            f"{path} exports must be a sequence of identifiers",
            subject=path,
            locus=pointer("exports"),
        )
    exports = tuple(cast(list[str], raw_exports))
    if len(exports) != len(set(exports)):
        raise RegistryError(
            f"{path} exports contains duplicates", subject=path, locus=pointer("exports")
        )
    missing = sorted(set(exports) - set(schemas))
    if missing:
        raise RegistryError(
            f"{path} exports identifiers absent from schemas: {', '.join(missing)}",
            subject=path,
            locus=pointer("exports"),
        )
    resources: dict[str, PurePosixPath] = {}
    if version >= 2:
        resources = _registry_paths(path, root.get("resources"), "resources")
        relative = sorted(uri for uri in resources if not is_absolute_uri(uri))
        if relative:
            raise RegistryError(
                f"{path} resources keys must be absolute URIs: {', '.join(relative)}",
                subject=path,
                locus=pointer("resources"),
            )
    return Registry(schemas=schemas, exports=exports, resources=resources, schema_version=version)


def resource_map(root: Path, files: Iterable[Path]) -> dict[str, PurePosixPath]:
    """Map each absolute root ``$id`` among ``files`` to its path relative to ``root``.

    The first file declaring an identifier keeps the entry. A later file may declare the same
    identifier only with identical bytes.
    """
    result: dict[str, PurePosixPath] = {}
    resolved_root = root.resolve()
    for path in files:
        identifier = root_identifier(_load_mapping(path, SCHEMA_UNREADABLE))
        if identifier is None:
            continue
        relative = PurePosixPath(path.resolve().relative_to(resolved_root).as_posix())
        previous = result.get(identifier)
        if previous is None:
            result[identifier] = relative
        elif (resolved_root / Path(previous)).read_bytes() != path.read_bytes():
            raise RegistryError(
                f"duplicate $id {identifier!r} names different bytes: {previous} and {relative}",
                rule=RESOURCES_CONFLICTING_BYTES,
                subject=path,
                locus=IDENTIFIER_LOCUS,
            )
    return result


def registry_bytes(registry: Registry) -> bytes:
    """Serialize a registry of format version 2 in stable key order."""
    lines = ["# Generated by kleisis; do not edit by hand.", f"schema_version: {REGISTRY_VERSION}"]
    if registry.schemas:
        lines.append("schemas:")
        for identifier, target in sorted(registry.schemas.items()):
            lines.append(f"  {json.dumps(identifier)}: {json.dumps(target.as_posix())}")
    else:
        lines.append("schemas: {}")
    if registry.exports:
        lines.append("exports:")
        for identifier in sorted(registry.exports):
            lines.append(f"  - {json.dumps(identifier)}")
    else:
        lines.append("exports: []")
    if registry.resources:
        lines.append("resources:")
        for uri, target in sorted(registry.resources.items()):
            lines.append(f"  {json.dumps(uri)}: {json.dumps(target.as_posix())}")
    else:
        lines.append("resources: {}")
    return ("\n".join(lines) + "\n").encode()
