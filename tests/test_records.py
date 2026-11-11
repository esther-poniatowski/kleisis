"""Tests for strict resource-record parsing."""

from pathlib import Path

import pytest

from kleisis.domain.records import load_resource_record
from kleisis.errors import RecordError


def write_record(tmp_path: Path, body: str) -> Path:
    """Write one test record."""
    path = tmp_path / "record.yml"
    path.write_text(body, encoding="utf-8")
    return path


def test_defaults_are_conventional_repository_paths(tmp_path: Path) -> None:
    record = load_resource_record(write_record(tmp_path, "_schema: kleisis:schema-resources@2\n"))

    assert record.paths.owned_schemas.as_posix() == "specs/schemas"
    assert record.paths.registry.as_posix() == "specs/schema-registry.yml"
    assert record.paths.state.as_posix() == ".kleisis"
    assert record.paths.resources.as_posix() == ".kleisis/resources"
    assert record.schemas.suffixes == (".schema.yaml", ".schema.yml", ".schema.json")
    assert record.schemas.binding == "_schema"
    assert record.format_version == 2


def test_the_resources_default_lies_in_the_configured_state_folder(tmp_path: Path) -> None:
    record = load_resource_record(
        write_record(
            tmp_path, "_schema: kleisis:schema-resources@2\npaths:\n  state: var/kleisis\n"
        )
    )

    assert record.paths.state.as_posix() == "var/kleisis"
    assert record.paths.resources.as_posix() == "var/kleisis/resources"


def test_a_configured_resources_folder_overrides_the_state_default(tmp_path: Path) -> None:
    record = load_resource_record(
        write_record(
            tmp_path,
            "_schema: kleisis:schema-resources@2\npaths:\n  resources: .schema-resources\n",
        )
    )

    assert record.paths.state.as_posix() == ".kleisis"
    assert record.paths.resources.as_posix() == ".schema-resources"


@pytest.mark.parametrize("body", ["_schema: kleisis:schema-resources@1\n", "format_version: 1\n"])
def test_a_format_1_record_names_the_moved_resources_default(tmp_path: Path, body: str) -> None:
    with pytest.raises(RecordError, match="paths.resources: .schema-resources") as caught:
        load_resource_record(write_record(tmp_path, body))
    assert "_schema: kleisis:schema-resources@2" in str(caught.value)
    assert caught.value.exit_status == 2


@pytest.mark.parametrize(
    "body, locus",
    [
        ("exports: []\n", "/_schema"),
        ("_schema: kleisis:schema-resources@1\n", "/_schema"),
        ("_schema: kleisis:schema-resources@3\n", "/_schema"),
        ("_schema: kleisis:schema-registry@2\n", "/_schema"),
        ("format_version: 1\n", "/format_version"),
        ("_schema: kleisis:schema-resources@2\nformat_version: 1\n", "/format_version"),
    ],
)
def test_a_record_without_its_format_identifier_is_rejected(
    tmp_path: Path, body: str, locus: str
) -> None:
    with pytest.raises(RecordError, match="_schema: kleisis:schema-resources@2") as caught:
        load_resource_record(write_record(tmp_path, body))
    assert caught.value.exit_status == 2
    assert caught.value.findings[0].locus == locus


def test_the_schema_convention_is_read_from_the_record(tmp_path: Path) -> None:
    record = load_resource_record(
        write_record(
            tmp_path,
            """_schema: kleisis:schema-resources@2
schemas:
  suffixes: [.contract.yml]
  binding: $contract
dependencies:
  - id: shared
    registry: specs/schema-registry.yml
    contracts: [shared:item@1]
    binding: _schema
    source:
      kind: git
      repository: https://example.invalid/shared.git
      commit: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
""".replace("$contract", "contract"),
        )
    )

    assert record.schemas.suffixes == (".contract.yml",)
    assert record.schemas.binding == "contract"
    assert record.dependencies[0].binding == "_schema"


def test_a_partial_schema_convention_keeps_the_other_default(tmp_path: Path) -> None:
    record = load_resource_record(
        write_record(tmp_path, "_schema: kleisis:schema-resources@2\nschemas:\n  binding: kind\n")
    )

    assert record.schemas.suffixes == (".schema.yaml", ".schema.yml", ".schema.json")
    assert record.schemas.binding == "kind"


@pytest.mark.parametrize(
    "body, message",
    [
        ("_schema: kleisis:schema-resources@2\nextra: true\n", "unknown fields"),
        ("_schema: kleisis:schema-resources@2\nschemas:\n  pattern: x\n", "unknown fields"),
        ("_schema: kleisis:schema-resources@2\nschemas:\n  suffixes: []\n", "must not be empty"),
        (
            "_schema: kleisis:schema-resources@2\nschemas:\n  suffixes: [.yml, .yml]\n",
            "must be unique",
        ),
        (
            "_schema: kleisis:schema-resources@2\nschemas:\n  suffixes: [schema.yml]\n",
            "open with a dot",
        ),
        (
            "_schema: kleisis:schema-resources@2\nschemas:\n  suffixes: [.a/b.yml]\n",
            "open with a dot",
        ),
        ("_schema: kleisis:schema-resources@2\nschemas:\n  binding: a/b\n", "property name"),
        ("_schema: kleisis:schema-resources@2\nschemas:\n  binding: ''\n", "non-empty string"),
        (
            """_schema: kleisis:schema-resources@2
dependencies:
  - id: shared
    registry: specs/schema-registry.yml
    contracts: [shared:item@1]
    source:
      kind: git
      repository: https://example.invalid/shared.git
      commit: abc123
""",
            "full lowercase Git commit",
        ),
        (
            """_schema: kleisis:schema-resources@2
paths:
  owned_schemas: ../schemas
""",
            "must stay inside",
        ),
        (
            """_schema: kleisis:schema-resources@2
dependencies:
  - id: shared
    registry: specs/schema-registry.yml
    contracts: [shared:item@1]
    source:
      kind: git
      repository: https://example.invalid/shared.git
      commit: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
      archive_roots: []
""",
            "must be non-empty when present",
        ),
        (
            """_schema: kleisis:schema-resources@2
dependencies:
  - id: shared
    registry: specs/schema-registry.yml
    contracts: [shared:item@1]
    source:
      kind: git
      repository: https://example.invalid/shared.git
      commit: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
      archive_roots: [utils/schemas, specs/schema-registry.yml]
""",
            "path order",
        ),
        (
            """_schema: kleisis:schema-resources@2
dependencies:
  - id: shared
    registry: specs/schema-registry.yml
    contracts: [shared:item@1]
    source:
      kind: git
      repository: https://example.invalid/shared.git
      commit: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
      archive_roots: [../schemas]
""",
            "must stay inside",
        ),
    ],
)
def test_invalid_records_are_rejected(tmp_path: Path, body: str, message: str) -> None:
    with pytest.raises(RecordError, match=message):
        load_resource_record(write_record(tmp_path, body))


def test_duplicate_yaml_keys_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(RecordError, match="duplicate key"):
        load_resource_record(
            write_record(
                tmp_path,
                "_schema: kleisis:schema-resources@2\n_schema: kleisis:schema-resources@2\n",
            )
        )


def test_git_source_records_credential_and_archive_roots(tmp_path: Path) -> None:
    record = load_resource_record(
        write_record(
            tmp_path,
            """_schema: kleisis:schema-resources@2
dependencies:
  - id: private-source
    source:
      kind: git
      repository: https://example.invalid/private.git
      commit: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
      credential_env: KLEISIS_GIT_AUTHORIZATION
      archive_roots:
        - schemas
        - specs/schema-registry.yml
    registry: specs/schema-registry.yml
    contracts: [private:item@1]
""",
        )
    )

    source = record.dependencies[0].source
    assert source.kind == "git"
    assert source.credential_env == "KLEISIS_GIT_AUTHORIZATION"
    assert tuple(path.as_posix() for path in source.archive_roots) == (
        "schemas",
        "specs/schema-registry.yml",
    )


@pytest.mark.parametrize("contracts", ["", "    contracts: []\n"])
def test_dependency_may_select_no_contract(tmp_path: Path, contracts: str) -> None:
    record = load_resource_record(
        write_record(
            tmp_path,
            f"""_schema: kleisis:schema-resources@2
dependencies:
  - id: primitives
    source:
      kind: git
      repository: https://example.invalid/primitives.git
      commit: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    registry: specs/schema-registry.yml
{contracts}""",
        )
    )

    assert record.dependencies[0].contracts == ()


def test_dependency_contracts_must_be_unique(tmp_path: Path) -> None:
    with pytest.raises(RecordError, match="contracts must be unique"):
        load_resource_record(
            write_record(
                tmp_path,
                """_schema: kleisis:schema-resources@2
dependencies:
  - id: shared
    source:
      kind: git
      repository: https://example.invalid/shared.git
      commit: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    registry: specs/schema-registry.yml
    contracts: [shared:item@1, shared:item@1]
""",
            )
        )
