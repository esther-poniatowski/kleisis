"""Tests for strict resource-record parsing."""

from pathlib import Path

import pytest

from kleisis.domain.models import safe_relative_path
from kleisis.domain.records import RECORD_FORMAT, load_resource_record
from kleisis.errors import RecordError
from kleisis.findings import KeyPath

HEADER = (
    f"_schema: {RECORD_FORMAT}\n"
    "paths:\n  owned_schemas: specs/schemas\n  registry: specs/schema-registry.yml\n"
)
"""The format and the two paths that every configuration declares."""


def write_record(tmp_path: Path, body: str) -> Path:
    """Write one test record."""
    path = tmp_path / "record.yml"
    path.write_text(body, encoding="utf-8")
    return path


def test_the_state_and_the_convention_take_their_defaults(tmp_path: Path) -> None:
    record = load_resource_record(write_record(tmp_path, HEADER))

    assert record.paths.owned_schemas.as_posix() == "specs/schemas"
    assert record.paths.registry.as_posix() == "specs/schema-registry.yml"
    assert record.paths.state.as_posix() == ".kleisis"
    assert record.paths.resources.as_posix() == ".kleisis/resources"
    assert record.schemas.suffixes == (".schema.yaml", ".schema.yml", ".schema.json")
    assert record.schemas.binding == "_schema"


@pytest.mark.parametrize(
    ("paths", "missing"),
    [
        ("", "owned_schemas"),
        ("paths:\n  registry: specs/schema-registry.yml\n", "owned_schemas"),
        ("paths:\n  owned_schemas: specs/schemas\n", "registry"),
    ],
)
def test_the_schemas_and_the_registry_must_be_declared(
    tmp_path: Path, paths: str, missing: str
) -> None:
    with pytest.raises(RecordError, match=f"declares no paths.{missing}") as caught:
        load_resource_record(write_record(tmp_path, f"_schema: {RECORD_FORMAT}\n{paths}"))
    assert caught.value.exit_status == 2
    assert caught.value.findings[0].locus == f"/paths/{missing}"


def test_the_resources_default_lies_in_the_configured_state_folder(tmp_path: Path) -> None:
    record = load_resource_record(write_record(tmp_path, HEADER + "  state: var/kleisis\n"))

    assert record.paths.state.as_posix() == "var/kleisis"
    assert record.paths.resources.as_posix() == "var/kleisis/resources"


def test_a_configured_resources_folder_overrides_the_state_default(tmp_path: Path) -> None:
    record = load_resource_record(
        write_record(
            tmp_path,
            HEADER + "  resources: .schema-resources\n",
        )
    )

    assert record.paths.state.as_posix() == ".kleisis"
    assert record.paths.resources.as_posix() == ".schema-resources"


@pytest.mark.parametrize("body", ["_schema: kleisis:schema-resources@1\n", "format_version: 1\n"])
def test_a_format_1_record_names_the_moved_resources_default(tmp_path: Path, body: str) -> None:
    with pytest.raises(RecordError, match="paths.resources: .schema-resources") as caught:
        load_resource_record(write_record(tmp_path, body))
    assert f"_schema: {RECORD_FORMAT}" in str(caught.value)
    assert caught.value.exit_status == 2


@pytest.mark.parametrize(
    "body, locus",
    [
        ("exports: []\n", "/_schema"),
        ("_schema: kleisis:schema-resources@1\n", "/_schema"),
        ("_schema: kleisis:schema-resources@2\n", "/_schema"),
        ("_schema: kleisis:schema-resources@3\n", "/_schema"),
        ("_schema: kleisis:schema-registry@2\n", "/_schema"),
        ("format_version: 1\n", "/format_version"),
        (HEADER + "format_version: 1\n", "/format_version"),
    ],
)
def test_a_record_without_its_format_identifier_is_rejected(
    tmp_path: Path, body: str, locus: str
) -> None:
    with pytest.raises(RecordError, match=f"_schema: {RECORD_FORMAT}") as caught:
        load_resource_record(write_record(tmp_path, body))
    assert caught.value.exit_status == 2
    assert caught.value.findings[0].locus == locus


def test_the_schema_convention_is_read_from_the_record(tmp_path: Path) -> None:
    record = load_resource_record(
        write_record(
            tmp_path,
            """_schema: kleisis:configuration@1
paths:
  owned_schemas: specs/schemas
  registry: specs/schema-registry.yml
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
    record = load_resource_record(write_record(tmp_path, HEADER + "schemas:\n  binding: kind\n"))

    assert record.schemas.suffixes == (".schema.yaml", ".schema.yml", ".schema.json")
    assert record.schemas.binding == "kind"


@pytest.mark.parametrize(
    "body, message",
    [
        (HEADER + "extra: true\n", "unknown fields"),
        (HEADER + "schemas:\n  pattern: x\n", "unknown fields"),
        (HEADER + "schemas:\n  suffixes: []\n", "must not be empty"),
        (
            HEADER + "schemas:\n  suffixes: [.yml, .yml]\n",
            "must be unique",
        ),
        (
            HEADER + "schemas:\n  suffixes: [schema.yml]\n",
            "open with a dot",
        ),
        (
            HEADER + "schemas:\n  suffixes: [.a/b.yml]\n",
            "open with a dot",
        ),
        (HEADER + "schemas:\n  binding: a/b\n", "property name"),
        (HEADER + "schemas:\n  binding: ''\n", "non-empty string"),
        (
            """_schema: kleisis:configuration@1
paths:
  owned_schemas: specs/schemas
  registry: specs/schema-registry.yml
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
            """_schema: kleisis:configuration@1
paths:
  owned_schemas: ../schemas
  registry: specs/schema-registry.yml
""",
            "must stay inside",
        ),
        (
            """_schema: kleisis:configuration@1
paths:
  owned_schemas: specs/schemas
  registry: specs/schema-registry.yml
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
            """_schema: kleisis:configuration@1
paths:
  owned_schemas: specs/schemas
  registry: specs/schema-registry.yml
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
            """_schema: kleisis:configuration@1
paths:
  owned_schemas: specs/schemas
  registry: specs/schema-registry.yml
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


def test_yaml_merge_keys_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(RecordError, match=r"(?s)merge key `<<`.*record\.yml\", line 7"):
        load_resource_record(
            write_record(
                tmp_path,
                HEADER + "base: &base {a: 1}\nother:\n  <<: *base\n",
            )
        )


def test_duplicate_yaml_keys_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(RecordError, match="duplicate key"):
        load_resource_record(
            write_record(
                tmp_path,
                HEADER + HEADER,
            )
        )


def test_git_source_records_credential_and_archive_roots(tmp_path: Path) -> None:
    record = load_resource_record(
        write_record(
            tmp_path,
            """_schema: kleisis:configuration@1
paths:
  owned_schemas: specs/schemas
  registry: specs/schema-registry.yml
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
            f"""_schema: kleisis:configuration@1
paths:
  owned_schemas: specs/schemas
  registry: specs/schema-registry.yml
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
                """_schema: kleisis:configuration@1
paths:
  owned_schemas: specs/schemas
  registry: specs/schema-registry.yml
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


@pytest.mark.parametrize(
    ("body", "locus"),
    [
        ("exports: [Consumer:owned@1]\n", "/exports/0"),
        (
            "dependencies:\n  - id: shared\n    source:\n      kind: git\n"
            "      repository: https://example.org/shared.git\n"
            f"      commit: {'a' * 40}\n"
            "    registry: specs/schema-registry.yml\n    contracts: [shared:main]\n",
            "/dependencies/0/contracts/0",
        ),
    ],
)
def test_a_contract_outside_the_grammar_is_rejected(tmp_path: Path, body: str, locus: str) -> None:
    with pytest.raises(RecordError, match="no contract identifier") as caught:
        load_resource_record(write_record(tmp_path, HEADER + body))
    finding = caught.value.findings[0]
    assert finding.rule.id == "contract/identifier"
    assert finding.locus == locus


def test_a_child_project_declares_its_registry(tmp_path: Path) -> None:
    with pytest.raises(RecordError, match=r"projects\[0\]\.registry") as caught:
        load_resource_record(write_record(tmp_path, HEADER + "projects:\n  - path: child\n"))
    assert caught.value.findings[0].locus == "/projects/0/registry"


@pytest.mark.parametrize(
    ("keywords", "locus"),
    [
        ('  kind: "a keyword without its prefix"\n', "/keywords/kind"),
        ("  x-Kind: a keyword in capitals\n", "/keywords/x-Kind"),
        ('  x-kind: ""\n', "/keywords/x-kind"),
    ],
)
def test_a_keyword_declaration_is_checked(tmp_path: Path, keywords: str, locus: str) -> None:
    with pytest.raises(RecordError) as caught:
        load_resource_record(write_record(tmp_path, HEADER + "keywords:\n" + keywords))
    assert caught.value.findings[0].locus == locus


@pytest.mark.parametrize(
    ("body", "locus", "label"),
    [
        (
            "keywords:\n  x-identifier: {meaning: a mapping}\n",
            "/keywords/x-identifier",
            "keyword x-identifier",
        ),
        (
            "keywords:\n  x-a/b: slashed\n  x-c~d: tilded\n",
            "/keywords/x-a~1b",
            "keyword 'x-a/b'",
        ),
        ("schemas:\n  suffixes: [.schema.yaml, schema.yml]\n", "/schemas/suffixes/1", ""),
        (
            "dependencies:\n  - id: shared\n    source:\n      kind: git\n"
            "      repository: https://example.org/shared.git\n"
            f"      commit: {'a' * 40}\n"
            "      archive_roots: [specs, ../outside]\n"
            "    registry: specs/schema-registry.yml\n",
            "/dependencies/0/source/archive_roots/1",
            "dependencies[0].source.archive_roots[1]",
        ),
        ("projects:\n  - path: child\n    registry: [x]\n", "/projects/0/registry", ""),
    ],
)
def test_a_defect_names_the_exact_locus(tmp_path: Path, body: str, locus: str, label: str) -> None:
    with pytest.raises(RecordError) as caught:
        load_resource_record(write_record(tmp_path, HEADER + body))
    finding = caught.value.findings[0]
    assert finding.locus == locus
    assert finding.message.startswith(label)


def test_a_key_holding_a_tilde_escapes_in_the_locus(tmp_path: Path) -> None:
    with pytest.raises(RecordError) as caught:
        load_resource_record(write_record(tmp_path, HEADER + "keywords:\n  x-c~d: tilded\n"))
    assert caught.value.findings[0].locus == "/keywords/x-c~0d"


def test_a_path_names_the_key_that_declares_it() -> None:
    place = KeyPath(("keywords", "x-identifier", "path"))

    with pytest.raises(RecordError, match="keywords.x-identifier.path must stay") as caught:
        safe_relative_path("../outside", place)
    assert caught.value.findings[0].locus == "/keywords/x-identifier/path"


def test_a_configuration_that_is_no_mapping_concerns_the_whole_file(tmp_path: Path) -> None:
    with pytest.raises(RecordError, match="configuration must be a mapping") as caught:
        load_resource_record(write_record(tmp_path, "- a list\n"))
    assert caught.value.findings[0].locus == ""
