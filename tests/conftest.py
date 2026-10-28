"""Shared fixtures for schema-closure tests."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from schema_closure.domain.models import Source


def concrete_schema(identifier: str, *, reference: str | None = None) -> str:
    """Return a minimal concrete schema with an optional external reference."""
    reference_property = ""
    if reference is not None:
        reference_property = f'\n    value:\n      $ref: "{reference}"'
    return f"""$schema: https://json-schema.org/draft/2020-12/schema
$id: {identifier.rsplit(":", 1)[-1].split("@", 1)[0]}.schema.yaml
type: object
properties:
  _schema:
    const: {identifier}{reference_property}
required:
  - _schema
"""


def git(arguments: list[str], root: Path) -> str:
    """Run Git in a disposable test repository."""
    completed = subprocess.run(
        ["git", *arguments], cwd=root, check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


class CopySourceProvider:
    """Supply one fixture tree without admitting local paths into canonical records."""

    def __init__(self, source_root: Path):
        self.source_root = source_root

    def acquire(self, source: Source, destination: Path) -> None:
        del source
        shutil.copytree(self.source_root, destination)


@pytest.fixture
def exact_source(tmp_path: Path) -> tuple[Path, str]:
    """Create an exact local Git source with one selected schema and one referenced file."""
    root = tmp_path / "source"
    schemas = root / "schemas"
    schemas.mkdir(parents=True)
    (schemas / "main.schema.yaml").write_text(
        concrete_schema("source:main@1", reference="defs.schema.yaml#/$defs/value"),
        encoding="utf-8",
    )
    (schemas / "defs.schema.yaml").write_text(
        """$id: defs.schema.yaml
$defs:
  value:
    type: string
""",
        encoding="utf-8",
    )
    (root / "specs").mkdir()
    (root / "specs/schema-registry.yml").write_text(
        """schema_version: 1
schemas:
  source:main@1: schemas/main.schema.yaml
exports:
  - source:main@1
""",
        encoding="utf-8",
    )
    git(["init", "--quiet"], root)
    git(["config", "user.name", "Schema Closure Tests"], root)
    git(["config", "user.email", "tests@example.invalid"], root)
    git(["add", "."], root)
    git(["commit", "--quiet", "-m", "fixture"], root)
    return root, git(["rev-parse", "HEAD"], root)
