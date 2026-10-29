"""Parsing and structural validation for resource records."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import yaml

from kleisis.domain.documents import load_document
from kleisis.domain.models import (
    ArtifactSource,
    Dependency,
    GitSource,
    Paths,
    Project,
    ResourceRecord,
    safe_relative_path,
)
from kleisis.errors import RecordError

_FULL_COMMIT = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DEPENDENCY_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_ENVIRONMENT_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")


def _mapping(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise RecordError(f"{field_name} must be a mapping")
    return cast(dict[str, Any], value)


def _sequence(value: object, field_name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RecordError(f"{field_name} must be a sequence")
    return value


def _keys(value: dict[str, Any], allowed: set[str], field_name: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise RecordError(f"{field_name} contains unknown fields: {', '.join(unknown)}")


def _nonempty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RecordError(f"{field_name} must be a non-empty string")
    return value


def _commit(value: object, field_name: str) -> str:
    result = _nonempty_string(value, field_name)
    if not _FULL_COMMIT.fullmatch(result):
        raise RecordError(f"{field_name} must be a full lowercase Git commit")
    return result


def _portable_https_url(value: object, field_name: str) -> str:
    result = _nonempty_string(value, field_name)
    parsed = urlparse(result)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise RecordError(f"{field_name} must be an HTTPS URL without embedded credentials")
    return result


def load_resource_record(path: Path) -> ResourceRecord:
    """Load a resource record and reject every undeclared field."""
    try:
        raw = load_document(path)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise RecordError(f"cannot read resource record {path}: {exc}") from exc
    root = _mapping(raw, "resource record")
    _keys(root, {"format_version", "paths", "exports", "dependencies", "projects"}, "record")
    if root.get("format_version") != 1:
        raise RecordError("format_version must equal 1")

    raw_paths = _mapping(root.get("paths", {}), "paths")
    _keys(raw_paths, {"owned_schemas", "registry", "resources"}, "paths")
    paths = Paths(
        owned_schemas=safe_relative_path(
            raw_paths.get("owned_schemas", "specs/schemas"), "paths.owned_schemas"
        ),
        registry=safe_relative_path(
            raw_paths.get("registry", "specs/schema-registry.yml"), "paths.registry"
        ),
        resources=safe_relative_path(
            raw_paths.get("resources", ".schema-resources"), "paths.resources"
        ),
    )

    exports = tuple(
        _nonempty_string(item, f"exports[{index}]")
        for index, item in enumerate(_sequence(root.get("exports"), "exports"))
    )
    if len(exports) != len(set(exports)):
        raise RecordError("exports contains duplicate identifiers")

    dependencies: list[Dependency] = []
    dependency_ids: set[str] = set()
    for index, item in enumerate(_sequence(root.get("dependencies"), "dependencies")):
        raw_dependency = _mapping(item, f"dependencies[{index}]")
        _keys(raw_dependency, {"id", "source", "registry", "contracts"}, f"dependencies[{index}]")
        dependency_id = _nonempty_string(raw_dependency.get("id"), f"dependencies[{index}].id")
        if not _DEPENDENCY_ID.fullmatch(dependency_id):
            raise RecordError(f"dependencies[{index}].id must be lower kebab-case")
        if dependency_id in dependency_ids:
            raise RecordError(f"duplicate dependency id: {dependency_id}")
        dependency_ids.add(dependency_id)

        raw_source = _mapping(raw_dependency.get("source"), f"dependencies[{index}].source")
        kind = raw_source.get("kind")
        source: GitSource | ArtifactSource
        if kind == "git":
            _keys(
                raw_source,
                {"kind", "repository", "commit", "credential_env", "archive_roots"},
                f"dependencies[{index}].source",
            )
            credential_env = raw_source.get("credential_env")
            if credential_env is not None and (
                not isinstance(credential_env, str)
                or not _ENVIRONMENT_NAME.fullmatch(credential_env)
            ):
                raise RecordError(
                    f"dependencies[{index}].source.credential_env must name an environment variable"
                )
            raw_archive_roots = raw_source.get("archive_roots")
            archive_roots = tuple(
                safe_relative_path(
                    item,
                    f"dependencies[{index}].source.archive_roots[{root_index}]",
                )
                for root_index, item in enumerate(
                    _sequence(
                        raw_archive_roots,
                        f"dependencies[{index}].source.archive_roots",
                    )
                )
            )
            if raw_archive_roots is not None and not archive_roots:
                raise RecordError(
                    f"dependencies[{index}].source.archive_roots must be non-empty when present"
                )
            if len(archive_roots) != len(set(archive_roots)):
                raise RecordError(f"dependencies[{index}].source.archive_roots must be unique")
            if archive_roots != tuple(sorted(archive_roots)):
                raise RecordError(
                    f"dependencies[{index}].source.archive_roots must follow path order"
                )
            source = GitSource(
                kind="git",
                repository=_portable_https_url(
                    raw_source.get("repository"), f"dependencies[{index}].source.repository"
                ),
                commit=_commit(raw_source.get("commit"), f"dependencies[{index}].source.commit"),
                credential_env=credential_env,
                archive_roots=archive_roots,
            )
        elif kind == "artifact":
            _keys(
                raw_source,
                {"kind", "locator", "source_commit", "sha256"},
                f"dependencies[{index}].source",
            )
            digest = _nonempty_string(
                raw_source.get("sha256"), f"dependencies[{index}].source.sha256"
            )
            if not _SHA256.fullmatch(digest):
                raise RecordError(f"dependencies[{index}].source.sha256 must be lowercase SHA-256")
            source = ArtifactSource(
                kind="artifact",
                locator=_portable_https_url(
                    raw_source.get("locator"), f"dependencies[{index}].source.locator"
                ),
                source_commit=_commit(
                    raw_source.get("source_commit"),
                    f"dependencies[{index}].source.source_commit",
                ),
                sha256=digest,
            )
        else:
            raise RecordError(f"dependencies[{index}].source.kind must be git or artifact")

        contracts = tuple(
            _nonempty_string(contract, f"dependencies[{index}].contracts[{contract_index}]")
            for contract_index, contract in enumerate(
                _sequence(raw_dependency.get("contracts"), f"dependencies[{index}].contracts")
            )
        )
        if not contracts or len(contracts) != len(set(contracts)):
            raise RecordError(f"dependencies[{index}].contracts must be non-empty and unique")
        dependencies.append(
            Dependency(
                id=dependency_id,
                source=source,
                registry=safe_relative_path(
                    raw_dependency.get("registry"), f"dependencies[{index}].registry"
                ),
                contracts=contracts,
            )
        )

    projects: list[Project] = []
    project_paths: set[object] = set()
    for index, item in enumerate(_sequence(root.get("projects"), "projects")):
        raw_project = _mapping(item, f"projects[{index}]")
        _keys(raw_project, {"path", "required"}, f"projects[{index}]")
        project_path = safe_relative_path(raw_project.get("path"), f"projects[{index}].path")
        if project_path in project_paths:
            raise RecordError(f"duplicate project path: {project_path}")
        project_paths.add(project_path)
        required = raw_project.get("required", True)
        if not isinstance(required, bool):
            raise RecordError(f"projects[{index}].required must be a boolean")
        projects.append(Project(path=project_path, required=required))

    return ResourceRecord(
        format_version=1,
        paths=paths,
        exports=exports,
        dependencies=tuple(dependencies),
        projects=tuple(projects),
    )
