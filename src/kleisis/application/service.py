"""Coordinate exact materialization and deterministic registry generation."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
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
    binding_locus,
    candidate_schemas,
    discover_owned_schemas,
    registry_bytes,
    resource_map,
)
from kleisis.errors import DriftError, RegistryError, SchemaClosureError
from kleisis.findings import (
    CONTRACT_DUPLICATE,
    CONTRACT_UNEXPORTED,
    CONTRACT_UNOWNED_EXPORT,
    DEPENDENCY_UNUSED,
    DRIFT_REGISTRY,
    DRIFT_RESOURCES,
    RECORD_PATH_OVERLAP,
    Finding,
    complete,
    pointer,
    relocate,
)
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


@contextmanager
def _relocated(roots: Sequence[tuple[Path, PurePosixPath]]) -> Iterator[None]:
    """Report the subject of each failure below one of ``roots`` by its repository path."""
    ordered = sorted(
        ((base.resolve(), target) for base, target in roots),
        key=lambda root: len(root[0].parts),
        reverse=True,
    )
    try:
        yield
    except SchemaClosureError as exc:
        exc.map_findings(lambda finding: relocate(finding, ordered))
        raise


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
        record = load_resource_record(
            resolve_inside(project_root, self.record_path, "record path", subject=self.record_path)
        )
        protected = {
            "paths.owned_schemas": record.paths.owned_schemas,
            "paths.registry": record.paths.registry,
            "record path": self.record_path,
        }
        for name, path in protected.items():
            if _paths_overlap(record.paths.resources, path):
                raise RegistryError(
                    f"paths.resources overlaps {name}: {path}",
                    rule=RECORD_PATH_OVERLAP,
                    subject=self.record_path,
                    locus=pointer("paths", "resources"),
                    discriminator=path.as_posix(),
                )
        if record.paths.registry == self.record_path:
            raise RegistryError(
                "paths.registry must differ from the resource record",
                rule=RECORD_PATH_OVERLAP,
                subject=self.record_path,
                locus=pointer("paths", "registry"),
            )
        for project in record.projects:
            if _paths_overlap(record.paths.resources, project.path):
                raise RegistryError(
                    f"paths.resources overlaps project checkout: {project.path}",
                    rule=RECORD_PATH_OVERLAP,
                    subject=self.record_path,
                    locus=pointer("paths", "resources"),
                    discriminator=project.path.as_posix(),
                )
        return record

    @contextmanager
    def _reported(self, project_root: Path) -> Iterator[None]:
        """Give each failure of a command a repository-relative subject."""
        try:
            with _relocated([(project_root, PurePosixPath("."))]):
                yield
        except SchemaClosureError as exc:
            exc.map_findings(lambda finding: complete(finding, self.record_path))
            raise

    def build(self, project_root: Path, staging_parent: Path | None = None) -> CandidateState:
        """Build a complete candidate state without modifying the project."""
        root = project_root.resolve()
        record = self.load(root)
        owned_root = resolve_inside(
            root,
            record.paths.owned_schemas,
            "paths.owned_schemas",
            subject=self.record_path,
            locus=pointer("paths", "owned_schemas"),
        )
        try:
            owned = discover_owned_schemas(root, owned_root, record.schemas)
        except SchemaClosureError as exc:
            exc.locate(self.record_path)
            raise
        undeclared_exports = sorted(set(record.exports) - set(owned))
        if undeclared_exports:
            raise RegistryError(
                f"exports are not owned by this repository: {', '.join(undeclared_exports)}",
                findings=[
                    Finding(
                        rule=CONTRACT_UNOWNED_EXPORT,
                        message=f"export {identifier} is not owned by this repository",
                        subject=self.record_path,
                        locus=pointer("exports"),
                        discriminator=identifier,
                    )
                    for identifier in undeclared_exports
                ],
            )
        unexported_contracts = sorted(set(owned) - set(record.exports))
        if unexported_contracts:
            raise RegistryError(
                f"owned contracts are absent from exports: {', '.join(unexported_contracts)}",
                findings=[
                    Finding(
                        rule=CONTRACT_UNEXPORTED,
                        message=f"owned contract {identifier} is absent from exports",
                        subject=owned[identifier],
                        locus=binding_locus(record.schemas.binding),
                    )
                    for identifier in unexported_contracts
                ],
            )

        parent = staging_parent or root.parent
        stage = Path(tempfile.mkdtemp(prefix="kleisis-stage-", dir=parent))
        resources_stage = stage / "resources"
        work_root = stage / "sources"
        validation_root = stage / "validation"
        staging_roots = [
            (resources_stage, record.paths.resources),
            (validation_root, PurePosixPath(".")),
            *(
                (work_root / dependency.id, record.paths.resources / dependency.id)
                for dependency in record.dependencies
            ),
        ]
        try:
            with _relocated(staging_roots):
                return self._build_in(
                    root, record, owned, work_root, resources_stage, validation_root
                )
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise

    def _build_in(
        self,
        root: Path,
        record: ResourceRecord,
        owned: dict[str, PurePosixPath],
        work_root: Path,
        resources_stage: Path,
        validation_root: Path,
    ) -> CandidateState:
        """Build the candidate state inside the staging directories."""
        work_root.mkdir()
        acquired = [
            self._acquire(record, index, work_root) for index in range(len(record.dependencies))
        ]
        requested: list[tuple[SchemaSource, Path]] = []
        while True:
            materialized, external = self._materialize(acquired, record, resources_stage, requested)
            owner_conflicts = sorted(set(owned) & set(external))
            if owner_conflicts:
                raise RegistryError(
                    "owned and external schemas use the same identifiers: "
                    f"{', '.join(owner_conflicts)}",
                    findings=[
                        Finding(
                            rule=CONTRACT_DUPLICATE,
                            message=f"owned and external schemas both provide {identifier}",
                            subject=owned[identifier],
                            locus=binding_locus(record.schemas.binding),
                        )
                        for identifier in owner_conflicts
                    ],
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
        local = Registry(schemas={**owned, **external}, exports=record.exports, resources=resources)
        composite = compose_registry(root, record, local, resources_stage, self.record_path)
        shutil.rmtree(work_root)
        shutil.rmtree(validation_root)
        return CandidateState(resources=resources_stage, registry=composite)

    def _acquire(self, record: ResourceRecord, index: int, work_root: Path) -> AcquiredDependency:
        """Acquire one dependency, reporting a failure of its declaration against the record."""
        try:
            return acquire_dependency(self.source_provider, record, index, work_root)
        except SchemaClosureError as exc:
            exc.locate(self.record_path, pointer("dependencies"), record.dependencies[index].id)
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
            validation_root / path.relative_to(root)
            for path in candidate_schemas(owned_root, record.schemas.suffixes)
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

    def _reject_unused(
        self, acquired: list[AcquiredDependency], materialized: list[MaterializedDependency]
    ) -> None:
        """Fail when a dependency that supplies only resources serves no reference."""
        for item, result in zip(acquired, materialized, strict=True):
            if not item.dependency.contracts and not result.files:
                raise RegistryError(
                    f"dependency {item.dependency.id} selects no contract, and no reference "
                    "of the closure reaches its resources; remove the unused dependency",
                    rule=DEPENDENCY_UNUSED,
                    subject=self.record_path,
                    locus=pointer("dependencies"),
                    discriminator=item.dependency.id,
                )

    def _materialize(
        self,
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
                    f"dependencies provide duplicate schema identifiers: {', '.join(duplicates)}",
                    findings=[
                        Finding(
                            rule=CONTRACT_DUPLICATE,
                            message=f"dependencies provide {identifier} more than once",
                            subject=self.record_path,
                            locus=pointer("dependencies"),
                            discriminator=identifier,
                        )
                        for identifier in duplicates
                    ],
                )
            external.update(result.schemas)
        return materialized, external

    def materialize(self, project_root: Path) -> None:
        """Publish a freshly built closure and registry."""
        root = project_root.resolve()
        with self._reported(root):
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
                    [
                        (candidate.resources, resources_target),
                        (registry_candidate, registry_target),
                    ],
                )
            finally:
                shutil.rmtree(candidate.resources.parent, ignore_errors=True)

    def check(self, project_root: Path) -> None:
        """Fail when a fresh candidate differs from the published project state.

        The resources directory and the registry are compared independently, so the failure
        holds one finding per output that differs.
        """
        root = project_root.resolve()
        with self._reported(root):
            record = self.load(root)
            candidate = self.build(root, staging_parent=Path(tempfile.gettempdir()).resolve())
            resources_target = resolve_inside(root, record.paths.resources, "paths.resources")
            registry_target = resolve_inside(root, record.paths.registry, "paths.registry")
            problems: list[Finding] = []
            try:
                expected_resources = tree_manifest(candidate.resources)
                try:
                    actual_resources = tree_manifest(resources_target)
                except DriftError as exc:
                    problems.extend(exc.findings)
                else:
                    if actual_resources != expected_resources:
                        problems.append(
                            Finding(
                                rule=DRIFT_RESOURCES,
                                message="materialized schema resources differ from a fresh build",
                                subject=record.paths.resources,
                                folder=True,
                            )
                        )
                expected_registry = registry_bytes(candidate.registry)
                if not registry_target.is_file():
                    problems.append(
                        Finding(
                            rule=DRIFT_REGISTRY,
                            message=f"generated registry is absent: {registry_target}",
                            subject=record.paths.registry,
                        )
                    )
                elif registry_target.read_bytes() != expected_registry:
                    problems.append(
                        Finding(
                            rule=DRIFT_REGISTRY,
                            message="generated schema registry differs from a fresh build",
                            subject=record.paths.registry,
                        )
                    )
            finally:
                shutil.rmtree(candidate.resources.parent, ignore_errors=True)
            if problems:
                raise DriftError(
                    "; ".join(problem.message for problem in problems), findings=problems
                )

    def validate(self, project_root: Path) -> None:
        """Validate the record and build all outputs without publishing them."""
        root = project_root.resolve()
        with self._reported(root):
            candidate = self.build(root, staging_parent=Path(tempfile.gettempdir()).resolve())
            shutil.rmtree(candidate.resources.parent, ignore_errors=True)
