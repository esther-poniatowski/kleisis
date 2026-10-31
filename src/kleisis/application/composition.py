"""Compose parent registries from explicit child exports."""

from pathlib import Path, PurePosixPath

from kleisis.domain.models import Registry, ResourceRecord, resolve_inside
from kleisis.domain.references import reference_closure
from kleisis.domain.registry import IDENTIFIER_LOCUS, load_registry
from kleisis.errors import RegistryError
from kleisis.findings import (
    CHILD_MISSING_DEPENDENCY,
    CHILD_SHARED_BYTES,
    CONTRACT_DUPLICATE,
    PATH_ABSENT,
    RESOURCES_CONFLICTING_BYTES,
    Finding,
    pointer,
)


def _parent_file(
    project_root: Path, record: ResourceRecord, resources_stage: Path, relative: PurePosixPath
) -> Path:
    """Locate a parent registry target, reading materialized files from the candidate stage."""
    if relative == record.paths.resources or record.paths.resources in relative.parents:
        return resources_stage / Path(relative.relative_to(record.paths.resources))
    return resolve_inside(project_root, relative, f"parent target {relative}")


def compose_registry(
    project_root: Path,
    record: ResourceRecord,
    registry: Registry,
    resources_stage: Path,
    record_path: PurePosixPath,
) -> Registry:
    """Import child exports after checking ownership and shared dependencies.

    ``record_path`` names the resource record, which declares each child project.
    """
    schemas = dict(registry.schemas)
    resources = dict(registry.resources)
    for project in record.projects:
        child_root = resolve_inside(
            project_root,
            project.path,
            "projects.path",
            subject=record_path,
            locus=pointer("projects"),
            discriminator=project.path.as_posix(),
        )
        child_registry_path = child_root / "specs/schema-registry.yml"
        if not child_registry_path.is_file():
            if project.required:
                raise RegistryError(
                    f"required project registry is absent: {child_registry_path}",
                    rule=PATH_ABSENT,
                    subject=record_path,
                    locus=pointer("projects"),
                    discriminator=project.path.as_posix(),
                )
            continue
        child = load_registry(child_registry_path)
        child_exports = [
            resolve_inside(
                child_root,
                child.schemas[identifier],
                f"child export {identifier}",
                subject=child_registry_path,
                locus=pointer("schemas", identifier),
            )
            for identifier in child.exports
        ]
        child_resources = {
            uri: resolve_inside(
                child_root,
                target,
                f"child resource {uri}",
                subject=child_registry_path,
                locus=pointer("resources", uri),
            )
            for uri, target in child.resources.items()
        }
        reference_closure(child_root, child_exports, child_resources)
        child_dependencies = set(child.schemas) - set(child.exports)
        missing = sorted(child_dependencies - set(registry.schemas))
        if missing:
            raise RegistryError(
                f"project {project.path} uses schemas absent from the parent: {', '.join(missing)}",
                findings=[
                    Finding(
                        rule=CHILD_MISSING_DEPENDENCY,
                        message=f"project {project.path} uses {identifier}, absent from the parent",
                        subject=child_registry_path,
                        locus=pointer("schemas", identifier),
                    )
                    for identifier in missing
                ],
            )
        for identifier in sorted(child_dependencies):
            parent_target = _parent_file(
                project_root, record, resources_stage, registry.schemas[identifier]
            )
            child_target = resolve_inside(
                child_root,
                child.schemas[identifier],
                f"child schema {identifier}",
                subject=child_registry_path,
                locus=pointer("schemas", identifier),
            )
            if parent_target.read_bytes() != child_target.read_bytes():
                raise RegistryError(
                    f"shared schema {identifier!r} differs in project {project.path}",
                    rule=CHILD_SHARED_BYTES,
                    subject=child_target,
                )
        for identifier in child.exports:
            if identifier in schemas:
                raise RegistryError(
                    f"duplicate exported schema identifier: {identifier}",
                    rule=CONTRACT_DUPLICATE,
                    subject=child_registry_path,
                    locus=pointer("schemas", identifier),
                )
            child_target = resolve_inside(
                child_root,
                child.schemas[identifier],
                f"child export {identifier}",
                subject=child_registry_path,
                locus=pointer("schemas", identifier),
            )
            if not child_target.is_file():
                raise RegistryError(
                    f"project {project.path} export target is absent: {child.schemas[identifier]}",
                    rule=PATH_ABSENT,
                    subject=child_registry_path,
                    locus=pointer("schemas", identifier),
                )
            schemas[identifier] = project.path / child.schemas[identifier]
        for uri, child_target in sorted(child_resources.items()):
            if not child_target.is_file():
                raise RegistryError(
                    f"project {project.path} resource target is absent: {child.resources[uri]}",
                    rule=PATH_ABSENT,
                    subject=child_registry_path,
                    locus=pointer("resources", uri),
                )
            imported = project.path / child.resources[uri]
            previous = resources.get(uri)
            if previous is None:
                resources[uri] = imported
            elif (
                _parent_file(project_root, record, resources_stage, previous).read_bytes()
                != child_target.read_bytes()
            ):
                raise RegistryError(
                    f"duplicate $id {uri!r} names different bytes: {previous} and {imported}",
                    rule=RESOURCES_CONFLICTING_BYTES,
                    subject=child_target,
                    locus=IDENTIFIER_LOCUS,
                )
    return Registry(schemas=schemas, exports=registry.exports, resources=resources)
