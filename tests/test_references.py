"""Tests for reference-closure resolution."""

from pathlib import Path

import pytest

from schema_closure.domain.references import reference_closure
from schema_closure.errors import ReferenceError


def test_relative_reference_uses_the_containing_file_directory(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    entrypoint = root / "root.schema.yaml"
    entrypoint.write_text(
        """$id: https://schemas.example/root.schema.yaml
$ref: defs.schema.yaml#/$defs/value
""",
        encoding="utf-8",
    )
    definition = root / "defs.schema.yaml"
    definition.write_text(
        """$id: https://different.example/another-name.schema.yaml
$defs:
  value:
    type: string
""",
        encoding="utf-8",
    )

    assert reference_closure(root, [entrypoint]) == {entrypoint, definition}


def test_reference_cannot_leave_exact_source(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    entrypoint = root / "root.schema.yaml"
    entrypoint.write_text("$ref: ../outside.schema.yaml\n", encoding="utf-8")
    (tmp_path / "outside.schema.yaml").write_text("type: string\n", encoding="utf-8")

    with pytest.raises(ReferenceError, match="leaves its source"):
        reference_closure(root, [entrypoint])


def test_reference_fragment_must_exist(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    entrypoint = root / "root.schema.yaml"
    entrypoint.write_text("$ref: defs.schema.yaml#/$defs/missing\n", encoding="utf-8")
    (root / "defs.schema.yaml").write_text("$defs: {}\n", encoding="utf-8")

    with pytest.raises(ReferenceError, match="fragment does not exist"):
        reference_closure(root, [entrypoint])


def test_json_schema_must_parse_as_json(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    entrypoint = root / "root.schema.yaml"
    entrypoint.write_text("$ref: defs.schema.json\n", encoding="utf-8")
    (root / "defs.schema.json").write_text("type: string\n", encoding="utf-8")

    with pytest.raises(ReferenceError, match="cannot read schema"):
        reference_closure(root, [entrypoint])


def test_uppercase_json_suffix_still_requires_json(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    entrypoint = root / "root.schema.yaml"
    entrypoint.write_text("$ref: defs.schema.JSON\n", encoding="utf-8")
    (root / "defs.schema.JSON").write_text("type: string\n", encoding="utf-8")

    with pytest.raises(ReferenceError, match="cannot read schema"):
        reference_closure(root, [entrypoint])


def test_rooted_reference_resolves_from_vault_root(tmp_path: Path) -> None:
    root = tmp_path / "source"
    entrypoint = root / "nested/root.schema.yaml"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("$ref: /schemas/defs.schema.yaml\n", encoding="utf-8")
    definition = root / "schemas/defs.schema.yaml"
    definition.parent.mkdir()
    definition.write_text("type: string\n", encoding="utf-8")

    assert reference_closure(root, [entrypoint]) == {entrypoint, definition}


def test_absolute_identifier_must_match_a_loaded_document(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    entrypoint = root / "root.schema.yaml"
    entrypoint.write_text(
        """allOf:
  - $ref: defs.schema.yaml
  - $ref: https://schemas.example/defs#/$defs/value
""",
        encoding="utf-8",
    )
    definition = root / "defs.schema.yaml"
    definition.write_text(
        """$id: https://schemas.example/defs
$defs:
  value:
    type: string
""",
        encoding="utf-8",
    )

    assert reference_closure(root, [entrypoint]) == {entrypoint, definition}


def test_unloaded_absolute_identifier_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    entrypoint = root / "root.schema.yaml"
    entrypoint.write_text("$ref: https://schemas.example/missing\n", encoding="utf-8")

    with pytest.raises(ReferenceError, match="no loaded schema file"):
        reference_closure(root, [entrypoint])


def test_reference_chain_must_terminate(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    entrypoint = root / "root.schema.yaml"
    entrypoint.write_text('$ref: "#"\n', encoding="utf-8")

    with pytest.raises(ReferenceError, match="exceeds 64 steps"):
        reference_closure(root, [entrypoint])


def test_fragment_is_percent_decoded_once(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    entrypoint = root / "root.schema.yaml"
    entrypoint.write_text(
        'properties:\n  "%2F": {type: string}\n$ref: "#/properties/%252F"\n',
        encoding="utf-8",
    )

    assert reference_closure(root, [entrypoint]) == {entrypoint}


def test_dynamic_anchor_does_not_replace_anchor(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    entrypoint = root / "root.schema.yaml"
    entrypoint.write_text(
        '$defs:\n  value:\n    $dynamicAnchor: label\n$ref: "#label"\n', encoding="utf-8"
    )

    with pytest.raises(ReferenceError, match="anchor does not exist"):
        reference_closure(root, [entrypoint])


def test_array_pointer_accepts_only_ascii_digits(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    entrypoint = root / "root.schema.yaml"
    entrypoint.write_text('items:\n  - {type: string}\n$ref: "#/items/\u0660"\n', encoding="utf-8")

    with pytest.raises(ReferenceError, match="fragment does not exist"):
        reference_closure(root, [entrypoint])
