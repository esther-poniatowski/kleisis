"""Tests for reference-closure resolution."""

from pathlib import Path

import pytest

from kleisis.domain.references import SchemaSource, reference_closure, source_closure
from kleisis.errors import ReferenceError


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


def test_schema_with_a_merge_key_names_the_key_and_its_file(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    entrypoint = root / "root.schema.yaml"
    entrypoint.write_text(
        "x-base: &base {type: string}\nproperties:\n  a:\n    <<: *base\n", encoding="utf-8"
    )

    pattern = r"(?s)merge key `<<`.*root\.schema\.yaml\", line 4"
    with pytest.raises(ReferenceError, match=pattern) as caught:
        reference_closure(root, [entrypoint])
    assert caught.value.findings[0].rule.id == "schema/unreadable"


def test_uppercase_json_suffix_still_requires_json(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    entrypoint = root / "root.schema.yaml"
    entrypoint.write_text("$ref: defs.schema.JSON\n", encoding="utf-8")
    (root / "defs.schema.JSON").write_text("type: string\n", encoding="utf-8")

    with pytest.raises(ReferenceError, match="cannot read schema"):
        reference_closure(root, [entrypoint])


def test_vault_rooted_reference_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "source"
    entrypoint = root / "nested/root.schema.yaml"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("$ref: /schemas/defs.schema.yaml\n", encoding="utf-8")
    definition = root / "schemas/defs.schema.yaml"
    definition.parent.mkdir()
    definition.write_text("type: string\n", encoding="utf-8")

    with pytest.raises(
        ReferenceError,
        match="'/schemas/defs.schema.yaml' in nested/root.schema.yaml .* absolute \\$id",
    ):
        reference_closure(root, [entrypoint])


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

    with pytest.raises(ReferenceError, match="neither its source nor a declared dependency"):
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


def write_source(root: Path, files: dict[str, str]) -> SchemaSource:
    """Write one source tree and map each absolute root ``$id`` to its file."""
    resources: dict[str, Path] = {}
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        first = text.splitlines()[0]
        if first.startswith("$id: ") and ":" in first.removeprefix("$id: "):
            resources[first.removeprefix("$id: ")] = path
    return SchemaSource(name=root.name, root=root, resources=resources)


def test_absolute_reference_resolves_through_a_dependency(tmp_path: Path) -> None:
    consumer = write_source(
        tmp_path / "consumer", {"root.schema.yaml": "$ref: urn:example:label#/$defs/text\n"}
    )
    dependency = write_source(
        tmp_path / "primitives",
        {
            "label.schema.yaml": (
                "$id: urn:example:label\n$defs:\n  text: {$ref: text.schema.yaml}\n"
            ),
            "text.schema.yaml": "type: string\n",
        },
    )
    entrypoint = tmp_path / "consumer/root.schema.yaml"

    owners = source_closure([(consumer, entrypoint)], [dependency])

    assert {path.name: source.name for path, source in owners.items()} == {
        "root.schema.yaml": "consumer",
        "label.schema.yaml": "primitives",
        "text.schema.yaml": "primitives",
    }


def test_current_source_declaration_precedes_dependencies(tmp_path: Path) -> None:
    consumer = write_source(
        tmp_path / "consumer",
        {
            "root.schema.yaml": "$ref: https://schemas.example/label\n",
            "label.schema.yaml": "$id: https://schemas.example/label\ntype: string\n",
        },
    )
    dependency = write_source(
        tmp_path / "primitives",
        {"label.schema.yaml": "$id: https://schemas.example/label\ntype: integer\n"},
    )

    owners = source_closure([(consumer, tmp_path / "consumer/root.schema.yaml")], [dependency])

    assert set(owners.values()) == {consumer}


def test_unknown_absolute_reference_names_uri_and_referring_file(tmp_path: Path) -> None:
    consumer = write_source(
        tmp_path / "consumer", {"nested/root.schema.yaml": "$ref: urn:example:missing\n"}
    )
    dependency = write_source(tmp_path / "primitives", {"other.schema.yaml": "type: string\n"})

    with pytest.raises(
        ReferenceError,
        match="maps \\$ref 'urn:example:missing' in nested/root.schema.yaml",
    ):
        source_closure([(consumer, tmp_path / "consumer/nested/root.schema.yaml")], [dependency])


def test_dependencies_mapping_one_uri_to_different_bytes_are_rejected(tmp_path: Path) -> None:
    consumer = write_source(
        tmp_path / "consumer", {"root.schema.yaml": "$ref: https://schemas.example/label\n"}
    )
    first = write_source(
        tmp_path / "first",
        {"label.schema.yaml": "$id: https://schemas.example/label\ntype: string\n"},
    )
    second = write_source(
        tmp_path / "second",
        {"label.schema.yaml": "$id: https://schemas.example/label\ntype: integer\n"},
    )

    with pytest.raises(ReferenceError, match="first and second map .* to different bytes"):
        source_closure([(consumer, tmp_path / "consumer/root.schema.yaml")], [first, second])


def test_dependencies_mapping_one_uri_to_identical_bytes_select_the_first(
    tmp_path: Path,
) -> None:
    consumer = write_source(
        tmp_path / "consumer", {"root.schema.yaml": "$ref: https://schemas.example/label\n"}
    )
    label = "$id: https://schemas.example/label\ntype: string\n"
    first = write_source(tmp_path / "first", {"label.schema.yaml": label})
    second = write_source(tmp_path / "second", {"label.schema.yaml": label})

    owners = source_closure([(consumer, tmp_path / "consumer/root.schema.yaml")], [first, second])

    assert owners[(tmp_path / "first/label.schema.yaml").resolve()] is first
    assert (tmp_path / "second/label.schema.yaml").resolve() not in owners


def test_resource_entry_absent_from_its_source_is_ignored(tmp_path: Path) -> None:
    consumer = write_source(
        tmp_path / "consumer", {"root.schema.yaml": "$ref: https://schemas.example/label\n"}
    )
    materialized_elsewhere = SchemaSource(
        name="tool",
        root=tmp_path / "tool",
        resources={"https://schemas.example/label": tmp_path / "tool/.schema-resources/l.yaml"},
    )
    (tmp_path / "tool").mkdir()
    owner = write_source(
        tmp_path / "primitives",
        {"label.schema.yaml": "$id: https://schemas.example/label\ntype: string\n"},
    )

    owners = source_closure(
        [(consumer, tmp_path / "consumer/root.schema.yaml")], [materialized_elsewhere, owner]
    )

    assert owners[(tmp_path / "primitives/label.schema.yaml").resolve()] is owner


def test_resource_entry_must_declare_its_uri(tmp_path: Path) -> None:
    consumer = write_source(
        tmp_path / "consumer", {"root.schema.yaml": "$ref: https://schemas.example/label\n"}
    )
    (tmp_path / "primitives").mkdir()
    (tmp_path / "primitives/label.schema.yaml").write_text(
        "$id: https://schemas.example/other\n", encoding="utf-8"
    )
    dependency = SchemaSource(
        name="primitives",
        root=tmp_path / "primitives",
        resources={"https://schemas.example/label": tmp_path / "primitives/label.schema.yaml"},
    )

    with pytest.raises(
        ReferenceError, match="which declares \\$id 'https://schemas.example/other'"
    ):
        source_closure([(consumer, tmp_path / "consumer/root.schema.yaml")], [dependency])


def test_relative_reference_cannot_leave_a_dependency_source(tmp_path: Path) -> None:
    consumer = write_source(
        tmp_path / "consumer", {"root.schema.yaml": "$ref: https://schemas.example/label\n"}
    )
    dependency = write_source(
        tmp_path / "primitives",
        {"label.schema.yaml": "$id: https://schemas.example/label\n$ref: ../outside.yaml\n"},
    )
    (tmp_path / "outside.yaml").write_text("type: string\n", encoding="utf-8")

    with pytest.raises(ReferenceError, match="leaves its source"):
        source_closure([(consumer, tmp_path / "consumer/root.schema.yaml")], [dependency])
