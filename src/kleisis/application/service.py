"""Coordinate exact materialization and deterministic registry generation."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from kleisis.application.composition import compose_registry
from kleisis.application.materialization import (
    AcquiredDependency,
    acquire_dependency,
    materialize_dependencies,
    receipt_bytes,
)
from kleisis.application.ports import SourceProvider
from kleisis.domain.models import (
    MaterializedDependency,
    Registry,
    ResourceRecord,
    resolve_inside,
)
from kleisis.domain.records import load_resource_record
from kleisis.domain.references import SchemaSource, source_closure
from kleisis.domain.registry import (
    candidate_schemas,
    discover_owned_schemas,
    registry_bytes,
    resource_map,
)
from kleisis.errors import DriftError, RegistryError
from kleisis.infrastructure.filesystem import copy_tree, tree_manifest
from kleisis.infrastructure.publication import publish_pair, recover_transaction
from kleisis.infrastructure.sources import ExactSourceProvider

_RECEIPT = "receipt.yml"
_DEFAULT_RECORD = PurePosixPath("specs/schema-resources.yml")


@dataclass(frozen=True)
class CandidateState:
    """A complete unpublished resources directory and registry."""

    resources: Path
    registry: Registry


@dataclass(frozen=True)
class ConsumerClosure:
    """The files loaded from the owned schemas of a candidate, with the source of each."""

    consumer: SchemaSource
    owners: dict[Path, SchemaSource]
    listed_files: tuple[Path, ...]


def _paths_overlap(first: PurePosixPath, second: PurePosixPath) -> bool:
    return first == second or first in second.parents or second in first.parents


class SchemaClosureService:
    """Build, publish, and verify the schema closure of one repository."""

    def __init__(
        self,
        record_path: PurePosixPath = _DEFAULT_RECORD,
        source_provider: SourceProvider | None = None,
    ):
        self.record_path = record_path
        self.source_provider = source_provider or ExactSourceProvider()

    def load(self, project_root: Path) -> ResourceRecord:
        """Load the resource record selected for a project root."""
        record = load_resource_record(resolve_inside(project_root, self.record_path, "record path"))
        protected = {
            "paths.owned_schemas": record.paths.owned_schemas,
            "paths.registry": record.paths.registry,
            "record path": self.record_path,
        }
        for name, path in protected.items():
            if _paths_overlap(record.paths.resources, path):
                raise RegistryError(f"paths.resources overlaps {name}: {path}")
        if record.paths.registry == self.record_path:
            raise RegistryError("paths.registry must differ from the resource record")
        for project in record.projects:
            if _paths_overlap(record.paths.resources, project.path):
                raise RegistryError(f"paths.resources overlaps project checkout: {project.path}")
        return record

    def build(self, project_root: Path, staging_parent: Path | None = None) -> CandidateState:
        """Build a complete candidate state without modifying the project."""
        root = project_root.resolve()
        record = self.load(root)
        owned_root = resolve_inside(root, record.paths.owned_schemas, "paths.owned_schemas")
        owned = discover_owned_schemas(root, owned_root)
        undeclared_exports = sorted(set(record.exports) - set(owned))
        if undeclared_exports:
            raise RegistryError(
                f"exports are not owned by this repository: {', '.join(undeclared_exports)}"
            )
        unexported_contracts = sorted(set(owned) - set(record.exports))
        if unexported_contracts:
            raise RegistryError(
                f"owned contracts are absent from exports: {', '.join(unexported_contracts)}"
            )

        parent = staging_parent or root.parent
        stage = Path(tempfile.mkdtemp(prefix="kleisis-stage-", dir=parent))
        try:
            resources_stage = stage / "resources"
            work_root = stage / "sources"
            work_root.mkdir()
            acquired = [
                acquire_dependency(self.source_provider, record, index, work_root)
                for index in range(len(record.dependencies))
            ]
            validation_root = stage / "validation"
            requested: list[tuple[SchemaSource, Path]] = []
            while True:
                materialized, external = self._materialize(
                    acquired, record, resources_stage, requested
                )
                owner_conflicts = sorted(set(owned) & set(external))
                if owner_conflicts:
                    raise RegistryError(
                        "owned and external schemas use the same identifiers: "
                        f"{', '.join(owner_conflicts)}"
                    )
                closure = self._close_consumer(
                    root, record, owned, materialized, acquired, resources_stage, validation_root
                )
                reached = [
                    (source, path)
                    for path, source in closure.owners.items()
                    if source is not closure.consumer
                ]
                if not reached:
                    break
                requested.extend(reached)
            self._reject_unused(acquired, materialized)
            consumer_files = sorted(
                path for path, source in closure.owners.items() if source is closure.consumer
            )
            resources = resource_map(validation_root, [*closure.listed_files, *consumer_files])
            (resources_stage / _RECEIPT).write_bytes(receipt_bytes(materialized, resources_stage))
            local = Registry(
                schemas={**owned, **external}, exports=record.exports, resources=resources
            )
            composite = compose_registry(root, record, local, resources_stage)
            shutil.rmtree(work_root)
            shutil.rmtree(validation_root)
            return CandidateState(resources=resources_stage, registry=composite)
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise

    @staticmethod
    def _close_consumer(
        root: Path,
        record: ResourceRecord,
        owned: dict[str, PurePosixPath],
        materialized: list[MaterializedDependency],
        acquired: list[AcquiredDependency],
        resources_stage: Path,
        validation_root: Path,
    ) -> ConsumerClosure:
        """Close the owned schemas inside a copy of the candidate repository.

        Absolute references resolve through the resources of owned and materialized files, and
        otherwise through the declared dependencies. A file reached in a dependency source is
        absent from the candidate, so the caller materializes it and closes again.
        """
        shutil.rmtree(validation_root, ignore_errors=True)
        owned_root = resolve_inside(root, record.paths.owned_schemas, "paths.owned_schemas")
        copy_tree(owned_root, validation_root / Path(record.paths.owned_schemas))
        copy_tree(resources_stage, validation_root / Path(record.paths.resources))
        owned_files = sorted(
            validation_root / path.relative_to(root) for path in candidate_schemas(owned_root)
        )
        materialized_files = sorted(
            validation_root / Path(record.paths.resources) / Path(path)
            for dependency in materialized
            for path in dependency.files
        )
        listed_files = [*owned_files, *materialized_files]
        resources = {
            uri: validation_root / Path(path)
            for uri, path in resource_map(validation_root, listed_files).items()
        }
        consumer = SchemaSource(name="", root=validation_root, resources=resources)
        resource_files = set(resources.values())
        entrypoints = [validation_root / Path(owned[identifier]) for identifier in record.exports]
        entrypoints += [path for path in owned_files if path in resource_files]
        owners = source_closure(
            [(consumer, path) for path in entrypoints], [item.source for item in acquired]
        )
        return ConsumerClosure(consumer=consumer, owners=owners, listed_files=tuple(listed_files))

    @staticmethod
    def _reject_unused(
        acquired: list[AcquiredDependency], materialized: list[MaterializedDependency]
    ) -> None:
        """Fail when a dependency that supplies only resources serves no reference."""
        for item, result in zip(acquired, materialized, strict=True):
            if not item.dependency.contracts and not result.files:
                raise RegistryError(
                    f"dependency {item.dependency.id} selects no contract, and no reference "
                    "of the closure reaches its resources; remove the unused dependency"
                )

    @staticmethod
    def _materialize(
        acquired: list[AcquiredDependency],
        record: ResourceRecord,
        resources_stage: Path,
        requested: list[tuple[SchemaSource, Path]],
    ) -> tuple[list[MaterializedDependency], dict[str, PurePosixPath]]:
        """Rebuild the resources directory from the selected and requested files."""
        shutil.rmtree(resources_stage, ignore_errors=True)
        resources_stage.mkdir()
        materialized = materialize_dependencies(acquired, record, resources_stage, requested)
        external: dict[str, PurePosixPath] = {}
        for result in materialized:
            duplicates = sorted(set(external) & set(result.schemas))
            if duplicates:
                raise RegistryError(
                    f"dependencies provide duplicate schema identifiers: {', '.join(duplicates)}"
                )
            external.update(result.schemas)
        return materialized, external

    def materialize(self, project_root: Path) -> None:
        """Publish a freshly built closure and registry."""
        root = project_root.resolve()
        record = self.load(root)
        resources_target = resolve_inside(root, record.paths.resources, "paths.resources")
        registry_target = resolve_inside(root, record.paths.registry, "paths.registry")
        recover_transaction(root, {resources_target, registry_target})
        candidate = self.build(root)
        registry_candidate = candidate.resources.parent / "schema-registry.yml"
        registry_candidate.write_bytes(registry_bytes(candidate.registry))
        try:
            publish_pair(
                root,
                [(candidate.resources, resources_target), (registry_candidate, registry_target)],
            )
        finally:
            shutil.rmtree(candidate.resources.parent, ignore_errors=True)

    def check(self, project_root: Path) -> None:
        """Fail when a fresh candidate differs from the published project state."""
        root = project_root.resolve()
        record = self.load(root)
        candidate = self.build(root, staging_parent=Path(tempfile.gettempdir()).resolve())
        resources_target = resolve_inside(root, record.paths.resources, "paths.resources")
        registry_target = resolve_inside(root, record.paths.registry, "paths.registry")
        problems: list[str] = []
        try:
            expected_resources = tree_manifest(candidate.resources)
            try:
                actual_resources = tree_manifest(resources_target)
            except DriftError as exc:
                problems.append(str(exc))
            else:
                if actual_resources != expected_resources:
                    problems.append("materialized schema resources differ from a fresh build")
            expected_registry = registry_bytes(candidate.registry)
            if not registry_target.is_file():
                problems.append(f"generated registry is absent: {registry_target}")
            elif registry_target.read_bytes() != expected_registry:
                problems.append("generated schema registry differs from a fresh build")
        finally:
            shutil.rmtree(candidate.resources.parent, ignore_errors=True)
        if problems:
            raise DriftError("; ".join(problems))

    def validate(self, project_root: Path) -> None:
        """Validate the record and build all outputs without publishing them."""
        candidate = self.build(
            project_root.resolve(), staging_parent=Path(tempfile.gettempdir()).resolve()
        )
        shutil.rmtree(candidate.resources.parent, ignore_errors=True)
