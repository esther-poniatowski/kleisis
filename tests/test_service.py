"""Integration tests for exact materialization and drift detection."""

import shutil
import tempfile
from pathlib import Path, PurePosixPath

import pytest
from conftest import CopySourceProvider, concrete_schema, git

from kleisis.application.materialization import acquire_dependency, materialize_dependencies
from kleisis.application.service import SchemaClosureService
from kleisis.domain.models import (
    Dependency,
    GitSource,
    MaterializedDependency,
    Paths,
    ResourceRecord,
    Source,
)
from kleisis.domain.registry import load_registry
from kleisis.errors import DriftError, ReferenceError, RegistryError
from kleisis.infrastructure.publication import publish_pair
from kleisis.infrastructure.sources import ExactSourceProvider


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


def make_linked_git_dependency(root: Path, linked_input: str) -> ResourceRecord:
    """Create one exact dependency whose selected or unrelated input can be a link."""
    source_root = root / "source"
    schemas = source_root / "schemas"
    registry_directory = source_root / "specs"
    schemas.mkdir(parents=True)
    registry_directory.mkdir()
    registry = registry_directory / "schema-registry.yml"
    registry_text = """schema_version: 1
schemas:
  source:main@1: schemas/main.schema.yaml
exports:
  - source:main@1
"""
    if linked_input == "registry":
        (registry_directory / "actual-registry.yml").write_text(registry_text, encoding="utf-8")
        registry.symlink_to("actual-registry.yml")
    else:
        registry.write_text(registry_text, encoding="utf-8")

    main = schemas / "main.schema.yaml"
    if linked_input == "schema":
        (schemas / "actual.schema.yaml").write_text(
            concrete_schema("source:main@1"), encoding="utf-8"
        )
        main.symlink_to("actual.schema.yaml")
    elif linked_input == "reference":
        main.write_text(
            concrete_schema("source:main@1", reference="defs.schema.yaml#/$defs/value"),
            encoding="utf-8",
        )
        (schemas / "actual-defs.schema.yaml").write_text(
            "$defs:\n  value: {type: string}\n", encoding="utf-8"
        )
        (schemas / "defs.schema.yaml").symlink_to("actual-defs.schema.yaml")
    else:
        main.write_text(concrete_schema("source:main@1"), encoding="utf-8")
        (schemas / "unrelated.schema.yaml").symlink_to("main.schema.yaml")

    git(["init", "--quiet"], source_root)
    git(["config", "user.name", "Kleisis Tests"], source_root)
    git(["config", "user.email", "tests@example.invalid"], source_root)
    git(["add", "."], source_root)
    git(["commit", "--quiet", "-m", "linked fixture"], source_root)
    commit = git(["rev-parse", "HEAD"], source_root)
    return ResourceRecord(
        format_version=1,
        paths=Paths(),
        dependencies=(
            Dependency(
                id="shared",
                source=GitSource(
                    kind="git",
                    repository=str(source_root),
                    commit=commit,
                    archive_roots=(PurePosixPath("schemas"), PurePosixPath("specs")),
                ),
                registry=PurePosixPath("specs/schema-registry.yml"),
                contracts=("source:main@1",),
            ),
        ),
    )


def run_exact_dependency(root: Path, record: ResourceRecord) -> MaterializedDependency:
    """Materialize one fixture through the real Git source provider."""
    resources = root / "resources"
    work = root / "work"
    resources.mkdir()
    work.mkdir()
    acquired = acquire_dependency(ExactSourceProvider(), record, 0, work)
    return materialize_dependencies([acquired], record, resources)[0]


def test_unrelated_git_link_does_not_invalidate_selected_closure(tmp_path: Path) -> None:
    record = make_linked_git_dependency(tmp_path, "unrelated")

    result = run_exact_dependency(tmp_path, record)

    assert (tmp_path / "resources/shared/schemas/main.schema.yaml").is_file()
    assert result.source["archive_roots"] == ["schemas", "specs"]


@pytest.mark.parametrize(
    ("linked_input", "error"),
    [
        ("registry", RegistryError),
        ("schema", RegistryError),
        ("reference", ReferenceError),
    ],
)
def test_selected_git_links_fail_closed(
    tmp_path: Path, linked_input: str, error: type[Exception]
) -> None:
    record = make_linked_git_dependency(tmp_path, linked_input)

    with pytest.raises(error):
        run_exact_dependency(tmp_path, record)


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


class MappedSourceProvider:
    """Supply one fixture tree per declared commit."""

    def __init__(self, roots: dict[str, Path]):
        self.roots = roots

    def acquire(self, source: Source, destination: Path) -> None:
        assert isinstance(source, GitSource)
        shutil.copytree(self.roots[source.commit], destination)


LABEL = "https://primitives.example/label"
NOTE = "https://primitives.example/note"


def write_tree(root: Path, files: dict[str, str]) -> Path:
    """Write one fixture tree."""
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def make_two_sources(tmp_path: Path, owned_reference: str) -> tuple[Path, MappedSourceProvider]:
    """Create a consumer whose closure spans two dependencies by absolute identifiers.

    The dependency ``primitives`` publishes a version 2 registry mapping two base resources.
    The dependency ``shared`` publishes a version 1 registry; its selected contract refers to
    one resource of ``primitives``. The consumer refers to ``owned_reference``.
    """
    primitives = write_tree(
        tmp_path / "primitives",
        {
            "schemas/label.schema.yaml": (
                f"$id: {LABEL}\n$defs:\n  text: {{$ref: text.schema.yaml}}\n"
            ),
            "schemas/text.schema.yaml": "type: string\n",
            "schemas/note.schema.yaml": f"$id: {NOTE}\ntype: string\n",
            "schemas/record.schema.yaml": concrete_schema("primitives:record@1", reference=LABEL),
            "specs/schema-registry.yml": f"""schema_version: 2
schemas:
  primitives:record@1: schemas/record.schema.yaml
exports: [primitives:record@1]
resources:
  {LABEL}: schemas/label.schema.yaml
  {NOTE}: schemas/note.schema.yaml
  https://other.example/materialized: .schema-resources/other/absent.schema.yaml
""",
        },
    )
    shared = write_tree(
        tmp_path / "shared",
        {
            "schemas/main.schema.yaml": concrete_schema(
                "shared:main@1", reference=f"{LABEL}#/$defs/text"
            ),
            "specs/schema-registry.yml": """schema_version: 1
schemas:
  shared:main@1: schemas/main.schema.yaml
exports: [shared:main@1]
""",
        },
    )
    consumer = write_tree(
        tmp_path / "consumer",
        {
            "specs/schemas/owned.schema.yaml": concrete_schema(
                "consumer:owned@1", reference=owned_reference
            ),
            "specs/schema-resources.yml": f"""format_version: 1
exports: [consumer:owned@1]
dependencies:
  - id: shared
    source: {{kind: git, repository: https://example.invalid/shared.git, commit: {"a" * 40}}}
    registry: specs/schema-registry.yml
    contracts: [shared:main@1]
  - id: primitives
    source: {{kind: git, repository: https://example.invalid/primitives.git, commit: {"b" * 40}}}
    registry: specs/schema-registry.yml
    contracts: [primitives:record@1]
""",
        },
    )
    return consumer, MappedSourceProvider({"a" * 40: shared, "b" * 40: primitives})


def test_closure_spans_two_sources_through_absolute_identifiers(tmp_path: Path) -> None:
    consumer, provider = make_two_sources(tmp_path, NOTE)
    service = SchemaClosureService(source_provider=provider)

    service.materialize(consumer)

    resources = consumer / ".schema-resources"
    assert sorted(
        path.relative_to(resources).as_posix() for path in resources.rglob("*.schema.yaml")
    ) == [
        "primitives/schemas/label.schema.yaml",
        "primitives/schemas/note.schema.yaml",
        "primitives/schemas/record.schema.yaml",
        "primitives/schemas/text.schema.yaml",
        "shared/schemas/main.schema.yaml",
    ]
    registry = load_registry(consumer / "specs/schema-registry.yml")
    assert {uri: path.as_posix() for uri, path in registry.resources.items()} == {
        LABEL: ".schema-resources/primitives/schemas/label.schema.yaml",
        NOTE: ".schema-resources/primitives/schemas/note.schema.yaml",
    }
    assert "schema_version: 2\n" in (consumer / "specs/schema-registry.yml").read_text(
        encoding="utf-8"
    )
    service.check(consumer)


def test_unknown_absolute_identifier_names_uri_and_referring_file(tmp_path: Path) -> None:
    consumer, provider = make_two_sources(tmp_path, "https://primitives.example/missing")

    with pytest.raises(
        ReferenceError,
        match="'https://primitives.example/missing' in specs/schemas/owned.schema.yaml",
    ):
        SchemaClosureService(source_provider=provider).validate(consumer)


def test_dependencies_mapping_one_identifier_to_different_bytes_fail(tmp_path: Path) -> None:
    consumer, provider = make_two_sources(tmp_path, NOTE)
    write_tree(
        tmp_path / "shared",
        {
            "schemas/note.schema.yaml": f"$id: {NOTE}\ntype: integer\n",
            "specs/schema-registry.yml": f"""schema_version: 2
schemas:
  shared:main@1: schemas/main.schema.yaml
exports: [shared:main@1]
resources:
  {NOTE}: schemas/note.schema.yaml
""",
        },
    )

    with pytest.raises(ReferenceError, match="shared and primitives map .* to different bytes"):
        SchemaClosureService(source_provider=provider).validate(consumer)


def test_owned_and_materialized_identifier_with_different_bytes_fails(tmp_path: Path) -> None:
    consumer, provider = make_two_sources(tmp_path, NOTE)
    write_tree(consumer, {"specs/schemas/label.schema.yaml": f"$id: {LABEL}\ntype: integer\n"})

    with pytest.raises(RegistryError, match="duplicate \\$id 'https://primitives.example/label'"):
        SchemaClosureService(source_provider=provider).validate(consumer)


def make_parent_with_resource_child(tmp_path: Path, child_label: str) -> Path:
    """Create a parent owning one resource and a child whose version 2 registry maps one."""
    root = write_tree(
        tmp_path / "parent",
        {
            "specs/schemas/parent.schema.yaml": concrete_schema("parent:owned@1"),
            "specs/schemas/label.schema.yaml": f"$id: {LABEL}\ntype: string\n",
            "specs/schema-resources.yml": """format_version: 1
exports: [parent:owned@1]
projects:
  - path: projects/child
""",
        },
    )
    write_tree(
        root / "projects/child",
        {
            "specs/schemas/child.schema.yaml": concrete_schema("child:owned@1", reference=NOTE),
            ".schema-resources/tool/note.schema.yaml": f"$id: {NOTE}\ntype: string\n",
            ".schema-resources/tool/label.schema.yaml": child_label,
            "specs/schema-registry.yml": f"""schema_version: 2
schemas:
  child:owned@1: specs/schemas/child.schema.yaml
exports: [child:owned@1]
resources:
  {LABEL}: .schema-resources/tool/label.schema.yaml
  {NOTE}: .schema-resources/tool/note.schema.yaml
""",
        },
    )
    return root


def test_composite_registry_imports_child_resources(tmp_path: Path) -> None:
    root = make_parent_with_resource_child(tmp_path, f"$id: {LABEL}\ntype: string\n")

    candidate = SchemaClosureService().build(root)
    try:
        assert {uri: path.as_posix() for uri, path in candidate.registry.resources.items()} == {
            LABEL: "specs/schemas/label.schema.yaml",
            NOTE: "projects/child/.schema-resources/tool/note.schema.yaml",
        }
    finally:
        shutil.rmtree(candidate.resources.parent)


def test_composite_registry_rejects_child_resource_with_different_bytes(tmp_path: Path) -> None:
    root = make_parent_with_resource_child(tmp_path, f"$id: {LABEL}\ntype: integer\n")

    with pytest.raises(RegistryError, match="duplicate \\$id 'https://primitives.example/label'"):
        SchemaClosureService().validate(root)


def make_resource_only(
    tmp_path: Path, owned_reference: str | None, registry_version: int = 2
) -> tuple[Path, MappedSourceProvider]:
    """Create a consumer whose dependency ``primitives`` selects no contract.

    The registry of ``primitives`` maps one base resource when its version is 2 or later. The
    consumer refers to ``owned_reference``, or to nothing.
    """
    resources = (
        f"resources:\n  {LABEL}: schemas/label.schema.yaml\n" if registry_version > 1 else ""
    )
    primitives = write_tree(
        tmp_path / "primitives",
        {
            "schemas/label.schema.yaml": (
                f"$id: {LABEL}\n$defs:\n  text: {{$ref: text.schema.yaml}}\n"
            ),
            "schemas/text.schema.yaml": "type: string\n",
            "specs/schema-registry.yml": (
                f"schema_version: {registry_version}\nschemas: {{}}\nexports: []\n{resources}"
            ),
        },
    )
    consumer = write_tree(
        tmp_path / "consumer",
        {
            "specs/schemas/owned.schema.yaml": concrete_schema(
                "consumer:owned@1", reference=owned_reference
            ),
            "specs/schema-resources.yml": f"""format_version: 1
exports: [consumer:owned@1]
dependencies:
  - id: primitives
    source: {{kind: git, repository: https://example.invalid/primitives.git, commit: {"b" * 40}}}
    registry: specs/schema-registry.yml
""",
        },
    )
    return consumer, MappedSourceProvider({"b" * 40: primitives})


def test_resource_only_dependency_serves_an_absolute_reference(tmp_path: Path) -> None:
    consumer, provider = make_resource_only(tmp_path, f"{LABEL}#/$defs/text")
    service = SchemaClosureService(source_provider=provider)

    service.materialize(consumer)

    resources = consumer / ".schema-resources"
    assert sorted(
        path.relative_to(resources).as_posix() for path in resources.rglob("*.schema.yaml")
    ) == ["primitives/schemas/label.schema.yaml", "primitives/schemas/text.schema.yaml"]
    registry = load_registry(consumer / "specs/schema-registry.yml")
    assert set(registry.schemas) == {"consumer:owned@1"}
    assert {uri: path.as_posix() for uri, path in registry.resources.items()} == {
        LABEL: ".schema-resources/primitives/schemas/label.schema.yaml"
    }
    service.check(consumer)


def test_resource_only_dependency_with_version_1_registry_fails(tmp_path: Path) -> None:
    consumer, provider = make_resource_only(tmp_path, LABEL, registry_version=1)

    with pytest.raises(
        RegistryError, match="dependency primitives selects no contract, and its registry of"
    ):
        SchemaClosureService(source_provider=provider).validate(consumer)


def test_unused_resource_only_dependency_fails(tmp_path: Path) -> None:
    consumer, provider = make_resource_only(tmp_path, None)

    with pytest.raises(
        RegistryError, match="dependency primitives selects no contract, and no reference"
    ):
        SchemaClosureService(source_provider=provider).validate(consumer)


ROOTED = "/specs/schemas/defs.schema.yaml"


def make_rooted_consumer(tmp_path: Path, export_reference: str | None) -> Path:
    """Create a consumer with no dependency, whose support schema holds a vault-rooted ``$ref``.

    The support schema lies outside the export closure; the export refers to
    ``export_reference``, or to nothing.
    """
    return write_tree(
        tmp_path / "consumer",
        {
            "specs/schemas/owned.schema.yaml": concrete_schema(
                "consumer:owned@1", reference=export_reference
            ),
            "specs/schemas/defs.schema.yaml": "type: string\n",
            "specs/schemas/support.schema.yaml": f"$ref: {ROOTED}\n",
            "specs/schema-resources.yml": "format_version: 1\nexports: [consumer:owned@1]\n",
        },
    )


def test_vault_rooted_reference_outside_every_export_closure_is_admitted(
    tmp_path: Path,
) -> None:
    SchemaClosureService().validate(make_rooted_consumer(tmp_path, None))


def test_vault_rooted_reference_in_an_export_closure_fails(tmp_path: Path) -> None:
    consumer = make_rooted_consumer(tmp_path, ROOTED)

    with pytest.raises(
        ReferenceError,
        match=f"'{ROOTED}' in specs/schemas/owned.schema.yaml .* absolute \\$id",
    ):
        SchemaClosureService().validate(consumer)


def test_vault_rooted_reference_in_a_materialized_file_fails(tmp_path: Path) -> None:
    consumer, provider = make_two_sources(tmp_path, NOTE)
    write_tree(
        tmp_path / "primitives",
        {"schemas/note.schema.yaml": f"$id: {NOTE}\n$ref: /schemas/text.schema.yaml\n"},
    )

    with pytest.raises(
        ReferenceError,
        match="'/schemas/text.schema.yaml' in schemas/note.schema.yaml in dependency "
        "primitives .* absolute \\$id",
    ):
        SchemaClosureService(source_provider=provider).validate(consumer)


def test_vault_rooted_reference_in_a_child_export_closure_fails(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path / "parent",
        {
            "specs/schemas/parent.schema.yaml": concrete_schema("parent:owned@1"),
            "specs/schema-resources.yml": """format_version: 1
exports: [parent:owned@1]
projects:
  - path: projects/child
""",
        },
    )
    write_tree(
        root / "projects/child",
        {
            "specs/schemas/child.schema.yaml": concrete_schema("child:owned@1", reference=ROOTED),
            "specs/schemas/defs.schema.yaml": "type: string\n",
            "specs/schema-registry.yml": """schema_version: 2
schemas:
  child:owned@1: specs/schemas/child.schema.yaml
exports: [child:owned@1]
resources: {}
""",
        },
    )

    with pytest.raises(
        ReferenceError, match=f"'{ROOTED}' in specs/schemas/child.schema.yaml .* absolute \\$id"
    ):
        SchemaClosureService().validate(root)
