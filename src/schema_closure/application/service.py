"""Coordinate exact materialization and deterministic registry generation."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from schema_closure.application.composition import compose_registry
from schema_closure.application.materialization import materialize_dependency, receipt_bytes
from schema_closure.application.ports import SourceProvider
from schema_closure.domain.models import (
    MaterializedDependency,
    Registry,
    ResourceRecord,
    resolve_inside,
)
from schema_closure.domain.records import load_resource_record
from schema_closure.domain.references import reference_closure
from schema_closure.domain.registry import (
    discover_owned_schemas,
    registry_bytes,
)
from schema_closure.errors import DriftError, RegistryError
from schema_closure.infrastructure.filesystem import copy_tree, tree_manifest
from schema_closure.infrastructure.publication import publish_pair, recover_transaction
from schema_closure.infrastructure.sources import ExactSourceProvider

_RECEIPT = "receipt.yml"
_DEFAULT_RECORD = PurePosixPath("specs/schema-resources.yml")


@dataclass(frozen=True)
class CandidateState:
    """A complete unpublished resources directory and registry."""

    resources: Path
    registry: Registry


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
        stage = Path(tempfile.mkdtemp(prefix="schema-closure-stage-", dir=parent))
        try:
            resources_stage = stage / "resources"
            resources_stage.mkdir()
            work_root = stage / "sources"
            work_root.mkdir()
            materialized: list[MaterializedDependency] = []
            external: dict[str, PurePosixPath] = {}
            for index in range(len(record.dependencies)):
                result = materialize_dependency(
                    self.source_provider, record, index, resources_stage, work_root
                )
                duplicates = sorted(set(external) & set(result.schemas))
                if duplicates:
                    raise RegistryError(
                        "dependencies provide duplicate schema identifiers: "
                        f"{', '.join(duplicates)}"
                    )
                external.update(result.schemas)
                materialized.append(result)
            owner_conflicts = sorted(set(owned) & set(external))
            if owner_conflicts:
                raise RegistryError(
                    "owned and external schemas use the same identifiers: "
                    f"{', '.join(owner_conflicts)}"
                )
            (resources_stage / _RECEIPT).write_bytes(receipt_bytes(materialized, resources_stage))
            local = Registry(schemas={**owned, **external}, exports=record.exports)
            validation_root = stage / "validation"
            copy_tree(owned_root, validation_root / Path(record.paths.owned_schemas))
            copy_tree(resources_stage, validation_root / Path(record.paths.resources))
            exported_targets = [
                validation_root / Path(owned[identifier]) for identifier in record.exports
            ]
            reference_closure(validation_root, exported_targets)
            composite = compose_registry(root, record, local, resources_stage)
            shutil.rmtree(work_root)
            shutil.rmtree(validation_root)
            return CandidateState(resources=resources_stage, registry=composite)
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise

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
