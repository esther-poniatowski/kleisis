"""Tests for concrete-schema discovery and registry generation."""

from pathlib import Path, PurePosixPath

import pytest

from schema_closure.domain.registry import discover_owned_schemas
from schema_closure.errors import RegistryError


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
