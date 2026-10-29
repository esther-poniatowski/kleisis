"""Integration tests for exact materialization and drift detection."""

import shutil
import tempfile
from pathlib import Path

import pytest
from conftest import CopySourceProvider, concrete_schema

from kleisis.application.service import SchemaClosureService
from kleisis.domain.models import Source
from kleisis.domain.registry import load_registry
from kleisis.errors import DriftError, ReferenceError, RegistryError
from kleisis.infrastructure.publication import publish_pair


def make_consumer(root: Path, commit: str) -> None:
    """Create a consumer that owns one schema and selects one exact dependency."""
    schemas = root / "specs/schemas"
    schemas.mkdir(parents=True)
    (schemas / "owned.schema.yaml").write_text(
        concrete_schema("consumer:owned@1"), encoding="utf-8"
    )
    (root / "specs/schema-resources.yml").write_text(
        f"""format_version: 1
exports:
  - consumer:owned@1
dependencies:
  - id: shared
    source:
      kind: git
      repository: https://example.invalid/source.git
      commit: {commit}
    registry: specs/schema-registry.yml
    contracts:
      - source:main@1
""",
        encoding="utf-8",
    )


def test_materialize_publishes_closure_and_registry(
    tmp_path: Path, exact_source: tuple[Path, str]
) -> None:
    source, commit = exact_source
    consumer = tmp_path / "consumer"
    make_consumer(consumer, commit)
    service = SchemaClosureService(source_provider=CopySourceProvider(source))

    service.materialize(consumer)

    assert (consumer / ".schema-resources/shared/schemas/main.schema.yaml").is_file()
    assert (consumer / ".schema-resources/shared/schemas/defs.schema.yaml").is_file()
    registry = load_registry(consumer / "specs/schema-registry.yml")
    assert registry.exports == ("consumer:owned@1",)
    assert registry.schemas["source:main@1"].as_posix() == (
        ".schema-resources/shared/schemas/main.schema.yaml"
    )
    service.check(consumer)


def test_check_detects_resource_drift(tmp_path: Path, exact_source: tuple[Path, str]) -> None:
    source, commit = exact_source
    consumer = tmp_path / "consumer"
    make_consumer(consumer, commit)
    service = SchemaClosureService(source_provider=CopySourceProvider(source))
    service.materialize(consumer)
    target = consumer / ".schema-resources/shared/schemas/main.schema.yaml"
    target.write_text(target.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")

    with pytest.raises(DriftError, match="resources differ"):
        service.check(consumer)


def test_resource_path_cannot_overlap_sources(tmp_path: Path) -> None:
    root = tmp_path / "consumer"
    (root / "specs/schemas").mkdir(parents=True)
    (root / "specs/schema-resources.yml").write_text(
        """format_version: 1
paths:
  resources: specs
""",
        encoding="utf-8",
    )

    with pytest.raises(RegistryError, match="overlaps"):
        SchemaClosureService().load(root)


def test_selected_registry_key_must_match_schema_binding(
    tmp_path: Path, exact_source: tuple[Path, str]
) -> None:
    source, commit = exact_source
    schema = source / "schemas/main.schema.yaml"
    schema.write_text(
        schema.read_text(encoding="utf-8").replace("source:main@1", "source:other@1"),
        encoding="utf-8",
    )
    consumer = tmp_path / "consumer"
    make_consumer(consumer, commit)
    service = SchemaClosureService(source_provider=CopySourceProvider(source))

    with pytest.raises(RegistryError, match="declaring 'source:other@1'"):
        service.validate(consumer)


def test_exported_schema_closure_must_be_present(tmp_path: Path) -> None:
    root = tmp_path / "consumer"
    schemas = root / "specs/schemas"
    schemas.mkdir(parents=True)
    (schemas / "owned.schema.yaml").write_text(
        concrete_schema("consumer:owned@1", reference="missing.schema.yaml"),
        encoding="utf-8",
    )
    (root / "specs/schema-resources.yml").write_text(
        "format_version: 1\nexports: [consumer:owned@1]\n", encoding="utf-8"
    )

    with pytest.raises(ReferenceError, match="not a regular source file"):
        SchemaClosureService().validate(root)


def test_every_owned_contract_must_be_exported(tmp_path: Path) -> None:
    root = tmp_path / "consumer"
    schemas = root / "specs/schemas"
    schemas.mkdir(parents=True)
    (schemas / "exported.schema.yaml").write_text(
        concrete_schema("consumer:exported@1"), encoding="utf-8"
    )
    (schemas / "hidden.schema.yaml").write_text(
        concrete_schema("consumer:hidden@1"), encoding="utf-8"
    )
    (root / "specs/schema-resources.yml").write_text(
        "format_version: 1\nexports: [consumer:exported@1]\n", encoding="utf-8"
    )

    with pytest.raises(RegistryError, match="owned contracts are absent from exports"):
        SchemaClosureService().validate(root)


class FailingProvider:
    """Fail after staging begins."""

    def acquire(self, source: Source, destination: Path) -> None:
        del source, destination
        raise RegistryError("planted acquisition failure")


def test_failed_build_removes_staging_directory(tmp_path: Path) -> None:
    root = tmp_path / "consumer"
    commit = "a" * 40
    make_consumer(root, commit)
    service = SchemaClosureService(source_provider=FailingProvider())

    with pytest.raises(RegistryError, match="planted acquisition failure"):
        service.validate(root)

    assert not list(tmp_path.glob("kleisis-stage-*"))


def test_check_does_not_stage_beside_consumer(
    tmp_path: Path,
    exact_source: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, commit = exact_source
    consumer = tmp_path / "consumer"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    make_consumer(consumer, commit)
    service = SchemaClosureService(source_provider=CopySourceProvider(source))
    service.materialize(consumer)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(scratch))

    service.check(consumer)

    assert not list(tmp_path.glob("kleisis-stage-*"))
    assert not list(scratch.glob("kleisis-stage-*"))


def test_publication_rolls_back_both_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "consumer"
    root.mkdir()
    resources_target = root / ".schema-resources"
    resources_target.mkdir()
    (resources_target / "old").write_text("old resources", encoding="utf-8")
    registry_target = root / "specs/schema-registry.yml"
    registry_target.parent.mkdir()
    registry_target.write_text("old registry", encoding="utf-8")
    resources_candidate = tmp_path / "new-resources"
    resources_candidate.mkdir()
    (resources_candidate / "new").write_text("new resources", encoding="utf-8")
    registry_candidate = tmp_path / "new-registry.yml"
    registry_candidate.write_text("new registry", encoding="utf-8")
    original_rename = Path.rename

    def failing_rename(path: Path, target: Path) -> Path:
        if path == registry_candidate:
            raise OSError("planted registry replacement failure")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", failing_rename)

    with pytest.raises(OSError, match="planted registry replacement failure"):
        publish_pair(
            root,
            [(resources_candidate, resources_target), (registry_candidate, registry_target)],
        )

    assert (resources_target / "old").read_text(encoding="utf-8") == "old resources"
    assert registry_target.read_text(encoding="utf-8") == "old registry"
    assert not (root / ".kleisis-transaction.json").exists()


def test_composite_registry_imports_child_exports_and_checks_shared_bytes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "parent"
    schemas = root / "specs/schemas"
    schemas.mkdir(parents=True)
    (schemas / "parent.schema.yaml").write_text(concrete_schema("parent:owned@1"), encoding="utf-8")
    shared = concrete_schema("shared:contract@1")
    (schemas / "shared.schema.yaml").write_text(shared, encoding="utf-8")
    (root / "specs/schema-resources.yml").write_text(
        """format_version: 1
exports: [parent:owned@1, shared:contract@1]
projects:
  - path: projects/child
""",
        encoding="utf-8",
    )
    child = root / "projects/child"
    (child / "specs/schemas").mkdir(parents=True)
    (child / "specs/schemas/child.schema.yaml").write_text(
        concrete_schema("child:owned@1"), encoding="utf-8"
    )
    (child / ".schema-resources").mkdir()
    (child / ".schema-resources/shared.schema.yaml").write_text(shared, encoding="utf-8")
    (child / "specs/schema-registry.yml").write_text(
        """schema_version: 1
schemas:
  child:owned@1: specs/schemas/child.schema.yaml
  shared:contract@1: .schema-resources/shared.schema.yaml
exports:
  - child:owned@1
""",
        encoding="utf-8",
    )

    candidate = SchemaClosureService().build(root)
    try:
        assert candidate.registry.schemas["child:owned@1"].as_posix() == (
            "projects/child/specs/schemas/child.schema.yaml"
        )
    finally:
        shutil.rmtree(candidate.resources.parent)


def test_composite_registry_rejects_missing_child_export_closure(tmp_path: Path) -> None:
    root = tmp_path / "parent"
    schemas = root / "specs/schemas"
    schemas.mkdir(parents=True)
    (schemas / "parent.schema.yaml").write_text(concrete_schema("parent:owned@1"), encoding="utf-8")
    (root / "specs/schema-resources.yml").write_text(
        """format_version: 1
exports: [parent:owned@1]
projects:
  - path: projects/child
""",
        encoding="utf-8",
    )
    child = root / "projects/child"
    (child / "specs/schemas").mkdir(parents=True)
    (child / "specs/schemas/child.schema.yaml").write_text(
        concrete_schema("child:owned@1", reference="missing.schema.yaml"),
        encoding="utf-8",
    )
    (child / "specs/schema-registry.yml").write_text(
        """schema_version: 1
schemas:
  child:owned@1: specs/schemas/child.schema.yaml
exports:
  - child:owned@1
""",
        encoding="utf-8",
    )

    with pytest.raises(ReferenceError, match="not a regular source file"):
        SchemaClosureService().validate(root)
