"""Typed records used by kleisis."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePath, PurePosixPath
from typing import Literal

from kleisis.errors import RecordError
from kleisis.findings import PATH_ESCAPE, field_pointer


def safe_relative_path(value: object, field_name: str) -> PurePosixPath:
    """Return a normalized repository-relative path."""
    if not isinstance(value, str) or not value.strip():
        raise RecordError(
            f"{field_name} must be a non-empty string", locus=field_pointer(field_name)
        )
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path == PurePosixPath("."):
        raise RecordError(
            f"{field_name} must stay inside the repository: {value!r}",
            rule=PATH_ESCAPE,
            locus=field_pointer(field_name),
        )
    return path


def resolve_inside(
    root: Path,
    relative: PurePosixPath,
    field_name: str,
    *,
    subject: PurePath | None = None,
    locus: str = "",
    discriminator: str = "",
) -> Path:
    """Resolve a relative path and prove that it stays under ``root``.

    ``subject``, ``locus`` and ``discriminator`` name the declaration of the path, which the
    failure reports.
    """
    resolved_root = root.resolve()
    resolved = (resolved_root / Path(relative)).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise RecordError(
            f"{field_name} leaves the repository: {relative}",
            rule=PATH_ESCAPE,
            subject=subject,
            locus=locus,
            discriminator=discriminator,
        )
    return resolved


DEFAULT_SCHEMA_SUFFIXES = (".schema.yaml", ".schema.yml", ".schema.json")
"""The name endings that mark a schema file unless a resource record declares others."""
DEFAULT_BINDING = "_schema"
"""The root property that binds a record to its contract unless a record declares another."""


@dataclass(frozen=True)
class SchemaConvention:
    """The names by which a source marks its schema files and its contracts.

    A file is a schema candidate when its name ends with one of ``suffixes``. A candidate is a
    contract when its root declares the property ``binding``, fixed by ``const`` to the contract
    identifier, and requires it.
    """

    suffixes: tuple[str, ...] = DEFAULT_SCHEMA_SUFFIXES
    binding: str = DEFAULT_BINDING


DEFAULT_CONVENTION = SchemaConvention()
"""The schema convention of a resource record that declares none."""


DEFAULT_STATE = PurePosixPath(".kleisis")
"""The state folder of a resource record that declares none."""
RESOURCES_IN_STATE = "resources"
"""The name of the resources folder inside the state folder, unless the record declares one."""
FORMER_RESOURCES = PurePosixPath(".schema-resources")
"""The default resources folder of format 1, which a journal of an earlier release may name."""


@dataclass(frozen=True)
class Paths:
    """Repository paths controlled by the resource record.

    The state folder holds what Kleisis alone reads: the resources folder by default, the
    publication journal and its backups. The defaults are those of a record that declares no
    path.
    """

    owned_schemas: PurePosixPath = PurePosixPath("specs/schemas")
    registry: PurePosixPath = PurePosixPath("specs/schema-registry.yml")
    resources: PurePosixPath = DEFAULT_STATE / RESOURCES_IN_STATE
    state: PurePosixPath = DEFAULT_STATE


@dataclass(frozen=True)
class GitSource:
    """An exact Git source state."""

    kind: Literal["git"]
    repository: str
    commit: str
    credential_env: str | None = None
    archive_roots: tuple[PurePosixPath, ...] = ()


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
    """One exact external source, with its selected contracts.

    A dependency with no contract supplies only the ``resources`` map of its registry.
    """

    id: str
    source: Source
    registry: PurePosixPath
    contracts: tuple[str, ...]
    binding: str = DEFAULT_BINDING


@dataclass(frozen=True)
class Project:
    """A checked-out child repository included in a composite registry."""

    path: PurePosixPath
    required: bool = True


@dataclass(frozen=True)
class ResourceRecord:
    """The complete source declaration for one consumer repository.

    ``format_version`` holds the major version of the format ``kleisis:schema-resources``
    that the record declares in its key ``_schema``.
    """

    format_version: int
    paths: Paths
    schemas: SchemaConvention = DEFAULT_CONVENTION
    exports: tuple[str, ...] = ()
    dependencies: tuple[Dependency, ...] = ()
    projects: tuple[Project, ...] = ()


@dataclass(frozen=True)
class Registry:
    """A deterministic registry of logical identifiers and absolute schema identifiers.

    ``schema_version`` records the layout read: 1 for a legacy registry without ``resources``,
    2 for a registry declaring ``_schema: kleisis:schema-registry@2`` or the legacy
    ``schema_version: 2``. The serializer always writes the current format.
    """

    schemas: dict[str, PurePosixPath]
    exports: tuple[str, ...] = ()
    resources: dict[str, PurePosixPath] = field(default_factory=dict)
    schema_version: int = 2


@dataclass(frozen=True)
class MaterializedDependency:
    """Registry entries and source facts produced for one dependency."""

    id: str
    schemas: dict[str, PurePosixPath]
    source: dict[str, object]
    files: tuple[PurePosixPath, ...] = field(default_factory=tuple)
