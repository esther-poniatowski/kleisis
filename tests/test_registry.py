"""Tests for concrete-schema discovery and registry generation."""

from pathlib import Path, PurePosixPath

import pytest

from kleisis.domain.models import Registry, SchemaConvention
from kleisis.domain.registry import (
    discover_owned_schemas,
    load_registry,
    registry_bytes,
    resource_map,
)
from kleisis.errors import RegistryError


def test_discovery_separates_contracts_from_support_schemas(tmp_path: Path) -> None:
    schema_root = tmp_path / "specs/schemas"
    schema_root.mkdir(parents=True)
    contract = schema_root / "record.schema.yaml"
    contract.write_text(
        """type: object
required: [_schema]
properties:
  _schema: {const: example:record@1}
""",
        encoding="utf-8",
    )
    (schema_root / "support.schema.yaml").write_text("type: string\n", encoding="utf-8")

    assert discover_owned_schemas(tmp_path, schema_root) == {
        "example:record@1": PurePosixPath("specs/schemas/record.schema.yaml")
    }


def test_discovery_follows_the_configured_suffixes_and_binding(tmp_path: Path) -> None:
    schema_root = tmp_path / "schemas"
    schema_root.mkdir()
    (schema_root / "record.contract.yml").write_text(
        "required: [kind]\nproperties:\n  kind: {const: example:record@1}\n", encoding="utf-8"
    )
    (schema_root / "record.base.yml").write_text(
        "properties:\n  _schema: {type: string}\n", encoding="utf-8"
    )
    (schema_root / "other.schema.yaml").write_text(
        "required: [_schema]\nproperties:\n  _schema: {const: example:other@1}\n",
        encoding="utf-8",
    )
    convention = SchemaConvention(suffixes=(".contract.yml", ".base.yml"), binding="kind")

    assert discover_owned_schemas(tmp_path, schema_root, convention) == {
        "example:record@1": PurePosixPath("schemas/record.contract.yml")
    }


def test_a_configured_binding_reports_its_own_name(tmp_path: Path) -> None:
    schema_root = tmp_path / "schemas"
    schema_root.mkdir()
    (schema_root / "record.schema.yml").write_text(
        "properties:\n  kind: {const: example:record@1}\n", encoding="utf-8"
    )

    with pytest.raises(RegistryError, match="does not require kind"):
        discover_owned_schemas(tmp_path, schema_root, SchemaConvention(binding="kind"))


def test_discovery_rejects_a_malformed_binding(tmp_path: Path) -> None:
    schema_root = tmp_path / "specs/schemas"
    schema_root.mkdir(parents=True)
    (schema_root / "record.schema.yaml").write_text(
        "properties:\n  _schema: invalid\n", encoding="utf-8"
    )

    with pytest.raises(RegistryError, match="invalid _schema property mapping"):
        discover_owned_schemas(tmp_path, schema_root)


def test_discovery_rejects_a_required_property_without_its_binding(tmp_path: Path) -> None:
    schema_root = tmp_path / "specs/schemas"
    schema_root.mkdir(parents=True)
    (schema_root / "record.schema.yaml").write_text(
        "required: [_schema]\nproperties: {}\n", encoding="utf-8"
    )

    with pytest.raises(RegistryError, match="requires _schema but has no"):
        discover_owned_schemas(tmp_path, schema_root)


def test_discovery_rejects_a_binding_that_is_not_required(tmp_path: Path) -> None:
    schema_root = tmp_path / "specs/schemas"
    schema_root.mkdir(parents=True)
    (schema_root / "record.schema.yaml").write_text(
        "properties:\n  _schema: {const: example:record@1}\n", encoding="utf-8"
    )

    with pytest.raises(RegistryError, match="does not require _schema"):
        discover_owned_schemas(tmp_path, schema_root)


def test_empty_registry_serialization_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "schema-registry.yml"
    path.write_bytes(registry_bytes(Registry(schemas={}, exports=())))

    assert path.read_text(encoding="utf-8").endswith(
        "schema_version: 2\nschemas: {}\nexports: []\nresources: {}\n"
    )
    assert load_registry(path) == Registry(schemas={}, exports=())


def test_version_one_registry_is_still_read(tmp_path: Path) -> None:
    path = tmp_path / "schema-registry.yml"
    path.write_text(
        "schema_version: 1\nschemas:\n  example:record@1: schemas/record.schema.yaml\n"
        "exports: [example:record@1]\n",
        encoding="utf-8",
    )

    assert load_registry(path) == Registry(
        schemas={"example:record@1": PurePosixPath("schemas/record.schema.yaml")},
        exports=("example:record@1",),
        schema_version=1,
    )


def test_version_one_registry_admits_no_resources(tmp_path: Path) -> None:
    path = tmp_path / "schema-registry.yml"
    path.write_text("schema_version: 1\nschemas: {}\nresources: {}\n", encoding="utf-8")

    with pytest.raises(RegistryError, match="unknown fields: resources"):
        load_registry(path)


@pytest.mark.parametrize("version", ["3", "true", "'2'"])
def test_registry_rejects_an_unsupported_version(tmp_path: Path, version: str) -> None:
    path = tmp_path / "schema-registry.yml"
    path.write_text(f"schema_version: {version}\nschemas: {{}}\n", encoding="utf-8")

    with pytest.raises(RegistryError, match="schema_version 1 or 2"):
        load_registry(path)


def test_version_two_registry_round_trips_resources(tmp_path: Path) -> None:
    registry = Registry(
        schemas={"example:record@1": PurePosixPath("specs/schemas/record.schema.yaml")},
        exports=("example:record@1",),
        resources={
            "urn:example:label": PurePosixPath(".schema-resources/tool/label.schema.yaml"),
            "https://schemas.example/record": PurePosixPath("specs/schemas/record.schema.yaml"),
        },
    )
    path = tmp_path / "schema-registry.yml"
    path.write_bytes(registry_bytes(registry))

    assert path.read_text(encoding="utf-8").endswith(
        "resources:\n"
        '  "https://schemas.example/record": "specs/schemas/record.schema.yaml"\n'
        '  "urn:example:label": ".schema-resources/tool/label.schema.yaml"\n'
    )
    assert load_registry(path) == registry


def test_resources_keys_must_be_absolute_uris(tmp_path: Path) -> None:
    path = tmp_path / "schema-registry.yml"
    path.write_text(
        "schema_version: 2\nschemas: {}\nexports: []\n"
        "resources:\n  label.schema.yaml: schemas/label.schema.yaml\n",
        encoding="utf-8",
    )

    with pytest.raises(RegistryError, match="must be absolute URIs: label.schema.yaml"):
        load_registry(path)


def test_resource_map_keeps_absolute_identifiers_only(tmp_path: Path) -> None:
    (tmp_path / "absolute.schema.yaml").write_text("$id: urn:example:a\n", encoding="utf-8")
    (tmp_path / "relative.schema.yaml").write_text("$id: relative.yaml\n", encoding="utf-8")

    assert resource_map(tmp_path, sorted(tmp_path.iterdir())) == {
        "urn:example:a": PurePosixPath("absolute.schema.yaml")
    }


def test_duplicate_identifier_with_identical_bytes_keeps_the_first_file(tmp_path: Path) -> None:
    for name in ("b.schema.yaml", "a.schema.yaml"):
        (tmp_path / name).write_text("$id: urn:example:a\n", encoding="utf-8")

    files = [tmp_path / "b.schema.yaml", tmp_path / "a.schema.yaml"]

    assert resource_map(tmp_path, files) == {"urn:example:a": PurePosixPath("b.schema.yaml")}


def test_duplicate_identifier_with_different_bytes_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "a.schema.yaml").write_text("$id: urn:example:a\n", encoding="utf-8")
    (tmp_path / "b.schema.yaml").write_text("$id: urn:example:a\ntype: string\n", encoding="utf-8")

    with pytest.raises(RegistryError, match="duplicate \\$id 'urn:example:a' names different"):
        resource_map(tmp_path, sorted(tmp_path.iterdir()))
