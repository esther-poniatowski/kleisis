"""Materialize selected contracts from one exact dependency source."""

from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from kleisis.application.ports import SourceProvider
from kleisis.domain.models import (
    ArtifactSource,
    GitSource,
    MaterializedDependency,
    ResourceRecord,
    resolve_inside,
)
from kleisis.domain.references import reference_closure
from kleisis.domain.registry import load_registry, schema_identifier
from kleisis.errors import RegistryError
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


def receipt_bytes(dependencies: list[MaterializedDependency], resources: Path) -> bytes:
    """Serialize exact source facts and copied-file digests."""
    value: dict[str, Any] = {"format_version": 1, "dependencies": []}
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


def materialize_dependency(
    source_provider: SourceProvider,
    record: ResourceRecord,
    dependency_index: int,
    resources_stage: Path,
    work_root: Path,
) -> MaterializedDependency:
    """Acquire and copy the selected contracts plus their relative-reference closure."""
    dependency = record.dependencies[dependency_index]
    source_root = work_root / dependency.id
    source_provider.acquire(dependency.source, source_root)
    source_registry_path = resolve_inside(
        source_root, dependency.registry, f"dependencies[{dependency_index}].registry"
    )
    source_registry = load_registry(source_registry_path)
    missing = sorted(set(dependency.contracts) - set(source_registry.schemas))
    if missing:
        raise RegistryError(
            f"dependency {dependency.id} does not provide selected contracts: {', '.join(missing)}"
        )
    entrypoints = [
        resolve_inside(
            source_root,
            source_registry.schemas[identifier],
            f"dependency {dependency.id} contract {identifier}",
        )
        for identifier in dependency.contracts
    ]
    for identifier, entrypoint in zip(dependency.contracts, entrypoints, strict=True):
        declared = schema_identifier(entrypoint)
        if declared != identifier:
            raise RegistryError(
                f"dependency {dependency.id} registry maps {identifier!r} to a schema "
                f"declaring {declared!r}"
            )
    closure = reference_closure(source_root, entrypoints)
    copied: list[PurePosixPath] = []
    for source_path in sorted(closure):
        source_relative = PurePosixPath(source_path.relative_to(source_root).as_posix())
        output_relative = PurePosixPath(dependency.id) / source_relative
        copy_file(source_path, resources_stage / Path(output_relative))
        copied.append(output_relative)
    schemas = {
        identifier: record.paths.resources
        / PurePosixPath(dependency.id)
        / source_registry.schemas[identifier]
        for identifier in dependency.contracts
    }
    return MaterializedDependency(
        id=dependency.id,
        schemas=schemas,
        source=_source_facts(dependency.source),
        files=tuple(copied),
    )
