"""Materialize selected contracts and their closure from exact dependency sources."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from kleisis.application.ports import SourceProvider
from kleisis.domain.models import (
    ArtifactSource,
    Dependency,
    GitSource,
    MaterializedDependency,
    ResourceRecord,
    resolve_inside,
)
from kleisis.domain.references import SchemaSource, source_closure
from kleisis.domain.registry import binding_locus, load_registry, schema_identifier
from kleisis.errors import RegistryError
from kleisis.findings import (
    CONTRACT_BINDING_MISMATCH,
    CONTRACT_UNPROVIDED,
    DEPENDENCY_NO_RESOURCES,
    Finding,
    pointer,
)
from kleisis.infrastructure.filesystem import copy_file, digest


def _source_facts(source: GitSource | ArtifactSource) -> dict[str, object]:
    if isinstance(source, GitSource):
        facts: dict[str, object] = {
            "kind": "git",
            "repository": source.repository,
            "commit": source.commit,
        }
        if source.credential_env is not None:
            facts["credential_env"] = source.credential_env
        if source.archive_roots:
            facts["archive_roots"] = [root.as_posix() for root in source.archive_roots]
        return facts
    return {
        "kind": "artifact",
        "locator": source.locator,
        "source_commit": source.source_commit,
        "sha256": source.sha256,
    }


RECEIPT_FORMAT = "kleisis:receipt@1"
"""The format identifier that a receipt declares in its first key ``_schema``."""


def receipt_bytes(dependencies: list[MaterializedDependency], resources: Path) -> bytes:
    """Serialize exact source facts and copied-file digests, with ``_schema`` first.

    No command reads a receipt back: ``check`` compares it by bytes with a fresh build, so a
    receipt in another format reports drift, and ``materialize`` rewrites it.
    """
    value: dict[str, Any] = {"_schema": RECEIPT_FORMAT, "dependencies": []}
    for dependency in sorted(dependencies, key=lambda item: item.id):
        files = [
            {"path": path.as_posix(), "sha256": digest(resources / Path(path))}
            for path in sorted(dependency.files)
        ]
        value["dependencies"].append(
            {
                "id": dependency.id,
                "source": dependency.source,
                "schemas": {
                    identifier: path.as_posix()
                    for identifier, path in sorted(dependency.schemas.items())
                },
                "files": files,
            }
        )
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True).encode()


@dataclass(frozen=True)
class AcquiredDependency:
    """One dependency acquired at its exact state, with its selected entrypoints."""

    dependency: Dependency
    source: SchemaSource
    contracts: dict[str, PurePosixPath]
    entrypoints: tuple[Path, ...]


def acquire_dependency(
    source_provider: SourceProvider,
    record: ResourceRecord,
    dependency_index: int,
    work_root: Path,
) -> AcquiredDependency:
    """Acquire one exact source and check its registry and selected contract bindings.

    A dependency that selects no contract supplies only resources, so its registry must be of
    version 2 or later. A failure of the declaration in the record is raised without subject,
    for the caller to name the record.
    """
    dependency = record.dependencies[dependency_index]
    source_root = work_root / dependency.id
    source_provider.acquire(dependency.source, source_root)
    source_registry_path = resolve_inside(
        source_root,
        dependency.registry,
        f"dependencies[{dependency_index}].registry",
    )
    source_registry = load_registry(source_registry_path)
    if not dependency.contracts and source_registry.schema_version < 2:
        raise RegistryError(
            f"dependency {dependency.id} selects no contract, and its registry of version "
            f"{source_registry.schema_version} supplies no resources",
            rule=DEPENDENCY_NO_RESOURCES,
            locus=pointer("dependencies"),
            discriminator=dependency.id,
        )
    missing = sorted(set(dependency.contracts) - set(source_registry.schemas))
    if missing:
        message = (
            f"dependency {dependency.id} does not provide selected contracts: {', '.join(missing)}"
        )
        raise RegistryError(
            message,
            findings=[
                Finding(
                    rule=CONTRACT_UNPROVIDED,
                    message=f"dependency {dependency.id} does not provide {identifier}",
                    locus=pointer("dependencies"),
                    discriminator=identifier,
                )
                for identifier in missing
            ],
        )
    entrypoints = tuple(
        resolve_inside(
            source_root,
            source_registry.schemas[identifier],
            f"dependency {dependency.id} contract {identifier}",
            subject=source_registry_path,
            locus=pointer("schemas", identifier),
        )
        for identifier in dependency.contracts
    )
    for identifier, entrypoint in zip(dependency.contracts, entrypoints, strict=True):
        declared = schema_identifier(entrypoint, dependency.binding)
        if declared != identifier:
            raise RegistryError(
                f"dependency {dependency.id} registry maps {identifier!r} to a schema "
                f"declaring {declared!r}",
                rule=CONTRACT_BINDING_MISMATCH,
                subject=entrypoint,
                locus=binding_locus(dependency.binding),
            )
    resources = {
        uri: resolve_inside(
            source_root,
            target,
            f"dependency {dependency.id} resource {uri}",
            subject=source_registry_path,
            locus=pointer("resources", uri),
        )
        for uri, target in source_registry.resources.items()
    }
    return AcquiredDependency(
        dependency=dependency,
        source=SchemaSource(name=dependency.id, root=source_root, resources=resources),
        contracts={
            identifier: source_registry.schemas[identifier] for identifier in dependency.contracts
        },
        entrypoints=entrypoints,
    )


def materialize_dependencies(
    acquired: Sequence[AcquiredDependency],
    record: ResourceRecord,
    resources_stage: Path,
    requested: Sequence[tuple[SchemaSource, Path]] = (),
) -> list[MaterializedDependency]:
    """Copy the selected contracts, the requested files, and their closure across sources."""
    sources = [item.source for item in acquired]
    entrypoints = [(item.source, path) for item in acquired for path in item.entrypoints]
    owners = source_closure([*entrypoints, *requested], sources)
    result: list[MaterializedDependency] = []
    for item in acquired:
        source_root = item.source.root.resolve()
        copied: list[PurePosixPath] = []
        for source_path in sorted(path for path, owner in owners.items() if owner is item.source):
            source_relative = PurePosixPath(source_path.relative_to(source_root).as_posix())
            output_relative = PurePosixPath(item.dependency.id) / source_relative
            copy_file(source_path, resources_stage / Path(output_relative))
            copied.append(output_relative)
        result.append(
            MaterializedDependency(
                id=item.dependency.id,
                schemas={
                    identifier: record.paths.resources / PurePosixPath(item.dependency.id) / target
                    for identifier, target in item.contracts.items()
                },
                source=_source_facts(item.dependency.source),
                files=tuple(copied),
            )
        )
    return result
