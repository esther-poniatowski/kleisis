"""Compose parent registries from explicit child exports."""

from pathlib import Path

from kleisis.domain.models import Registry, ResourceRecord, resolve_inside
from kleisis.domain.references import reference_closure
from kleisis.domain.registry import load_registry
from kleisis.errors import RegistryError


def compose_registry(
    project_root: Path,
    record: ResourceRecord,
    registry: Registry,
    resources_stage: Path,
) -> Registry:
    """Import child exports after checking ownership and shared dependencies."""
    schemas = dict(registry.schemas)
    for project in record.projects:
        child_root = resolve_inside(project_root, project.path, "projects.path")
        child_registry_path = child_root / "specs/schema-registry.yml"
        if not child_registry_path.is_file():
            if project.required:
                raise RegistryError(f"required project registry is absent: {child_registry_path}")
            continue
        child = load_registry(child_registry_path)
        child_exports = [
            resolve_inside(child_root, child.schemas[identifier], f"child export {identifier}")
            for identifier in child.exports
        ]
        reference_closure(child_root, child_exports)
        child_dependencies = set(child.schemas) - set(child.exports)
        missing = sorted(child_dependencies - set(registry.schemas))
        if missing:
            raise RegistryError(
                f"project {project.path} uses schemas absent from the parent: {', '.join(missing)}"
            )
        for identifier in sorted(child_dependencies):
            relative_target = registry.schemas[identifier]
            if (
                relative_target == record.paths.resources
                or record.paths.resources in relative_target.parents
            ):
                inside_resources = relative_target.relative_to(record.paths.resources)
                parent_target = resources_stage / Path(inside_resources)
            else:
                parent_target = resolve_inside(
                    project_root, relative_target, f"parent schema {identifier}"
                )
            child_target = resolve_inside(
                child_root, child.schemas[identifier], f"child schema {identifier}"
            )
            if parent_target.read_bytes() != child_target.read_bytes():
                raise RegistryError(
                    f"shared schema {identifier!r} differs in project {project.path}"
                )
        for identifier in child.exports:
            if identifier in schemas:
                raise RegistryError(f"duplicate exported schema identifier: {identifier}")
            child_target = resolve_inside(
                child_root, child.schemas[identifier], f"child export {identifier}"
            )
            if not child_target.is_file():
                raise RegistryError(
                    f"project {project.path} export target is absent: {child.schemas[identifier]}"
                )
            schemas[identifier] = project.path / child.schemas[identifier]
    return Registry(schemas=schemas, exports=registry.exports)
