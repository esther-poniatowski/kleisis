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
    record = load_resource_record(write_record(tmp_path, "format_version: 1\n"))

    assert record.paths.owned_schemas.as_posix() == "specs/schemas"
    assert record.paths.registry.as_posix() == "specs/schema-registry.yml"
    assert record.paths.resources.as_posix() == ".schema-resources"


@pytest.mark.parametrize(
    "body, message",
    [
        ("format_version: 1\nextra: true\n", "unknown fields"),
        (
            """format_version: 1
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
            """format_version: 1
paths:
  owned_schemas: ../schemas
""",
            "must stay inside",
        ),
        (
            """format_version: 1
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
            """format_version: 1
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
            """format_version: 1
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
        load_resource_record(write_record(tmp_path, "format_version: 1\nformat_version: 1\n"))


def test_git_source_records_credential_and_archive_roots(tmp_path: Path) -> None:
    record = load_resource_record(
        write_record(
            tmp_path,
            """format_version: 1
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
