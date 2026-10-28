"""Typed records used by schema-closure."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

from schema_closure.errors import RecordError


def safe_relative_path(value: object, field_name: str) -> PurePosixPath:
    """Return a normalized repository-relative path."""
    if not isinstance(value, str) or not value.strip():
        raise RecordError(f"{field_name} must be a non-empty string")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path == PurePosixPath("."):
        raise RecordError(f"{field_name} must stay inside the repository: {value!r}")
    return path


def resolve_inside(root: Path, relative: PurePosixPath, field_name: str) -> Path:
    """Resolve a relative path and prove that it stays under ``root``."""
    resolved_root = root.resolve()
    resolved = (resolved_root / Path(relative)).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise RecordError(f"{field_name} leaves the repository: {relative}")
    return resolved


@dataclass(frozen=True)
class Paths:
    """Repository paths controlled by the resource record."""

    owned_schemas: PurePosixPath = PurePosixPath("specs/schemas")
    registry: PurePosixPath = PurePosixPath("specs/schema-registry.yml")
    resources: PurePosixPath = PurePosixPath(".schema-resources")


@dataclass(frozen=True)
class GitSource:
    """An exact Git source state."""

    kind: Literal["git"]
    repository: str
    commit: str
    credential_env: str | None = None


@dataclass(frozen=True)
class ArtifactSource:
    """An immutable retained artifact tied to its source commit."""

    kind: Literal["artifact"]
    locator: str
    source_commit: str
    sha256: str


Source = GitSource | ArtifactSource


@dataclass(frozen=True)
class Dependency:
    """A selected set of contracts from one exact external source."""

    id: str
    source: Source
    registry: PurePosixPath
    contracts: tuple[str, ...]


@dataclass(frozen=True)
class Project:
    """A checked-out child repository included in a composite registry."""

    path: PurePosixPath
    required: bool = True


@dataclass(frozen=True)
class ResourceRecord:
    """The complete source declaration for one consumer repository."""

    format_version: int
    paths: Paths
    exports: tuple[str, ...] = ()
    dependencies: tuple[Dependency, ...] = ()
    projects: tuple[Project, ...] = ()


@dataclass(frozen=True)
class Registry:
    """A deterministic logical identifier registry."""

    schemas: dict[str, PurePosixPath]
    exports: tuple[str, ...] = ()


@dataclass(frozen=True)
class MaterializedDependency:
    """Registry entries and source facts produced for one dependency."""

    id: str
    schemas: dict[str, PurePosixPath]
    source: dict[str, str]
    files: tuple[PurePosixPath, ...] = field(default_factory=tuple)
